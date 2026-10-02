"""実機スモーク: bge-m3(Ollama)による実DB(866件)への意味的想起確認。設計書 §5.2

自動テストスイートには含めない（Ollama起動が前提のため手動実行）。
Phase2の繰り越し事項（DECISIONS 2026-07-10）を解消する。
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.store import MemoryStore
from mind.core.soul import DATA_DIR

DB_PATH = DATA_DIR / "serina_memory.db"


def main() -> None:
    print("=" * 60)
    print("bge-m3 実機想起スモーク（実DB866件）")
    print("=" * 60)

    embedder = OllamaEmbedder()
    store = MemoryStore(str(DB_PATH), embedder=embedder, vector_dim=1024)

    queries = ["マスターとの約束", "セリナの性格について", "今日の天気の話"]
    for q in queries:
        results = store.recall(q, top_k=3)
        print(f"\nクエリ: {q}")
        for r in results:
            preview = r.content[:40].replace("\n", " ")
            print(f"  score={r.score:.4f} grade={r.protection_grade} {preview}")
        if not results:
            print("  [NG] 想起結果が0件")
            sys.exit(1)

    print("\n疎通確認 成功（意味的な想起が866件から機能している）")


if __name__ == "__main__":
    main()
