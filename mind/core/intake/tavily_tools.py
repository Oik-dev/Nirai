"""Tavily検索の薄い入口。外への印の判定はCore側で先に済ませる。"""

from __future__ import annotations

from dataclasses import dataclass

from mind.skills.tavily_search.skill import TavilyResult, TavilySearchSkill


@dataclass(frozen=True)
class TavilySearchOutcome:
    result: TavilyResult | None = None
    executed: bool = False
    reason: str = ''


def execute_tavily_search(query: str, skill: TavilySearchSkill | None) -> TavilySearchOutcome:
    if skill is None or not skill.enabled:
        return TavilySearchOutcome(reason='Tavily未接続')
    if not query.strip():
        return TavilySearchOutcome(reason='空の検索文')
    try:
        result = skill.search(query)
    except Exception as exc:
        return TavilySearchOutcome(reason=type(exc).__name__)
    return (TavilySearchOutcome(result=result, executed=True) if result is not None
            else TavilySearchOutcome(reason=skill.last_failure_reason or '検索失敗'))
