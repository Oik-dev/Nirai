"""家の印と通訳の可用性による候補の順序。無料APIの回数は数えない。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from mind.core.routing.registry import BrainEntry


def decide_brains(*, registry: list[BrainEntry], now: datetime,
                  is_alive: Callable[[str], bool] | None = None,
                  topic_home: bool = True) -> list[str]:
    is_alive = is_alive or (lambda _: True)
    local = next((entry for entry in registry if entry.location == "local"), None)
    if local is None:
        raise ValueError("local brain is required")
    if topic_home:
        return [local.name]
    return [entry.name for entry in registry if entry.location == "cloud"
            and is_alive(entry.name)] + [local.name]


def decide_brain(**kwargs) -> str:
    """活動など、1回だけ呼び出す場面の選択。"""
    return decide_brains(**kwargs)[0]
