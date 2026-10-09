"""app/server.py の1日の流れと、Masterが消すときのテスト。設計書 §2.4・§4。

守るもの：
- 見回りスレッドは例外を飲み込んで黙って止まらない。
- 起動はMasterの来訪ではない。Masterが来るまで、Pulseは目覚めて伝えたいことだけ（暇や気分では話しかけない）。
  目覚めのあとにMasterが来たことは、記録から読むので、再起動しても忘れない（伝えたことをもう一度伝えに行かない）。
- 日界の処理は、会話が途切れてから。眠り終えてから、手元の会話の流れを、まだ眠っていない今日の発言だけにする。
  眠りの途中で起こされたら切らない。精神が何日も動き続けても、日界は毎日1回来る。
- 起動したらすぐ話せる。眠りは見回りが裏で、すぐに始める（話しかけられたら区切りで起きる）。手元の会話の流れは記録から作る：
  眠り終えるまでは、記録のうちまだ記憶になっていない前の日の会話があり（起動し直しを重ねても、眠りが途中でも、
  何時間あけて起動しても）、眠り終えたら今日の分だけになる。
- 起こし直した直後でも、記録の最後のMasterの発言から会話中を守る（すぐには眠らず、話しかけない）。
- 脳の不調で眠れなかったら、しばらくあけてから続きから眠る（見回りのたびに失敗を繰り返さない。眠り残しは忘れない）。
- Masterの手元が忙しい間は、Pulseと眠りを始めない。眠りの途中で忙しくなったら区切りで止まり、手が空いたら続きから眠る。
  会話中でなければ脳をグラボから下ろす。Masterが話しかけたら答える（話した直後は下ろさない）。
- Masterが消した発言は、記録で本文を消した印の行になり、その発言に拠っていた記憶のページも外れる。手元の会話の流れからも消える。
- 帳簿のころの口（セッション・発言のID・静的なページ）はもうない。
Ollama不要（フェイクの埋め込みと、眠り・人格の見直しの替え玉）。
"""

from __future__ import annotations

import contextlib
import json
import logging
import queue
import sys
import threading
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.app import server
from mind.app.idle_config import AppTimingConfig
from mind.core.chores.busy import Busy, BusyRule, Reading
from mind.core.chores.persona_propose import ProposeOutcome
from mind.core.config import ThresholdsConfig, load_thresholds
from mind.core.feeling.appraisal import Appraisal
from mind.core.feeling.feelings import Feelings
from mind.core.idea import Idea
from mind.core.lifelog import MASTER, ConversationLog, FeelingLog, read_conversation
from mind.core.memory.memory import Memory
from mind.core.memory.page import Page, load_pages, write_page
from mind.core.memory.sleep import SleepReport
from mind.core.memory.structure import conversation_refs
from mind.core.memory.waking import Waking
from mind.core.memory.writing import WordsRejected
from mind.core.perception import BodyCatalog
from mind.core.protection import ChangeLog, GenerationStore
from mind.core.runtime import Core
from mind.core.state.serina_boundary_state import load_serina_boundary_state
from mind.core.state.serina_day import serina_day_id
from mind.core.state.session import SessionState, Turn

JST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 5, 9, 30, tzinfo=JST)  # 10/5 の Serina 日（朝7時から）
TIMING = AppTimingConfig(idle_poll_interval_seconds=0.05, serina_day_grace_after_activity_seconds=900)  # type: ignore[arg-type]
NAME = "Serina"


class _Core:
    """日界の処理が触る Core の部分だけ。"""

    def __init__(self, memory=None, warm=None) -> None:  # noqa: ANN001
        self.memory = memory
        self.warm = warm
        self.session = SessionState()
        self.feelings = None
        self.thresholds = ThresholdsConfig()

    def end_session(self, *, keep=()) -> None:  # noqa: ANN001
        self.session = SessionState(turns=keep)


def _says(asked: list[str], words: str):  # noqa: ANN202
    """Core.pulse の替え玉。わけを積み、words を話す。"""

    def pulse(candidate, *, now, on_said, on_body=None, on_approach=None) -> bool:  # noqa: ANN001, ARG001
        asked.append(candidate.kind)
        on_said(words)
        return True

    return pulse


def _idea(tmp: Path) -> Idea:
    root = tmp / "idea"
    if not root.exists():
        root.mkdir()
        (root / "identity.toml").write_text(f'name = "{NAME}"\n', encoding="utf-8")
    return Idea.open(root)


def _state(tmp: Path, *, core: _Core | None = None, idea: Idea | None = None) -> server.MindState:
    idea = idea or _idea(tmp)
    state = server.MindState.__new__(server.MindState)
    state.core = core or _Core()
    state.conversation = ConversationLog(idea.conversation)
    state.name = idea.name
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.call_fn = lambda _prompt: ""
    state.change_log = ChangeLog(tmp / "change_log.jsonl")
    state.generation_store = GenerationStore(tmp / "generations.jsonl")
    state.last_sleep = None
    state.sleep_owed = False
    state.sleep_retry_at = None
    state.warm_failing = False
    state.last_activity_at = NOW - timedelta(hours=1)
    state.serina_boundary_state_path = tmp / "serina_boundary_state.json"
    state.last_boundary_serina_day = date(2026, 10, 4)
    state.persona_propose_state_path = tmp / "persona_propose_state.json"
    state.last_persona_propose_at = None
    state.pulse_state_path = tmp / "pulse.json"
    state.busy = Busy(BusyRule(every_seconds=0), sense=Reading)  # 忙しくない手元
    server.STATE = state
    return state


