"""Pulse がセッション履歴（チャット欄）へ載る回帰。"""

from __future__ import annotations

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.app import gui_server
from mind.core import debug_log
from mind.core.config import load_thresholds
from mind.core.memory.session_store import SessionStore


def test_pulse_fire_writes_assistant_history(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "sess.db")
    sid = "s_pulse"
    store.create_session(sid)
    dbg = tmp_path / "debug.jsonl"
    debug_log.configure(dbg)

    real = load_thresholds()
    core = MagicMock()
    core.thresholds = real
    core.list_schedule_pulse_candidates = MagicMock(return_value=[])
    core.generate_pulse_text = MagicMock(return_value="ちょっと様子見てるよ")
    core.emotion = MagicMock()
    core.emotion.mood = {k: 0.0 for k in (
        "喜び", "信頼", "恐れ", "驚き", "悲しみ", "嫌悪", "怒り", "期待",
    )}

    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    state.session_store = store
    state.session_id = sid
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.lane_call_fns = {}
    now = datetime.now(timezone.utc).replace(hour=12)
    state.last_activity_at = now - timedelta(seconds=real.pulse_idle_before_seconds + 120)
    state.has_had_first_turn = True  # 2026-08-01是正: 会話開始済みの状況を想定するテストのため
    state.pulse_mute = False
    state.pulse_queue = []
    state._pulse_lock = threading.Lock()
    state.pulse_state_path = tmp_path / "pulse.json"
    state.schedule_pulse_state_path = tmp_path / "schedule_pulse.json"

    try:
        gui_server._maybe_fire_pulse_inner(state, now=now)
    finally:
        debug_log.configure(None)

    hist = store.get_session_history(sid)
    row = json.loads(dbg.read_text(encoding="utf-8").strip())
    assert row["kind"] == "pulse"
    assert row["action"] == "fire"
    assert row["pulse_kind"] == "time"
    assert row["session_id"] == sid
    assert any(m["role"] == "assistant" and "様子" in m["content"] for m in hist)
    assert state.pulse_queue and state.pulse_queue[0]["text"] == "ちょっと様子見てるよ"
    core.generate_pulse_text.assert_called_once()
