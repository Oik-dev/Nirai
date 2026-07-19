"""アドバイザー道具実行（Core 関所専用）。Wave 7 C4 / GO §3.3。"""

from __future__ import annotations

from dataclasses import dataclass, field

from serina.core.state.routing_rules import RoutingRules
from serina.skills.gemini_advisor.skill import GeminiAdvisorSkill

ADVISOR_TOOL_TYPES = frozenset({
    "advisor_consult",
    "web_search",
    "code_qa",
})

MAX_ADVISOR_CALLS = 3


@dataclass
class AdvisorToolOutcome:
    """アドバイザー実行結果（失敗は個別記録、会話継続）。"""

    executed: list[dict] = field(default_factory=list)
    discarded: list[dict] = field(default_factory=list)
    skipped_disabled: bool = False


def parse_advisor_tool_calls(raw_calls: object) -> tuple[list[dict], list[dict]]:
    """lenient 解析: 壊れた要素は捨て、会話は続行。"""
    if not isinstance(raw_calls, list):
        return [], [{"reason": "advisor_tool_calls が list でない"}]
    valid: list[dict] = []
    discarded: list[dict] = []
    for item in raw_calls[:MAX_ADVISOR_CALLS]:
        if not isinstance(item, dict):
            discarded.append({"reason": "要素が dict でない", "item": item})
            continue
        tool_type = item.get("type") or item.get("tool")
        if tool_type not in ADVISOR_TOOL_TYPES:
            discarded.append({"reason": "未知のツール種別", "item": item})
            continue
        valid.append(item)
    return valid, discarded


def advisor_calls_from_fusen(fusen_list: list[dict]) -> list[dict]:
    """付箋「道具使用」から gemini_advisor 相談を抽出する。"""
    calls: list[dict] = []
    for fusen in fusen_list:
        if not isinstance(fusen, dict):
            continue
        if fusen.get("kind") != "道具使用":
            continue
        content = fusen.get("content")
        if not isinstance(content, dict):
            continue
        tool_name = content.get("tool") or content.get("skill")
        if tool_name not in ("gemini_advisor", "advisor", None):
            continue
        query = content.get("query") or content.get("q") or ""
        if not isinstance(query, str) or not query.strip():
            continue
        category = content.get("category") or "general"
        calls.append({
            "type": "advisor_consult",
            "query": query.strip(),
            "category": category,
        })
    return calls[:MAX_ADVISOR_CALLS]


def _category_for_call(call: dict) -> str:
    tool_type = call.get("type") or call.get("tool")
    if tool_type == "web_search":
        return "web_search"
    if tool_type == "code_qa":
        return "code_qa"
    return str(call.get("category") or "general")


def execute_advisor_tool_calls(
    calls: list[dict],
    skill: GeminiAdvisorSkill | None,
    *,
    routing_rules: RoutingRules | None = None,
) -> AdvisorToolOutcome:
    """Core 関所で Gemini アドバイザーを呼ぶ。Brain / Skill から DB 直触り経路は作らない。"""
    outcome = AdvisorToolOutcome()
    if not calls:
        return outcome
    if skill is None or not skill.enabled:
        outcome.skipped_disabled = True
        outcome.discarded.append({"reason": "gemini_advisor が無効（APIキー無し等）"})
        return outcome

    for call in calls:
        tool_type = call.get("type") or call.get("tool")
        query = call.get("query") or call.get("q") or ""
        if not isinstance(query, str) or not query.strip():
            outcome.discarded.append({"tool": tool_type, "reason": "query が空"})
            continue
        category = _category_for_call(call)
        try:
            answer = skill.consult(
                query.strip(),
                category=category,
                routing_rules=routing_rules,
            )
            if answer is None:
                outcome.discarded.append({
                    "tool": tool_type,
                    "query": query.strip(),
                    "reason": "相談失敗または機微フィルタで拒否",
                })
                continue
            outcome.executed.append({
                "tool": tool_type,
                "query": query.strip(),
                "category": category,
                "answer": answer,
            })
        except Exception as exc:  # noqa: BLE001
            outcome.discarded.append({"tool": tool_type, "reason": str(exc), "call": call})
    return outcome