def _say(idea: Idea, speaker: str, text: str, *, at: datetime) -> None:
    ConversationLog(idea.conversation).append(ts=at.astimezone(timezone.utc).isoformat(), speaker=speaker, text=text)


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

    monkeypatch.setattr(server, "run_sleep", fake_sleep)
    monkeypatch.setattr(server, "run_waking", fake_wake)
    monkeypatch.setattr(server, "run_persona_growth_for", fake_grow)
    return calls


# --- 見回り ---------------------------------------------------------------------


class _StopWatching(BaseException):
    """見回りを終える合図。Exception ではないので、見回りは飲み込まずに抜ける。"""


def test_idle_watchdog_survives_several_ticks_without_exception(tmp_path: Path, sleeping, caplog, monkeypatch) -> None:  # noqa: ANN001
    """沈黙する失敗モード対策：見回りスレッドは複数tick後も生きていて、例外ログを出していない。
    終えたら見回りを止めて回収する（残すと、後のテストの替え玉を今の時刻で呼んでしまう）。"""
    state = _state(tmp_path)
    state.last_activity_at = None  # Masterはまだ一度も来ていない
    caplog.set_level(logging.ERROR, logger="mind.app.server")
    stop = threading.Event()
    ticks: list[datetime] = []
    tick = server._watchdog_tick_at

    def counted_tick(*args, now):  # noqa: ANN002, ANN202
        if stop.is_set():
            raise _StopWatching
        tick(*args, now=now)
        ticks.append(now)

    def watch() -> None:
        with contextlib.suppress(_StopWatching):
            server._idle_watchdog(state, TIMING)

    monkeypatch.setattr(server, "_watchdog_tick_at", counted_tick)
    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    try:
        time.sleep(0.3)
        assert thread.is_alive() and len(ticks) > 1
    finally:
        stop.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    assert [r.message for r in caplog.records if r.levelno >= logging.ERROR] == []


# --- 日界：眠ってから、手元を今日の分に ----------------------------------------------


def test_boundary_waits_for_the_first_turn(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    state.last_activity_at = None  # Masterはまだ一度も来ていない
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 0


def test_boundary_waits_until_the_conversation_pauses(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    state.last_activity_at = NOW - timedelta(minutes=5)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 0


def test_boundary_sleeps_then_keeps_only_todays_turns(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    yesterday = Turn("master", "昨日の話", ts=(NOW - timedelta(hours=12)).isoformat())
    today = Turn("master", "今朝の話", ts=(NOW - timedelta(hours=1)).isoformat())
    state.core.session = SessionState(turns=[yesterday, today])

    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)

    assert (sleeping.sleeps, sleeping.grows) == (1, 1)
    assert state.core.session.turns == [today]  # 昨日の分は眠って記憶になった
    assert state.last_boundary_serina_day == date(2026, 10, 5)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=1))
    assert sleeping.sleeps == 1  # 同じ日にもう一度は眠らない


def test_woken_sleep_keeps_yesterday_in_hand(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """眠りの途中で起こされたら、手元を切らない（昨日の会話は手元に残る）。次に会話が途切れたら続きから眠る。"""
    sleeping.finished = False
    state = _state(tmp_path)
    yesterday = Turn("master", "昨日の話", ts=(NOW - timedelta(hours=12)).isoformat())
    state.core.session = SessionState(turns=[yesterday])

    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)

    assert state.core.session.turns == [yesterday]
    assert state.last_boundary_serina_day == date(2026, 10, 4)
    assert sleeping.grows == 0


