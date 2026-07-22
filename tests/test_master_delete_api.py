"""マスター手動削除 API（アルバム日記・会話セッション・汎用記憶）のテスト。"""

from __future__ import annotations

import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.app import gui_server
from serina.core.chores.chore_box import ChoreBox
from serina.core.config import ThresholdsConfig
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog, GenerationStore
from serina.core.memory.session_store import SessionStore
from serina.core.memory.store import MemoryStore
from serina.core.state.emotion import EmotionState
from serina.core.state.session import SessionState


class _StubCore:
    def __init__(self, chore_box: ChoreBox, memory_store: MemoryStore, thresholds: ThresholdsConfig) -> None:
        self.chore_box = chore_box
        self.memory_store = memory_store
        self.thresholds = thresholds
        self.emotion = EmotionState()
        self.session = SessionState()

    def end_session(self) -> list[int]:
        self.session = SessionState()
        return []


def _fresh_memory() -> MemoryStore:
    embedder = OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])
    return MemoryStore(str(Path(tempfile.mkdtemp()) / "mem.db"), embedder=embedder, vector_dim=4)


def _install_delete_state(tmp: Path) -> gui_server.GuiState:
    mem = _fresh_memory()
    core = _StubCore(
        ChoreBox(tmp / "chore.db"),
        mem,
        ThresholdsConfig(fusen_confidence={"default": 0.5}, mood_guard_max_delta_per_turn=0.1),
    )
    session_store = SessionStore(tmp / "session.db")
    session_store.create_session("s_current")
    session_store.create_session("s_past")
    session_store.set_session_status("s_past", "pending")
    session_store.add_history("s_past", "user", "消したい会話")
    session_store.add_history("s_past", "assistant", "了解です")

    state = gui_server.GuiState.__new__(gui_server.GuiState)
    state.core = core
    state.session_store = session_store
    state.session_mgr = None
    state.session_id = "s_current"
    state.turn_lock = threading.Lock()
    state.lane_call_fns = {}
    state.change_log = ChangeLog(tmp / "change_log.jsonl")
    state.generation_store = GenerationStore(tmp / "generations.jsonl")
    state.db_path = Path(mem._db_path)  # noqa: SLF001
    state.diary_state_path = tmp / "diary_state.json"
    state.last_diary_at = datetime.now(timezone.utc)
    state.watchdog_lock = threading.Lock()
    state.session_ended = False
    state.last_activity_at = datetime.now(timezone.utc)
    gui_server.STATE = state
    return state


def test_album_delete_requires_confirm_and_physical_deletes(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory(
        "変な日記", type="diary", importance=0.5, protection_grade="A"
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))

    client = TestClient(gui_server.app)
    denied = client.delete(f"/api/album/{mid}")
    assert denied.status_code == 400

    album = client.get("/api/album")
    assert album.status_code == 200
    assert any(d["id"] == mid for d in album.json())

    ok = client.delete(f"/api/album/{mid}?confirm=true")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert state.core.memory_store.get_memory_by_id(mid) is None
    assert client.get("/api/album").json() == []
    assert any("物理削除" in (r.action or "") for r in state.change_log.read_all())


def test_album_delete_cascades_distilled_but_keeps_inherited(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    store = state.core.memory_store
    distilled = store.add_memory("蒸留ノイズ", type="fact", importance=0.5, protection_grade="B")
    inherited = store.add_memory("原典", type="fact", importance=0.5, protection_grade="B")
    diary_id = store.add_memory("日記", type="diary", importance=0.5, protection_grade="A")
    conn = store._connect()  # noqa: SLF001
    try:
        conn.execute("UPDATE memories SET source=? WHERE id=?", ("セリナの記憶.json", inherited))
        # 本番の created_at は UTC(+00:00) 保存なので、テストも本番形式に揃える。
        conn.execute(
            "UPDATE memories SET created_at=? WHERE id=?",
            ("2026-07-21T01:00:00+00:00", distilled),
        )
        conn.execute(
            "UPDATE memories SET created_at=? WHERE id=?",
            ("2026-07-21T01:30:00+00:00", inherited),
        )
        conn.execute(
            "UPDATE memories SET created_at=? WHERE id=?",
            ("2026-07-21T03:00:00+00:00", diary_id),
        )
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)
    ok = client.delete(f"/api/album/{diary_id}?confirm=true")
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert distilled in body["cascade_deleted"]
    assert inherited not in body["cascade_deleted"]
    assert store.get_memory_by_id(diary_id) is None
    assert store.get_memory_by_id(distilled) is None
    assert store.get_memory_by_id(inherited) is not None


