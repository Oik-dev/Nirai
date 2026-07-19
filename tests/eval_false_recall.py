"""誤想起率ハーネス（§5.2）。

無関係クエリの top_k に正典句（forbidden_substrings）が混入する率を測る。
Ollama + 実DB 前提。eval_suite.py --live から呼ばれる。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from dataclasses import dataclass
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
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_negative_queries.json"
DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"


@dataclass(frozen=True)
class FalseRecallEvalResult:
    average_rate: float
    ok: bool
    per_query: list[tuple[str, float]]
    error: str = ""


def _production_recall_params() -> RecallParams:
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


def _resolve_max_rate(override: float | None) -> float:
    if override is not None:
        return override
    try:
        import tomllib

        with DEFAULT_EVAL_THRESHOLDS.open("rb") as f:
            thresholds = tomllib.load(f)
        return float(thresholds.get("false_recall_rate", {}).get("max_rate", 0.1))
    except (OSError, ValueError, TypeError):
        return 0.1


def run_false_recall_eval(
    *,
    db_path: Path | None = None,
    golden_path: Path | None = None,
    max_rate: float | None = None,
    quiet: bool = False,
) -> FalseRecallEvalResult:
    target_db = db_path or DB_PATH
    target_golden = golden_path or GOLDEN_PATH
    if not target_db.exists():
        return FalseRecallEvalResult(0.0, False, [], error=f"実DBが見つからない: {target_db}")

    golden = json.loads(target_golden.read_text(encoding="utf-8"))
    top_k = golden["k"]
    trials = golden.get("trials", 20)
    threshold = _resolve_max_rate(max_rate)

    with tempfile.TemporaryDirectory() as tmp_dir:
        db_copy = Path(tmp_dir) / "serina_memory_copy.db"
        shutil.copyfile(target_db, db_copy)
        store = MemoryStore(
            str(db_copy),
            embedder=OllamaEmbedder(),
            vector_dim=1024,
            recall_params=_production_recall_params(),
        )

        rates: list[float] = []
        per_query: list[tuple[str, float]] = []
        for q in golden["queries"]:
            contaminated = 0
            forbidden = q["forbidden_substrings"]
            for _ in range(trials):
                results = store.recall(q["query"], top_k=top_k)
                blob = "\n".join(r.content for r in results)
                if any(token in blob for token in forbidden):
                    contaminated += 1
            rate = contaminated / trials
            rates.append(rate)
            per_query.append((q["name"], rate))
            if not quiet:
                status = "OK" if rate <= threshold else "参考NG"
                print(
                    f"[{status}] {q['name']!r}: 混入率={rate:.0%} "
                    f"({contaminated}/{trials})"
                )

    average_rate = sum(rates) / len(rates) if rates else 0.0
    ok = average_rate <= threshold
    if not quiet:
        print(f"\n全クエリ平均混入率: {average_rate:.0%}（上限{threshold:.0%}）")
        print("合格" if ok else "不合格")
    return FalseRecallEvalResult(average_rate=average_rate, ok=ok, per_query=per_query)


def main() -> None:
    print("=" * 60)
    print("誤想起率 評価（golden_negative_queries.json）")
    print("=" * 60)
    result = run_false_recall_eval(quiet=False)
    if result.error:
        print(f"[NG] {result.error}")
        sys.exit(1)
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
