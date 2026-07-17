"""想起品質の回帰評価ハーネス。設計書 §4.4（二経路: ベクトル想起＋約束・正典級の確実想起）

実DBのコピーに対し golden_queries.json のゴールデンクエリを検索し、
expect_substring が max_rank位以内に現れるかを検証する。
自動テストスイートには含めない（Ollama起動・実DBコピーが前提のため手動実行）。
recall_with_promises の二経路配線が退行していないかを測る回帰ゲート
（Phase6大掃除でこの経路自体とeval_recall.py本体が失われた反省を踏まえて再建）。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore

DB_PATH = ROOT / "data" / "serina_memory.db"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_queries.json"


def main() -> None:
    print("=" * 60)
    print("想起品質 回帰評価（golden_queries.json）")
    print("=" * 60)

    if not DB_PATH.exists():
        print(f"[NG] 実DBが見つからない: {DB_PATH}")
        sys.exit(1)

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    top_k = golden["k"]

    # 実DBは読み取り専用で使う（recallはlast_accessedを書き換えるため、コピーに対して実行する）
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_copy = Path(tmp_dir) / "serina_memory_copy.db"
        shutil.copyfile(DB_PATH, db_copy)

        embedder = OllamaEmbedder()
        store = MemoryStore(str(db_copy), embedder=embedder, vector_dim=1024)

        failed = 0
        for q in golden["queries"]:
            results = store.recall_with_promises(q["query"], top_k=top_k)
            rank = None
            for i, r in enumerate(results, start=1):
                if q["expect_substring"] in r.content:
                    rank = i
                    break
            ok = rank is not None and rank <= q["max_rank"]
            if not ok:
                failed += 1
            status = "OK" if ok else "NG"
            print(f"[{status}] {q['name']!r}: rank={rank} (max_rank={q['max_rank']})")
            for i, r in enumerate(results[:5], start=1):
                snippet = r.content.replace("\n", " ")[:30]
                print(f"    {i}. score={r.score:.4f} grade={r.protection_grade} {snippet}")

    if failed:
        print(f"\n{failed}件 失敗")
        sys.exit(1)
    print("\n全ゴールデンクエリ合格")


if __name__ == "__main__":
    main()
