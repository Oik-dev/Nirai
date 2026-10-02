# -*- coding: utf-8 -*-
"""ベクトル index（facts_vec）を DB 本文から全再構築する。

使い方:
    python tools/rebuild_facts_index.py
    python tools/rebuild_facts_index.py --dry-run
    python tools/rebuild_facts_index.py --db path/to.db

本文（facts）は無傷。index 破損・埋め込みモデル更新時の復旧用。
memory_vec の `tools/rebuild_index.py` と対になる。モデル差し替え時は両方を回すこと。
破壊的操作の前に `python tools/backup_db.py` を1回実行すること（保護3原則・可逆性）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.store import MemoryStore, VECTOR_DIM_DEFAULT
from mind.core.soul import DATA_DIR

DEFAULT_DB = DATA_DIR / "serina_memory.db"


def rebuild_facts_index(
    db_path: Path | str,
    *,
    dry_run: bool = False,
    embedder: OllamaEmbedder | None = None,
    vector_dim: int = VECTOR_DIM_DEFAULT,
) -> int:
    """facts_vec を再構築し、件数を返す。dry_run なら件数を数えるだけ。"""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DB が見つかりません: {path}")

    if dry_run:
        conn = sqlite3.connect(str(path))
        try:
            n = conn.execute("SELECT COUNT(*) FROM facts").fetchone()[0]
        finally:
            conn.close()
        return int(n)

    store = MemoryStore(
        str(path),
        embedder=embedder or OllamaEmbedder(),
        vector_dim=vector_dim,
    )
    return store.rebuild_facts_vector_index()


def main() -> int:
    parser = argparse.ArgumentParser(description="facts_vec を facts から全再構築")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="対象 DB パス")
    parser.add_argument("--dry-run", action="store_true", help="再構築せず件数のみ表示")
    args = parser.parse_args()

    try:
        count = rebuild_facts_index(args.db, dry_run=args.dry_run)
    except FileNotFoundError as e:
        print(f"[NG] {e}")
        return 1

    if args.dry_run:
        print(f"[dry-run] facts={count} 件（再構築は行わない）")
    else:
        print(f"[OK] facts_vec を {count} 件再構築しました")
    return 0


if __name__ == "__main__":
    sys.exit(main())