def test_boundary_waits_while_a_turn_is_in_progress(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    with state.turn_lock:
        server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert state.last_boundary_serina_day == date(2026, 10, 4)


@pytest.fixture
def restarts(tmp_path: Path):  # noqa: ANN201
    """本物の記録と記憶で、起動し直しを重ねる。

    start(at) はその時刻に起動する（本物と同じく前の起動が保存した境界を読み、起動の朝礼まで）。
    say は、その時刻に記録に書く（Master か本人か、ほかの話し手）。
    slept(text) は、その発言を出来事のページにする（眠って記憶になった）。
    """
    idea = _idea(tmp_path)
    memory = Memory(idea, embed=_embed, embed_model="fake")
    world = SimpleNamespace(state=None, idea=idea)

    def start(at: datetime) -> server.MindState:
        core = Core(persona_text="人格", absolute_rules="ルール", thresholds=ThresholdsConfig(), memory=memory)
        state = _state(tmp_path, core=core, idea=idea)  # type: ignore[arg-type]
        state.last_activity_at = None
        state.last_boundary_serina_day = load_serina_boundary_state(state.serina_boundary_state_path)
        server.run_startup_morning_routine(state, now=at)
        world.state = state
        return state

    def say(speaker: str, text: str, *, at: datetime) -> None:
        _say(idea, speaker, text, at=at)

    def slept(text: str) -> None:
        line = next(line for line in read_conversation(idea.conversation) if line.text == text)
        page = Page(id=f"ep-{line.day_file}-01", kind="episode", start=line.ts, end=line.ts,
                    source=conversation_refs([line]), concepts=())
        write_page(idea.memory, page.with_words(title="話", gist="要点", importance=5, written_by="test"))

    world.start, world.say, world.slept = start, say, slept
    return world


def _yesterday_and_this_morning(restarts) -> server.MindState:  # noqa: ANN001
    """一昨日（もう記憶になった）・昨日の夜・今朝に話してから、今起動する。"""
    restarts.start(NOW - timedelta(days=2))
    restarts.say(MASTER, "一昨日の話", at=NOW - timedelta(days=2))
    restarts.slept("一昨日の話")
    restarts.start(NOW - timedelta(hours=12))
    restarts.say(MASTER, "昨日の話", at=NOW - timedelta(hours=12))
    restarts.say(NAME, "昨日の返事", at=NOW - timedelta(hours=12) + timedelta(seconds=5))
    restarts.start(NOW - timedelta(minutes=20))
    restarts.say(MASTER, "今朝の話", at=NOW - timedelta(minutes=20))
    return restarts.start(NOW)


def test_startup_is_ready_at_once_and_keeps_the_unslept_talk_in_hand(restarts, sleeping) -> None:  # noqa: ANN001
    state = _yesterday_and_this_morning(restarts)

    assert sleeping.sleeps == 0 and state.sleep_owed  # 起動では眠らない（すぐ話せる）
    # まだ記憶になっていない前の日の会話は、眠り終えるまで手元にある（記憶になった一昨日の分は戻さない）
    assert [t.text for t in state.core.session.turns] == ["昨日の話", "昨日の返事", "今朝の話"]
    assert state.last_activity_at == NOW - timedelta(minutes=20)  # Masterが最後に話した時刻は記録から
    assert not (restarts.idea.data / "ledger.db").exists()

    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))

    assert sleeping.sleeps == 1 and not state.sleep_owed  # 会話が途切れていれば、すぐ裏で眠る
    assert [t.text for t in state.core.session.turns] == ["今朝の話"]  # 眠り終えたら、今日の分だけ
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(hours=1))
    assert sleeping.sleeps == 1  # 同じ日に、もう眠らない


def test_hours_between_startups_do_not_drop_this_mornings_talk(restarts) -> None:  # noqa: ANN001
    """朝8時に話し、6時間以上あけて起動し直しても、今朝の会話は手元にある（帳簿のセッションの区切りで抜けていた穴）。"""
    restarts.start(NOW.replace(hour=8))
    restarts.say(MASTER, "今朝の話", at=NOW.replace(hour=8, minute=1))
    restarts.say(NAME, "今朝の返事", at=NOW.replace(hour=8, minute=2))
    state = restarts.start(NOW.replace(hour=14, minute=30))
    assert [t.text for t in state.core.session.turns] == ["今朝の話", "今朝の返事"]


def test_the_flow_holds_only_master_and_the_resident(restarts) -> None:  # noqa: ANN001
    restarts.say(MASTER, "絵を描いて", at=NOW - timedelta(minutes=30))
    restarts.say("dalle.text2im", "[画像]", at=NOW - timedelta(minutes=29))
    restarts.say(NAME, "描いたよ", at=NOW - timedelta(minutes=28))
    state = restarts.start(NOW)
    assert [(t.speaker, t.text) for t in state.core.session.turns] == [("master", "絵を描いて"), ("serina", "描いたよ")]


def test_a_restart_brings_back_yesterdays_talk_and_a_delete_takes_it_from_hand(restarts) -> None:  # noqa: ANN001
    yesterday = NOW - timedelta(days=1)
    restarts.start(yesterday.replace(hour=10))
    restarts.say(MASTER, "昨日の朝の話", at=yesterday.replace(hour=10))
    restarts.start(yesterday.replace(hour=18))  # 昨日の朝の話は、まだ同じ Serina 日なので眠っていない
    restarts.say(MASTER, "昨日の夜の話", at=yesterday.replace(hour=18, minute=5))

    state = restarts.start(NOW)
    assert [t.text for t in state.core.session.turns] == ["昨日の朝の話", "昨日の夜の話"]

    client = TestClient(server.app)
    morning = next(m for m in client.get("/api/conversation").json()["messages"] if m["text"] == "昨日の朝の話")
    assert client.delete(f"/api/conversation/{quote(morning['ref'], safe='')}?confirm=true").status_code == 200
    assert [t.text for t in state.core.session.turns] == ["昨日の夜の話"]  # 手元からも消える


@pytest.mark.parametrize("trouble", [{"finished": False}, {"fail": True}], ids=["woken", "failed"])
def test_a_restart_after_an_unfinished_sleep_keeps_yesterday_in_hand(restarts, sleeping, trouble) -> None:  # noqa: ANN001
    restarts.start(NOW - timedelta(hours=12))
    restarts.say(MASTER, "昨日の話", at=NOW - timedelta(hours=12))
    state = restarts.start(NOW)
    vars(sleeping).update(trouble)  # 眠りの途中で起こされた・脳の不調で眠れなかった
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))
    assert sleeping.sleeps == 1 and state.sleep_owed
    assert load_serina_boundary_state(state.serina_boundary_state_path) is None  # 眠り終えるまで、日界は済まない

    state = restarts.start(NOW + timedelta(minutes=5))  # 同じ日に起動し直した
    assert [t.text for t in state.core.session.turns] == ["昨日の話"]


