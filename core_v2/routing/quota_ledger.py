"""残弾台帳。設計書v2 §2.6, §3.2④残弾チェック

Brainごとの日次残り回数・直近1分の使用数を管理する。日付が変われば日次分は自動リセットする。
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta

UNLIMITED = -1
_MINUTE_WINDOW = timedelta(seconds=60)


class QuotaLedger:
    def __init__(self) -> None:
        self._daily_used: dict[str, int] = defaultdict(int)
        self._daily_date: dict[str, object] = {}
        self._minute_uses: dict[str, deque[datetime]] = defaultdict(deque)

    def can_use(self, brain_name: str, *, daily_quota: int, per_minute_quota: int, now: datetime) -> bool:
        self._roll_day_if_needed(brain_name, now)
        if daily_quota != UNLIMITED and self._daily_used[brain_name] >= daily_quota:
            return False
        if per_minute_quota != UNLIMITED:
            self._evict_old_minute_uses(brain_name, now)
            if len(self._minute_uses[brain_name]) >= per_minute_quota:
                return False
        return True

    def record_use(self, brain_name: str, *, now: datetime) -> None:
        self._roll_day_if_needed(brain_name, now)
        self._daily_used[brain_name] += 1
        self._minute_uses[brain_name].append(now)

    def _roll_day_if_needed(self, brain_name: str, now: datetime) -> None:
        today = now.date()
        if self._daily_date.get(brain_name) != today:
            self._daily_date[brain_name] = today
            self._daily_used[brain_name] = 0

    def _evict_old_minute_uses(self, brain_name: str, now: datetime) -> None:
        window = self._minute_uses[brain_name]
        while window and (now - window[0]) >= _MINUTE_WINDOW:
            window.popleft()
