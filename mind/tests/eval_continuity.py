"""継続性ヒットハーネス（§5.2）。

昨日の約束・話題の翌日再生を、実DB想起ヒットで近似する。
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
GOLDEN_PATH = Path(__file__).resolve().parent / "golden_continuity_cases.json"
DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"


@dataclass(frozen=True)
class ContinuityEvalResult:
    hit_rate: float
    ok: bool
    per_case: list[tuple[str, float]]
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


def _resolve_min_rate(override: float | None) -> float:
    if override is not None:
        return override
    try:
        import tomllib

        with DEFAULT_EVAL_THRESHOLDS.open("rb") as f:
            thresholds = tomllib.load(f)
        return float(thresholds.get("continuity_hit_rate", {}).get("min_rate", 0.5))
    except (OSError, ValueError, TypeError):
        return 0.5


def run_continuity_eval(
    *,
    db_path: Path | None = None,
    golden_path: Path | None = None,
    min_rate: float | None = None,
    quiet: bool = False,
) -> ContinuityEvalResult:
    target_db = db_path or DB_PATH
    target_golden = golden_path or GOLDEN_PATH
    if not target_db.exists():
        return ContinuityEvalResult(0.0, False, [], error=f"実DBが見つからない: {target_db}")

    golden = json.loads(target_golden.read_text(encoding="utf-8"))
    top_k = golden["k"]
    trials = golden.get("trials", 20)
    threshold = _resolve_min_rate(min_rate)

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
        per_case: list[tuple[str, float]] = []
        for case in golden["cases"]:
            hits = 0
            for _ in range(trials):
                results = store.recall(case["query"], top_k=top_k)
                rank = next(
                    (
                        i
                        for i, r in enumerate(results, start=1)
                        if case["expect_substring"] in r.content
                    ),
                    None,
                )
                if rank is not None and rank <= case["max_rank"]:
                    hits += 1
            rate = hits / trials
            rates.append(rate)
            per_case.append((case["name"], rate))
            if not quiet:
                status = "OK" if rate >= threshold else "参考NG"
                print(
                    f"[{status}] {case['name']!r}: ヒット率={rate:.0%} "
                    f"({hits}/{trials}, max_rank={case['max_rank']})"
                )

    hit_rate = sum(rates) / len(rates) if rates else 0.0
    ok = hit_rate >= threshold
    if not quiet:
        print(f"\n継続性平均ヒット率: {hit_rate:.0%}（目標{threshold:.0%}以上）")
        print("合格" if ok else "不合格")
    return ContinuityEvalResult(hit_rate=hit_rate, ok=ok, per_case=per_case)


def main() -> None:
    print("=" * 60)
    print("継続性ヒット（golden_continuity_cases.json）")
    print("=" * 60)
    result = run_continuity_eval(quiet=False)
    if result.error:
        print(f"[NG] {result.error}")
        sys.exit(1)
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