def test_talking_right_after_startup_wakes_the_sleep_and_keeps_yesterday_in_hand(restarts, sleeping, monkeypatch) -> None:  # noqa: ANN001
    state = _yesterday_and_this_morning(restarts)
    stops: list[bool] = []

    def woken_sleep(core, *, now, should_stop, progress):  # noqa: ANN001, ARG001
        state.last_activity_at = NOW + timedelta(seconds=30)  # 眠っている間に、Masterが話しかけた
        stops.append(should_stop())
        return SleepReport(finished=not stops[-1])

    monkeypatch.setattr(server, "run_sleep", woken_sleep)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))

    assert stops == [True] and state.sleep_owed
    assert [t.text for t in state.core.session.turns] == ["昨日の話", "昨日の返事", "今朝の話"]  # 昨日の分は手元に残る
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=5))
    assert stops == [True]  # 会話中は眠らない
    monkeypatch.setattr(server, "run_sleep", lambda core, **_k: SleepReport(finished=True))  # noqa: ARG005
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=20))
    assert not state.sleep_owed and [t.text for t in state.core.session.turns] == ["今朝の話"]


def test_a_restart_in_the_middle_of_a_talk_keeps_it_a_talk(restarts, sleeping) -> None:  # noqa: ANN001
    """起こし直した直後でも、記録の最後のMasterの発言から会話中を守る：会話が途切れるまで眠らず、話しかけない。"""
    restarts.say(MASTER, "ちょっと待ってて", at=NOW - timedelta(minutes=2))
    state = restarts.start(NOW)
    state.core.feelings = SimpleNamespace(lonely=lambda _now: True)
    asked: list[str] = []
    state.core.pulse = _says(asked, "ねえ")

    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))
    server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(seconds=20))
    assert (sleeping.sleeps, asked) == (0, [])

    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=14))
    assert sleeping.sleeps == 1  # 最後の発言から grace（15分）たてば眠る


def test_without_any_master_line_she_sleeps_at_once(restarts, sleeping) -> None:  # noqa: ANN001
    state = restarts.start(NOW)
    assert state.last_activity_at is None
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(seconds=20))
    assert sleeping.sleeps == 1


def test_running_for_days_she_sleeps_once_a_day(restarts, sleeping) -> None:  # noqa: ANN001
    """精神が止まらずに動き続けても（起動の朝礼が毎朝は来ない）、Serina 日ごとに1回、眠って目覚める。"""
    restarts.say(MASTER, "おやすみ", at=NOW - timedelta(hours=12))
    state = restarts.start(NOW)
    for at in (NOW + timedelta(seconds=20), NOW + timedelta(hours=1),
               NOW + timedelta(days=1), NOW + timedelta(days=1, hours=1),
               NOW + timedelta(days=2), NOW + timedelta(days=2, hours=3)):
        server._maybe_run_serina_day_boundary_inner(state, TIMING, now=at)
    assert (sleeping.sleeps, sleeping.wakes) == (3, 3)
    assert state.last_boundary_serina_day == serina_day_id(NOW + timedelta(days=2))


def test_she_wakes_after_sleeping_to_the_end_and_growing(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """眠る → 人格を見直す → 目覚めて今の自分を書く。起こされた眠りでは目覚めない。今の自分を書けなくても眠りは済む。"""
    state = _state(tmp_path)
    sleeping.finished = False
    assert not server._sleep_and_grow(state, now=NOW)
    assert (sleeping.grows, sleeping.wakes) == (0, 0)
    sleeping.finished = True
    assert server._sleep_and_grow(state, now=NOW)
    assert (sleeping.grows, sleeping.wakes) == (1, 1)
    sleeping.cannot_write_self = True
    assert server._sleep_and_grow(state, now=NOW)
    assert sleeping.wakes == 2


def test_failed_sleep_waits_and_then_continues(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """脳が止まっていたら、決めた間（既定10分）はやり直さない。間があいたら続きから眠り、日界を済ませる。"""
    sleeping.fail = True
    state = _state(tmp_path)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 1 and state.sleep_owed
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=1))
    assert sleeping.sleeps == 1  # 間をあける
    sleeping.fail = False
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=11))
    assert sleeping.sleeps == 2 and not state.sleep_owed
    assert state.last_boundary_serina_day == date(2026, 10, 5)


def test_sleep_owed_from_the_morning_waits_after_a_failure(restarts, sleeping) -> None:  # noqa: ANN001
    """起動のあとの眠りが脳の不調で失敗したら、間をあけて続きから眠る。その間にMasterが来たら、会話が途切れるまで待つ。"""
    sleeping.fail = True
    state = restarts.start(NOW)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)
    assert sleeping.sleeps == 1 and state.sleep_owed
    sleeping.fail = False
    state.last_activity_at = NOW + timedelta(minutes=10)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=15))
    assert sleeping.sleeps == 1  # まだ会話中（グレースの中）
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW + timedelta(minutes=30))
    assert sleeping.sleeps == 2 and not state.sleep_owed
    assert state.last_boundary_serina_day == date(2026, 10, 5)


# --- ターン後の要約 ---------------------------------------------------------------


