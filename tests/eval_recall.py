"""想起品質の回帰評価ハーネス。設計書 §4.4（2026-07-17改訂: 足し算の活性化モデル）

実DBのコピーに対し golden_queries.json のゴールデンクエリを複数回引き、
expect_substring が max_rank位以内に現れた割合（ヒット率）を測る。
ゆらぎ（noise_sigma）により想起は意図的に非決定論のため、1回の合否ではなく
排出率で判定する（§4.4付帯ルール4）。
合否は**全クエリの平均ヒット率**が min_hit_rate 以上かで判定する（個別クエリ単位ではない。
2026-07-17マスター合意: 意味的に近い競合が特に多いクエリが1問際どくても、全体平均で
目標を満たせば許容する。個別クエリの表示は調整時の目安に留める）。
自動テストスイートには含めない（Ollama起動・実DBコピーが前提のため手動実行）。
重み・下駄・足切りのツマミ（config/thresholds.toml [recall]）の調整はこのハーネスで行う。
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

from serina.core.config import load_thresholds
from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.store import MemoryStore, RecallParams

DB_PATH = ROOT / "data" / "serina_memory.db"
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_queries.json"


def _production_recall_params() -> RecallParams:
    """本番と同じツマミ（thresholds.toml [recall]）で測る。factory.pyの組み立てと同一。"""
    t = load_thresholds()
    return RecallParams(
        weight_relevance=t.recall_weight_relevance,
        weight_importance=t.recall_weight_importance,
        weight_recency=t.recall_weight_recency,
        grade_bonus_s=t.recall_grade_bonus_s,
        grade_bonus_a=t.recall_grade_bonus_a,
        spread_decay=t.recall_spread_decay,
        spread_seeds=t.recall_spread_seeds,
        noise_sigma=t.recall_noise_sigma,
        activation_floor=t.recall_activation_floor,
    )


def main() -> None:
    print("=" * 60)
    print("想起品質 回帰評価（golden_queries.json・複数回試行ヒット率）")
    print("=" * 60)

    if not DB_PATH.exists():
        print(f"[NG] 実DBが見つからない: {DB_PATH}")
        sys.exit(1)

    golden = json.loads(GOLDEN_PATH.read_text(encoding="utf-8"))
    top_k = golden["k"]
    trials = golden.get("trials", 20)
    min_hit_rate = golden.get("min_hit_rate", 0.9)

    # 実DBは読み取り専用で使う（recallはlast_accessedを書き換えるため、コピーに対して実行する）
    # 注: 試行を重ねるとコピー上でヒットした記憶の鮮度が回復していく。本番でも
    # 「一度想起された記憶は浮かびやすくなる」（§4.1）ため、これは仕様どおりの測定条件。
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_copy = Path(tmp_dir) / "serina_memory_copy.db"
        shutil.copyfile(DB_PATH, db_copy)

        embedder = OllamaEmbedder()
        store = MemoryStore(
            str(db_copy), embedder=embedder, vector_dim=1024,
            recall_params=_production_recall_params(),
        )

        rates = []
        for q in golden["queries"]:
            hits = 0
            last_results = []
            for _ in range(trials):
                results = store.recall(q["query"], top_k=top_k)
                last_results = results
                rank = next(
                    (i for i, r in enumerate(results, start=1) if q["expect_substring"] in r.content),
                    None,
                )
                if rank is not None and rank <= q["max_rank"]:
                    hits += 1
            rate = hits / trials
            rates.append(rate)
            # 個別クエリの目安表示（参考。合否は全クエリ平均で判定する。2026-07-17マスター合意:
            # 意味的に近い競合が特に多いクエリ1問だけが際どくても、全体平均で90%以上なら許容する）
            status = "OK" if rate >= min_hit_rate else "参考NG"
            print(f"[{status}] {q['name']!r}: ヒット率={rate:.0%} ({hits}/{trials}, max_rank={q['max_rank']})")
            for i, r in enumerate(last_results[:5], start=1):
                snippet = r.content.replace("\n", " ")[:30]
                print(f"    {i}. act={r.score:.4f} grade={r.protection_grade} {snippet}")

    average_rate = sum(rates) / len(rates)
    print(f"\n全クエリ平均ヒット率: {average_rate:.0%}（目標{min_hit_rate:.0%}以上）")
    if average_rate < min_hit_rate:
        print("不合格")
        sys.exit(1)
    print("合格")


if __name__ == "__main__":
    main()
