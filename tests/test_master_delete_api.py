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
    state.summary_lock = threading.Lock()
    state.lane_call_fns = {}
    state.change_log = ChangeLog(tmp / "change_log.jsonl")
    state.generation_store = GenerationStore(tmp / "generations.jsonl")
    state.db_path = Path(mem._db_path)  # noqa: SLF001
    state.episodic_state_path = tmp / "episodic_state.json"
    state.last_episodic_at = datetime.now(timezone.utc)
    state.watchdog_lock = threading.Lock()
    state.session_ended = False
    state.last_activity_at = datetime.now(timezone.utc)
    state.has_had_first_turn = True  # 2026-08-01是正: 削除APIテストは会話進行中の想定
    state.pulse_state_path = tmp / "pulse.json"
    state.schedule_pulse_state_path = tmp / "schedule_pulse.json"
    gui_server.STATE = state
    return state


def test_memories_diary_material_lists_distilled_but_not_inherited(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """§4.8.1: 読み取り専用の参照記憶レビュー。削除はしない（旧アルバムの自動連鎖から変更）。"""
    state = _install_delete_state(tmp_path)
    store = state.core.memory_store
    distilled = store.add_memory("蒸留ノイズ", type="fact", importance=0.5, protection_grade="B")
    inherited = store.add_memory("原典", type="fact", importance=0.5, protection_grade="B")
    diary_id = store.add_memory("日記", type="episodic", importance=0.5, protection_grade="A")
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
    res = client.get(f"/api/memories/{diary_id}/diary_material")
    assert res.status_code == 200
    ids = {item["id"] for item in res.json()}
    assert distilled in ids
    assert inherited not in ids
    # 読み取り専用: 何も物理削除されていない
    assert store.get_memory_by_id(diary_id) is not None
    assert store.get_memory_by_id(distilled) is not None
    assert store.get_memory_by_id(inherited) is not None


def test_memories_diary_material_rejects_non_episodic(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory("ただのfact", type="fact", importance=0.5)
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)
    res = client.get(f"/api/memories/{mid}/diary_material")
    assert res.status_code == 400


def test_memories_delete_episodic_cascades_legacy_chunks_and_resyncs_state(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """§4.8.1: parent_id で親を参照するレガシーチャンクは自動連鎖削除・last_episodic_atを再同期。"""
    state = _install_delete_state(tmp_path)
    store = state.core.memory_store
    remaining_diary = store.add_memory(
        "残る日記", type="episodic", importance=0.5, protection_grade="A",
        created_at="2026-07-20T00:00:00+00:00",
    )
    parent_id = store.add_memory(
        "消す日記（親・原文庫）", type="episodic", importance=0.5, protection_grade="A",
        embed=False, created_at="2026-07-22T00:00:00+00:00",
    )
    child1 = store.add_memory(
        "検索用チャンク1", type="semantic", importance=0.5, protection_grade="B", parent_id=parent_id,
    )
    child2 = store.add_memory(
        "検索用チャンク2", type="semantic", importance=0.5, protection_grade="B", parent_id=parent_id,
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)
    last_episodic_at_before_delete = state.last_episodic_at

    ok = client.delete(f"/api/memories/{parent_id}?confirm=true")
    assert ok.status_code == 200
    body = ok.json()
    assert set(body["cascade_deleted"]) == {child1, child2}
    assert store.get_memory_by_id(parent_id) is None
    assert store.get_memory_by_id(child1) is None
    assert store.get_memory_by_id(child2) is None
    assert store.get_memory_by_id(remaining_diary) is not None
    # 2026-08-01是正: last_episodic_at は後退させない。削除前(now)の方が残存最新の
    # episodic(2026-07-20)より新しいため、再同期後も削除前の値のまま変わらない
    # （2026-07-23に日記削除→2026-08-01に複数日分が再生成された事故の回帰防止）。
    assert state.last_episodic_at == last_episodic_at_before_delete


def test_memories_delete_semantic_does_not_touch_unrelated_children(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """type=semanticの削除ではレガシーチャンク連鎖は発動しない（別内容を巻き込まない）。"""
    state = _install_delete_state(tmp_path)
    store = state.core.memory_store
    mid = store.add_memory("ただのsemantic", type="semantic", importance=0.5, protection_grade="B")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/memories/{mid}?confirm=true")
    assert ok.status_code == 200
    assert ok.json()["cascade_deleted"] == []


def test_memories_edit_requires_confirm_and_updates_content(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory(
        "誤字がある記憶", type="semantic", importance=0.5, protection_grade="B"
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    denied = client.patch(f"/api/memories/{mid}", json={"content": "直した記憶"})
    assert denied.status_code == 400

    ok = client.patch(
        f"/api/memories/{mid}?confirm=true", json={"content": "直した記憶"}
    )
    assert ok.status_code == 200
    assert ok.json()["content"] == "直した記憶"
    record, _pinned = state.core.memory_store.get_memory_by_id(mid)
    assert record.content == "直した記憶"
    assert any("内容編集" in (r.action or "") for r in state.change_log.read_all())


def test_memories_edit_grade_change_recorded(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory(
        "等級を上げたい記憶", type="semantic", importance=0.5, protection_grade="B"
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.patch(
        f"/api/memories/{mid}?confirm=true", json={"protection_grade": "A"}
    )
    assert ok.status_code == 200
    assert ok.json()["protection_grade"] == "A"
    record, _pinned = state.core.memory_store.get_memory_by_id(mid)
    assert record.protection_grade == "A"
    assert any("B→A" in (r.reason or "") for r in state.change_log.read_all())


def test_memories_edit_rejects_pinned(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory("正典", type="semantic", importance=1.0, protection_grade="S")
    conn = state.core.memory_store._connect()  # noqa: SLF001
    try:
        conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (mid,))
        conn.commit()
    finally:
        conn.close()
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    res = client.patch(f"/api/memories/{mid}?confirm=true", json={"content": "改ざん"})
    assert res.status_code == 400
    record, _pinned = state.core.memory_store.get_memory_by_id(mid)
    assert record.content == "正典"


def test_memories_edit_rejects_empty_request(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory("何か", type="semantic", importance=0.5, protection_grade="B")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    res = client.patch(f"/api/memories/{mid}?confirm=true", json={})
    assert res.status_code == 400


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
    store.add_memory("日記の一行", type="episodic", importance=0.5, protection_grade="A")
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
    assert set(sample) == {
        "id", "type", "content", "protection_grade", "created_at", "pinned", "metadata",
    }
    assert sample["metadata"] == {}
    assert sample["pinned"] is False
    assert sample["type"] == "fact"

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


def test_memories_delete_clears_assessment_failure(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """2026-08-01是正: 記憶メンテ画面からの直接削除でも査定失敗台帳の残骸が消える。"""
    state = _install_delete_state(tmp_path)
    mid = state.core.memory_store.add_memory(
        "査定に失敗し続けたfact", type="fact", importance=0.5, protection_grade="B"
    )
    state.core.chore_box.note_assessment_failure(mid, reason="AssessmentParseError")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/memories/{mid}?confirm=true")

    assert ok.status_code == 200
    assert state.core.chore_box.assessment_failure_reason(mid) is None


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


def test_message_delete_clears_current_session_summary_and_pack_reflects_it(
    tmp_path: Path, monkeypatch,
) -> None:  # noqa: ANN001
    """2026-08-01是正: 発言削除後、要約(rolling/fine)に削除内容が残らない。

    フィールドが空になったことだけでなく、ContextPackが実際にフォールバックし、
    削除済みテキストが以降の文脈に混入しないことまで検証する。
    """
    state = _install_delete_state(tmp_path)
    keep_id = state.session_store.add_history("s_current", "user", "残る発言")
    doomed_id = state.session_store.add_history("s_current", "user", "消される発言・秘密の話題")
    state.core.session.rolling_summary = "消される発言・秘密の話題を含む粗要約"
    state.core.session.fine_summary = "消される発言・秘密の話題を含む細かい要約"
    state.core.session.summarized_turn_count = 3
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/messages/{doomed_id}?confirm=true")

    assert ok.status_code == 200
    assert any("要約" in n for n in ok.json()["notes"])
    assert state.core.session.rolling_summary == ""
    assert state.core.session.fine_summary == ""
    assert state.core.session.summarized_turn_count == 0

    from serina.core.context.pack import build_context_pack

    pack = build_context_pack(
        persona_text="人格",
        absolute_rules="ルール",
        session=state.core.session,
        master_utterance="今の発言",
    )
    text = pack.render()
    assert "消される発言・秘密の話題" not in text
    assert "残る発言" in text
    assert keep_id != doomed_id


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


def test_message_delete_removes_matching_shelved_job(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """2026-08-01是正: 車線振替後も失敗して棚上げ(shelf)された宿題も、発言削除で除去される。

    2026-08-01是正(serina-code-reviewer指摘M-2): shelfはchoresと別採番のため、1件だけ
    積んで棚上げするとid一致が偶然になり弁別力が無い。無関係な宿題も積んでおき、
    削除対象の宿題だけが棚から消えることを検証する。
    """
    state = _install_delete_state(tmp_path)
    msg_id = state.session_store.add_history("s_current", "user", "棚上げされた発言")
    unrelated_job_id = state.core.chore_box.enqueue(
        "蒸留", lane="local", payload={"turns": [{"speaker": "master", "text": "無関係な発言"}]},
    )
    job_id = state.core.chore_box.enqueue(
        "蒸留",
        lane="local",
        payload={"turns": [{"speaker": "master", "text": "棚上げされた発言"}]},
    )
    state.core.chore_box.shelve(job_id, reason="3回連続失敗のため棚上げ")
    state.core.chore_box.shelve(unrelated_job_id, reason="3回連続失敗のため棚上げ（無関係）")
    assert state.core.chore_box.shelved_count() == 2
    shelved_target = next(
        j for j in state.core.chore_box.shelved()
        if j.payload["turns"][0]["text"] == "棚上げされた発言"
    )
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/messages/{msg_id}?confirm=true")

    assert ok.status_code == 200
    assert shelved_target.id in ok.json()["chore_jobs_removed"]
    assert state.core.chore_box.shelved_count() == 1
    remaining = state.core.chore_box.shelved()
    assert remaining[0].payload["turns"][0]["text"] == "無関係な発言"


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


def test_message_delete_physical_delete_clears_assessment_failure(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """2026-08-01是正: 記憶の物理削除に伴い、死んだmemory_idの査定失敗台帳も消える。"""
    state = _install_delete_state(tmp_path)
    quote = "この発言だけが根拠の記憶(査定失敗あり)"
    msg_id = state.session_store.add_history("s_current", "user", quote)
    store = state.core.memory_store
    mid = store.add_memory(
        "蒸留結果",
        type="fact",
        importance=0.5,
        protection_grade="B",
        metadata_obj={"source_quotes": [quote]},
    )
    state.core.chore_box.note_assessment_failure(mid, reason="AssessmentParseError")
    monkeypatch.setattr(gui_server, "backup_db", MagicMock(return_value=tmp_path / "b.db"))
    client = TestClient(gui_server.app)

    ok = client.delete(f"/api/messages/{msg_id}?confirm=true")

    assert ok.status_code == 200
    assert mid in ok.json()["memories_deleted"]
    assert state.core.chore_box.assessment_failure_reason(mid) is None


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
