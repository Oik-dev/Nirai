"""eval_suite ハーネスのスモークテスト。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from eval_suite import METRIC_RUNNERS, load_eval_thresholds, run_eval_suite


def test_eval_thresholds_file_loads() -> None:
    raw = load_eval_thresholds()
    assert "recall_hit_rate" in raw
    assert "pulse_annoyance" not in raw


def test_eval_suite_has_nine_metrics() -> None:
    assert len(METRIC_RUNNERS) == 9


def test_eval_suite_dry_run_no_fail() -> None:
    results = run_eval_suite()
    assert len(results) == 9
    assert all(r.status in ("skipped", "pass") for r in results)
    assert all(r.name != "Pulse嫌悪" for r in results)


def test_eval_suite_dry_run_skips_live_metrics() -> None:
    results = {r.name: r for r in run_eval_suite(live=False)}
    assert results["想起ヒット率"].status == "skipped"
    assert results["誤想起率"].status == "skipped"
    assert results["継続性ヒット"].status == "skipped"
    assert results["応答体感秒数"].status == "skipped"
    assert "--live" in results["想起ヒット率"].detail


def test_eval_suite_dry_run_wires_deterministic_metrics() -> None:
    results = {r.name: r for r in run_eval_suite(live=False)}
    assert results["時間クエリ正答率"].status == "pass"
    assert results["成長反映率（構造ゲート）"].status == "pass"
    assert results["訂正再発率"].status == "pass"
    assert results["アシスタント化率"].status == "pass"
