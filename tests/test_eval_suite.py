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
    assert "pulse_annoyance" in raw


def test_eval_suite_has_ten_metrics() -> None:
    assert len(METRIC_RUNNERS) == 10


def test_eval_suite_dry_run_no_fail() -> None:
    results = run_eval_suite()
    assert len(results) == 10
    assert all(r.status in ("skipped", "pass") for r in results)