def test_post_turn_summary_reentry_skips_when_lock_held(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    called = []
    monkeypatch.setattr(server, "run_post_turn_summaries", lambda core, call_fn: called.append(1))
    with state.summary_lock:
        server._run_post_turn_summaries_async(state)
    assert called == []
    server._run_post_turn_summaries_async(state)
    assert called == [1] and not state.summary_lock.locked()


# --- 忙しい間（重い作業とかぶらない）---------------------------------------------------


class _Hands:
    """Masterの手元の忙しさの替え玉。見回りと眠りの区切りが、いつ聞いたかも持つ。"""

    def __init__(self, *, busy: bool = False) -> None:
        self.now_busy = busy
        self.asked: list[datetime] = []

    def busy(self, now: datetime) -> bool:
        self.asked.append(now)
        return self.now_busy


@pytest.fixture
def resting(monkeypatch):  # noqa: ANN001, ANN201
    """脳を下ろす頼みの替え玉。頼んだ回数を持つ。"""
    calls: list[object] = []
    monkeypatch.setattr(server, "rest_brain", lambda core: calls.append(core) or True)
    return calls


def test_busy_hands_start_neither_pulse_nor_sleep_and_the_brain_rests(tmp_path: Path, sleeping, resting, monkeypatch) -> None:  # noqa: ANN001
    pulses: list[datetime] = []
    monkeypatch.setattr(server, "_maybe_fire_pulse", lambda _state, _timing, *, now: pulses.append(now))
    state = _state(tmp_path)  # 日界を過ぎて、会話は1時間前に途切れている
    state.busy = _Hands(busy=True)
    server._watchdog_tick_at(state, TIMING, now=NOW)
    assert (sleeping.sleeps, pulses, len(resting)) == (0, [], 1)
    state.busy.now_busy = False
    server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=10))
    assert sleeping.sleeps == 1 and len(pulses) == 1


def test_while_hands_are_free_the_brain_is_kept_loaded_and_while_busy_it_rests(tmp_path: Path, sleeping, resting) -> None:  # noqa: ANN001
    warmed: list[int] = []
    state = _state(tmp_path, core=_Core(warm=lambda: warmed.append(1)))
    hands = state.busy = _Hands(busy=True)
    server._watchdog_tick_at(state, TIMING, now=NOW)
    assert (warmed, len(resting)) == ([], 1)  # 忙しい間は載せない（下ろす）
    hands.now_busy = False
    server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=10))
    server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=11))
    assert (warmed, len(resting)) == ([1, 1], 1)  # 暇になったら載せ直し、見回りのたびに載っているか確かめる


def test_a_brain_that_cannot_be_loaded_is_logged_once_and_the_watchdog_goes_on(tmp_path: Path, sleeping, caplog) -> None:  # noqa: ANN001
    def broken() -> None:
        raise ConnectionError("Ollama がない")

    state = _state(tmp_path, core=_Core(warm=broken))
    state.busy = _Hands()
    for minutes in (0, 1, 2):
        server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=minutes))
    assert [r.getMessage() for r in caplog.records].count("見回り: 脳を載せられなかった（載せられるまで、見回りのたびにやり直す）") == 1
    assert sleeping.sleeps == 1  # 載せられなくても、眠りは始まる（眠りの頼みが、そのときにまた載せる）
    state.core.warm = lambda: None
    server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=3))
    assert not state.warm_failing


def test_getting_busy_in_the_middle_stops_the_sleep_and_it_continues_when_hands_are_free(tmp_path: Path, sleeping, monkeypatch) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    hands = state.busy = _Hands()
    stops: list[bool] = []

    def busy_sleep(core, *, now, should_stop, progress):  # noqa: ANN001, ARG001
        hands.now_busy = True  # 眠っている間に、Masterがゲームを始めた
        stops.append(should_stop())
        return SleepReport(finished=not stops[-1])

    monkeypatch.setattr(server, "run_sleep", busy_sleep)
    server._watchdog_tick_at(state, TIMING, now=NOW)
    assert stops == [True] and state.sleep_owed and sleeping.grows == 0
    assert NOW <= hands.asked[-1] < NOW + timedelta(minutes=1)  # 眠りの区切りも、見回りと同じ時の流れで聞く
    assert state.last_boundary_serina_day == date(2026, 10, 4)
    server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=5))
    assert stops == [True]  # 忙しい間は、続きを眠らない
    hands.now_busy = False
    monkeypatch.setattr(server, "run_sleep", lambda core, **_k: SleepReport(finished=True))  # noqa: ARG005
    server._watchdog_tick_at(state, TIMING, now=NOW + timedelta(minutes=30))
    assert not state.sleep_owed and (sleeping.grows, sleeping.wakes) == (1, 1)
    assert state.last_boundary_serina_day == date(2026, 10, 5)


def test_getting_busy_after_the_pages_puts_off_growing_and_waking(tmp_path: Path, sleeping) -> None:  # noqa: ANN001
    """人格の見直しと目覚めも眠りの続き。忙しくなったら（話しかけられても）始めず、次に眠るときにそこから。"""
    state = _state(tmp_path)
    assert not server._sleep_and_grow(state, now=NOW, should_stop=lambda: True)
    assert (sleeping.grows, sleeping.wakes) == (0, 0)
    answers = iter([False, True])
    assert not server._sleep_and_grow(state, now=NOW, should_stop=lambda: next(answers))
    assert (sleeping.grows, sleeping.wakes) == (1, 0)
    assert server._sleep_and_grow(state, now=NOW, should_stop=lambda: False)
    assert (sleeping.grows, sleeping.wakes) == (2, 1)


