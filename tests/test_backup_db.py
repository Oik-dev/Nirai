"""tools/backup_db.backup_db 公開関数のテスト（B7 土台）。"""

from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.backup_db import backup_db  # noqa: E402


def test_backup_db_copies_and_rotates() -> None:
    tmp = Path(tempfile.mkdtemp())
    src = tmp / "src.db"
    dest_dir = tmp / "backups"
    conn = sqlite3.connect(str(src))
    conn.execute("CREATE TABLE t (x INTEGER)")
    conn.execute("INSERT INTO t VALUES (1)")
    conn.commit()
    conn.close()

    dest1 = backup_db(src, dest_dir, keep_generations=2)
    assert dest1.exists()
    dest2 = backup_db(src, dest_dir, keep_generations=2)
    assert dest2.exists()
    dest3 = backup_db(src, dest_dir, keep_generations=2)
    backups = list(dest_dir.glob("serina_memory_*.db"))
    assert len(backups) == 2
    assert dest3 in backups


def main() -> None:
    try:
        test_backup_db_copies_and_rotates()
        print("  [OK] test_backup_db_copies_and_rotates")
        print("全テスト合格")
    except AssertionError as e:
        print(f"  [NG] {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
