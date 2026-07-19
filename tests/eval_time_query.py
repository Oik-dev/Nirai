"""時間クエリ正答率ハーネス（§5.2）。

Planner の temporal 検出と、フィクスチャ fact の時間範囲解決を測る。
Ollama 不要。eval_suite の空回しでも実行する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.memory.embedder import OllamaEmbedder
from serina.core.memory.recall_planner import plan_recall, resolve_facts_for_plan
from serina.core.memory.store import MemoryStore, RecallParams

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_time_queries.json"
DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"


@dataclass(frozen=True)
class TimeQueryEvalResult:
    accuracy: float
    ok: bool
    per_case: list[tuple[str, bool]]
    error: str = ""


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])


def _resolve_min_rate(override: float | None) -> float:
    if override is not None:
        return override
    try:
        import tomllib

        with DEFAULT_EVAL_THRESHOLDS.open("rb") as f:
            thresholds = tomllib.load(f)
        return float(thresholds.get("time_query_accuracy", {}).get("min_rate", 0.8))
    except (OSError, ValueError, TypeError):
        return 0.8


def _parse_now(raw: str) -> datetime:
    return datetime.fromisoformat(raw)


def _case_ok(case: dict, now: datetime) -> bool:
    utterance = case["utterance"]
    plan = plan_recall(utterance, now=now)
    expect_types = set(case.get("expect_plan_types", []))
    actual_types = {q.type for q in plan.queries}

    if case.get("expect_empty_plan"):
        return len(plan.queries) == 0

    if expect_types and not expect_types.issubset(actual_types):
        return False

    for keyword in case.get("expect_temporal_keywords", []):
        if not any(q.type == "temporal" and q.q == keyword for q in plan.queries):
            return False

    contains = case.get("time_range_contains")
    if contains:
        temporal = [q for q in plan.queries if q.type == "temporal" and q.time_range]
        if not temporal:
            return False
        joined = "|".join(f"{a}|{b}" for a, b in (q.time_range for q in temporal if q.time_range))
        if contains not in joined:
            return False

    fixture_facts = case.get("fixture_facts")
    if fixture_facts:
        db_path = Path(tempfile.mkdtemp()) / "time_query.db"
        store = MemoryStore(
            str(db_path),
            embedder=_fake_embedder(),
            vector_dim=4,
            recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0, activation_floor=0.0),
        )
        for fact in fixture_facts:
            store.facts.add_fact(
                subject=fact["subject"],
                predicate=fact["predicate"],
                object=fact["object"],
                statement=fact["statement"],
                valid_from=fact.get("valid_from"),
                valid_to=fact.get("valid_to"),
                status=fact.get("status", "active"),
                episode_ids=[1],
            )
        facts = resolve_facts_for_plan(plan, store.facts)
        blob = "\n".join(f.statement for f in facts)
        expect_sub = case.get("expect_fact_substring")
        forbid_sub = case.get("forbid_fact_substring")
        if expect_sub and expect_sub not in blob:
            return False
        if forbid_sub and forbid_sub in blob:
            return False

    return True


def run_time_query_eval(
    *,
    golden_path: Path | None = None,
    min_rate: float | None = None,
    quiet: bool = False,
) -> TimeQueryEvalResult:
    target_golden = golden_path or GOLDEN_PATH
    if not target_golden.exists():
        return TimeQueryEvalResult(0.0, False, [], error=f"ゴールデンなし: {target_golden}")

    golden = json.loads(target_golden.read_text(encoding="utf-8"))
    now = _parse_now(golden["now"])
    threshold = _resolve_min_rate(min_rate)

    per_case: list[tuple[str, bool]] = []
    for case in golden["cases"]:
        ok = _case_ok(case, now)
        per_case.append((case["name"], ok))
        if not quiet:
            print(f"[{'OK' if ok else 'NG'}] {case['name']}")

    accuracy = (sum(1 for _, ok in per_case if ok) / len(per_case)) if per_case else 0.0
    passed = accuracy >= threshold
    if not quiet:
        print(f"\n正答率: {accuracy:.0%}（目標{threshold:.0%}以上）")
        print("合格" if passed else "不合格")
    return TimeQueryEvalResult(accuracy=accuracy, ok=passed, per_case=per_case)


def main() -> None:
    print("=" * 60)
    print("時間クエリ正答率（golden_time_queries.json）")
    print("=" * 60)
    result = run_time_query_eval(quiet=False)
    if result.error:
        print(f"[NG] {result.error}")
        sys.exit(1)
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
