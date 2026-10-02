"""wipe_memory_runtime の backup 実在ガードの単体テスト。"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import mind.tools.wipe_memory_runtime as wipe_mod
from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.protection import ChangeLog
from mind.core.memory.store import MemoryStore
from mind.tools.wipe_memory_runtime import find_recent_backup, wipe_memory_runtime


def test_find_recent_backup_returns_none_when_dir_missing() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "serina_memory.db"
        db.write_bytes(b"dummy")
        missing_dir = tmp / "no_such_backup_dir"
        assert find_recent_backup(db, backup_dir=missing_dir) is None


def test_find_recent_backup_returns_none_when_no_matching_file() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "serina_memory.db"
        db.write_bytes(b"dummy")
        backup_dir = tmp / "backups"
        backup_dir.mkdir()
        (backup_dir / "other_file.db").write_bytes(b"x")
        assert find_recent_backup(db, backup_dir=backup_dir) is None


def test_find_recent_backup_rejects_stale_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        backup_dir = tmp / "backups"
        backup_dir.mkdir()
        stale = backup_dir / "serina_memory_20200101_000000_000000.db"
        stale.write_bytes(b"old")
        time.sleep(0.05)
        db = tmp / "serina_memory.db"
        db.write_bytes(b"dummy")  # db の更新が backup より後 = 控えが古い
        assert find_recent_backup(db, backup_dir=backup_dir) is None


def test_find_recent_backup_accepts_fresh_backup() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db = tmp / "serina_memory.db"
        db.write_bytes(b"dummy")
        time.sleep(0.05)
        backup_dir = tmp / "backups"
        backup_dir.mkdir()
        fresh = backup_dir / "serina_memory_20260722_005038_698315.db"
        fresh.write_bytes(b"fresh")  # backup の更新が db より後 = 有効な控え
        found = find_recent_backup(db, backup_dir=backup_dir)
        assert found == fresh


def test_wipe_clears_facts_vec_too(monkeypatch) -> None:  # noqa: ANN001
    """2026-07-26 B2是正(serina-code-reviewer指摘I-2): factsを消す前にfacts_vecも
    空にする（孤児防止）。本番のdata/配下は一切触らない
    （STATE_FILES_TO_DELETEを空に差し替え、change_logもtmp配下へ向ける）。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        db_path = tmp / "test_memory.db"
        store = MemoryStore(
            str(db_path),
            embedder=OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0]),
            vector_dim=4,
        )
        store.facts.add_fact(
            subject="a", predicate="b", object="c", statement="消える事実",
            episode_ids=[1], embedding=[1.0, 0.0, 0.0, 0.0],
        )

        # 本番data/配下を一切触らないよう空へ差し替える
        monkeypatch.setattr(wipe_mod, "STATE_FILES_TO_DELETE", ())
        change_log = ChangeLog(tmp / "change_log.jsonl")

        summary = wipe_memory_runtime(db_path, change_log=change_log, skip_backup_check=True)

        assert summary["cleared_tables"]["facts_vec"] == 1
        assert summary["cleared_tables"]["facts"] == 1
