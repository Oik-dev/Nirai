# -*- coding: utf-8 -*-
r"""serina_memory.db のバックアップ（G:\SerinaDB Backup、7世代ローテーション）。

タスクスケジューラから毎日実行される想定。手動実行も可:
    python tools/backup_db.py

破壊的工程（tombstone / persona 改訂等）からは `backup_db()` を import して呼ぶ（B7）。
SQLite の online backup API を使うため、REPL 稼働中でも安全にコピーできる。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.soul import DATA_DIR  # noqa: E402

DB_PATH = DATA_DIR / "serina_memory.db"
BACKUP_DIR = Path(r"G:\SerinaDB Backup")
KEEP_GENERATIONS = 7


def backup_db(
    db_path: Path | str | None = None,
    backup_dir: Path | str | None = None,
    *,
    keep_generations: int = KEEP_GENERATIONS,
) -> Path:
    """DB をバックアップし、保存先 Path を返す。

    呼び出し元（裏方便・migrate 等）が破壊前に使える公開関数。
    """
    src_path = Path(db_path) if db_path is not None else DB_PATH
    dest_dir = Path(backup_dir) if backup_dir is not None else BACKUP_DIR

    if not src_path.exists():
        raise FileNotFoundError(f"DB が見つかりません: {src_path}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    # 同一秒内の連続呼び出しでも上書きしない（ミリ秒付き）
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    dest = dest_dir / f"serina_memory_{stamp}.db"

    src = sqlite3.connect(str(src_path))
    try:
        dst = sqlite3.connect(str(dest))
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()

    backups = sorted(dest_dir.glob("serina_memory_*.db"), reverse=True)
    for old in backups[keep_generations:]:
        old.unlink()

    return dest


def main() -> int:
    try:
        dest = backup_db()
    except FileNotFoundError as e:
        print(f"[NG] {e}")
        return 1

    size_mb = dest.stat().st_size / (1024 * 1024)
    print(f"[OK] {dest.name} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
