"""評価 10 指標ハーネス（合意台帳 §5 / Wave 6 C2）。

各指標は空回し（dry-run）可能。実 DB / Ollama が無くてもスイート全体が走る。
合格ラインは config/eval_thresholds.toml（正典に数値固定しない）。
"""

from __future__ import annotations

import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"


@dataclass(frozen=True)
class MetricResult:
    name: str
    status: str  # "skipped" | "pass" | "fail"
    detail: str


def load_eval_thresholds(path: Path | None = None) -> dict:
    target = path or DEFAULT_EVAL_THRESHOLDS
    with target.open("rb") as f:
        return tomllib.load(f)


def _metric_recall_hit_rate(thresholds: dict) -> MetricResult:
    min_avg = thresholds.get("recall_hit_rate", {}).get("min_average", 0.9)
    return MetricResult(
        name="想起ヒット率",
        status="skipped",
        detail=f"手動: tests/eval_recall.py（目標平均{min_avg:.0%}）",
    )


def _metric_false_recall_rate(thresholds: dict) -> MetricResult:
    max_rate = thresholds.get("false_recall_rate", {}).get("max_rate", 0.1)
    return MetricResult(
        name="誤想起率",
        status="skipped",
        detail=f"未配線（上限{max_rate:.0%}）。ゴールデン negative セット待ち",
    )


def _metric_time_query_accuracy(thresholds: dict) -> MetricResult:
    min_rate = thresholds.get("time_query_accuracy", {}).get("min_rate", 0.8)
    return MetricResult(
        name="時間クエリ正答率",
        status="skipped",
        detail=f"未配線（目標{min_rate:.0%}）",
    )


def _metric_response_latency(thresholds: dict) -> MetricResult:
    max_p95 = thresholds.get("response_latency_seconds", {}).get("max_p95", 30.0)
    return MetricResult(
        name="応答体感秒数",
        status="skipped",
        detail=f"未配線（p95上限{max_p95}s）",
    )


def _metric_growth_reflection(thresholds: dict) -> MetricResult:
    min_rate = thresholds.get("growth_reflection_rate", {}).get("min_rate", 0.5)
    return MetricResult(
        name="成長反映率",
        status="skipped",
        detail=f"未配線（目標{min_rate:.0%}）",
    )


def _metric_correction_recurrence(thresholds: dict) -> MetricResult:
    max_rate = thresholds.get("correction_recurrence_rate", {}).get("max_rate", 0.15)
    return MetricResult(
        name="訂正再発率",
        status="skipped",
        detail=f"未配線（上限{max_rate:.0%}）",
    )


def _metric_visible_growth(thresholds: dict) -> MetricResult:
    min_ratio = thresholds.get("visible_growth", {}).get("min_weekly_nonempty_ratio", 0.3)
    life_dir = ROOT / "life"
    if not life_dir.exists():
        return MetricResult(
            name="可視成長",
            status="skipped",
            detail=f"life/ 未生成（目標週次非空{min_ratio:.0%}）",
        )
    md_files = list(life_dir.glob("**/*.md"))
    if not md_files:
        return MetricResult(
            name="可視成長",
            status="skipped",
            detail=f"life/ 空（目標週次非空{min_ratio:.0%}）",
        )
    return MetricResult(
        name="可視成長",
        status="pass",
        detail=f"life/ に {len(md_files)} ファイル（閾値参照のみ）",
    )


def _metric_assistant_tone(thresholds: dict) -> MetricResult:
    max_rate = thresholds.get("assistant_tone_rate", {}).get("max_rate", 0.2)
    marker_ok = "からかい許容度" in (ROOT / "prompt" / "persona" / "04_voice.md").read_text(encoding="utf-8")
    return MetricResult(
        name="アシスタント化率",
        status="pass" if marker_ok else "fail",
        detail=f"刃明文={'あり' if marker_ok else 'なし'}（監視上限{max_rate:.0%}）",
    )


def _metric_continuity_hit(thresholds: dict) -> MetricResult:
    min_rate = thresholds.get("continuity_hit_rate", {}).get("min_rate", 0.5)
    return MetricResult(
        name="継続性ヒット",
        status="skipped",
        detail=f"未配線（目標{min_rate:.0%}）",
    )


def _metric_pulse_annoyance(thresholds: dict) -> MetricResult:
    max_score = thresholds.get("pulse_annoyance", {}).get("max_weekly_score", 3.0)
    return MetricResult(
        name="Pulse嫌悪",
        status="skipped",
        detail=f"主観週次（上限{max_score}）。GUI 運用後に記録",
    )


METRIC_RUNNERS = (
    _metric_recall_hit_rate,
    _metric_false_recall_rate,
    _metric_time_query_accuracy,
    _metric_response_latency,
    _metric_growth_reflection,
    _metric_correction_recurrence,
    _metric_visible_growth,
    _metric_assistant_tone,
    _metric_continuity_hit,
    _metric_pulse_annoyance,
)


def run_eval_suite(*, thresholds_path: Path | None = None, strict: bool = False) -> list[MetricResult]:
    thresholds = load_eval_thresholds(thresholds_path)
    results = [fn(thresholds) for fn in METRIC_RUNNERS]
    if strict and any(r.status == "fail" for r in results):
        raise SystemExit(1)
    return results


def main() -> None:
    print("=" * 60)
    print("評価セット（§5）空回しハーネス")
    print("=" * 60)
    results = run_eval_suite()
    for r in results:
        print(f"[{r.status.upper():7}] {r.name}: {r.detail}")
    fails = [r for r in results if r.status == "fail"]
    if fails:
        print(f"\n{len(fails)} 指標が fail")
        sys.exit(1)
    print("\n空回し完了（skipped は未配線指標）")


if __name__ == "__main__":
    main()
