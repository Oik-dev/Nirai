from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
from pathlib import Path
import threading
import time

import pytest

import core.usage_budget as usage_budget_module
import core.usage_providers as usage_providers_module

from core.agents import AgentWorkspacePolicy, CodexAppServerAdapter
from core.usage_budget import (
    UsageBudgetFetchError,
    UsageBudgetService,
    cursor_model_pool,
    parse_codex_rate_limits,
    parse_cursor_usage_summary,
    usage_is_hard_limited,
)
from core.usage_providers import CodexUsageProvider, CursorUsageProvider


def test_codex_parser_keeps_multiple_provider_windows_without_inventing_missing_windows() -> None:
    payload = {
        "rateLimits": {
            "limitId": "codex",
            "primary": {
                "usedPercent": 38.0,
                "windowDurationMins": 300,
                "resetsAt": 1_800_000_000,
            },
            "secondary": {
                "usedPercent": 70.0,
                "windowDurationMins": 10_080,
                "resetsAt": 1_800_604_800,
            },
            "rateLimitReachedType": None,
        }
    }

    snapshot = parse_codex_rate_limits(payload, fetched_at=datetime(2026, 9, 12, tzinfo=timezone.utc))

    assert snapshot.provider == "codex"
    assert snapshot.status == "available"
    assert [window.id for window in snapshot.windows] == ["5h", "7d"]
    assert snapshot.windows[0].used_percent == 38.0
    assert snapshot.windows[0].remaining_percent == 62.0
    assert snapshot.windows[1].used_percent == 70.0
    assert snapshot.windows[1].remaining_percent == 30.0
    assert snapshot.windows[0].reset_at is not None

    weekly_only = parse_codex_rate_limits({
        "rateLimits": {
            "limitId": "codex",
            "primary": {
                "usedPercent": 12.0,
                "windowDurationMins": 10_080,
                "resetsAt": 1_800_604_800,
            },
            "secondary": None,
        }
    })
    assert [window.id for window in weekly_only.windows] == ["7d"]


def test_codex_stacked_windows_are_hard_limited_when_either_window_is_exhausted() -> None:
    snapshot = parse_codex_rate_limits({
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
            "secondary": {"usedPercent": 70, "windowDurationMins": 10_080, "resetsAt": 1_800_604_800},
            "rateLimitReachedType": "primary",
        }
    })

    assert [window.limit_reached for window in snapshot.windows] == [True, False]
    assert usage_is_hard_limited(snapshot) is True


def test_codex_parser_prefers_codex_limit_id_when_map_is_available() -> None:
    payload = {
        "rateLimits": {"limitId": "other", "primary": None, "secondary": None},
        "rateLimitsByLimitId": {
            "codex": {
                "limitId": "codex",
                "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                "secondary": None,
                "rateLimitReachedType": "primary",
            }
        },
    }

    snapshot = parse_codex_rate_limits(payload)

    assert snapshot.status == "limited"
    assert len(snapshot.windows) == 1
    assert snapshot.windows[0].limit_reached is True


def test_cursor_parser_exposes_two_billing_cycle_pools_and_current_model_mapping() -> None:
    payload = {
        "billingCycleStart": "2026-09-01T00:00:00.000Z",
        "billingCycleEnd": "2026-10-01T00:00:00.000Z",
        "individualUsage": {
            "plan": {
                "autoPercentUsed": 42.5,
                "apiPercentUsed": 10.0,
                "totalPercentUsed": 31.0,
            }
        },
    }

    snapshot = parse_cursor_usage_summary(payload)

    assert [window.id for window in snapshot.windows] == ["cursor_models", "other_models"]
    assert snapshot.windows[0].used_percent == 42.5
    assert snapshot.windows[0].remaining_percent == 57.5
    assert snapshot.windows[1].used_percent == 10.0
    assert cursor_model_pool("cursor-grok-4.6-xhigh") == "cursor_models"
    assert cursor_model_pool("composer-2.5") == "cursor_models"
    assert cursor_model_pool("gpt-5.6-sol") == "other_models"


