"""アドバイザー道具実行（Core 関所専用）。Wave 7 C4 / GO §3.3。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from serina.core.state.routing_rules import RoutingRules
from serina.skills.gemini_advisor.skill import GeminiAdvisorSkill

logger = logging.getLogger(__name__)

ADVISOR_TOOL_TYPES = frozenset({
    "advisor_consult",
    "web_search",
    "code_qa",
})

MAX_ADVISOR_CALLS = 3

# 1ターンの外聞き合計時間予算（秒）。1件あたりのタイムアウト（Antigravity 180s）が
# 複数件で積み上がり、turn_lock を長時間塞ぐのを防ぐ。超過後の相談は捨てて会話を返す。
# 実値は thresholds.toml [advisor].turn_budget_seconds（Core が引数で渡す）。
DEFAULT_ADVISOR_TURN_BUDGET_SECONDS = 180.0


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
    turn_budget_seconds: float = DEFAULT_ADVISOR_TURN_BUDGET_SECONDS,
    clock: Callable[[], float] | None = None,
) -> AdvisorToolOutcome:
    """Core 関所で Gemini アドバイザーを呼ぶ。Brain / Skill から DB 直触り経路は作らない。

    2件目以降は経過時間が turn_budget_seconds 未満のときだけ開始する
    （実行中の1件は中断しないため、最悪 turn_budget + 1件分タイムアウトまで）。
    破棄はすべて理由付きで discarded に残し、ログにも出す（沈黙失敗の禁止）。
    """
    outcome = AdvisorToolOutcome()
    if not calls:
        return outcome
    if skill is None or not skill.enabled:
        outcome.skipped_disabled = True
        outcome.discarded.append({"reason": "gemini_advisor が無効（APIキー無し等）"})
        logger.info("外聞き: gemini_advisor が無効のため %d 件を見送り", len(calls))
        return outcome

    now_fn = clock or time.monotonic
    started_at = now_fn()
    attempted = False
    for call in calls:
        tool_type = call.get("type") or call.get("tool")
        query = call.get("query") or call.get("q") or ""
        if not isinstance(query, str) or not query.strip():
            outcome.discarded.append({"tool": tool_type, "reason": "query が空"})
            continue
        elapsed = now_fn() - started_at
        if attempted and turn_budget_seconds > 0 and elapsed >= turn_budget_seconds:
            reason = f"外聞きの時間予算（{turn_budget_seconds:.0f}s）超過のため見送り"
            outcome.discarded.append({
                "tool": tool_type, "query": query.strip(), "reason": reason,
            })
            logger.warning("外聞き見送り: %s（query=%s）", reason, query.strip()[:40])
            continue
        category = _category_for_call(call)
        try:
            attempted = True
            answer = skill.consult(
                query.strip(),
                category=category,
                routing_rules=routing_rules,
            )
            if answer is None:
                reason = (
                    getattr(skill, "last_failure_reason", None)
                    or "相談失敗または機微フィルタで拒否"
                )
                outcome.discarded.append({
                    "tool": tool_type,
                    "query": query.strip(),
                    "reason": reason,
                })
                logger.warning("外聞き失敗: %s（query=%s）", reason, query.strip()[:40])
                continue
            outcome.executed.append({
                "tool": tool_type,
                "query": query.strip(),
                "category": category,
                "answer": answer,
            })
        except Exception as exc:  # noqa: BLE001
            outcome.discarded.append({"tool": tool_type, "reason": str(exc), "call": call})
            logger.warning("外聞き例外: %s（query=%s）", exc, query.strip()[:40])
    return outcome
