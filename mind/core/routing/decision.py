"""家の印に従う二つの脳の選択。未判定・家の話は手元の脳へ。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from mind.core.routing.quota_ledger import QuotaLedger
from mind.core.routing.registry import BrainEntry


def decide_brain(
    *,
    registry: list[BrainEntry],
    quota_ledger: QuotaLedger,
    now: datetime,
    is_alive: Callable[[str], bool] | None = None,
    topic_home: bool = True,
) -> str:
    is_alive = is_alive or (lambda _name: True)
    by_role = {entry.role: entry for entry in registry}
    primary = by_role["primary"]
    fallback = next((entry for entry in registry if entry.location == 'local'), primary)

    if topic_home:
        return fallback.name

    cloud = next((entry for entry in registry if entry.location == 'cloud'), None)
    if cloud is not None and quota_ledger.can_use(
        cloud.name,
        daily_quota=cloud.daily_quota,
        per_minute_quota=cloud.per_minute_quota,
        now=now,
    ) and is_alive(cloud.name):
        return cloud.name

    # 最終防衛線: セリナは沈黙しない
    return fallback.name
