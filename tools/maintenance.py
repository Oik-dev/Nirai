"""Serina メンテナンス（アーカイブ保持など）"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.embedder import OllamaEmbedder
from serina.core.config import CoreConfig
from serina.memory.db import DEFAULT_DB_PATH
from serina.memory.store import MemoryStore


def main() -> None:
    parser = argparse.ArgumentParser(description="Serina メンテナンス")
    parser.add_argument(
        "--purge-archives", action="store_true", help="保持期間より古い退避ログを物理削除"
    )
    parser.add_argument(
        "--expire-threads", action="store_true", help="期限切れの未解決スレッドをexpired化"
    )
    parser.add_argument("--days", type=int, default=CoreConfig().archive_retention_days)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args()

    store = MemoryStore(OllamaEmbedder(), db_path=args.db)

    did_something = False
    if args.purge_archives:
        deleted = store.purge_archived_older_than(args.days)
        print(f"{args.days}日より古い退避ログを {deleted} 件削除しました。")
        did_something = True
    if args.expire_threads:
        expired = store.expire_open_threads()
        print(f"期限切れスレッドを {expired} 件 expired 化しました。")
        did_something = True
    if not did_something:
        print("何もしていません。--purge-archives / --expire-threads を指定してください。")


if __name__ == "__main__":
    main()