class _Talker:
    """Masterの話しかけに答える Core の替え玉。"""

    def __init__(self) -> None:
        self.session = SessionState()
        self.felt: list[object] = []

    def turn_routed(self, text, *, now, on_token=None, on_reply=None, on_body=None):  # noqa: ANN001, ANN201, ARG002
        on_reply("おかえり")
        return SimpleNamespace(report=SimpleNamespace(reply="おかえり"), citations=None)

    def feel(self, result, *, source, now) -> None:  # noqa: ANN001, ARG002
        self.felt.append(source)


def test_master_is_answered_while_busy_and_the_brain_rests_only_after_the_talk(tmp_path: Path, resting, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(server, "run_post_turn_summaries", lambda core, call_fn: None)  # noqa: ARG005
    state = _state(tmp_path, core=_Talker())  # type: ignore[arg-type]
    state.busy = _Hands(busy=True)
    events: queue.Queue = queue.Queue()

    server._produce_turn("ただいま", events)

    sent = []
    while (item := events.get(timeout=5)) is not None:
        sent.append(json.loads(item))
    assert sent[-1] == {"type": "done", "reply": "おかえり", "citations": None}
    assert [(line.speaker, line.text) for line in read_conversation(state.conversation.directory)] == [
        (MASTER, "ただいま"), (NAME, "おかえり"),
    ]
    spoke = state.last_activity_at
    server._watchdog_tick_at(state, TIMING, now=spoke + timedelta(minutes=1))
    assert resting == []  # 話した直後は下ろさない（次の話しかけを待たせない）
    server._watchdog_tick_at(state, TIMING, now=spoke + timedelta(minutes=16))
    assert len(resting) == 1


# --- Pulse -----------------------------------------------------------------------


def test_after_startup_she_goes_only_when_she_misses_master(tmp_path: Path) -> None:
    """起動してからMasterがまだ来ていなくても、人恋しければ会いに行く（つながりは気持ちの記録から分かる）。
    人恋しくなければ、どれだけ暇でも話しかけない。話しかけたことは、会話の記録に本人の発言として残る。"""
    idea = _idea(tmp_path)
    _say(idea, MASTER, "またね", at=NOW - timedelta(days=2))
    lonely = {"now": False}
    state = _state(tmp_path, idea=idea)
    state.last_activity_at = None
    state.core.feelings = SimpleNamespace(lonely=lambda _now: lonely["now"])
    asked: list[str] = []
    state.core.pulse = _says(asked, "ねえ、元気にしてた？")

    server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(hours=3))
    assert asked == []
    lonely["now"] = True
    server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(hours=3))
    assert asked == ["connection"]
    assert [(line.speaker, line.text) for line in read_conversation(idea.conversation)] == [
        (MASTER, "またね"), (NAME, "ねえ、元気にしてた？"),
    ]


def test_she_tells_what_she_woke_with_before_master_comes(tmp_path: Path, sleeping, monkeypatch) -> None:  # noqa: ANN001
    """目覚めて伝えたいことができたら、Masterがまだ来ていなくても本人から伝えに行く（1回の目覚めで1度だけ）。"""
    latest: list[Waking] = []

    def fake_wake(core, *, now):  # noqa: ANN001, ARG001
        latest.append(Waking(at=now, after="d1", written_by="test", self_text="今のわたし", tell="約束が楽しみ"))
        return latest[-1]

    monkeypatch.setattr(server, "run_waking", fake_wake)
    idea = _idea(tmp_path)
    memory = SimpleNamespace(  # 記録もページもない記憶（目覚めだけが分かる）
        waking=lambda: latest[-1] if latest else None, pages_lock=threading.Lock(), idea=idea,
    )
    state = _state(tmp_path, core=_Core(memory=memory), idea=idea)  # type: ignore[arg-type]
    asked: list[str] = []
    state.core.pulse = _says(asked, "おはよう、約束楽しみだね")

    server.run_startup_morning_routine(state, now=NOW)
    server._maybe_run_serina_day_boundary_inner(state, TIMING, now=NOW)  # 見回りが裏で眠り、目覚める
    server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(minutes=1))

    assert asked == ["wake"]
    assert [line.text for line in read_conversation(idea.conversation)] == ["おはよう、約束楽しみだね"]
    server._maybe_fire_pulse_inner(state, TIMING, now=NOW + timedelta(hours=2))
    assert asked == ["wake"]  # 同じ目覚めで二度は行かない。人恋しくなければ、暇でも話しかけない


@pytest.mark.parametrize(("master_spoke", "tells"), [(timedelta(minutes=30), False), (timedelta(minutes=-30), True)])
def test_restart_does_not_forget_that_master_came_after_waking(tmp_path: Path, master_spoke: timedelta, tells: bool) -> None:
    """7:30に目覚め → 8:00にMasterと話した → 9:00に再起動。もう伝えに行かない（記録にMasterの発言がある）。

    Masterが目覚めより前に話しただけなら、再起動のあとでも伝えに行く。
    """
    woke = NOW.replace(hour=7, minute=30)
    idea = _idea(tmp_path)
    _say(idea, MASTER, "おはよう", at=woke + master_spoke)
    waking = Waking(at=woke, after="d1", written_by="test", self_text="今のわたし", tell="約束が楽しみ")
    state = _state(tmp_path, core=_Core(memory=SimpleNamespace(waking=lambda: waking)), idea=idea)
    state.last_activity_at = None  # 再起動したところ
    asked: list[str] = []
    state.core.pulse = _says(asked, "おはよう、約束楽しみだね")

    server._maybe_fire_pulse_inner(state, TIMING, now=NOW.replace(hour=9, minute=0))

    assert asked == (["wake"] if tells else [])


