"""評価 10 指標ハーネス（合意台帳 §5 / Wave 6 C2）。

空回し（既定）と実測（--live）の二モード。
合格ラインは config/eval_thresholds.toml（正典に数値固定しない）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

import requests

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

DEFAULT_EVAL_THRESHOLDS = ROOT / "config" / "eval_thresholds.toml"
PULSE_LOG_PATH = ROOT / "data" / "eval_pulse_log.json"
LATENCY_PROMPT = "こんにちは。短く一言だけ返して。"


@dataclass(frozen=True)
class MetricResult:
    name: str
    status: str  # "skipped" | "pass" | "fail"
    detail: str


def load_eval_thresholds(path: Path | None = None) -> dict:
    target = path or DEFAULT_EVAL_THRESHOLDS
    with target.open("rb") as f:
        return tomllib.load(f)


def _metric_recall_hit_rate(thresholds: dict, *, live: bool) -> MetricResult:
    min_avg = thresholds.get("recall_hit_rate", {}).get("min_average", 0.9)
    if not live:
        return MetricResult(
            name="想起ヒット率",
            status="skipped",
            detail=f"--live で実測（目標平均{min_avg:.0%}）。手動: tests/eval_recall.py",
        )
    from eval_recall import run_recall_eval

    result = run_recall_eval(min_hit_rate=min_avg, quiet=True)
    if result.error:
        return MetricResult(name="想起ヒット率", status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name="想起ヒット率",
        status=status,
        detail=f"平均{result.average_rate:.0%}（目標{min_avg:.0%}以上）",
    )


def _metric_false_recall_rate(thresholds: dict, *, live: bool) -> MetricResult:
    max_rate = thresholds.get("false_recall_rate", {}).get("max_rate", 0.1)
    if not live:
        return MetricResult(
            name="誤想起率",
            status="skipped",
            detail=f"--live で実測（上限{max_rate:.0%}）。手動: tests/eval_false_recall.py",
        )
    from eval_false_recall import run_false_recall_eval

    result = run_false_recall_eval(max_rate=max_rate, quiet=True)
    if result.error:
        return MetricResult(name="誤想起率", status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name="誤想起率",
        status=status,
        detail=f"平均混入{result.average_rate:.0%}（上限{max_rate:.0%}）",
    )


def _metric_time_query_accuracy(thresholds: dict, *, live: bool) -> MetricResult:
    min_rate = thresholds.get("time_query_accuracy", {}).get("min_rate", 0.8)
    from eval_time_query import run_time_query_eval

    result = run_time_query_eval(min_rate=min_rate, quiet=True)
    if result.error:
        return MetricResult(name="時間クエリ正答率", status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name="時間クエリ正答率",
        status=status,
        detail=f"正答率{result.accuracy:.0%}（目標{min_rate:.0%}以上）",
    )


def _metric_response_latency(thresholds: dict, *, live: bool) -> MetricResult:
    max_p95 = thresholds.get("response_latency_seconds", {}).get("max_p95", 30.0)
    if not live:
        return MetricResult(
            name="応答体感秒数",
            status="skipped",
            detail=f"--live で実測（p95上限{max_p95}s）",
        )
    from serina.brains.qwen.adapter import DEFAULT_BASE_URL, DEFAULT_MODEL

    def _one_call() -> float:
        started = time.perf_counter()
        response = requests.post(
            f"{DEFAULT_BASE_URL}/api/generate",
            json={
                "model": DEFAULT_MODEL,
                "prompt": LATENCY_PROMPT,
                "stream": False,
                "think": False,
            },
            timeout=240.0,
        )
        response.raise_for_status()
        if not response.json().get("response"):
            raise RuntimeError("empty response")
        return time.perf_counter() - started

    samples: list[float] = []
    errors: list[str] = []
    try:
        # コールドスタート（モデルロード）を計測から除外
        _one_call()
    except Exception as exc:  # noqa: BLE001
        return MetricResult(
            name="応答体感秒数",
            status="fail",
            detail=f"暖機失敗: {exc}",
        )
    for _ in range(3):
        try:
            samples.append(_one_call())
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))
    if len(samples) < 2:
        return MetricResult(
            name="応答体感秒数",
            status="fail",
            detail=f"計測失敗（{'; '.join(errors) or 'samples不足'}）",
        )
    # 標本が少ないので近似: 最大値を p95 代理にする
    # 注: 短プロンプト煙測。フルパック会話は暖機後15〜21s（MILESTONE環境メモ）
    p95 = max(samples)
    median = statistics.median(samples)
    status = "pass" if p95 <= max_p95 else "fail"
    return MetricResult(
        name="応答体感秒数",
        status=status,
        detail=f"暖機後 n={len(samples)} median={median:.1f}s p95≈{p95:.1f}s（上限{max_p95}s）",
    )


def _metric_growth_reflection(thresholds: dict, *, live: bool) -> MetricResult:
    min_rate = thresholds.get("growth_reflection_rate", {}).get("min_rate", 0.5)
    from eval_growth import run_growth_eval

    result = run_growth_eval(min_rate=min_rate, quiet=True)
    name = "成長反映率（構造ゲート）"  # 要約→パック配管の検査。会話内容の成長判定ではない
    if result.error:
        return MetricResult(name=name, status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name=name,
        status=status,
        detail=f"配管反映{result.reflection_rate:.0%}（目標{min_rate:.0%}以上・会話live判定は保留）",
    )


def _metric_correction_recurrence(thresholds: dict, *, live: bool) -> MetricResult:
    max_rate = thresholds.get("correction_recurrence_rate", {}).get("max_rate", 0.15)
    from eval_correction import run_correction_eval

    result = run_correction_eval(max_rate=max_rate, quiet=True)
    if result.error:
        return MetricResult(name="訂正再発率", status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name="訂正再発率",
        status=status,
        detail=f"再発率{result.recurrence_rate:.0%}（上限{max_rate:.0%}）",
    )


WEEKLY_LOG_PATH = ROOT / "data" / "eval_life_weekly.json"


def _metric_visible_growth(thresholds: dict, *, live: bool) -> MetricResult:
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
    if WEEKLY_LOG_PATH.exists():
        try:
            payload = json.loads(WEEKLY_LOG_PATH.read_text(encoding="utf-8"))
            weeks = payload.get("weeks", [])
            if weeks:
                nonempty = sum(1 for w in weeks if w.get("nonempty"))
                ratio = nonempty / len(weeks)
                status = "pass" if ratio >= min_ratio else "fail"
                return MetricResult(
                    name="可視成長",
                    status=status,
                    detail=f"週次非空{ratio:.0%}（{nonempty}/{len(weeks)}、目標{min_ratio:.0%}）",
                )
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            return MetricResult(
                name="可視成長",
                status="fail",
                detail=f"週次ログ読込失敗: {exc}",
            )
    return MetricResult(
        name="可視成長",
        status="pass",
        detail=f"life/ に {len(md_files)} ファイル（週次ログ未蓄積・存在チェックのみ）",
    )


def _metric_assistant_tone(thresholds: dict, *, live: bool) -> MetricResult:
    max_rate = thresholds.get("assistant_tone_rate", {}).get("max_rate", 0.2)
    # 2026-07-20: からかい許容度の刃明文チェックは退役。会話出力ベースの出現率は未配線。
    return MetricResult(
        name="アシスタント化率",
        status="skipped",
        detail=f"刃明文チェック退役・会話出力監視は未配線（監視上限{max_rate:.0%}）",
    )


def _metric_continuity_hit(thresholds: dict, *, live: bool) -> MetricResult:
    min_rate = thresholds.get("continuity_hit_rate", {}).get("min_rate", 0.5)
    if not live:
        return MetricResult(
            name="継続性ヒット",
            status="skipped",
            detail=f"--live で実測（目標{min_rate:.0%}）。手動: tests/eval_continuity.py",
        )
    from eval_continuity import run_continuity_eval

    result = run_continuity_eval(min_rate=min_rate, quiet=True)
    if result.error:
        return MetricResult(name="継続性ヒット", status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name="継続性ヒット",
        status=status,
        detail=f"平均{result.hit_rate:.0%}（目標{min_rate:.0%}以上）",
    )


def _metric_pulse_annoyance(thresholds: dict, *, live: bool) -> MetricResult:
    max_score = thresholds.get("pulse_annoyance", {}).get("max_weekly_score", 3.0)
    if not PULSE_LOG_PATH.exists():
        return MetricResult(
            name="Pulse嫌悪",
            status="skipped",
            detail=f"主観週次（上限{max_score}）。記録先: {PULSE_LOG_PATH.name}",
        )
    try:
        payload = json.loads(PULSE_LOG_PATH.read_text(encoding="utf-8"))
        score = float(payload.get("weekly_score", max_score + 1))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return MetricResult(
            name="Pulse嫌悪",
            status="fail",
            detail=f"記録読込失敗: {exc}",
        )
    status = "pass" if score <= max_score else "fail"
    return MetricResult(
        name="Pulse嫌悪",
        status=status,
        detail=f"週次スコア={score}（上限{max_score}）",
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


def run_eval_suite(
    *,
    thresholds_path: Path | None = None,
    live: bool = False,
    strict: bool = False,
) -> list[MetricResult]:
    thresholds = load_eval_thresholds(thresholds_path)
    results = [fn(thresholds, live=live) for fn in METRIC_RUNNERS]
    if strict and any(r.status == "fail" for r in results):
        raise SystemExit(1)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Serina 評価セット（§5.2）")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Ollama/実DBで想起・誤想起・継続性・応答秒数を実測する",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail が1件でもあれば exit 1",
    )
    args = parser.parse_args()

    mode = "実測" if args.live else "空回し"
    print("=" * 60, flush=True)
    print(f"評価セット（§5）{mode}ハーネス", flush=True)
    print("=" * 60, flush=True)
    results = run_eval_suite(live=args.live, strict=args.strict)
    for r in results:
        print(f"[{r.status.upper():7}] {r.name}: {r.detail}", flush=True)
    fails = [r for r in results if r.status == "fail"]
    if fails:
        print(f"\n{len(fails)} 指標が fail", flush=True)
        sys.exit(1)
    skipped = sum(1 for r in results if r.status == "skipped")
    print(f"\n{mode}完了（skipped={skipped} / fail=0）", flush=True)


if __name__ == "__main__":
    main()
