"""Pulse が会話の記録に本人の発言として載り、窓へ said で届く回帰。人恋しくなって会いに行く（connection）。"""

from __future__ import annotations

import json
import queue
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.app import server
from mind.app.idle_config import load_app_timing
from mind.core import debug_log
from mind.core.config import load_thresholds
from mind.core.lifelog import ConversationLog, read_conversation
from mind.core.perception import BodyChoice


def _state(tmp_path: Path, core: MagicMock) -> server.MindState:
    state = server.MindState.__new__(server.MindState)
    state.core = core
    state.conversation = ConversationLog(tmp_path / "conversation")
    state.name = "Serina"
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.pulse_state_path = tmp_path / "pulse.json"
    state.event_subscribers = set()
    state._event_lock = threading.Lock()
    return state


def test_pulse_fire_writes_the_residents_line(tmp_path: Path) -> None:
    dbg = tmp_path / "debug.jsonl"
    debug_log.configure(dbg)

    real = load_thresholds()
    core = MagicMock()
    core.thresholds = real
    core.pulse = MagicMock(side_effect=lambda candidate, *, now, on_said, on_body=None, on_approach=None: on_said("ちょっと様子見てるよ") or True)
    core.memory.waking = MagicMock(return_value=None)  # まだ目覚めていない
    core.feelings.lonely = MagicMock(return_value=True)  # 会えない時間で、人恋しくなった

    state = _state(tmp_path, core)
    now = datetime.now(timezone.utc).replace(hour=12)
    timing = load_app_timing()
    state.last_activity_at = now - timedelta(seconds=timing.serina_day_grace_after_activity_seconds + 120)
    events: queue.Queue[dict] = queue.Queue()
    state.event_subscribers = {events}

    try:
        server._maybe_fire_pulse_inner(state, timing, now=now)
    finally:
        debug_log.configure(None)

    row = json.loads(dbg.read_text(encoding="utf-8").strip())
    assert row["kind"] == "pulse"
    assert row["action"] == "fire"
    assert row["pulse_kind"] == "connection"
    lines = read_conversation(tmp_path / "conversation")
    assert [(line.speaker, line.text, line.ts, line.session) for line in lines] == [
        ("Serina", "ちょっと様子見てるよ", now, None),
    ]
    pushed = events.get_nowait()
    assert pushed["type"] == "said"
    assert pushed["text"] == "ちょっと様子見てるよ"
    assert pushed["kind"] == "connection"
    assert pushed["ref"] == f"lifelog/conversation/{lines[0].day_file}.jsonl#1-1"
    core.pulse.assert_called_once()
    # 本人が話したことなので、手元の会話の流れにも置く（次のマスターの返事は、これへの返事）
    turn = core.session.add_turn.call_args.args[0]
    assert (turn.speaker, turn.text) == ("serina", "ちょっと様子見てるよ")


def test_pulse_waits_while_the_conversation_has_just_paused(tmp_path: Path) -> None:
    """最後の発言から少しのあいだは会話中として扱い、人恋しくても話しかけない（眠りと同じく、会話が途切れてから）。"""
    core = MagicMock()
    core.thresholds = load_thresholds()
    core.memory.waking = MagicMock(return_value=None)
    core.feelings.lonely = MagicMock(return_value=True)
    state = _state(tmp_path, core)
    now = datetime.now(timezone.utc).replace(hour=12)
    state.last_activity_at = now - timedelta(seconds=60)

    server._maybe_fire_pulse_inner(state, load_app_timing(), now=now)

    core.pulse.assert_not_called()
    assert not (tmp_path / "pulse.json").exists()  # 抑えた候補は、行ったことにならない


