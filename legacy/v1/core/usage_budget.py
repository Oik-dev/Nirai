"""Generic provider usage/quota snapshots for Resident task routing."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math
import time
from typing import Any, Mapping, Protocol

USAGE_STATUS_AVAILABLE = "available"
USAGE_STATUS_LIMITED = "limited"
USAGE_STATUS_UNKNOWN = "unknown"
USAGE_FETCH_TIMEOUT_SECONDS = 15.0
_USAGE_STATUSES = frozenset({USAGE_STATUS_AVAILABLE, USAGE_STATUS_LIMITED, USAGE_STATUS_UNKNOWN})


class UsageBudgetFetchError(RuntimeError):
    pass


@dataclass(frozen=True)
class UsageWindowSnapshot:
    id: str
    type: str
    duration_seconds: int | None = None
    used_percent: float | None = None
    remaining_percent: float | None = None
    used_amount: float | None = None
    limit_amount: float | None = None
    unit: str | None = None
    reset_at: datetime | None = None
    limit_reached: bool = False

    def to_protocol(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now() if now is None else _as_utc(now)
        reset_in_seconds = None
        if self.reset_at is not None:
            reset_in_seconds = max(0, int((_as_utc(self.reset_at) - current).total_seconds()))
        return {
            "id": self.id,
            "type": self.type,
            "duration_seconds": self.duration_seconds,
            "used_percent": self.used_percent,
            "remaining_percent": self.remaining_percent,
            "used_amount": self.used_amount,
            "limit_amount": self.limit_amount,
            "unit": self.unit,
            "reset_at": _iso_utc(self.reset_at),
            "reset_in_seconds": reset_in_seconds,
            "limit_reached": self.limit_reached,
        }


@dataclass(frozen=True)
class UsageBudgetSnapshot:
    provider: str
    status: str
    fetched_at: datetime
    source: str
    stale: bool
    windows: tuple[UsageWindowSnapshot, ...]
    profile: str | None = None
    last_error: str | None = None

    def __post_init__(self) -> None:
        if self.status not in _USAGE_STATUSES:
            raise ValueError(f"Invalid Usage Budget status: {self.status}")

    def to_protocol(self, *, now: datetime | None = None) -> dict[str, Any]:
        current = _utc_now() if now is None else _as_utc(now)
        return {
            "provider": self.provider,
            "profile": self.profile,
            "status": self.status,
            "fetched_at": _iso_utc(self.fetched_at),
            "source": self.source,
            "stale": self.stale,
            "last_error": self.last_error,
            "windows": [window.to_protocol(now=current) for window in self.windows],
        }


class UsageBudgetProvider(Protocol):
    provider: str

    async def fetch(self) -> UsageBudgetSnapshot: ...


class UsageBudgetService:
    def __init__(self, providers: Mapping[str, UsageBudgetProvider] | None = None, *, refresh_interval_seconds: float = 300.0) -> None:
        if refresh_interval_seconds <= 0:
            raise ValueError("refresh_interval_seconds must be positive")
        self._providers = dict(providers or {})
        self._refresh_interval_seconds = float(refresh_interval_seconds)
        self._snapshots: dict[str, UsageBudgetSnapshot] = {}
        self._fetched_monotonic: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def register(self, provider: UsageBudgetProvider) -> None:
        self._providers[provider.provider] = provider

    def snapshot(self, provider: str) -> UsageBudgetSnapshot | None:
        return self._snapshots.get(provider)

    def snapshots(self) -> tuple[UsageBudgetSnapshot, ...]:
        return tuple(self._snapshots[key] for key in sorted(self._snapshots))

    async def refresh_many(
        self,
        providers: tuple[str, ...] | list[str] | set[str],
        *,
        force: bool = False,
    ) -> dict[str, UsageBudgetSnapshot]:
        ordered = tuple(dict.fromkeys(providers))
        results = await asyncio.gather(
            *(self.refresh(provider, force=force) for provider in ordered)
        )
        return dict(zip(ordered, results, strict=True))

    async def refresh(self, provider: str, *, force: bool = False) -> UsageBudgetSnapshot:
        adapter = self._providers.get(provider)
        if adapter is None:
            return self._unknown(provider, "unsupported", "Usage monitor is not configured")
        lock = self._locks.setdefault(provider, asyncio.Lock())
        async with lock:
            if not force:
                last = self._fetched_monotonic.get(provider)
                cached = self._snapshots.get(provider)
                if last is not None and cached is not None and time.monotonic() - last < self._refresh_interval_seconds:
                    return cached
            try:
                snapshot = await asyncio.wait_for(adapter.fetch(), timeout=USAGE_FETCH_TIMEOUT_SECONDS)
                if snapshot.provider != provider:
                    raise UsageBudgetFetchError("Usage provider returned a mismatched provider id")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                safe_error = _safe_error_text(exc)
                previous = self._snapshots.get(provider)
                snapshot = (
                    replace(previous, status=USAGE_STATUS_UNKNOWN, stale=True, last_error=safe_error)
                    if previous is not None
                    else self._unknown(provider, "fetch_failed", safe_error)
                )
            self._snapshots[provider] = snapshot
            self._fetched_monotonic[provider] = time.monotonic()
            return snapshot

    @staticmethod
    def _unknown(provider: str, source: str, error: str) -> UsageBudgetSnapshot:
        return UsageBudgetSnapshot(provider, USAGE_STATUS_UNKNOWN, _utc_now(), source, True, (), last_error=error)


def parse_codex_rate_limits(payload: Mapping[str, Any], *, fetched_at: datetime | None = None) -> UsageBudgetSnapshot:
    selected: Mapping[str, Any] | None = None
    by_id = payload.get("rateLimitsByLimitId")
    if isinstance(by_id, Mapping) and isinstance(by_id.get("codex"), Mapping):
        selected = by_id["codex"]
    elif isinstance(payload.get("rateLimits"), Mapping):
        selected = payload["rateLimits"]
    if selected is None:
        raise UsageBudgetFetchError("Codex rate-limit response did not contain rateLimits")
    reached_type = selected.get("rateLimitReachedType")
    reached_text = reached_type.casefold() if isinstance(reached_type, str) else ""
    windows: list[UsageWindowSnapshot] = []
    for position, native_id in enumerate(("primary", "secondary")):
        raw = selected.get(native_id)
        if not isinstance(raw, Mapping):
            continue
        duration_minutes = _optional_positive_int(raw.get("windowDurationMins"))
        used = _optional_percent(raw.get("usedPercent"))
        reset_at = _datetime_from_epoch(raw.get("resetsAt"))
        window_id, window_type = _codex_window_identity(duration_minutes, native_id, position)
        windows.append(UsageWindowSnapshot(
            id=window_id,
            type=window_type,
            duration_seconds=duration_minutes * 60 if duration_minutes is not None else None,
            used_percent=used,
            remaining_percent=None if used is None else round(100.0 - used, 6),
            reset_at=reset_at,
            limit_reached=reached_text in {native_id, window_id, window_type} or (used is not None and used >= 100.0),
        ))
    hard_limited = any(window.limit_reached for window in windows)
    if reached_text and reached_text not in {"none", "null"}:
        hard_limited = True
    if not hard_limited and not any(window.used_percent is not None for window in windows):
        raise UsageBudgetFetchError("Codex rate-limit response did not contain usage data")
    return UsageBudgetSnapshot(
        provider="codex",
        profile=_optional_text(selected.get("planType")),
        status=USAGE_STATUS_LIMITED if hard_limited else USAGE_STATUS_AVAILABLE,
        fetched_at=_utc_now() if fetched_at is None else _as_utc(fetched_at),
        source="codex_app_server",
        stale=False,
        windows=tuple(windows),
    )


def parse_cursor_usage_summary(payload: Mapping[str, Any], *, fetched_at: datetime | None = None) -> UsageBudgetSnapshot:
    individual = payload.get("individualUsage")
    plan = individual.get("plan") if isinstance(individual, Mapping) else None
    if not isinstance(plan, Mapping):
        raise UsageBudgetFetchError("Cursor usage response did not contain individualUsage.plan")
    reset_at = _datetime_from_iso(payload.get("billingCycleEnd"))
    windows: list[UsageWindowSnapshot] = []
    for window_id, raw_used in (
        ("cursor_models", plan.get("autoPercentUsed")),
        ("other_models", plan.get("apiPercentUsed")),
    ):
        used = _optional_percent(raw_used)
        if used is None:
            continue
        windows.append(UsageWindowSnapshot(
            id=window_id,
            type=window_id,
            used_percent=used,
            remaining_percent=round(100.0 - used, 6),
            reset_at=reset_at,
            limit_reached=used >= 100.0,
        ))
    if not windows:
        used = _optional_percent(plan.get("totalPercentUsed"))
        if used is None:
            raise UsageBudgetFetchError("Cursor usage response did not contain percentage usage")
        windows.append(UsageWindowSnapshot(
            id="monthly",
            type="billing_cycle",
            used_percent=used,
            remaining_percent=round(100.0 - used, 6),
            reset_at=reset_at,
            limit_reached=used >= 100.0,
        ))
    return UsageBudgetSnapshot(
        provider="cursor",
        profile=_optional_text(plan.get("name") or payload.get("planType")),
        status=(
            USAGE_STATUS_LIMITED
            if all(window.limit_reached for window in windows)
            and (len(windows) == 2 or windows[0].id == "monthly")
            else USAGE_STATUS_AVAILABLE
        ),
        fetched_at=_utc_now() if fetched_at is None else _as_utc(fetched_at),
        source="cursor_usage_summary",
        stale=False,
        windows=tuple(windows),
    )


def cursor_model_pool(model: str | None) -> str | None:
    if not isinstance(model, str) or not model.strip():
        return None
    folded = model.strip().casefold()
    if folded.startswith(("cursor-grok-", "grok-4.5", "grok-4.6", "composer-")):
        return "cursor_models"
    if folded in {"auto", "cursor-auto"}:
        return None
    return "other_models"


def routing_windows_for_model(snapshot: UsageBudgetSnapshot, *, model: str | None) -> tuple[UsageWindowSnapshot, ...]:
    if snapshot.provider != "cursor":
        return snapshot.windows
    pool = cursor_model_pool(model)
    if pool is None:
        return snapshot.windows
    matched = tuple(window for window in snapshot.windows if window.id == pool)
    # An absent model pool is unknown, not interchangeable with another pool.
    # Only the legacy aggregate monthly window applies to every Cursor model.
    return matched or tuple(window for window in snapshot.windows if window.id == "monthly")


def usage_is_hard_limited(snapshot: UsageBudgetSnapshot | None, *, model: str | None = None) -> bool:
    if snapshot is None or snapshot.stale or snapshot.status == USAGE_STATUS_UNKNOWN:
        return False
    if snapshot.provider != "cursor" and snapshot.status == USAGE_STATUS_LIMITED:
        return True
    windows = routing_windows_for_model(snapshot, model=model)
    if not windows:
        return snapshot.status == USAGE_STATUS_LIMITED
    if snapshot.provider == "cursor" and cursor_model_pool(model) is None:
        # Auto/unknown Cursor models may draw from more than one independent
        # pool. Treat them as hard-limited only when the provider snapshot says
        # every usable pool is exhausted; do not block because one pool is full.
        return snapshot.status == USAGE_STATUS_LIMITED
    # Stacked windows (for example Codex 5h + weekly) constrain the same work.
    # Exhausting either window makes the provider unable to start more work.
    return any(window.limit_reached for window in windows)


def _codex_window_identity(duration_minutes: int | None, native_id: str, position: int) -> tuple[str, str]:
    if duration_minutes == 300:
        return "5h", "rolling_5h"
    if duration_minutes == 10_080:
        return "7d", "weekly"
    if duration_minutes is not None:
        return f"{duration_minutes}m", "rolling"
    return native_id or f"window_{position + 1}", "rolling"


def _optional_percent(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = float(value)
    if not math.isfinite(numeric):
        return None
    return round(min(100.0, max(0.0, numeric)), 6)


def _optional_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    numeric = int(value)
    return numeric if numeric > 0 else None


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _datetime_from_epoch(value: object) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _datetime_from_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _safe_error_text(exc: Exception) -> str:
    if isinstance(exc, UsageBudgetFetchError):
        text = str(exc).strip()
        return text[:300] if text else "Usage monitor failed"
    return f"{type(exc).__name__}: usage monitor failed"
