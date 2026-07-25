"""評価 9 指標ハーネス（設計書 §5.2）。

空回し（既定）と実測（--live）の二モード。
合格ラインは config/eval_thresholds.toml（正典に数値固定しない）。
Pulse嫌悪（マスター週次主観）は 2026-07-20 退役。
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
    from serina.brains.ollama.adapter import DEFAULT_BASE_URL, DEFAULT_MODEL

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
    from eval_assistant_tone import load_sample_replies, run_assistant_tone_eval

    # live 時はゴールデン＋本番DB直近を合算（DB空ならゴールデンのみ）
    replies: list[str] | None = None
    src = "ゴールデン"
    if live:
        golden = load_sample_replies()
        recent = _load_recent_assistant_replies() or []
        replies = golden + recent
        src = "実DB+ゴールデン" if recent else "ゴールデン"
    result = run_assistant_tone_eval(max_rate=max_rate, replies=replies, quiet=True)
    if result.error:
        return MetricResult(name="アシスタント化率", status="fail", detail=result.error)
    status = "pass" if result.ok else "fail"
    return MetricResult(
        name="アシスタント化率",
        status=status,
        detail=(
            f"接客口調{result.rate:.0%}（{result.flagged}/{result.total}・"
            f"上限{max_rate:.0%}・{src}）"
        ),
    )


def _load_recent_assistant_replies(*, limit: int = 40) -> list[str] | None:
    """本番DBから直近assistant返答を取る。失敗・空なら None（ゴールデンへフォールバック）。"""
    db = ROOT / "data" / "serina_memory.db"
    if not db.exists():
        return None
    try:
        from serina.core.memory.session_store import SessionStore

        store = SessionStore(db)
        previews = store.list_session_previews(limit=8)
        texts: list[str] = []
        for preview in previews:
            sid = preview.get("id")
            if not sid:
                continue
            hist = store.get_session_history(sid) or store.get_archived_history(sid) or []
            for row in hist:
                if row.get("role") == "assistant" and row.get("content"):
                    texts.append(str(row["content"]))
        if not texts:
            return None
        return texts[-limit:]
    except Exception:  # noqa: BLE001 — eval は本番DB不調で落とさない
        return None


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
