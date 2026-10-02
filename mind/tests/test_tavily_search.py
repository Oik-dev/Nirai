"""Tavily Skill（skills/tavily_search）と Core側関所（execute_tavily_search）のテスト。

Skill単体テスト（TavilySearchSkill.search）は query と routing_rules のみを渡す
（master_utterance は登場しない＝Skill層のシグネチャに原発話を持たせない設計の確認）。
execute_tavily_search（Core側）は master_utterance と query の両方を検査し、
機微ならSkillを一切呼ばないことをモックでアサートする（実装前レビュー指摘の受け入れ基準）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT.parent) not in sys.path:
    sys.path.insert(0, str(ROOT.parent))

from serina.core.intake.advisor_tools import execute_tavily_search
from serina.core.state.routing_rules import RoutingRules
from serina.skills.tavily_search.skill import TavilyResult, TavilySearchSkill


# ---------------------------------------------------------------------------
# TavilySearchSkill.search() 単体テスト
# ---------------------------------------------------------------------------


def test_disabled_without_key_or_call_fn() -> None:
    skill = TavilySearchSkill()
    assert not skill.enabled
    result = skill.search("東京 天気", routing_rules=RoutingRules())
    assert result is None
    assert skill.last_failure_reason


def test_call_fn_exception_returns_none() -> None:
    def boom(query: str) -> dict:
        raise RuntimeError("network down")

    skill = TavilySearchSkill(call_fn=boom)
    result = skill.search("東京 天気", routing_rules=RoutingRules())
    assert result is None
    assert "network down" in (skill.last_failure_reason or "")


def test_success_result_structure() -> None:
    def fake_call(query: str) -> dict:
        return {
            "answer": "東京は明日晴れです。",
            "results": [
                {"title": "東京の天気予報", "url": "https://example.com/weather", "content": "晴れ"},
            ],
        }

    skill = TavilySearchSkill(call_fn=fake_call)
    result = skill.search("東京 明日 天気", routing_rules=RoutingRules())
    assert isinstance(result, TavilyResult)
    assert result.answer == "東京は明日晴れです。"
    assert result.results == [
        {"title": "東京の天気予報", "url": "https://example.com/weather", "snippet": "晴れ"},
    ]


def test_routing_rules_none_is_default_deny() -> None:
    """fail-closed: routing_rules が None（未接続）なら送信しない。"""
    calls: list[str] = []

    def fake_call(query: str) -> dict:
        calls.append(query)
        return {"answer": "x", "results": []}

    skill = TavilySearchSkill(call_fn=fake_call)
    result = skill.search("東京 天気", routing_rules=None)
    assert result is None
    assert not calls
    assert "門番" in (skill.last_failure_reason or "")


def test_sensitive_query_is_not_sent() -> None:
    calls: list[str] = []

    def fake_call(query: str) -> dict:
        calls.append(query)
        return {"answer": "x", "results": []}

    rules = RoutingRules()
    rules.tighten("09012345678")
    skill = TavilySearchSkill(call_fn=fake_call)
    result = skill.search("09012345678の持ち主", routing_rules=rules)
    assert result is None
    assert not calls
    assert "機微" in (skill.last_failure_reason or "")


# ---------------------------------------------------------------------------
# execute_tavily_search（Core側）テスト
# ---------------------------------------------------------------------------


def test_execute_returns_no_result_when_skill_disabled() -> None:
    outcome = execute_tavily_search(
        "宮古島の方言ってどういう意味？", "宮古島 方言 意味",
        TavilySearchSkill(), routing_rules=RoutingRules(),
    )
    assert not outcome.executed
    assert outcome.result is None
    assert outcome.reason


def test_execute_fail_closed_when_routing_rules_missing() -> None:
    skill = TavilySearchSkill(call_fn=lambda q: {"answer": "x", "results": []})
    outcome = execute_tavily_search(
        "宮古島の方言ってどういう意味？", "宮古島 方言 意味", skill, routing_rules=None,
    )
    assert not outcome.executed
    assert "門番" in (outcome.reason or "")


def test_execute_rejects_when_master_utterance_is_sensitive_but_query_is_clean() -> None:
    """原発話（master_utterance）に機微語が含まれるが、言い換え後のクエリは無害なケースでも
    拒否され、かつ skill.search() 自体が一切呼ばれないことを確認する
    （Phase C 注意2の受け入れ基準。判定主体は Core、Skill 層へ原発話を渡さない設計）。
    """
    rules = RoutingRules()
    rules.tighten("09012345678")
    skill = Mock(spec=TavilySearchSkill)
    skill.enabled = True

    outcome = execute_tavily_search(
        "09012345678に電話してほしいんだけど、まず一般的な電話対応マナーを調べて",
        "電話対応 マナー",  # 言い換え後は無害
        skill,
        routing_rules=rules,
    )

    assert not outcome.executed
    assert outcome.result is None
    assert "機微" in (outcome.reason or "")
    skill.search.assert_not_called()


def test_execute_rejects_when_query_itself_is_sensitive() -> None:
    rules = RoutingRules()
    rules.tighten("09012345678")
    skill = Mock(spec=TavilySearchSkill)
    skill.enabled = True

    outcome = execute_tavily_search(
        "この番号について調べて", "09012345678 持ち主", skill, routing_rules=rules,
    )

    assert not outcome.executed
    skill.search.assert_not_called()


def test_execute_success_path_calls_skill_with_query_and_routing_rules() -> None:
    rules = RoutingRules()
    fake_result = TavilyResult(answer="晴れ", results=[])
    skill = Mock(spec=TavilySearchSkill)
    skill.enabled = True
    skill.search.return_value = fake_result

    outcome = execute_tavily_search(
        "明日の東京の天気教えて", "東京 明日 天気", skill, routing_rules=rules,
    )

    assert outcome.executed
    assert outcome.result is fake_result
    skill.search.assert_called_once_with("東京 明日 天気", routing_rules=rules)


def test_execute_reports_skill_failure_reason() -> None:
    rules = RoutingRules()
    skill = Mock(spec=TavilySearchSkill)
    skill.enabled = True
    skill.search.return_value = None
    skill.last_failure_reason = "TimeoutError: timed out"

    outcome = execute_tavily_search(
        "明日の東京の天気教えて", "東京 明日 天気", skill, routing_rules=rules,
    )

    assert not outcome.executed
    assert outcome.reason == "TimeoutError: timed out"
