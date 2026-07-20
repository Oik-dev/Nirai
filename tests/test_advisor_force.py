"""事実レーン（advisor_force）の規則テスト。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.routing.advisor_force import (
    FACT_LANE_HOLD_REPLY,
    plan_forced_advisor,
)


def test_weather_with_freshness_forces_web_search() -> None:
    plan = plan_forced_advisor("ねえ、今日の東京の天気、ネットで検索して教えてくれる？")
    assert plan is not None
    assert plan.tool == "web_search"
    assert "天気" in plan.query


def test_weather_question_without_explicit_search_still_forces() -> None:
    plan = plan_forced_advisor("明日の天気教えて")
    assert plan is not None
    assert plan.tool == "web_search"


def test_casual_today_does_not_force() -> None:
    assert plan_forced_advisor("おはよう、調子はどう？") is None
    assert plan_forced_advisor("今日も一緒にいよう") is None
    assert plan_forced_advisor("ねえ、最近ちょっと仕事で悩んでてさ。") is None


def test_code_plus_advisor_forces_code_qa() -> None:
    plan = plan_forced_advisor(
        "Pythonのrequestsライブラリで、SSL証明書エラーが出てAPIに繋がらないんだけど、"
        "原因と直し方をアドバイザーに聞いて調べてくれる？"
    )
    assert plan is not None
    assert plan.tool == "code_qa"


def test_hold_reply_is_short_and_non_factual() -> None:
    assert "度" not in FACT_LANE_HOLD_REPLY
    assert "晴れ" not in FACT_LANE_HOLD_REPLY
    assert len(FACT_LANE_HOLD_REPLY) < 40
