"""訂正再発率ハーネス（§5.2）。

supersede 後に旧 fact が検索結果へ再混入しない率を測る。
フィクスチャDB・Ollama不要。eval_suite の空回しでも実行する。
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.memory.embedder import OllamaEmbedder
from mind.core.memory.store import MemoryStore, RecallParams

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_correction_cases.json"
DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"


@dataclass(frozen=True)
class CorrectionEvalResult:
    recurrence_rate: float
    ok: bool
    per_case: list[tuple[str, bool]]
    error: str = ""


def _fake_embedder() -> OllamaEmbedder:
    return OllamaEmbedder(call_fn=lambda model, text: [1.0, 0.0, 0.0, 0.0])


def _resolve_max_rate(override: float | None) -> float:
    if override is not None:
        return override
    try:
        import tomllib

        with DEFAULT_EVAL_THRESHOLDS.open("rb") as f:
            thresholds = tomllib.load(f)
        return float(thresholds.get("correction_recurrence_rate", {}).get("max_rate", 0.15))
    except (OSError, ValueError, TypeError):
        return 0.15


def _build_store() -> MemoryStore:
    db_path = Path(tempfile.mkdtemp()) / "correction.db"
    return MemoryStore(
        str(db_path),
        embedder=_fake_embedder(),
        vector_dim=4,
        recall_params=RecallParams(noise_sigma=0.0, spread_decay=0.0, activation_floor=0.0),
    )


def _add_and_supersede(store: MemoryStore, case: dict) -> None:
    old = case["old_fact"]
    new = case["new_fact"]
    old_id = store.facts.add_fact(
        subject=old["subject"],
        predicate=old["predicate"],
        object=old["object"],
        statement=old["statement"],
        valid_from=old.get("valid_from"),
        status="active",
        episode_ids=[1],
    )
    store.facts.supersede_fact(
        old_id,
        subject=new["subject"],
        predicate=new["predicate"],
        object=new["object"],
        statement=new["statement"],
        valid_from=new.get("valid_from"),
        episode_ids=[1],
    )


def _case_clean(case: dict) -> bool:
    """再発なし（清潔）なら True。"""
    store = _build_store()
    _add_and_supersede(store, case)
    mode = case["mode"]
    allowed = case["allowed_substring"]
    forbidden = case["forbidden_substring"]

    if mode == "fact_search":
        facts = store.facts.search_by_entity(case["search_entity"])
    elif mode == "fact_time_search":
        start, end = case["time_range"]
        facts = store.facts.search_by_time_range(start, end)
    else:
        raise ValueError(f"未知 mode: {mode}")

    blob = "\n".join(f.statement for f in facts)
    if allowed not in blob:
        return False
    if forbidden in blob:
        return False
    if any(f.status == "superseded" for f in facts):
        return False
    return True


def run_correction_eval(
    *,
    golden_path: Path | None = None,
    max_rate: float | None = None,
    quiet: bool = False,
) -> CorrectionEvalResult:
    target_golden = golden_path or GOLDEN_PATH
    if not target_golden.exists():
        return CorrectionEvalResult(1.0, False, [], error=f"ゴールデンなし: {target_golden}")

    golden = json.loads(target_golden.read_text(encoding="utf-8"))
    threshold = _resolve_max_rate(max_rate)

    per_case: list[tuple[str, bool]] = []
    for case in golden["cases"]:
        clean = _case_clean(case)
        per_case.append((case["name"], clean))
        if not quiet:
            print(f"[{'OK' if clean else 'NG'}] {case['name']}")

    # recurrence = 再発したケース率（清潔でない率）
    recurrence = (sum(1 for _, clean in per_case if not clean) / len(per_case)) if per_case else 1.0
    ok = recurrence <= threshold
    if not quiet:
        print(f"\n再発率: {recurrence:.0%}（上限{threshold:.0%}）")
        print("合格" if ok else "不合格")
    return CorrectionEvalResult(recurrence_rate=recurrence, ok=ok, per_case=per_case)


def main() -> None:
    print("=" * 60)
    print("訂正再発率（golden_correction_cases.json）")
    print("=" * 60)
    result = run_correction_eval(quiet=False)
    if result.error:
        print(f"[NG] {result.error}")
        sys.exit(1)
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