def _lonely(tmp_path: Path, pulse) -> tuple[server.MindState, MagicMock, "queue.Queue[dict]", datetime]:  # noqa: ANN001
    core = MagicMock()
    core.thresholds = load_thresholds()
    core.pulse = MagicMock(side_effect=pulse)
    core.memory.waking = MagicMock(return_value=None)
    core.feelings.lonely = MagicMock(return_value=True)
    state = _state(tmp_path, core)
    now = datetime.now(timezone.utc).replace(hour=12)
    state.last_activity_at = now - timedelta(seconds=load_app_timing().serina_day_grace_after_activity_seconds + 120)
    events: queue.Queue[dict] = queue.Queue()
    state.event_subscribers = {events}
    return state, core, events, now


def test_a_pulse_she_declines_leaves_no_line_and_counts_once(tmp_path: Path) -> None:
    """本人が今は話さないと決めたら、記録にも窓にも出さず、1回と数える（すぐ聞き直して脳を何度も呼ばない）。"""
    state, core, events, now = _lonely(tmp_path, lambda candidate, *, now, on_said, on_body=None, on_approach=None: False)

    server._maybe_fire_pulse_inner(state, load_app_timing(), now=now)
    server._maybe_fire_pulse_inner(state, load_app_timing(), now=now + timedelta(minutes=5))

    core.pulse.assert_called_once()
    assert json.loads((tmp_path / "pulse.json").read_text(encoding="utf-8"))["last_by_kind"] == {"connection": now.isoformat()}
    assert read_conversation(tmp_path / "conversation") == [] and events.empty()


def test_a_failing_brain_is_not_counted_as_her_choice(tmp_path: Path) -> None:
    def broken(candidate, *, now, on_said, on_body=None, on_approach=None):  # noqa: ANN001, ANN202, ARG001
        raise ConnectionError("Ollama が止まっている")

    state, _core, _events, now = _lonely(tmp_path, broken)

    server._maybe_fire_pulse(state, load_app_timing(), now=now)

    assert not (tmp_path / "pulse.json").exists()


def test_the_body_she_chooses_after_a_pulse_points_at_its_line(tmp_path: Path) -> None:
    def pulse(candidate, *, now, on_said, on_body=None, on_approach=None):  # noqa: ANN001, ANN202, ARG001
        on_said("ねえ")
        on_body(BodyChoice(expression="喜び", gesture="うなずく"))
        return True

    state, _core, events, now = _lonely(tmp_path, pulse)

    server._maybe_fire_pulse_inner(state, load_app_timing(), now=now)

    said, body = events.get_nowait(), events.get_nowait()
    assert said["type"] == "said" and said["text"] == "ねえ"
    assert body == {"type": "body", "by": "pulse", "ref": said["ref"], "expression": "喜び", "gesture": "うなずく"}


def test_approach_is_published_before_speech_and_failed_when_speech_never_arrives(tmp_path: Path) -> None:
    def speaks(candidate, *, now, on_said, on_body=None, on_approach=None):  # noqa: ANN001, ANN202, ARG001
        on_approach()
        on_said("ただいま")
        return True

    state, _core, events, now = _lonely(tmp_path, speaks)
    server._maybe_fire_pulse_inner(state, load_app_timing(), now=now)
    approach, said = events.get_nowait(), events.get_nowait()
    assert approach == {"type": "approach", "kind": "connection", "ref": f"pulse:{now.isoformat()}"}
    assert said["type"] == "said" and said["text"] == "ただいま"
    assert events.empty()


def test_failed_pulse_closes_approach_with_the_same_ref(tmp_path: Path) -> None:
    def broken(candidate, *, now, on_said, on_body=None, on_approach=None):  # noqa: ANN001, ANN202, ARG001
        on_approach()
        raise RuntimeError("generation failed")

    state, _core, events, now = _lonely(tmp_path, broken)
    server._maybe_fire_pulse(state, load_app_timing(), now=now)
    approach, failed = events.get_nowait(), events.get_nowait()
    assert approach["type"] == "approach"
    assert failed == {**approach, "failed": True}
    assert events.empty()