# --- Masterが消す ---------------------------------------------------------------


def _embed(text: str) -> list[float]:
    return [1.0, float(len(text) % 7), 0.5, 0.0]


@pytest.fixture
def living(tmp_path: Path):  # noqa: ANN201
    """会話がひとつあって、それが出来事のページになっているイデア。"""
    idea = _idea(tmp_path)
    _say(idea, MASTER, "高野漁港の話", at=NOW - timedelta(days=1))
    _say(idea, NAME, "約束の海だね", at=NOW - timedelta(days=1) + timedelta(seconds=5))
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
    state = _state(tmp_path, core=core, idea=idea)  # type: ignore[arg-type]
    return SimpleNamespace(idea=idea, state=state, memory=memory, feelings=feelings)


def _ref_of(client: TestClient, text: str) -> str:
    messages = client.get("/api/conversation?limit=20").json()["messages"]
    return quote(next(m for m in messages if m["text"] == text)["ref"], safe="")


def test_deleting_requires_confirmation_and_the_ledger_doors_are_gone(living) -> None:  # noqa: ANN001
    client = TestClient(server.app)
    assert client.delete(f"/api/conversation/{_ref_of(client, '約束の海だね')}").status_code == 400
    for method, path in [("delete", "/api/messages/1?confirm=true"), ("delete", "/api/sessions/s_past?confirm=true"),
                         ("post", "/api/sessions/new?confirm=true"), ("get", "/api/history"), ("get", "/api/sessions"),
                         ("get", "/api/pulse/pending"), ("post", "/api/pulse/mute"), ("get", "/")]:
        assert getattr(client, method)(path).status_code in (404, 405), path
    assert client.get("/api/state").json() == {"unwritten_pages": 0}


