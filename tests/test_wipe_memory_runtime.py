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

from serina.tools.wipe_memory_runtime import find_recent_backup


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
