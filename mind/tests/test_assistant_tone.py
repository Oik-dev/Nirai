"""アシスタント化率のパターン照合テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
ROOT = TESTS.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))
if str(TESTS) not in sys.path:
    sys.path.insert(0, str(TESTS))

from eval_assistant_tone import (
    is_assistant_tone,
    run_assistant_tone_eval,
)


def test_partner_tone_is_clean() -> None:
    assert not is_assistant_tone("おかえり。今日はどんな感じ？")


def test_customer_service_tone_is_flagged() -> None:
    assert is_assistant_tone("承知いたしました。何かお困りですか？")
    assert is_assistant_tone("How can I help you today?")


def test_golden_samples_pass_threshold() -> None:
    result = run_assistant_tone_eval(max_rate=0.2)
    assert result.error is None
    assert result.ok
    assert result.rate <= 0.2


def test_high_assistant_rate_fails() -> None:
    replies = [
        "承知いたしました。ご案内します。",
        "お気軽にお問い合わせください。",
        "ご確認ください。",
    ]
    result = run_assistant_tone_eval(max_rate=0.2, replies=replies)
    assert not result.ok
    assert result.rate == 1.0
