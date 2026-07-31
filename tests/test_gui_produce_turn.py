"""app/gui_server._produce_turn のイベント順テスト（2026-07-20 応答高速化）。

期待順序: token* → done(reply・1通目確定) → done(reply+session_id+citations・終幕)。
Ollama 不要（フェイク Core のみ）。

2026-07-31 Phase E: 旧「保留文→2通目」機構（followupイベント）は退役済み。
出典（citations）は終幕の done イベントへ付加フィールドとして届く
（reply・セッション履歴には混ざらない契約。Phase D-6）。
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
from serina.core.state.desire import DesireState
from serina.core.state.emotion import EmotionState
from serina.core.state.relationship import RelationshipState


class _FakeStore:
    def __init__(self) -> None:
        self.history: list[tuple[str, str]] = []

    def add_history(self, session_id: str, role: str, content: str) -> None:
        self.history.append((role, content))


class _FakeCore:
    """turn_routed の callbacks 契約だけを再現する。"""

    def __init__(
        self,
        reply: str,
        citations: list[dict] | None = None,
        raise_after_reply: bool = False,
    ) -> None:
        self._reply = reply
        self._citations = citations
        self._raise_after_reply = raise_after_reply
        self.emotion = EmotionState()
        self.desire = DesireState()
        self.relationship = RelationshipState()

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
            citations=self._citations,
        )


def _run_turn(text: str, core: _FakeCore) -> tuple[list[dict], _FakeStore]:
    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    store = _FakeStore()
    state.session_store = store
    state.session_mgr = None
    state.session_id = "s_test"
    state.turn_lock = threading.Lock()
    state.summary_lock = threading.Lock()
    state.watchdog_lock = threading.Lock()
    state.last_activity_at = datetime.now(timezone.utc)
    state.session_ended = False
    state.lane_call_fns = {}
    state.episodic_state_path = Path(tempfile.mkdtemp()) / "episodic_state.json"
    state.emotion_state_path = Path(tempfile.mkdtemp()) / "emotion_state.json"
    state.desire_state_path = Path(tempfile.mkdtemp()) / "desire_state.json"
    state.relationship_state_path = Path(tempfile.mkdtemp()) / "relationship_state.json"
    state.last_episodic_at = datetime.now(timezone.utc)
    gui_server.STATE = state

    events: "queue.Queue[str | None]" = queue.Queue()
    gui_server._produce_turn(text, events)
    out: list[dict] = []
    while (item := events.get_nowait()) is not None:
        out.append(json.loads(item))
    return out, store


def test_event_order_token_done_citations_finaldone() -> None:
    """統合パイプライン（Phase D/E）: 1ターン=1通のみ。citationsは終幕doneの付加フィールド。"""
    citations = [{"url": "https://example.com/weather"}]
    events, store = _run_turn("天気教えて", _FakeCore("晴れだよ", citations=citations))

    types = [e["type"] for e in events]
    assert types == ["token"] * 4 + ["done", "done"], "followupイベントは退役済み（1通完結）"
    assert "".join(e["text"] for e in events[:4]) == "晴れだよ"
    first_done, final_done = events[4], events[5]
    assert first_done["reply"] == "晴れだよ"
    assert "session_id" not in first_done, "1通目確定の done は session_id を持たない"
    assert final_done["session_id"] == "s_test"
    assert final_done["citations"] == citations
    assert store.history == [("user", "天気教えて"), ("assistant", "晴れだよ")], (
        "citationsはセッション履歴（記憶蒸留材料）に混入しない"
    )


def test_no_citations_emits_null_citations_field() -> None:
    events, store = _run_turn("こんにちは", _FakeCore("やあ"))

    types = [e["type"] for e in events]
    assert types == ["token"] * 2 + ["done", "done"]
    assert events[-1]["citations"] is None
    assert store.history == [("user", "こんにちは"), ("assistant", "やあ")]


def test_failure_after_reply_delivered_emits_notice_not_error() -> None:
    """1通目が届いた後の裏方失敗で、表示済み本文を謝り文言で上書きしない。"""
    events, _ = _run_turn("こんにちは", _FakeCore("やあ", raise_after_reply=True))

    types = [e["type"] for e in events]
    assert types[-1] == "notice"
    assert "error" not in types
