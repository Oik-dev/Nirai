"""指示忘却のテスト。Wave 2 / 合意台帳 §3.5。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from serina.core.memory.directed_forget import (
    confirm_forget,
    propose_forget_candidates,
)
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.protection import ChangeLog, GenerationStore, ProtectionError
from serina.core.memory.store import MemoryStore, RecallParams


def _fake_embedder() -> OllamaEmbedder:
    vectors = {
        "天": [1.0, 0.0, 0.0, 0.0],
        "海": [0.0, 1.0, 0.0, 0.0],
    }
    return OllamaEmbedder(call_fn=lambda model, text: vectors.get(text[0], [0.0, 0.0, 0.0, 1.0]))


def _fresh_store(tmp: Path) -> MemoryStore:
    return MemoryStore(
        str(tmp / "mem.db"),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0),
    )


def _stores(tmp: Path) -> tuple[ChangeLog, GenerationStore]:
    return ChangeLog(tmp / "changes.jsonl"), GenerationStore(tmp / "generations.jsonl")


def test_propose_forget_candidates_returns_recall_hits() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        store.add_memory("天気の話", type="fact", importance=0.5)
        store.add_memory("海の思い出", type="event", importance=0.5)

        candidates = propose_forget_candidates(store, "天気の話題", top_k=2)
        assert len(candidates) >= 1
        assert candidates[0].kind == "memory"
        assert "天気" in candidates[0].preview


def test_tombstone_excludes_from_recall(monkeypatch) -> None:
    backup_mock = MagicMock(return_value=Path("backup.db"))
    monkeypatch.setattr("tools.backup_db.backup_db", backup_mock)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        change_log, generation_store = _stores(tmp)
        mid = store.add_memory("天気の話", type="fact", importance=0.9)

        confirm_forget(
            store,
            memory_id=mid,
            reason="マスター指示",
            change_log=change_log,
            generation_store=generation_store,
            backup_dir=tmp / "backups",
        )

        assert backup_mock.called
        assert store.is_memory_tombstoned(mid)
        results = store.recall("天気の話題", top_k=3)
        assert all(r.id != mid for r in results)

        conn = store._connect()  # noqa: SLF001
        try:
            vec = conn.execute("SELECT 1 FROM memory_vec WHERE memory_id = ?", (mid,)).fetchone()
        finally:
            conn.close()
        assert vec is None


def test_canonical_pinned_memory_is_rejected() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        change_log, generation_store = _stores(tmp)
        mid = store.add_memory("正典", type="promise", importance=1.0, protection_grade="S")
        conn = sqlite3.connect(store._db_path)  # noqa: SLF001
        conn.execute("UPDATE memories SET pinned = 1 WHERE id = ?", (mid,))
        conn.commit()
        conn.close()

        try:
            confirm_forget(
                store,
                memory_id=mid,
                master_confirmed_s=True,
                reason="忘れて",
                change_log=change_log,
                generation_store=generation_store,
                backup_dir=tmp / "backups",
            )
            raise AssertionError("正典 pinned が忘却できてしまった")
        except ProtectionError:
            pass


def test_grade_s_requires_master_confirmation() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        change_log, generation_store = _stores(tmp)
        mid = store.add_memory("約束", type="promise", importance=1.0, protection_grade="S")

        try:
            confirm_forget(
                store,
                memory_id=mid,
                master_confirmed_s=False,
                reason="忘れて",
                change_log=change_log,
                generation_store=generation_store,
                backup_dir=tmp / "backups",
            )
            raise AssertionError("S 確認なしで忘却できてしまった")
        except ProtectionError:
            pass


def test_grade_s_allowed_with_master_confirmation(monkeypatch) -> None:
    backup_mock = MagicMock(return_value=Path("backup.db"))
    monkeypatch.setattr("tools.backup_db.backup_db", backup_mock)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        change_log, generation_store = _stores(tmp)
        mid = store.add_memory("約束", type="promise", importance=1.0, protection_grade="S")

        confirm_forget(
            store,
            memory_id=mid,
            master_confirmed_s=True,
            reason="マスター確認済み忘却",
            change_log=change_log,
            generation_store=generation_store,
            backup_dir=tmp / "backups",
        )
        assert store.is_memory_tombstoned(mid)
        assert len(change_log.read_all()) == 1


def test_fact_tombstone(monkeypatch) -> None:
    backup_mock = MagicMock(return_value=Path("backup.db"))
    monkeypatch.setattr("tools.backup_db.backup_db", backup_mock)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        change_log, generation_store = _stores(tmp)
        fid = store.facts.add_fact(
            subject="a",
            predicate="b",
            object="c",
            statement="忘れたい事実",
            episode_ids=[1],
        )

        confirm_forget(
            store,
            fact_id=fid,
            reason="事実忘却",
            change_log=change_log,
            generation_store=generation_store,
            backup_dir=tmp / "backups",
        )

        fact = store.facts.get_fact(fid)
        assert fact is not None
        assert fact.status == "tombstone"
        assert fact.valid_to is not None
        assert backup_mock.called


def test_tombstone_excludes_from_table_scans(monkeypatch) -> None:
    """忘却済み記憶が Pulse(list_by_type)・日記材料(list_memories_since)・
    機微査定(get_unassessed_memories) の直読み経路で甦らないこと（serina-code-reviewer 2026-07-19 Important）。"""
    backup_mock = MagicMock(return_value=Path("backup.db"))
    monkeypatch.setattr("tools.backup_db.backup_db", backup_mock)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        store = _fresh_store(tmp)
        change_log, generation_store = _stores(tmp)
        forgotten = store.add_memory("天気の約束", type="promise", importance=0.9)
        kept = store.add_memory("海の約束", type="promise", importance=0.9)

        confirm_forget(
            store,
            memory_id=forgotten,
            reason="マスター指示",
            change_log=change_log,
            generation_store=generation_store,
            backup_dir=tmp / "backups",
        )

        by_type_ids = {r.id for r in store.list_by_type("promise")}
        assert forgotten not in by_type_ids
        assert kept in by_type_ids

        since_ids = {r.id for r in store.list_memories_since(since_iso="2000-01-01T00:00:00")}
        assert forgotten not in since_ids
        assert kept in since_ids

        unassessed_ids = {r.id for r in store.get_unassessed_memories(limit=10)}
        assert forgotten not in unassessed_ids
        assert kept in unassessed_ids