def test_album_delete_rejects_non_diary(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory("ただのfact", type="fact", importance=0.5)
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)
    res = client.delete(f"/api/album/{mid}?confirm=true")
    assert res.status_code == 400
    assert state.core.memory_store.get_memory_by_id(mid) is not None


def test_session_delete_past_ok_current_rejected(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    bad = client.delete("/api/sessions/s_current?confirm=true")
    assert bad.status_code == 400

    denied = client.delete("/api/sessions/s_past")
    assert denied.status_code == 400

    ok = client.delete("/api/sessions/s_past?confirm=true")
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["history_deleted"] == 2
    assert state.session_store.get_session_history("s_past") == []
    assert any(s["id"] != "s_past" for s in state.session_store.list_sessions()) or \
        all(s["id"] != "s_past" for s in state.session_store.list_sessions())


def test_memories_list_and_keyword_search(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    store = state.core.memory_store
    a = store.add_memory("りんごが好き", type="fact", importance=0.5, protection_grade="B")
    b = store.add_memory("みかんを買った", type="fact", importance=0.5, protection_grade="A")
    store.add_memory("日記の一行", type="diary", importance=0.5, protection_grade="A")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    all_res = client.get("/api/memories")
    assert all_res.status_code == 200
    body = all_res.json()
    assert body["limit"] == 100
    assert body["page"] == 1
    assert body["total"] >= 3
    ids = {item["id"] for item in body["items"]}
    assert {a, b}.issubset(ids)
    sample = next(item for item in body["items"] if item["id"] == a)
    assert set(sample) == {"id", "content", "protection_grade", "created_at", "pinned"}
    assert sample["pinned"] is False
    assert "type" not in sample

    hit = client.get("/api/memories", params={"q": "りんご"})
    assert hit.status_code == 200
    hit_ids = [item["id"] for item in hit.json()["items"]]
    assert hit_ids == [a]


def test_memories_list_pagination(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    store = state.core.memory_store
    for i in range(5):
        store.add_memory(f"ページ用-{i}", type="fact", importance=0.4)
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    page1 = client.get("/api/memories", params={"limit": 2, "page": 1})
    assert page1.status_code == 200
    body1 = page1.json()
    assert body1["limit"] == 2
    assert body1["page"] == 1
    assert body1["total"] == 5
    assert body1["pages"] == 3
    assert len(body1["items"]) == 2

    page2 = client.get("/api/memories", params={"limit": 2, "page": 2})
    body2 = page2.json()
    assert body2["page"] == 2
    assert len(body2["items"]) == 2
    assert {i["id"] for i in body1["items"]}.isdisjoint({i["id"] for i in body2["items"]})

    page3 = client.get("/api/memories", params={"limit": 2, "page": 3})
    assert len(page3.json()["items"]) == 1


def test_memories_delete_requires_confirm_and_backup(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory(
        "テスト汚染のfact", type="fact", importance=0.5, protection_grade="B"
    )
    backup = MagicMock(return_value=tmp_path / "b.db")
    monkeypatch.setattr(gui_server, "backup_db", backup)
    client = TestClient(gui_server.app)

    denied = client.delete(f"/api/memories/{mid}")
    assert denied.status_code == 400
    backup.assert_not_called()
    assert state.core.memory_store.get_memory_by_id(mid) is not None

    ok = client.delete(f"/api/memories/{mid}?confirm=true")
    assert ok.status_code == 200
    assert ok.json()["ok"] is True
    assert ok.json()["memory_id"] == mid
    backup.assert_called_once()
    assert state.core.memory_store.get_memory_by_id(mid) is None
    assert any("物理削除" in (r.action or "") for r in state.change_log.read_all())


def test_memories_delete_rejects_pinned(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory("正典", type="fact", importance=1.0, protection_grade="S")
    conn = state.core.memory_store._connect()  # noqa: SLF001
    try:
        conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    listed = client.get("/api/memories").json()["items"]
    pinned_row = next(item for item in listed if item["id"] == mid)
    assert pinned_row["pinned"] is True

    res = client.delete(f"/api/memories/{mid}?confirm=true")
    assert res.status_code == 400
    assert state.core.memory_store.get_memory_by_id(mid) is not None


def test_memories_delete_s_grade_with_gui_confirm(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory(
        "S級だが固定ではない", type="fact", importance=0.9, protection_grade="S"
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/memories/{mid}?confirm=true")
    assert ok.status_code == 200
    assert state.core.memory_store.get_memory_by_id(mid) is None


def test_message_delete_requires_confirm_and_removes_history(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    msg_id = state.session_store.add_history("s_current", "user", "消したい一言")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    denied = client.delete(f"/api/messages/{msg_id}")
    assert denied.status_code == 400

    ok = client.delete(f"/api/messages/{msg_id}?confirm=true")
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["message_id"] == msg_id
    history = state.session_store.get_session_history("s_current")
    assert all(row["id"] != msg_id for row in history)
    assert any("発言単位削除" in (r.action or "") for r in state.change_log.read_all())


def test_message_delete_removes_matching_chore_job(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    msg_id = state.session_store.add_history("s_current", "user", "宿題に載る発言")
    job_id = state.core.chore_box.enqueue(
        "蒸留",
        lane="local",
        payload={
            "turns": [
                {"speaker": "master", "text": "宿題に載る発言"},
                {"speaker": "serina", "text": "了解"},
            ]
        },
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/messages/{msg_id}?confirm=true")
    assert ok.status_code == 200
    assert job_id in ok.json()["chore_jobs_removed"]
    assert state.core.chore_box.count(kind="蒸留") == 0


def test_message_delete_physical_deletes_single_source_memory(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    quote = "この発言だけが根拠の記憶"
    msg_id = state.session_store.add_history("s_current", "user", quote)
    store = state.core.memory_store
    mid = store.add_memory(
        "蒸留結果",
        type="fact",
        importance=0.5,
        protection_grade="B",
        metadata_obj={"source_quotes": [quote]},
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/messages/{msg_id}?confirm=true")
    assert ok.status_code == 200
    assert mid in ok.json()["memories_deleted"]
    assert store.get_memory_by_id(mid) is None


def test_message_delete_trims_quote_when_multiple_sources(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    quote_a = "削除対象の発言"
    quote_b = "残す発言"
    msg_id = state.session_store.add_history("s_current", "user", quote_a)
    store = state.core.memory_store
    mid = store.add_memory(
        "複数出所の記憶",
        type="fact",
        importance=0.5,
        protection_grade="B",
        metadata_obj={"source_quotes": [quote_a, quote_b]},
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/messages/{msg_id}?confirm=true")
    assert ok.status_code == 200
    assert mid in ok.json()["memories_quote_trimmed"]
    assert store.get_memory_by_id(mid) is not None
    conn = store._connect()  # noqa: SLF001
    try:
        row = conn.execute("SELECT metadata FROM memories WHERE id = ?", (mid,)).fetchone()
        import json

        meta = json.loads(row["metadata"])
        assert meta["source_quotes"] == [quote_b]
    finally:
        conn.close()


def test_sessions_new_requires_confirm(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    from serina.core.state.session_book import SessionBookConfig, SessionManager

    state = _install_delete_state(tmp_path)
    state.session_mgr = SessionManager(state.session_store, SessionBookConfig())
    state.session_store.add_history("s_current", "user", "旧会話")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    denied = client.post("/api/sessions/new")
    assert denied.status_code == 400

    ok = client.post("/api/sessions/new?confirm=true")
    assert ok.status_code == 200
    body = ok.json()
    assert body["ok"] is True
    assert body["session_id"] != "s_current"
    assert state.session_id == body["session_id"]
