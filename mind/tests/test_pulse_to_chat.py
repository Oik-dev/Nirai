"""Pulse がセッション履歴（チャット欄）へ載る回帰。人恋しくなって会いに行く（connection）。"""

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

from mind.app import gui_server
from mind.app.idle_config import load_app_timing
from mind.core import debug_log
from mind.core.config import load_thresholds
from mind.core.lifelog import ConversationLog
from mind.core.memory.session_store import SessionStore


def test_pulse_fire_writes_assistant_history(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sess.db", conversation_log=ConversationLog(tmp_path / "conversation"))
    sid = "s_pulse"
    store.create_session(sid)
    dbg = tmp_path / "debug.jsonl"
    debug_log.configure(dbg)

    real = load_thresholds()
    core = MagicMock()
    core.thresholds = real
    core.generate_pulse_text = MagicMock(return_value="ちょっと様子見てるよ")
    core.memory.waking = MagicMock(return_value=None)  # まだ目覚めていない
    core.feelings.lonely = MagicMock(return_value=True)  # 会えない時間で、人恋しくなった

    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    state.session_store = store
    state.session_id = sid
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    now = datetime.now(timezone.utc).replace(hour=12)
    timing = load_app_timing()
    state.last_activity_at = now - timedelta(seconds=timing.serina_day_grace_after_activity_seconds + 120)
    state.pulse_mute = False
    state.pulse_queue = []
    state._pulse_lock = threading.Lock()
    events: queue.Queue[dict] = queue.Queue()
    state.event_subscribers = {events}
    state._event_lock = threading.Lock()
    state.pulse_state_path = tmp_path / "pulse.json"

    try:
        gui_server._maybe_fire_pulse_inner(state, timing, now=now)
    finally:
        debug_log.configure(None)

    hist = store.get_session_history(sid)
    row = json.loads(dbg.read_text(encoding="utf-8").strip())
    assert row["kind"] == "pulse"
    assert row["action"] == "fire"
    assert row["pulse_kind"] == "connection"
    assert row["session_id"] == sid
    assert any(m["role"] == "assistant" and "様子" in m["content"] for m in hist)
    assert state.pulse_queue and state.pulse_queue[0]["text"] == "ちょっと様子見てるよ"
    pushed = events.get_nowait()
    assert pushed["type"] == "said"
    assert pushed["text"] == "ちょっと様子見てるよ"
    assert pushed["ref"].startswith("lifelog/conversation/")
    core.generate_pulse_text.assert_called_once()
    # 本人が話したことなので、手元の会話の流れにも置く（次のマスターの返事は、これへの返事）
    turn = core.session.add_turn.call_args.args[0]
    assert (turn.speaker, turn.text) == ("serina", "ちょっと様子見てるよ")


def test_pulse_waits_while_the_conversation_has_just_paused(tmp_path: Path) -> None:
    """最後の発言から少しのあいだは会話中として扱い、人恋しくても話しかけない（眠りと同じく、会話が途切れてから）。"""
    store = SessionStore(tmp_path / "sess.db")
    store.create_session("s")
    core = MagicMock()
    core.thresholds = load_thresholds()
    core.memory.waking = MagicMock(return_value=None)
    core.feelings.lonely = MagicMock(return_value=True)
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core, state.session_store, state.session_id = core, store, "s"
    state.turn_lock, state.watchdog_lock, state._pulse_lock = threading.Lock(), threading.Lock(), threading.Lock()
    now = datetime.now(timezone.utc).replace(hour=12)
    state.last_activity_at = now - timedelta(seconds=60)
    state.pulse_mute, state.pulse_queue, state.pulse_state_path = False, [], tmp_path / "pulse.json"

    gui_server._maybe_fire_pulse_inner(state, load_app_timing(), now=now)

    core.generate_pulse_text.assert_not_called()
    assert not (tmp_path / "pulse.json").exists()  # 抑えた候補は、行ったことにならない
