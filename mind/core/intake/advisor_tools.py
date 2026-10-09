"""アドバイザー道具実行（Core 関所専用）。設計書 §5.6。"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from mind.core.state.routing_rules import RoutingRules
from mind.skills.gemini_advisor.skill import GeminiAdvisorSkill
from mind.skills.tavily_search.skill import TavilyResult, TavilySearchSkill

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


@dataclass
class TavilySearchOutcome:
    """execute_tavily_search の結果。executed=False のとき reason に必ず理由が入る。

    `GeminiAdvisorSkill.last_failure_reason`（呼び出し先オブジェクトの属性）とは違い、
    本関数は Skill を呼ぶ前に Core 側で拒否するケース（機微判定・門番未接続）を持つため、
    Skill 側の属性を借りずに独立した戻り値として理由を持たせる
    （Skill 呼び出し前に拒否した場合、Skill の属性は今回の呼び出しについて更新されず
    古い値が残るため、そこから理由を読むと誤った理由を報告しかねない）。
    """

    result: TavilyResult | None = None
    executed: bool = False
    reason: str | None = None


def execute_tavily_search(
    master_utterance: str,
    query: str,
    skill: TavilySearchSkill | None,
    *,
    routing_rules: RoutingRules | None = None,
) -> TavilySearchOutcome:
    """Core 関所で Tavily を呼ぶ。Brain / Skill から DB 直触り経路は作らない。

    **機微の関所（主体はここ・Core側）**: `master_utterance`（マスターの原発話）と
    `query`（Phase B の判定が生成した送信クエリ）の**両方**を、Skill を呼び出す**前**に
    `routing_rules.is_sensitive()` へ通す。いずれかが機微に触れる、または
    `routing_rules` が None（未接続）の場合は **`skill.search()` 自体を呼ばない**
    （fail-closed。機微を含みうる原発話を Skill 層のシグネチャへ一切渡さない設計。
    実装前レビュー指摘で確定した — 渡した後に道具側が自制する設計は、渡した後の
    自制に依存する点で安全側ではないため採らない）。

    Phase B の判定出力（言い換えクエリ）はあくまで提案（A-3）であり、外へ出してよいかの
    最終判断は本関数（Core）が原発話に対して下す。`skill.search()` 内の関所は
    Core の判断漏れに備えた従属的な最終防御網に過ぎない。
    """
    if skill is None or not skill.enabled:
        logger.info("Tavily検索: skillが無効のため見送り")
        return TavilySearchOutcome(reason="Tavily無効（APIキー無し等）")
    if routing_rules is None:
        logger.info("Tavily検索: routing_rules未接続のため見送り（fail-closed）")
        return TavilySearchOutcome(reason="門番（routing_rules）未接続のため送信拒否")
    if routing_rules.is_sensitive(master_utterance) or routing_rules.is_sensitive(query):
        logger.warning("Tavily検索: 機微判定で見送り（原発話または送信クエリのいずれかに該当）")
        return TavilySearchOutcome(reason="機微フィルタで拒否（原発話または送信クエリ）")

    result = skill.search(query, routing_rules=routing_rules)
    if result is None:
        reason = getattr(skill, "last_failure_reason", None) or "Tavily検索失敗"
        logger.warning("Tavily検索失敗: %s", reason)
        return TavilySearchOutcome(reason=reason)
    return TavilySearchOutcome(result=result, executed=True)
