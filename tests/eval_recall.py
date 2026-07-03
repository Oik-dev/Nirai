r"""想起品質の回帰評価ハーネス（ゴールデンクエリ）

実DBの一時コピーに対して golden_queries.json のクエリを検索し、
期待した記憶が上位に想起されるかを検証する。減衰式・係数・pinned特例の
変更（スライス3c）の前後で実行し、想起品質の劣化を検出する。

- 実DBは変更しない（search は last_accessed を更新するため、必ずコピーに対して実行）
- 埋め込みに Ollama(bge-m3) が必要。未起動なら exit 2 でスキップ扱い
- 使い方: python tests/eval_recall.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.connectors.embedder import OllamaEmbedder
from serina.memory.store import MemoryStore

# Windowsコンソール(cp932)でも ✓/✗ が出力できるようにする
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REAL_DB = ROOT / "data" / "serina_memory.db"
GOLDEN_PATH = Path(__file__).parent / "golden_queries.json"


def copy_db(src: Path, dest: Path) -> None:
    """WAL含め安全にコピーするため sqlite3 online backup API を使う"""
    src_conn = sqlite3.connect(str(src))
    try:
        dest_conn = sqlite3.connect(str(dest))
        try:
            src_conn.backup(dest_conn)
        finally:
            dest_conn.close()
    finally:
        src_conn.close()


def main() -> int:
    if not REAL_DB.exists():
        print(f"[NG] 実DBが見つかりません: {REAL_DB}")
        return 1

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    k = int(golden.get("k", 8))
    queries = golden["queries"]

    embedder = OllamaEmbedder()
    try:
        embedder.embed("疎通確認")
    except Exception as exc:
        print(f"[SKIP] Ollama(bge-m3) に接続できないため評価をスキップします: {exc}")
        return 2

    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        tmp_db = Path(tmp) / "eval_copy.db"
        copy_db(REAL_DB, tmp_db)
        store = MemoryStore(embedder, db_path=tmp_db)

        print(f"=== 想起品質評価（k={k}, クエリ{len(queries)}件, DB={REAL_DB.name} のコピー） ===")
        for q in queries:
            results = store.search(q["query"], k)
            rank = None
            score = None
            for i, mem in enumerate(results, start=1):
                if q["expect_substring"] in mem["content"]:
                    rank = i
                    score = mem["score"]
                    break

            max_rank = int(q.get("max_rank", 3))
            if rank is not None and rank <= max_rank:
                print(f"✓ {q['name']}: {rank}位 (score={score:.3f}, 基準: {max_rank}位以内)")
            elif rank is not None:
                failures += 1
                print(f"✗ {q['name']}: {rank}位 — 基準の{max_rank}位以内に届かず (score={score:.3f})")
            else:
                failures += 1
                print(f"✗ {q['name']}: top-{k} 圏外 — 『{q['expect_substring']}』を含む記憶が想起されず")

    if failures:
        print(f"\n=== 不合格 {failures}/{len(queries)} 件。減衰式・係数の変更が想起品質を壊していないか確認してください ===")
        return 1
    print(f"\n=== 全{len(queries)}件合格 ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
