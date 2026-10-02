"""Serina メンテナンス（退避履歴の保持期間掃除）"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.session_store import DEFAULT_SESSION_DB_PATH, SessionStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Serina メンテナンス")
    parser.add_argument(
        "--purge-archives",
        action="store_true",
        help="保持期間より古い退避ログを物理削除",
    )
    parser.add_argument("--days", type=int, default=30, help="退避保持日数（既定 30）")
    parser.add_argument("--db", type=Path, default=DEFAULT_SESSION_DB_PATH)
    args = parser.parse_args()

    if not args.purge_archives:
        print("何もしていません。--purge-archives を指定してください。")
        return

    store = SessionStore(args.db)
    deleted = store.purge_archived_older_than(args.days)
    print(f"{args.days}日より古い退避ログを {deleted} 件削除しました。")


if __name__ == "__main__":
    main()