def test_cursor_parser_falls_back_to_one_monthly_window_when_pool_breakdown_is_missing() -> None:
    snapshot = parse_cursor_usage_summary({
        "billingCycleEnd": "2026-10-01T00:00:00Z",
        "individualUsage": {"plan": {"totalPercentUsed": 81.25}},
    })

    assert [window.id for window in snapshot.windows] == ["monthly"]
    assert snapshot.windows[0].used_percent == 81.25
    assert snapshot.status == "available"


class _FakeProvider:
    def __init__(self, provider: str, results: list[object]) -> None:
        self.provider = provider
        self.results = list(results)
        self.calls = 0

    async def fetch(self):
        self.calls += 1
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_usage_service_keeps_last_snapshot_but_marks_it_stale_and_unknown_on_fetch_failure() -> None:
    async def scenario() -> None:
        initial = parse_cursor_usage_summary({
            "billingCycleEnd": "2026-10-01T00:00:00Z",
            "individualUsage": {"plan": {"totalPercentUsed": 25.0}},
        })
        provider = _FakeProvider("cursor", [initial, UsageBudgetFetchError("usage endpoint unavailable")])
        service = UsageBudgetService({"cursor": provider}, refresh_interval_seconds=300)

        fresh = await service.refresh("cursor", force=True)
        stale = await service.refresh("cursor", force=True)

        assert fresh.stale is False
        assert stale.stale is True
        assert stale.status == "unknown"
        assert stale.windows == fresh.windows
        assert stale.last_error == "usage endpoint unavailable"

    asyncio.run(scenario())


def test_codex_native_usage_fetch_does_not_block_callers_event_loop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blocking_fetch(_self):
        started.set()
        time.sleep(0.2)
        return {
            "rateLimits": {
                "limitId": "codex",
                "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                "secondary": None,
            }
        }

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        provider = CodexUsageProvider(CodexAppServerAdapter(policy))
        monkeypatch.setattr(CodexAppServerAdapter, "fetch_rate_limits", blocking_fetch)

        task = asyncio.create_task(provider.fetch())
        assert await asyncio.to_thread(started.wait, 1.0)
        before = time.perf_counter()
        await asyncio.sleep(0.02)
        assert time.perf_counter() - before < 0.1
        snapshot = await task
        assert snapshot.provider == "codex"
        assert snapshot.windows[0].used_percent == 12.0

    started = threading.Event()
    asyncio.run(scenario())


def test_codex_native_usage_fetch_cancellation_before_worker_task_creation_is_finite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_fetch(_self):
        await asyncio.Event().wait()

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        provider = CodexUsageProvider(CodexAppServerAdapter(policy))
        monkeypatch.setattr(CodexAppServerAdapter, "fetch_rate_limits", fake_fetch)
        real_new_event_loop = asyncio.new_event_loop
        worker_entered = threading.Event()
        release_worker = threading.Event()

        def delayed_new_event_loop():
            worker_entered.set()
            release_worker.wait(1.0)
            return real_new_event_loop()

        monkeypatch.setattr(usage_providers_module.asyncio, "new_event_loop", delayed_new_event_loop)
        task = asyncio.create_task(provider.fetch())
        assert await asyncio.to_thread(worker_entered.wait, 1.0)
        task.cancel()
        release_worker.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
        assert not any(
            thread.name == "nirai-codex-usage" and thread.is_alive()
            for thread in threading.enumerate()
        )

    asyncio.run(scenario())


def test_codex_native_usage_fetch_cancellation_reaps_worker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def blocking_fetch(_self):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    async def scenario() -> None:
        policy = AgentWorkspacePolicy(tmp_path, ("runtime\\workspace",))
        provider = CodexUsageProvider(CodexAppServerAdapter(policy))
        monkeypatch.setattr(CodexAppServerAdapter, "fetch_rate_limits", blocking_fetch)

        task = asyncio.create_task(provider.fetch())
        assert await asyncio.to_thread(started.wait, 1.0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=1.0)
        assert cleaned.wait(0.5)

    started = threading.Event()
    cleaned = threading.Event()
    asyncio.run(scenario())


