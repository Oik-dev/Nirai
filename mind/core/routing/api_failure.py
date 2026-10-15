"""Google APIの拒否を入口で整え、同じ休みの規則を通訳と調べ役で使う。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.error import HTTPError, URLError
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Failure:
    reason: str  # daily / short / unusable / content
    status: int | None = None
    quota_id: str | None = None
    rest_seconds: int | None = None


def classify(exc: Exception, now: datetime | None = None) -> Failure:
    """Response bodyは一度だけ読み、ログにはmessageを一切残さない。"""
    now = now or datetime.now(timezone.utc)
    if not isinstance(exc, HTTPError):
        return Failure("short" if isinstance(exc, (TimeoutError, URLError, ConnectionError, OSError))
                       else "content")
    status = exc.code
    try:
        data = json.loads(exc.read().decode("utf-8"))
    except (ValueError, UnicodeError, OSError):
        data = {}
    error = data.get("error") if isinstance(data, dict) else {}
    if not isinstance(error, dict):
        error = {}
    details = error.get("details") if isinstance(error.get("details"), list) else []
    ids = [v.get("quotaId", "") for d in details if isinstance(d, dict)
           and "QuotaFailure" in d.get("@type", "")
           for v in d.get("violations", []) if isinstance(v, dict)]
    quota_id = next((v for v in ids if "PerDay" in v), next(iter(ids), None))
    if status == 429 and (any("PerDay" in v for v in ids)
                           or error.get("status") == "quota_exceeded"
                           or error.get("code") == "quota_exceeded"):
        pacific = now.astimezone(ZoneInfo("America/Los_Angeles"))
        following = (pacific + timedelta(days=1)).date()
        midnight = datetime(following.year, following.month, following.day,
                            tzinfo=ZoneInfo("America/Los_Angeles"))
        return Failure("daily", status, quota_id, max(1, int((midnight - now).total_seconds())))
    if status in (400, 401, 402, 403, 404):
        return Failure("unusable", status, quota_id)
    if status == 429 or status == 408 or status >= 500:
        delay = None
        for d in details:
            if isinstance(d, dict) and "RetryInfo" in d.get("@type", ""):
                raw = str(d.get("retryDelay", ""))
                match = re.fullmatch(r"(\d+(?:\.\d+)?)s", raw)
                if match:
                    delay = float(match.group(1))
                    break
        if delay is None:
            try:
                header = exc.headers.get("Retry-After")
                if header:
                    delay = (float(header) if header.isdecimal() else
                             (parsedate_to_datetime(header) - now).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
        return Failure("short", status, quota_id, max(1, min(600, int(delay or 60))))
    return Failure("content", status, quota_id)


class Rest:
    """プロセス内だけの休み。モデルの稼働状態はGoogleが正本。"""

    def __init__(self):
        self.rest_until: datetime | None = None
        self.unusable = False
        self.last_failure: Failure | None = None

    def ready(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return not self.unusable and (self.rest_until is None or now >= self.rest_until)

    def failed(self, exc: Exception, *, now: datetime | None = None) -> Failure:
        now = now or datetime.now(timezone.utc)
        failure = classify(exc, now)
        self.last_failure = failure
        if failure.reason == "unusable":
            self.unusable = True
        elif failure.reason in ("short", "daily"):
            self.rest_until = now + timedelta(seconds=failure.rest_seconds or 60)
        return failure
