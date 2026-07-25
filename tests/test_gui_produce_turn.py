"""app/gui_server._produce_turn のイベント順テスト（2026-07-20 応答高速化）。

期待順序: token* → done(reply・1通目確定) → followup?（advisor 2通目）
→ done(reply+session_id・終幕)。Ollama 不要（フェイク Core のみ）。
"""

from __future__ import annotations

import json
import queue
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.app import gui_server
from serina.core.state.emotion import EmotionState


class _FakeStore:
    def __init__(self) -> None:
        self.history: list[tuple[str, str]] = []

    def add_history(self, session_id: str, role: str, content: str) -> None:
        self.history.append((role, content))


class _FakeCore:
    """turn_routed の callbacks 契約だけを再現する。"""

    def __init__(self, reply: str, followup: str | None = None, raise_after_reply: bool = False) -> None:
        self._reply = reply
        self._followup = followup
        self._raise_after_reply = raise_after_reply
        self.emotion = EmotionState()

    def turn_routed(self, text: str, *, now, on_token=None, on_reply=None):  # noqa: ANN001, ANN201
        for ch in self._reply:
            if on_token is not None:
                on_token(ch)
        if on_reply is not None:
            on_reply(self._reply)
        if self._raise_after_reply:
            raise RuntimeError("抽出段の失敗")
        return SimpleNamespace(
            report=SimpleNamespace(reply=self._reply),
            followup_reply=self._followup,
        )


def _run_turn(text: str, core: _FakeCore) -> tuple[list[dict], _FakeStore]:
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    store = _FakeStore()
    state.session_store = store
    state.session_mgr = None
    state.session_id = "s_test"
    state.turn_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.last_activity_at = datetime.now(timezone.utc)
    state.session_ended = False
    state.lane_call_fns = {}
    state.episodic_state_path = Path(tempfile.mkdtemp()) / "episodic_state.json"
    state.emotion_state_path = Path(tempfile.mkdtemp()) / "emotion_state.json"
    state.last_episodic_at = datetime.now(timezone.utc)
    gui_server.STATE = state

    events: "queue.Queue[str | None]" = queue.Queue()
    gui_server._produce_turn(text, events)
    out: list[dict] = []
    while (item := events.get_nowait()) is not None:
        out.append(json.loads(item))
    return out, store


def test_event_order_token_done_followup_finaldone() -> None:
    events, store = _run_turn("天気教えて", _FakeCore("晴れだよ", followup="2通目だよ"))

    types = [e["type"] for e in events]
    assert types == ["token"] * 4 + ["done", "followup", "done"]
    assert "".join(e["text"] for e in events[:4]) == "晴れだよ"
    first_done, final_done = events[4], events[6]
    assert first_done["reply"] == "晴れだよ"
    assert "session_id" not in first_done, "1通目確定の done は session_id を持たない"
    assert final_done["session_id"] == "s_test"
    assert events[5]["text"] == "2通目だよ"
    assert store.history == [
        ("user", "天気教えて"), ("assistant", "晴れだよ"), ("assistant", "2通目だよ"),
    ]


def test_no_followup_emits_single_message_events() -> None:
    events, store = _run_turn("こんにちは", _FakeCore("やあ"))

    types = [e["type"] for e in events]
    assert types == ["token"] * 2 + ["done", "done"]
    assert store.history == [("user", "こんにちは"), ("assistant", "やあ")]


def test_failure_after_reply_delivered_emits_notice_not_error() -> None:
    """1通目が届いた後の裏方失敗で、表示済み本文を謝り文言で上書きしない。"""
    events, _ = _run_turn("こんにちは", _FakeCore("やあ", raise_after_reply=True))

    types = [e["type"] for e in events]
    assert types[-1] == "notice"
    assert "error" not in types
