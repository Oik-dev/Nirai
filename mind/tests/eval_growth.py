"""成長反映率ハーネス（§5.2）— 構造ゲート。

§1.5改訂後: セッション要約（粗い／細かめ）が ContextPack に載る率を測る。
prefs/relation 常駐はパックから外したため対象外。LLM 応答内容の判定は含めない。
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.context.pack import build_context_pack
from mind.core.state.session import SessionState

GOLDEN_PATH = Path(__file__).resolve().parent / "golden_growth_cases.json"
DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"


@dataclass(frozen=True)
class GrowthEvalResult:
    reflection_rate: float
    ok: bool
    per_case: list[tuple[str, bool]]
    error: str = ""


def _resolve_min_rate(override: float | None) -> float:
    if override is not None:
        return override
    try:
        import tomllib

        with DEFAULT_EVAL_THRESHOLDS.open("rb") as f:
            thresholds = tomllib.load(f)
        return float(thresholds.get("growth_reflection_rate", {}).get("min_rate", 0.5))
    except (OSError, ValueError, TypeError):
        return 0.5


def _case_ok(case: dict) -> bool:
    session = SessionState()
    session.rolling_summary = case.get("rolling_summary", "") or ""
    session.fine_summary = case.get("fine_summary", "") or ""
    pack = build_context_pack(
        persona_text="人格テスト",
        absolute_rules="絶対ルール",
        session=session,
        master_utterance=case["day2_utterance"],
    )
    rendered = pack.render()
    return all(token in rendered for token in case["expect_in_pack"])


def run_growth_eval(
    *,
    golden_path: Path | None = None,
    min_rate: float | None = None,
    quiet: bool = False,
) -> GrowthEvalResult:
    target_golden = golden_path or GOLDEN_PATH
    if not target_golden.exists():
        return GrowthEvalResult(0.0, False, [], error=f"ゴールデンなし: {target_golden}")

    golden = json.loads(target_golden.read_text(encoding="utf-8"))
    threshold = _resolve_min_rate(min_rate)

    per_case: list[tuple[str, bool]] = []
    for case in golden["cases"]:
        ok = _case_ok(case)
        per_case.append((case["name"], ok))
        if not quiet:
            print(f"[{'OK' if ok else 'NG'}] {case['name']}")

    rate = (sum(1 for _, ok in per_case if ok) / len(per_case)) if per_case else 0.0
    passed = rate >= threshold
    if not quiet:
        print(f"\n反映率: {rate:.0%}（目標{threshold:.0%}以上）")
        print("合格" if passed else "不合格")
    return GrowthEvalResult(reflection_rate=rate, ok=passed, per_case=per_case)


def main() -> None:
    print("=" * 60)
    print("成長反映率 構造ゲート（golden_growth_cases.json）")
    print("=" * 60)
    result = run_growth_eval(quiet=False)
    if result.error:
        print(f"[NG] {result.error}")
        sys.exit(1)
    if not result.ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
