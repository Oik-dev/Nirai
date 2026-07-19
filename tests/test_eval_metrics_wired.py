"""決定論 eval 指標の配線スモーク（Ollama不要）。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TESTS = Path(__file__).resolve().parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from eval_correction import run_correction_eval
from eval_growth import run_growth_eval
from eval_time_query import run_time_query_eval


def test_time_query_eval_passes() -> None:
    result = run_time_query_eval(quiet=True)
    assert not result.error
    assert result.ok
    assert result.accuracy >= 0.8


def test_correction_eval_passes() -> None:
    result = run_correction_eval(quiet=True)
    assert not result.error
    assert result.ok
    assert result.recurrence_rate <= 0.15


def test_growth_eval_passes() -> None:
    result = run_growth_eval(quiet=True)
    assert not result.error
    assert result.ok
    assert result.reflection_rate >= 0.5