def test_usage_providers_normalize_native_fetches_without_persisting_auth_material() -> None:
    class FakeCodexAdapter:
        async def fetch_rate_limits(self):
            return {
                "rateLimits": {
                    "limitId": "codex",
                    "primary": {"usedPercent": 12, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
                    "secondary": None,
                }
            }

    async def scenario() -> None:
        codex = await CodexUsageProvider(FakeCodexAdapter()).fetch()  # type: ignore[arg-type]
        observed_tokens: list[str] = []

        def fetch_cursor_json(token: str):
            observed_tokens.append(token)
            return {
                "billingCycleEnd": "2026-10-01T00:00:00Z",
                "individualUsage": {"plan": {"autoPercentUsed": 15, "apiPercentUsed": 5}},
            }

        cursor = await CursorUsageProvider(
            token_reader=lambda: "test-session-secret",
            fetch_json=fetch_cursor_json,
        ).fetch()

        assert codex.provider == "codex"
        assert cursor.provider == "cursor"
        assert observed_tokens == ["test-session-secret"]
        serialized = str(cursor.to_protocol()) + str(codex.to_protocol())
        assert "test-session-secret" not in serialized

    asyncio.run(scenario())


@pytest.mark.skipif(
    os.environ.get("NIRAI_LIVE_USAGE_SMOKE") != "1",
    reason="Live provider usage smoke is opt-in only",
)
def test_live_provider_usage_smoke_reads_real_accounts_without_starting_work() -> None:
    async def scenario() -> None:
        root = Path(__file__).resolve().parents[2]
        policy = AgentWorkspacePolicy(root, ("runtime\\workspace",))
        codex = await CodexUsageProvider(CodexAppServerAdapter(policy)).fetch()
        cursor = await CursorUsageProvider().fetch()

        for snapshot, expected in ((codex, "codex"), (cursor, "cursor")):
            assert snapshot.provider == expected
            assert snapshot.stale is False
            assert snapshot.status in {"available", "limited"}
            assert snapshot.windows
            serialized = str(snapshot.to_protocol())
            assert "token" not in serialized.casefold()
            assert "cookie" not in serialized.casefold()

    asyncio.run(scenario())


def test_usage_protocol_reset_in_is_derived_not_persisted_as_source_data() -> None:
    snapshot = parse_codex_rate_limits({
        "rateLimits": {
            "limitId": "codex",
            "primary": {"usedPercent": 50, "windowDurationMins": 300, "resetsAt": 1_800_000_000},
            "secondary": None,
        }
    }, fetched_at=datetime.fromtimestamp(1_799_999_000, tz=timezone.utc))

    payload = snapshot.to_protocol(now=datetime.fromtimestamp(1_799_999_500, tz=timezone.utc))
    assert payload["windows"][0]["reset_in_seconds"] == 500
    assert payload["windows"][0]["remaining_percent"] == 50.0


@pytest.mark.parametrize("raw", [{}, {"primary": None, "secondary": None}])
def test_codex_missing_usage_does_not_report_available(raw) -> None:
    with pytest.raises(UsageBudgetFetchError):
        parse_codex_rate_limits({"rateLimits": raw})


def test_codex_explicit_hard_limit_applies_even_when_its_window_is_missing() -> None:
    snapshot = parse_codex_rate_limits({"rateLimits": {
        "primary": None,
        "secondary": {"usedPercent": 25, "windowDurationMins": 10080},
        "rateLimitReachedType": "primary",
    }})
    assert usage_is_hard_limited(snapshot)


def test_cursor_missing_model_pool_does_not_borrow_another_pools_limit() -> None:
    snapshot = parse_cursor_usage_summary({"individualUsage": {"plan": {
        "autoPercentUsed": 100,
    }}})
    assert usage_is_hard_limited(snapshot, model="composer-2.5")
    assert not usage_is_hard_limited(snapshot, model="gpt-5.6-sol")
    assert not usage_is_hard_limited(snapshot, model="auto")


def test_usage_refresh_times_out_an_unresponsive_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    async def scenario() -> None:
        cancelled = asyncio.Event()

        class StuckProvider:
            provider = "codex"

            async def fetch(self):
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()

        monkeypatch.setattr(usage_budget_module, "USAGE_FETCH_TIMEOUT_SECONDS", 0.02, raising=False)
        service = UsageBudgetService({"codex": StuckProvider()})
        result = await asyncio.wait_for(service.refresh("codex"), 0.5)
        assert result.status == "unknown"
        assert result.stale
        assert cancelled.is_set()

    asyncio.run(scenario())
