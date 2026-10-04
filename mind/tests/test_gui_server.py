"""app/gui_server.py の1日の流れと、Masterが消すときのテスト。設計書 §2.4・§4。

守るもの：
- 見回りスレッドは例外を飲み込んで黙って止まらない。
- 起動はMasterの来訪ではない。Masterが来るまで、Pulseは目覚めて伝えたいことだけ（暇や気分では話しかけない）。
  目覚めのあとにMasterが来たことは、再起動しても忘れない（伝えたことをもう一度伝えに行かない）。
- 日界の処理は、Masterの最初の発言のあと、会話が途切れてから。眠り終えてからセッションを切り替え、
  手元の会話の流れには、まだ眠っていない今日の発言だけを残す。眠りの途中で起こされたら切り替えない。
- 起動時の朝礼は眠ってから今日の境界を記録する（見回りが今日の日界をもう一度走らせない）。
- 脳の不調で眠れなかったら、しばらくあけてから続きから眠る（見回りのたびに失敗を繰り返さない。眠り残しは忘れない）。
- Masterが消した発言は、帳簿と生ログ（本文を消した印の行になる）から消え、その発言に拠っていた記憶のページも外れる。
Ollama不要（フェイクの埋め込みと、眠り・人格の見直しの替え玉）。
"""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
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
from mind.core.config import ThresholdsConfig
from mind.core.idea import Idea
from mind.core.lifelog import ConversationLog, read_conversation
from mind.core.memory.memory import Memory
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.session_store import SessionStore
from mind.core.memory.sleep import SleepReport
from mind.core.memory.structure import conversation_refs
from mind.core.memory.waking import Waking
from mind.core.memory.writing import WordsRejected
from mind.core.protection import ChangeLog, GenerationStore
from mind.core.state.emotion import EmotionState
from mind.core.state.serina_day import serina_day_id, serina_day_start
from mind.core.state.session import SessionState, Turn

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 5, 9, 30, tzinfo=JST)  # 10/5 の Serina 日（朝7時から）
TIMING = AppTimingConfig(idle_poll_interval_seconds=0.05, gpu_busy_threshold_percent=1_000_000.0,  # type: ignore[arg-type]
                         serina_day_grace_after_activity_seconds=900)


class _Core:
    """日界の処理が触る Core の部分だけ。"""

    def __init__(self, memory=None) -> None:  # noqa: ANN001
        self.memory = memory
        self.session = SessionState()
        self.emotion = EmotionState()
        self.thresholds = ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1)

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
    state.emotion_state_path = tmp / "emotion_state.json"
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

    def fake_sleep(core, *, now, should_stop, on_diary, progress):  # noqa: ANN001, ARG001
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
    state = _state(tmp_path)
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


def test_morning_routine_sleeps_and_records_todays_boundary(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    class _Store:
        def sync_conversation_log(self) -> int:
            return 0

    state = _state(tmp_path, store=_Store())  # type: ignore[arg-type]
    gui_server.run_startup_morning_routine(state, TIMING, now=NOW)
    assert sleeping.sleeps == 1
    assert state.last_boundary_serina_day == serina_day_id(NOW)
    state.last_activity_at = NOW - timedelta(hours=2)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(hours=1))
    assert sleeping.sleeps == 1 and state.session_id == "s_current"  # 会話中に二度目の日界を走らせない


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


def test_sleep_owed_from_the_morning_is_taken_when_the_conversation_pauses(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """起動時の眠りが失敗しても、その日の境界は済んだことにする（会話中に切り替えない）。眠り残しは、会話が途切れたら眠る。"""
    class _Store:
        def sync_conversation_log(self) -> int:
            return 0

    sleeping.fail = True
    state = _state(tmp_path, store=_Store())  # type: ignore[arg-type]
    gui_server.run_startup_morning_routine(state, TIMING, now=NOW)
    assert state.sleep_owed and state.last_boundary_serina_day == date(2026, 10, 5)
    sleeping.fail = False
    state.last_activity_at = NOW + timedelta(minutes=10)
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=15))
    assert sleeping.sleeps == 1  # まだ会話中（グレースの中）
    gui_server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=30))
    assert sleeping.sleeps == 2 and not state.sleep_owed
    assert state.session_id == "s_current"  # 日界ではないので切り替えない


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


def test_pulse_waits_for_the_first_turn(tmp_path: Path) -> None:
    """起動してからMasterが来るまで、暇や気分では話しかけない（目覚めて伝えたいことがなければ、何もしない）。"""
    state = _state(tmp_path)
    state.last_activity_at = None
    state.core.emotion.mood = {axis: 1.0 for axis in state.core.emotion.mood}
    state.core.generate_pulse_text = lambda _c: pytest.fail("最初の発言の前に話しかけた")
    gui_server._maybe_fire_pulse_inner(state, now=NOW + timedelta(hours=3))


def test_she_tells_what_she_woke_with_before_master_comes(tmp_path: Path, sleeping, monkeypatch) -> None:  # noqa: ANN001
    """起動時の朝礼で目覚めて伝えたいことができたら、Masterがまだ来ていなくても本人から伝えに行く（1回の目覚めで1度だけ）。"""
    class _Store:
        def __init__(self) -> None:
            self.history: list[tuple[str, str]] = []

        def sync_conversation_log(self) -> int:
            return 0

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
    state = _state(tmp_path, core=_Core(memory=SimpleNamespace(waking=lambda: latest[-1] if latest else None)), store=store)  # type: ignore[arg-type]
    state.last_activity_at = None  # 起動したところ。Masterはまだ来ていない
    asked: list[str] = []
    state.core.generate_pulse_text = lambda c: asked.append(c.kind) or "おはよう、約束楽しみだね"

    gui_server.run_startup_morning_routine(state, TIMING, now=NOW)
    gui_server._maybe_fire_pulse_inner(state, now=NOW + timedelta(minutes=1))

    assert asked == ["wake"]
    assert store.history == [("assistant", "おはよう、約束楽しみだね")]
    gui_server._maybe_fire_pulse_inner(state, now=NOW + timedelta(hours=2))
    assert asked == ["wake"]  # 同じ目覚めで二度は行かない。暇でも、Masterが来るまでは話しかけない


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

    gui_server._maybe_fire_pulse_inner(state, now=NOW.replace(hour=9, minute=0))

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
    keep = store.add_history("s_past", "user", "高野漁港の話")
    drop = store.add_history("s_past", "assistant", "約束の海だね")
    lines = read_conversation(idea.conversation)
    page = Page(
        id="ep-2026-10-04-01", kind="episode", start=lines[0].ts, end=lines[-1].ts,
        source=conversation_refs(lines), concepts=("高野漁港",),
    )
    write_page(idea.memory, page.with_words(title="約束の海", gist="要点", importance=8, feeling={}, written_by="test"))
    memory = Memory(idea, embed=_embed, embed_model="fake")
    memory.rebuild_index(progress=lambda _m: None)
    state = _state(tmp_path, core=_Core(memory), store=store)
    return SimpleNamespace(idea=idea, store=store, state=state, keep=keep, drop=drop, memory=memory)


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
