"""think ON/OFF ルール先行判定のテスト（§3.1改訂・2026-07-20 応答高速化）。

規則で確信できる発話は LLM(judge) を呼ばず即決し、
中間帯だけ judge へ相談する。既定は false（速度優先）。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.think_rules import plan_think


def test_casual_utterance_is_immediate_false() -> None:
    """雑談・感情吐露はルールで即 false（judge 発注なし＝体感速度の主目的）。"""
    for utterance in ("おはよう", "今日は疲れたよ", "ゲーム楽しかった！"):
        decision = plan_think(utterance)
        assert decision.think is False, f"{utterance!r} は即 false であるべき: {decision}"


def test_explicit_deep_request_is_immediate_true() -> None:
    for utterance in ("じっくり考えて答えてほしい", "この命題を証明して", "論理パズル出すね"):
        decision = plan_think(utterance)
        assert decision.think is True, f"{utterance!r} は即 true であるべき: {decision}"


def test_daily_kangaeteru_is_not_a_deep_marker() -> None:
    """「〜と考えてる」等の日常表現を深考マーカーに巻き込まない（誤爆すると毎ターン遅くなる）。"""
    decision = plan_think("明日出かけようと考えてるんだ")
    assert decision.think is False


def test_math_expression_is_immediate_true() -> None:
    assert plan_think("128 * 47っていくつ？").think is True
    assert plan_think("12+34は？").think is True


def test_date_and_range_notation_is_not_math() -> None:
    """レビュー指摘（2026-07-20）: 日付・範囲表記の「-」を数式と誤検出して
    日常会話を無言で深考（低速）に倒さない。"""
    assert plan_think("2026-07-20の予定どう？").think is False
    assert plan_think("7-20時なら空いてるよ").think is False


def test_ambiguous_utterance_defers_to_judge() -> None:
    """中間帯マーカーは規則で白黒つけず judge へ委任（think=None）。"""
    for utterance in ("これってなんでこうなるんだろう", "どう思う？", "新作のアイデアほしいな"):
        decision = plan_think(utterance)
        assert decision.think is None, f"{utterance!r} は judge 委任であるべき: {decision}"


def test_long_utterance_defers_to_judge() -> None:
    decision = plan_think("あ" * 250)
    assert decision.think is None


def test_decision_carries_why() -> None:
    assert plan_think("おはよう").why
    assert plan_think("証明して").why
