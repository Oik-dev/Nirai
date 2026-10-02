"""毎ターンのBrain選択。設計書 §3.2 / §9（Brain単一運用（ローカルOllama））。

会話をクラウド Brain へ振る経路は退役済み。残るのは primary の残弾・生死と、
全滅時の fallback（登録が無ければ primary 自身）のみ。

外への相談（天気・コード）は Brain 切替ではなく Gemini アドバイザー Skill（§5.6）。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from serina.core.routing.quota_ledger import QuotaLedger
from serina.core.routing.registry import BrainEntry
from serina.core.state.routing_rules import RoutingRules


def decide_brain(
    *,
    registry: list[BrainEntry],
    quota_ledger: QuotaLedger,
    routing_rules: RoutingRules,
    master_utterance: str,
    now: datetime,
    switch_requested: bool = False,
    escalate_requested: bool = False,
    night_release: bool = False,
    is_alive: Callable[[str], bool] | None = None,
) -> str:
    # 以下は互換シグネチャ用。会話クラウド振り分けは使わない。
    _ = (routing_rules, master_utterance, switch_requested, escalate_requested, night_release)

    is_alive = is_alive or (lambda _name: True)
    by_role = {entry.role: entry for entry in registry}
    primary = by_role["primary"]
    fallback = by_role.get("fallback", primary)

    if quota_ledger.can_use(
        primary.name,
        daily_quota=primary.daily_quota,
        per_minute_quota=primary.per_minute_quota,
        now=now,
    ) and is_alive(primary.name):
        return primary.name

    # 最終防衛線: セリナは沈黙しない
    return fallback.name
