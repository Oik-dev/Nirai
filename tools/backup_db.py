# -*- coding: utf-8 -*-
r"""serina_memory.db の日次バックアップ（G:\SerinaDB Backup、7世代ローテーション）。

タスクスケジューラから毎日実行される想定。手動実行も可:
    python tools/backup_db.py
SQLite の online backup API を使うため、REPL 稼働中でも安全にコピーできる。
"""
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "serina_memory.db"
BACKUP_DIR = Path(r"G:\SerinaDB Backup")
KEEP_GENERATIONS = 7


def main() -> int:
    if not DB_PATH.exists():
        print(f"[NG] DB が見つかりません: {DB_PATH}")
        return 1
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"serina_memory_{stamp}.db"

    src = sqlite3.connect(str(DB_PATH))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"[OK] {dest.name} ({size_mb:.1f} MB)")

    # 古い世代を削除（新しい順に KEEP_GENERATIONS 件残す）
    backups = sorted(BACKUP_DIR.glob("serina_memory_*.db"), reverse=True)
    for old in backups[KEEP_GENERATIONS:]:
        old.unlink()
        print(f"[rotate] 削除: {old.name}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
