"""毎ターンのBrain選択（決定論チェックリスト）。設計書 §3.2

① 交代要請チェック → ② プライバシー判定 → ③ 昇格判定 → ④ 残弾チェック → ⑤ 生死確認
→ 最終防衛線: fallback役（セリナは沈黙しない）

2026-07-18: Brain構成刷新（合意台帳 §9.1）でQwen単一運用へ。role制スキーマ（primary/
escalation/fallback）自体は将来の複数Brain運用再開に備えて維持するが、escalation/fallback
役が登録簿に存在しない構成を許容する（存在しなければfallback役は実質primary自身になる）。
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
    is_alive = is_alive or (lambda _name: True)
    by_role = {entry.role: entry for entry in registry}
    primary = by_role["primary"]
    fallback = by_role.get("fallback", primary)
    escalation = by_role.get("escalation")

    # ① 交代要請チェック: 前ターンの自己評価に従う
    if switch_requested:
        return fallback.name

    # ② プライバシー判定: センシティブ／個人情報の話題はローカル確定。以降のチェック省略
    if routing_rules.is_sensitive(master_utterance):
        return fallback.name

    # ③ 昇格判定: 自己申告 or 夜間放出なら上位モデルを優先候補に（escalation役が居ない
    # 構成では該当条件でもprimaryのみを候補にする＝実質no-op）
    if (escalate_requested or night_release) and escalation is not None:
        candidates = [escalation, primary]
    else:
        candidates = [primary]

    # ④ 残弾チェック ⑤ 生死確認
    for candidate in candidates:
        quota_ok = quota_ledger.can_use(
            candidate.name,
            daily_quota=candidate.daily_quota,
            per_minute_quota=candidate.per_minute_quota,
            now=now,
        )
        if quota_ok and is_alive(candidate.name):
            return candidate.name

    # 最終防衛線: セリナは沈黙しない
    return fallback.name
