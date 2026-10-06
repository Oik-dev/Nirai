"""app/gui_server.py の1日の流れと、Masterが消すときのテスト。設計書 §2.4・§4。

守るもの：
- 見回りスレッドは例外を飲み込んで黙って止まらない。
- 起動はMasterの来訪ではない。Masterが来るまで、Pulseは目覚めて伝えたいことだけ（暇や気分では話しかけない）。
  目覚めのあとにMasterが来たことは、再起動しても忘れない（伝えたことをもう一度伝えに行かない）。
- 日界の処理は、Masterの最初の発言のあと、会話が途切れてから。眠り終えてからセッションを切り替え、
  手元の会話の流れには、まだ眠っていない今日の発言だけを残す。眠りの途中で起こされたら切り替えない。
- 起動したらすぐ話せる。眠りは見回りが裏で、すぐに始める（話しかけられたら区切りで起きる）。眠り終えるまでは、
  記録のうちまだ記憶になっていない前の日の会話が、どのセッションのものも手元にあり（起動し直しを重ねても、眠りが途中でも）、
  眠り終えたら今日の分だけになる。起動で今日の境界は済む（見回りが今日の日界をもう一度走らせない）。
- 脳の不調で眠れなかったら、しばらくあけてから続きから眠る（見回りのたびに失敗を繰り返さない。眠り残しは忘れない）。
- Masterが消した発言は、帳簿と生ログ（本文を消した印の行になる）から消え、その発言に拠っていた記憶のページも外れる。
  手元の会話の流れからも消える（どのセッションの発言でも）。
Ollama不要（フェイクの埋め込みと、眠り・人格の見直しの替え玉）。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from urllib.parse import quote
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.app import gui_server
from mind.app.idle_config import AppTimingConfig
from mind.core.chores.persona_propose import ProposeOutcome
from mind.core.config import ThresholdsConfig, load_thresholds
from mind.core.feeling.appraisal import Appraisal
from mind.core.feeling.feelings import Feelings
from mind.core.idea import RESIDENT_NAME, Idea
from mind.core.lifelog import ConversationLog, FeelingLog, read_conversation
from mind.core.memory import session_store as ledger_module
from mind.core.memory.memory import Memory
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.session_store import SessionStore
from mind.core.memory.sleep import SleepReport
from mind.core.memory.structure import conversation_refs
from mind.core.memory.waking import Waking
from mind.core.memory.writing import WordsRejected
from mind.core.protection import ChangeLog, GenerationStore
from mind.core.runtime import Core
from mind.core.state.serina_boundary_state import load_serina_boundary_state
from mind.core.state.serina_day import serina_day_id, serina_day_start
from mind.core.state.session import SessionState, Turn
from mind.core.state.session_book import SessionManager

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 5, 9, 30, tzinfo=JST)  # 10/5 の Serina 日（朝7時から）
TIMING = AppTimingConfig(idle_poll_interval_seconds=0.05, gpu_busy_threshold_percent=1_000_000.0,  # type: ignore[arg-type]
                         serina_day_grace_after_activity_seconds=900)


class _Core:
    """日界の処理が触る Core の部分だけ。"""

    def __init__(self, memory=None) -> None:  # noqa: ANN001
        self.memory = memory
        self.session = SessionState()
        self.feelings = None
        self.thresholds = ThresholdsConfig()

    def end_session(self, *, keep=()) -> None:  # noqa: ANN001
        self.session = SessionState(turns=keep)


class _Rotator:
    def __init__(self) -> None:
        self.rotations = 0

    def rotate(self, current: str, *, now: datetime) -> str:  # noqa: ARG002
        self.rotations += 1
        return f"s_new_{self.rotations}"


def _state(tmp: Path, *, core: _Core | None = None, store: SessionStore | None = None) -> gui_server.GuiState:
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core or _Core()
    state.session_store = store
    state.session_mgr = _Rotator()
    state.session_id = "s_current"
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.call_fn = lambda _prompt: ""
    state.change_log = ChangeLog(tmp / "change_log.jsonl")
    state.generation_store = GenerationStore(tmp / "generations.jsonl")
    state.last_sleep = None
    state.sleep_owed = False
    state.sleep_retry_at = None
    state.last_activity_at = NOW - timedelta(hours=1)
    state.serina_boundary_state_path = tmp / "serina_boundary_state.json"
    state.last_boundary_serina_day = date(2026, 10, 4)
    state.persona_propose_state_path = tmp / "persona_propose_state.json"
    state.last_persona_propose_at = None
    state.pulse_state_path = tmp / "pulse.json"
    state.pulse_mute = False
    state.pulse_queue = []
    state._pulse_lock = threading.Lock()
    gui_server.STATE = state
    return state


@pytest.fixture
def sleeping(monkeypatch):  # noqa: ANN001, ANN201
    """眠りと人格の見直しと目覚めの替え玉。finished を変えると、起こされた眠りになる。"""
    calls = SimpleNamespace(sleeps=0, grows=0, wakes=0, finished=True, fail=False, cannot_write_self=False)

    def fake_sleep(core, *, now, should_stop, progress):  # noqa: ANN001, ARG001
        calls.sleeps += 1
        if calls.fail:
            raise ConnectionError("Ollama が止まっている")
        return SleepReport(finished=calls.finished)

    def fake_grow(core, **_kwargs):  # noqa: ANN001, ANN003, ARG001
        calls.grows += 1
        return ProposeOutcome(asked=True, revise=False)

    def fake_wake(core, *, now):  # noqa: ANN001, ARG001
        calls.wakes += 1
        if calls.cannot_write_self:
            raise WordsRejected("3回とも書けなかった")

    monkeypatch.setattr(gui_server, "run_sleep", fake_sleep)
    monkeypatch.setattr(gui_server, "run_waking", fake_wake)
    monkeypatch.setattr(gui_server, "run_persona_growth_for", fake_grow)
    monkeypatch.setattr(gui_server, "is_gpu_busy", lambda _threshold: False)
    return calls


# --- 見回り ---------------------------------------------------------------------


def test_idle_watchdog_survives_several_ticks_without_exception(tmp_path: Path, sleeping, caplog) -> None:  # noqa: ANN001
    """沈黙する失敗モード対策：見回りスレッドは複数tick後も生きていて、例外ログを出していない。"""
    state = _state(tmp_path, store=_QuietStore())  # type: ignore[arg-type]
    state.last_activity_at = None  # 起動してから、Masterはまだ来ていない
    caplog.set_level(logging.ERROR, logger="mind.app.gui_server")
    thread = threading.Thread(target=gui_server._idle_watchdog, args=(state, TIMING), daemon=True)
    thread.start()
    time.sleep(0.3)
    assert thread.is_alive()
    assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []


# --- 日界：眠ってから切り替える ---------------------------------------------------


def test_boundary_waits_for_the_first_turn(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    state.last_activity_at = None  # 起動してから、Masterはまだ来ていない
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 0


def test_boundary_waits_until_the_conversation_pauses(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    state.last_activity_at = NOW - timedelta(minutes=5)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 0


def test_boundary_sleeps_then_rotates_and_keeps_only_todays_turns(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    yesterday = Turn("master", "昨日の話", ts=(NOW - timedelta(hours=12)).isoformat())
    today = Turn("master", "今朝の話", ts=(NOW - timedelta(hours=1)).isoformat())
    state.core.session = SessionState(turns=[yesterday, today])

    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)

    assert (sleeping.sleeps, sleeping.grows) == (1, 1)
    assert state.session_id == "s_new_1"
    assert state.core.session.turns == [today]  # 昨日の分は眠って記憶になった
    assert state.last_boundary_serina_day == date(2026, 10, 5)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=1))
    assert sleeping.sleeps == 1  # 同じ日にもう一度は眠らない


def test_woken_sleep_does_not_rotate(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """眠りの途中で起こされたら、切り替えない（昨日の会話は手元に残る）。次に会話が途切れたら続きから眠る。"""
    sleeping.finished = False
    state = _state(tmp_path)
    yesterday = Turn("master", "昨日の話", ts=(NOW - timedelta(hours=12)).isoformat())
    state.core.session = SessionState(turns=[yesterday])

    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)

    assert state.session_id == "s_current"
    assert state.core.session.turns == [yesterday]
    assert state.last_boundary_serina_day == date(2026, 10, 4)
    assert sleeping.grows == 0


def test_rotation_waits_while_a_turn_is_in_progress(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    with state.turn_lock:
        gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert state.session_id == "s_current"
    assert state.last_boundary_serina_day == date(2026, 10, 4)


@pytest.fixture
def restarts(tmp_path: Path, monkeypatch):  # noqa: ANN001, ANN201
    """本物の帳簿・セッションの決まり（6時間あくと区切る）・記憶で、起動し直しを重ねる。

    start(at) はその時刻に起動する（本物と同じく前の起動が保存した境界を読み、起動の朝礼まで）。
    say は、いちばん新しく起動したときのセッションで話す。
    slept(text) は、その発言を出来事のページにする（眠って記憶になった）。
    """
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text(f'name = "{RESIDENT_NAME}"\n', encoding="utf-8")  # 帳簿が生ログに書く話者の名前
    idea = Idea.open(root)
    store = SessionStore(tmp_path / "ledger.db", conversation_log=ConversationLog(idea.conversation))
    manager = SessionManager(store)
    memory = Memory(idea, embed=_embed, embed_model="fake")
    world = SimpleNamespace(state=None)

    def start(at: datetime) -> gui_server.GuiState:
        session_id, _closed = manager.resolve_active_session(now=at)
        core = Core(persona_text="人格", absolute_rules="ルール", thresholds=ThresholdsConfig(), memory=memory)
        state = _state(tmp_path, core=core, store=store)  # type: ignore[arg-type]
        state.session_id = session_id
        state.last_activity_at = None  # 起動したところ
        state.last_boundary_serina_day = load_serina_boundary_state(state.serina_boundary_state_path)
        gui_server.run_startup_morning_routine(state, now=at)
        world.state = state
        return state

    def say(role: str, text: str, *, at: datetime) -> int:
        monkeypatch.setattr(ledger_module, "_utc_now_iso", lambda: at.astimezone(timezone.utc).isoformat())
        return store.add_history(world.state.session_id, role, text).id

    def slept(text: str) -> None:
        line = next(line for line in read_conversation(idea.conversation) if line.text == text)
        page = Page(id=f"ep-{line.day_file}-01", kind="episode", start=line.ts, end=line.ts,
                    source=conversation_refs([line]), concepts=())
        write_page(idea.memory, page.with_words(title="話", gist="要点", importance=5, written_by="test"))

    world.start, world.say, world.slept = start, say, slept
    return world


def _yesterday_and_this_morning(restarts) -> gui_server.GuiState:  # noqa: ANN001
    """一昨日（もう記憶になった）・昨日の夜・今朝に話してから、今起動する。どれも別のセッション（6時間あいた）。"""
    restarts.start(NOW - timedelta(days=2))
    restarts.say("user", "一昨日の話", at=NOW - timedelta(days=2))
    restarts.slept("一昨日の話")
    restarts.start(NOW - timedelta(hours=12))
    restarts.say("user", "昨日の話", at=NOW - timedelta(hours=12))
    restarts.say("assistant", "昨日の返事", at=NOW - timedelta(hours=12) + timedelta(seconds=5))
    restarts.start(NOW - timedelta(minutes=20))
    restarts.say("user", "今朝の話", at=NOW - timedelta(minutes=20))
    return restarts.start(NOW)


def test_startup_is_ready_at_once_and_keeps_the_unslept_talk_in_hand(restarts, sleeping) -> None:  # noqa: ANN001
    state = _yesterday_and_this_morning(restarts)
    session = state.session_id

    assert sleeping.sleeps == 0  # 起動では眠らない（すぐ話せる）
    assert state.sleep_owed and state.last_boundary_serina_day == serina_day_id(NOW)
    # まだ記憶になっていない前の日の会話は、どのセッションのものも、眠り終えるまで手元にある（記憶になった一昨日の分は戻さない）
    assert [t.text for t in state.core.session.turns] == ["昨日の話", "昨日の返事", "今朝の話"]

    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))

    assert sleeping.sleeps == 1 and not state.sleep_owed  # 誰も来ていなければ、すぐ裏で眠る
    assert [t.text for t in state.core.session.turns] == ["今朝の話"]  # 眠り終えたら、今日の分だけ
    assert state.session_id == session  # 起動のときに帳簿が決めたセッションのまま
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(hours=1))
    assert sleeping.sleeps == 1  # 同じ日に、もう眠らない


def test_a_restart_brings_back_unslept_talk_from_every_session(restarts) -> None:  # noqa: ANN001
    """昨日10時に話し、18時に起動し直して（6時間あいたのでセッションが区切られる）また話し、今朝また起動した。"""
    yesterday = NOW - timedelta(days=1)
    restarts.start(yesterday.replace(hour=10))
    morning = restarts.say("user", "昨日の朝の話", at=yesterday.replace(hour=10))
    restarts.start(yesterday.replace(hour=18))  # 昨日の朝の話は、まだ同じ Serina 日なので眠っていない
    restarts.say("user", "昨日の夜の話", at=yesterday.replace(hour=18, minute=5))

    state = restarts.start(NOW)
    assert [t.text for t in state.core.session.turns] == ["昨日の朝の話", "昨日の夜の話"]

    TestClient(gui_server.app).delete(f"/api/messages/{morning}?confirm=true")  # 前のセッションの発言を消すと
    assert [t.text for t in state.core.session.turns] == ["昨日の夜の話"]  # 手元からも消える


@pytest.mark.parametrize("trouble", [{"finished": False}, {"fail": True}], ids=["woken", "failed"])
def test_a_restart_after_an_unfinished_sleep_keeps_yesterday_in_hand(restarts, sleeping, trouble) -> None:  # noqa: ANN001
    restarts.start(NOW - timedelta(hours=12))
    restarts.say("user", "昨日の話", at=NOW - timedelta(hours=12))
    state = restarts.start(NOW)
    vars(sleeping).update(trouble)  # 眠りの途中で起こされた・脳の不調で眠れなかった
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))
    assert sleeping.sleeps == 1 and state.sleep_owed
    assert load_serina_boundary_state(state.serina_boundary_state_path) == serina_day_id(NOW)

    state = restarts.start(NOW + timedelta(minutes=5))  # 同じ日に起動し直した（今日の境界は、もう済んでいる）
    assert [t.text for t in state.core.session.turns] == ["昨日の話"]


def test_talking_right_after_startup_wakes_the_sleep_and_keeps_yesterday_in_hand(restarts, sleeping, monkeypatch) -> None:  # noqa: ANN001
    state = _yesterday_and_this_morning(restarts)
    session = state.session_id
    stops: list[bool] = []

    def woken_sleep(core, *, now, should_stop, progress):  # noqa: ANN001, ARG001
        state.last_activity_at = NOW + timedelta(seconds=30)  # 眠っている間に、Masterが話しかけた
        stops.append(should_stop())
        return SleepReport(finished=not stops[-1])

    monkeypatch.setattr(gui_server, "run_sleep", woken_sleep)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))

    assert stops == [True] and state.sleep_owed
    assert [t.text for t in state.core.session.turns] == ["昨日の話", "昨日の返事", "今朝の話"]  # 昨日の分は手元に残る
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=5))
    assert stops == [True]  # 会話中は眠らない
    monkeypatch.setattr(gui_server, "run_sleep", lambda core, **_k: SleepReport(finished=True))  # noqa: ARG005
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=20))
    assert not state.sleep_owed and [t.text for t in state.core.session.turns] == ["今朝の話"]
    assert state.session_id == session


def test_she_wakes_after_sleeping_to_the_end_and_growing(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """眠る → 人格を見直す → 目覚めて今の自分を書く。起こされた眠りでは目覚めない。今の自分を書けなくても眠りは済む。"""
    state = _state(tmp_path)
    sleeping.finished = False
    assert not gui_server._sleep_and_grow(state, now=NOW)
    assert (sleeping.grows, sleeping.wakes) == (0, 0)
    sleeping.finished = True
    assert gui_server._sleep_and_grow(state, now=NOW)
    assert (sleeping.grows, sleeping.wakes) == (1, 1)
    sleeping.cannot_write_self = True
    assert gui_server._sleep_and_grow(state, now=NOW)
    assert sleeping.wakes == 2


def test_failed_sleep_waits_and_then_continues(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """脳が止まっていたら、決めた間（既定10分）はやり直さない。間があいたら続きから眠り、日界を済ませる。"""
    sleeping.fail = True
    state = _state(tmp_path)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 1 and state.sleep_owed and state.session_id == "s_current"
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=1))
    assert sleeping.sleeps == 1  # 間をあける
    sleeping.fail = False
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=11))
    assert sleeping.sleeps == 2 and not state.sleep_owed
    assert state.session_id == "s_new_1" and state.last_boundary_serina_day == date(2026, 10, 5)


def test_sleep_owed_from_the_morning_waits_after_a_failure_and_never_rotates(restarts, sleeping) -> None:  # noqa: ANN001
    """起動のあとの眠りが脳の不調で失敗しても、その日の境界は済んでいる（会話中に切り替えない）。間をあけて続きから眠る。"""
    sleeping.fail = True
    state = restarts.start(NOW)
    session = state.session_id
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 1 and state.sleep_owed and state.last_boundary_serina_day == date(2026, 10, 5)
    sleeping.fail = False
    state.last_activity_at = NOW + timedelta(minutes=10)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=15))
    assert sleeping.sleeps == 1  # まだ会話中（グレースの中）
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=30))
    assert sleeping.sleeps == 2 and not state.sleep_owed
    assert state.session_id == session  # 日界ではないので切り替えない


def test_flow_turns_are_only_todays_master_and_resident_lines() -> None:
    rows = [
        {"role": "user", "content": "昨日", "ts": (NOW - timedelta(hours=12)).isoformat()},
        {"role": "user", "content": "今日", "ts": (NOW - timedelta(minutes=30)).isoformat()},
        {"role": "assistant", "content": "うん", "ts": (NOW - timedelta(minutes=29)).isoformat()},
        {"role": "system", "content": "x", "ts": NOW.isoformat()},
    ]
    turns = gui_server.flow_turns(rows, since=serina_day_start(serina_day_id(NOW)))
    assert [(t.speaker, t.text) for t in turns] == [("master", "今日"), ("serina", "うん")]


# --- ターン後の要約 ---------------------------------------------------------------


def test_post_turn_summary_reentry_skips_when_lock_held(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    called = []
    monkeypatch.setattr(gui_server, "run_post_turn_summaries", lambda core, call_fn: called.append(1))
    with state.summary_lock:
        gui_server._run_post_turn_summaries_async(state)
    assert called == []
    gui_server._run_post_turn_summaries_async(state)
    assert called == [1] and not state.summary_lock.locked()


# --- Pulse -----------------------------------------------------------------------


class _QuietStore:
    def __init__(self) -> None:
        self.history: list[tuple[str, str]] = []

    def add_history(self, _session_id: str, role: str, text: str) -> None:
        self.history.append((role, text))

    def last_master_spoke_at(self) -> datetime:
        return NOW - timedelta(days=2)


def test_after_startup_she_goes_only_when_she_misses_master(tmp_path: Path) -> None:
    """起動してからMasterがまだ来ていなくても、人恋しければ会いに行く（つながりは気持ちの記録から分かる）。
    人恋しくなければ、どれだけ暇でも話しかけない。"""
    lonely = {"now": False}
    state = _state(tmp_path, store=_QuietStore())  # type: ignore[arg-type]
    state.last_activity_at = None
    state.core.feelings = SimpleNamespace(lonely=lambda _now: lonely["now"])
    asked: list[str] = []
    state.core.generate_pulse_text = lambda c: asked.append(c.kind) or "ねえ、元気にしてた？"

    gui_server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(hours=3))
    assert asked == []
    lonely["now"] = True
    gui_server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(hours=3))
    assert asked == ["connection"]
    assert state.session_store.history == [("assistant", "ねえ、元気にしてた？")]


def test_she_tells_what_she_woke_with_before_master_comes(tmp_path: Path, sleeping, monkeypatch) -> None:  # noqa: ANN001
    """起動時の朝礼で目覚めて伝えたいことができたら、Masterがまだ来ていなくても本人から伝えに行く（1回の目覚めで1度だけ）。"""
    class _Store:
        def __init__(self) -> None:
            self.history: list[tuple[str, str]] = []

        def sync_conversation_log(self) -> int:
            return 0

        def get_session_history(self, _session_id: str) -> list[dict]:
            return []

        def add_history(self, _session_id: str, role: str, text: str) -> None:
            self.history.append((role, text))

        def last_master_spoke_at(self) -> None:
            return None

    latest: list[Waking] = []

    def fake_wake(core, *, now):  # noqa: ANN001, ARG001
        latest.append(Waking(at=now, after="d1", written_by="test", self_text="今のわたし", tell="約束が楽しみ"))
        return latest[-1]

    monkeypatch.setattr(gui_server, "run_waking", fake_wake)
    store = _Store()
    memory = SimpleNamespace(  # 記録もページもない記憶（目覚めだけが分かる）
        waking=lambda: latest[-1] if latest else None, pages_lock=threading.Lock(),
        idea=SimpleNamespace(conversation=tmp_path / "conversation", memory=tmp_path / "memory", name="Serina"),
    )
    state = _state(tmp_path, core=_Core(memory=memory), store=store)  # type: ignore[arg-type]
    state.last_activity_at = None  # 起動したところ。Masterはまだ来ていない
    asked: list[str] = []
    state.core.generate_pulse_text = lambda c: asked.append(c.kind) or "おはよう、約束楽しみだね"

    gui_server.run_startup_morning_routine(state, now=NOW)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)  # 見回りが裏で眠り、目覚める
    gui_server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(minutes=1))

    assert asked == ["wake"]
    assert store.history == [("assistant", "おはよう、約束楽しみだね")]
    gui_server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(hours=2))
    assert asked == ["wake"]  # 同じ目覚めで二度は行かない。人恋しくなければ、暇でも話しかけない


@pytest.mark.parametrize(("master_spoke", "tells"), [(timedelta(minutes=30), False), (timedelta(minutes=-30), True)])
def test_restart_does_not_forget_that_master_came_after_waking(tmp_path: Path, master_spoke: timedelta, tells: bool) -> None:
    """7:30に目覚め → 8:00にMasterと話した（日界で片付いたセッション） → 9:00に再起動。もう伝えに行かない。

    Masterが目覚めより前に話しただけなら、再起動のあとでも伝えに行く。
    """
    woke = NOW.replace(hour=7, minute=30)
    store = SessionStore(tmp_path / "ledger.db", conversation_log=ConversationLog(tmp_path / "conversation"))
    store.create_session("s_morning")
    conn = store._connect()  # noqa: SLF001 — 発言の時刻を決めて帳簿に置く
    conn.execute(
        "INSERT INTO history (session_id, role, content, ts) VALUES (?, 'user', 'おはよう', ?)",
        ("s_morning", (woke + master_spoke).astimezone(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()
    store.archive_session_history("s_morning")
    store.create_session("s_current")

    waking = Waking(at=woke, after="d1", written_by="test", self_text="今のわたし", tell="約束が楽しみ")
    state = _state(tmp_path, core=_Core(memory=SimpleNamespace(waking=lambda: waking)), store=store)
    state.last_activity_at = None  # 再起動したところ
    asked: list[str] = []
    state.core.generate_pulse_text = lambda c: asked.append(c.kind) or "おはよう、約束楽しみだね"

    gui_server._maybe_fire_pulse_inner(state, TIMING, now=NOW.replace(hour=9, minute=0))

    assert asked == (["wake"] if tells else [])


# --- Masterが消す ---------------------------------------------------------------


def _embed(text: str) -> list[float]:
    return [1.0, float(len(text) % 7), 0.5, 0.0]


@pytest.fixture
def living(tmp_path: Path):  # noqa: ANN201
    """会話がひとつあって、それが出来事のページになっているイデア。"""
    root = tmp_path / "idea"
    root.mkdir()
    (root / "identity.toml").write_text('name = "Serina"\n', encoding="utf-8")
    idea = Idea.open(root)
    log = ConversationLog(idea.conversation)
    store = SessionStore(tmp_path / "ledger.db", conversation_log=log)
    store.create_session("s_past")
    store.create_session("s_current")
    keep = store.add_history("s_past", "user", "高野漁港の話").id
    drop = store.add_history("s_past", "assistant", "約束の海だね").id
    lines = read_conversation(idea.conversation)
    page = Page(
        id="ep-2026-10-04-01", kind="episode", start=lines[0].ts, end=lines[-1].ts,
        source=conversation_refs(lines), concepts=("高野漁港",),
    )
    write_page(idea.memory, page.with_words(title="約束の海", gist="要点", importance=8, written_by="test"))
    memory = Memory(idea, embed=_embed, embed_model="fake")
    memory.rebuild_index(progress=lambda _m: None)
    feelings = Feelings(FeelingLog(idea.feeling), load_thresholds().feeling)
    feelings.feel(
        Appraisal(feeling="海の約束、うれしかった", valence="うれしい", arousal="少し動いた", distance="近づいた",
                  master_state="懐かしそう"),
        source=conversation_refs(lines), at=lines[-1].ts,
    )
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=ThresholdsConfig(), memory=memory, feelings=feelings)
    state = _state(tmp_path, core=core, store=store)  # type: ignore[arg-type]
    return SimpleNamespace(idea=idea, store=store, state=state, keep=keep, drop=drop, memory=memory, feelings=feelings)


def test_deleting_requires_confirmation(living) -> None:  # noqa: ANN001
    client = TestClient(gui_server.app)
    assert client.delete(f"/api/messages/{living.drop}").status_code == 400
    assert client.delete("/api/sessions/s_past").status_code == 400
    assert client.post("/api/sessions/new").status_code == 400


def test_deleting_a_message_erases_it_and_forgets_the_page_built_on_it(living) -> None:  # noqa: ANN001
    response = TestClient(gui_server.app).delete(f"/api/messages/{living.drop}?confirm=true")
    assert response.status_code == 200
    assert response.json()["forgotten_pages"] == ["ep-2026-10-04-01"]
    assert [line.text for line in read_conversation(living.idea.conversation)] == ["高野漁港の話"]
    raw = (living.idea.conversation).glob("*.jsonl")
    rows = [json.loads(r) for path in raw for r in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[1]["deleted"] is True and "text" not in rows[1]  # 行番号は変わらない
    assert load_pages(living.idea.memory) == []
    assert "ep-2026-10-04-01" not in living.memory.index.pages
    assert any("発言" in r.action for r in living.state.change_log.read_all())
    felt = next(living.feelings.log.rows())  # 気持ちの記録は、その会話に拠った言葉だけが消え、数は残る
    assert felt["feeling"] == "" and felt["evaluation"]["master_state"] == "" and felt["after"]["connection"] > 0


def test_deleting_a_past_session_forgets_its_pages_and_the_current_one_is_refused(living) -> None:  # noqa: ANN001
    client = TestClient(gui_server.app)
    assert client.delete("/api/sessions/s_current?confirm=true").status_code == 400
    response = client.delete("/api/sessions/s_past?confirm=true")
    assert response.status_code == 200
    assert response.json()["forgotten_pages"] == ["ep-2026-10-04-01"]
    assert read_conversation(living.idea.conversation) == []


def test_new_session_starts_a_fresh_flow(living) -> None:  # noqa: ANN001
    living.state.core.session = SessionState(turns=[Turn("master", "前の話")])
    response = TestClient(gui_server.app).post("/api/sessions/new?confirm=true")
    assert response.status_code == 200
    assert living.state.core.session.turns == []
    assert living.state.session_id == "s_new_1"


def test_conversation_api_reads_backwards_from_lifelog_with_stable_refs(living) -> None:  # noqa: ANN001
    living.store.add_history("s_current", "user", "三つ目")
    living.store.add_history("s_current", "assistant", "四つ目")
    client = TestClient(gui_server.app)

    latest = client.get("/api/conversation?limit=2")
    assert latest.status_code == 200
    data = latest.json()
    assert [m["text"] for m in data["messages"]] == ["三つ目", "四つ目"]
    assert data["has_more"] is True
    first_ref = data["messages"][0]["ref"]
    assert first_ref.startswith("lifelog/conversation/")

    older = client.get("/api/conversation", params={"limit": 2, "before": first_ref})
    assert older.status_code == 200
    assert [m["text"] for m in older.json()["messages"]] == ["高野漁港の話", "約束の海だね"]
    assert older.json()["has_more"] is False


def test_conversation_ref_delete_requires_confirm_and_forgets_the_page(living) -> None:  # noqa: ANN001
    client = TestClient(gui_server.app)
    messages = client.get("/api/conversation?limit=20").json()["messages"]
    target = next(m for m in messages if m["text"] == "約束の海だね")

    encoded = quote(target["ref"], safe="")
    assert client.delete(f"/api/conversation/{encoded}").status_code == 400
    deleted = client.delete(f"/api/conversation/{encoded}", params={"confirm": "true"})
    assert deleted.status_code == 200
    assert deleted.json()["forgotten_pages"] == ["ep-2026-10-04-01"]
    assert [line.text for line in read_conversation(living.idea.conversation)] == ["高野漁港の話"]


def test_event_stream_publishes_said_without_replay_sequence(living) -> None:  # noqa: ANN001
    stream = gui_server._sse_events()
    try:
        assert next(stream) == ": connected\n\n"
        gui_server._publish_event(
            living.state,
            {"type": "said", "ref": "lifelog/conversation/2026-10-06.jsonl#1-1", "text": "いるよ"},
        )
        event = next(stream)
        assert '"type": "said"' in event
        assert '"text": "いるよ"' in event
    finally:
        stream.close()