def test_hands_excludes_workshop_and_reports_unknown_idle_safely(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from mind.core.chores import busy as busy_module

    state = _state(tmp_path)
    state.busy = Busy(BusyRule(every_seconds=20), sense=lambda: Reading(workshop=True))
    monkeypatch.setattr(busy_module, "last_input_away_seconds", lambda: None)
    client = TestClient(server.app)
    assert state.busy.busy(NOW)
    assert client.get("/api/hands").json() == {"busy": False, "away_seconds": None}
    state.busy = Busy(BusyRule(every_seconds=20), sense=lambda: Reading(fullscreen=True))
    monkeypatch.setattr(busy_module, "last_input_away_seconds", lambda: 1900.0)
    assert client.get("/api/hands").json() == {"busy": True, "away_seconds": 1900.0}


def test_motion_describe_validates_and_serializes_with_the_conversation(tmp_path, caplog) -> None:
    from mind.brains.ollama.adapter import MotionDescribeInvalid, MotionDescribeUnavailable

    state = _state(tmp_path)
    calls: list[str] = []

    def describe(wish: str) -> dict:
        calls.append(wish)
        if wish == "通信失敗":
            raise MotionDescribeUnavailable
        if wish == "形が違う":
            raise MotionDescribeInvalid
        return {"text": "a person raises both arms gently.", "seconds": 3}

    state.core.describe_motion = describe
    client = TestClient(server.app)
    caplog.set_level(logging.INFO)
    for invalid in ("", " ", "なし", "そのまま", "ほかの動き", ".隠し", "A/B", "x" * 41, "改行\n入り"):
        assert client.post("/api/motion/describe", json={"wish": invalid}).status_code == 400
    assert not calls
    assert client.post("/api/motion/describe", json={"wish": "両手を挙げる"}).json() == {
        "text": "a person raises both arms gently.", "seconds": 3,
    }
    assert calls == ["両手を挙げる"]
    assert client.post("/api/motion/describe", json={"wish": "通信失敗"}).status_code == 503
    assert client.post("/api/motion/describe", json={"wish": "形が違う"}).status_code == 502
    state.core.describe_motion = lambda _wish: {"text": "invalid", "seconds": True}
    assert client.post("/api/motion/describe", json={"wish": "手を振る"}).status_code == 502
    state.turn_lock.acquire()
    try:
        assert client.post("/api/motion/describe", json={"wish": "手を振る"}).status_code == 503
    finally:
        state.turn_lock.release()
    assert "両手を挙げる" not in caplog.text


def test_deleting_a_line_marks_it_and_forgets_the_page_built_on_it(living) -> None:  # noqa: ANN001
    client = TestClient(server.app)
    living.state.reseed_flow(now=NOW)
    response = client.delete(f"/api/conversation/{_ref_of(client, '約束の海だね')}", params={"confirm": "true"})
    assert response.status_code == 200
    assert response.json()["forgotten_pages"] == ["ep-2026-10-04-01"]
    assert [line.text for line in read_conversation(living.idea.conversation)] == ["高野漁港の話"]
    raw = living.idea.conversation.glob("*.jsonl")
    rows = [json.loads(r) for path in raw for r in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 2 and rows[1]["deleted"] is True and "text" not in rows[1]  # 行番号は変わらない
    assert rows[1]["speaker"] == NAME and "session" not in rows[1]
    assert load_pages(living.idea.memory) == []
    assert "ep-2026-10-04-01" not in living.memory.index.pages
    assert any("発言" in r.action for r in living.state.change_log.read_all())
    felt = next(living.feelings.log.rows())  # 気持ちの記録は、その会話に拠った言葉だけが消え、数は残る
    assert felt["feeling"] == "" and felt["evaluation"]["master_state"] == "" and felt["after"]["connection"] > 0
    # 記憶から外れた残りの発言は、まだ眠っていない発言として手元に戻る
    assert [t.text for t in living.state.core.session.turns] == ["高野漁港の話"]
    assert client.delete(f"/api/conversation/{quote(response.json()['ref'], safe='')}?confirm=true").status_code == 404


def test_conversation_api_reads_backwards_from_lifelog_with_stable_refs(living) -> None:  # noqa: ANN001
    living.state.conversation.append(ts=NOW.astimezone(timezone.utc).isoformat(), speaker=MASTER, text="三つ目")
    living.state.conversation.append(ts=(NOW + timedelta(seconds=5)).astimezone(timezone.utc).isoformat(),
                                     speaker=NAME, text="四つ目")
    client = TestClient(server.app)

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


AWAKE = 'data: {"type": "state", "state": "awake"}\n\n'
ASLEEP = 'data: {"type": "state", "state": "asleep"}\n\n'


def test_event_stream_publishes_said_without_replay_sequence(living) -> None:  # noqa: ANN001
    stream = server._sse_events()
    try:
        assert next(stream) == ": connected\n\n"
        assert next(stream) == AWAKE
        server._publish_event(
            living.state,
            {"type": "said", "ref": "lifelog/conversation/2026-10-06.jsonl#1-1", "text": "いるよ"},
        )
        event = next(stream)
        assert '"type": "said"' in event
        assert '"text": "いるよ"' in event
    finally:
        stream.close()


def test_a_stream_opened_while_asleep_starts_with_asleep(tmp_path: Path, sleeping, monkeypatch) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    opened: list[str] = []

    def sleep_and_look(core, *, now, should_stop, progress):  # noqa: ANN001, ARG001
        stream = server._sse_events()  # 眠っている最中に流れにつないだ人は、まず今の値（asleep）を受け取る
        try:
            opened.extend([next(stream), next(stream)])
        finally:
            stream.close()
        return SleepReport(finished=True)

    monkeypatch.setattr(server, "run_sleep", sleep_and_look)
    assert server._try_sleep(state, TIMING, now=NOW, should_stop=lambda: False)
    assert opened == [": connected\n\n", ASLEEP]


@pytest.mark.parametrize("outcome", ["finished", "stopped", "raised"])
def test_the_sleep_is_told_asleep_then_awake_however_it_ends(tmp_path: Path, sleeping, outcome: str) -> None:  # noqa: ANN001
    state = _state(tmp_path)
    sleeping.finished = outcome == "finished"  # 止めたときは、眠りが最後まで済まない
    sleeping.fail = outcome == "raised"
    stream = server._sse_events()
    try:
        assert [next(stream), next(stream)] == [": connected\n\n", AWAKE]
        server._try_sleep(state, TIMING, now=NOW, should_stop=lambda: outcome == "stopped")
        assert [next(stream), next(stream)] == [ASLEEP, AWAKE]
    finally:
        stream.close()


def test_the_sleep_state_is_told_only_when_it_changes(tmp_path: Path) -> None:
    state = _state(tmp_path)
    events = queue.Queue()
    server._event_parts(state)[0].add(events)
    server._set_sleep_state(state, "awake")  # 今も awake なので、知らせない
    server._set_sleep_state(state, "asleep")
    server._set_sleep_state(state, "asleep")  # 同じ値をもう一度
    assert events.get_nowait() == {"type": "state", "state": "asleep"}
    assert events.empty()


def test_the_mind_keeps_the_latest_body_the_world_tells(tmp_path: Path) -> None:
    """世界が送る今の体を覚え、新しいものが来たら差し替える。知らない知覚・形の違うカタログは受け取らない。"""
    core = Core(persona_text="人格", absolute_rules="ルール", thresholds=ThresholdsConfig())
    _state(tmp_path, core=core)  # type: ignore[arg-type]
    client = TestClient(server.app)

    first = client.post("/api/perceive", json={"kind": "body", "catalog": {"expressions": ["喜び", "なし"], "gestures": ["うなずく"]}})
    assert first.status_code == 200 and first.json() == {"expressions": 1, "gestures": 1}
    client.post("/api/perceive", json={"kind": "body", "catalog": {"expressions": ["驚き"], "gestures": []}})
    assert core.body_catalog == BodyCatalog(expressions=("驚き",))

    for wrong in ({"kind": "smell", "catalog": {"expressions": [], "gestures": []}},
                  {"kind": "body", "catalog": {"expressions": "喜び", "gestures": []}}):
        assert client.post("/api/perceive", json=wrong).status_code == 400
    assert core.body_catalog == BodyCatalog(expressions=("驚き",))
