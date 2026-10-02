"""Gemini（お友達枠）呼びかけトリガー（advisor_force）の規則テスト。

2026-07-31 改訂: 鮮度・事実ドメイン・明示検索マーカーによる自動発火は全廃。
「Gemini」呼びかけの明示のみで発火することを確認する。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from mind.core.routing.advisor_force import plan_forced_advisor


def test_gemini_call_word_forces_web_search() -> None:
    plan = plan_forced_advisor("ねえGemini、今日の東京の天気教えてくれる？")
    assert plan is not None
    assert plan.tool == "web_search"
    assert "天気" in plan.query


def test_gemini_call_word_japanese_variant_forces() -> None:
    plan = plan_forced_advisor("ジェミニに聞いてみたいことがあるんだけど")
    assert plan is not None
    assert plan.tool == "web_search"


def test_weather_question_without_gemini_call_does_not_force() -> None:
    """旧: 鮮度＋事実ドメインで自動発火していたが、合言葉ゼロの自動発火は全廃済み。"""
    assert plan_forced_advisor("明日の天気教えて") is None
    assert plan_forced_advisor("今日の東京の天気、ネットで検索して教えてくれる？") is None


def test_casual_today_does_not_force() -> None:
    assert plan_forced_advisor("おはよう、調子はどう？") is None
    assert plan_forced_advisor("今日も一緒にいよう") is None
    assert plan_forced_advisor("ねえ、最近ちょっと仕事で悩んでてさ。") is None


def test_code_plus_gemini_call_forces_code_qa() -> None:
    plan = plan_forced_advisor(
        "Pythonのrequestsライブラリで、SSL証明書エラーが出てAPIに繋がらないんだけど、"
        "原因と直し方をGeminiに聞いて調べてくれる？"
    )
    assert plan is not None
    assert plan.tool == "code_qa"


def test_code_question_without_gemini_call_does_not_force() -> None:
    """旧: コードドメイン＋質問サインの単独発火は全廃済み。合言葉が要る。"""
    assert (
        plan_forced_advisor(
            "PythonのrequestsライブラリでSSL証明書エラーが出るんだけど、原因は何？"
        )
        is None
    )
