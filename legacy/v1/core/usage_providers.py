"""Provider-specific Usage Budget fetch adapters.

Only this module knows provider transport details. It returns generic snapshots
and never stores provider authentication values in Nirai state.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any, Awaitable, Callable, Mapping, TypeVar
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .agents.codex_app_server import CodexAppServerAdapter
from .usage_budget import (
    UsageBudgetFetchError,
    UsageBudgetSnapshot,
    parse_codex_rate_limits,
    parse_cursor_usage_summary,
)

_CURSOR_USAGE_URL = "https://cursor.com/api/usage-summary"
_MAX_CURSOR_USAGE_BYTES = 1_048_576
_CODEX_USAGE_WORKER_JOIN_TIMEOUT_SECONDS = 15.0
LOGGER = logging.getLogger("nirai.core.usage_providers")
_T = TypeVar("_T")


async def _run_cancellable_coroutine_in_worker(
    factory: Callable[[], Awaitable[_T]],
) -> _T:
    """Run one async provider operation on an isolated event-loop thread.

    Windows subprocess creation can briefly block the event loop even through
    asyncio.create_subprocess_exec(). Usage polling is supplemental and must not
    stall World input, cancellation, or rendering. The worker owns creation of
    both its event loop and provider Task, so cancellation cannot strand an
    empty ``run_forever`` loop between thread startup and Task submission.
    """
    from concurrent.futures import Future

    result: Future[_T] = Future()
    cancel_requested = threading.Event()
    state_lock = threading.Lock()
    holder: dict[str, object] = {}

    def worker() -> None:
        loop: asyncio.AbstractEventLoop | None = None
        task: asyncio.Task[_T] | None = None
        outcome: tuple[str, object] | None = None
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            coroutine = factory()
            task = loop.create_task(coroutine)
            with state_lock:
                holder["loop"] = loop
                holder["task"] = task
            if cancel_requested.is_set():
                task.cancel()
            try:
                outcome = ("result", loop.run_until_complete(task))
            except asyncio.CancelledError:
                outcome = ("cancelled", None)
            except BaseException as exc:
                outcome = ("error", exc)
        except BaseException as exc:
            outcome = ("error", exc)
        finally:
            if loop is not None:
                try:
                    pending = [candidate for candidate in asyncio.all_tasks(loop) if not candidate.done()]
                    for candidate in pending:
                        candidate.cancel()
                    if pending:
                        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                    loop.run_until_complete(loop.shutdown_asyncgens())
                finally:
                    loop.close()
            if not result.done():
                if outcome is None or outcome[0] == "cancelled":
                    result.cancel()
                elif outcome[0] == "error":
                    result.set_exception(outcome[1])  # type: ignore[arg-type]
                else:
                    result.set_result(outcome[1])  # type: ignore[arg-type]

    thread = threading.Thread(target=worker, name="nirai-codex-usage", daemon=True)
    thread.start()

    def cancel_worker() -> None:
        cancel_requested.set()
        with state_lock:
            loop = holder.get("loop")
            task = holder.get("task")
        if isinstance(loop, asyncio.AbstractEventLoop) and isinstance(task, asyncio.Task):
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                # The worker already completed and closed its loop.
                pass

    try:
        return await asyncio.wrap_future(result)
    except asyncio.CancelledError:
        cancel_worker()
        raise
    finally:
        if not result.done():
            cancel_worker()
        # The provider adapter bounds process shutdown and credential cleanup.
        # Keep the join off Core's event loop while ensuring cleanup finishes
        # before the usage operation itself is considered gone.
        join_task = asyncio.create_task(asyncio.to_thread(
            thread.join,
            _CODEX_USAGE_WORKER_JOIN_TIMEOUT_SECONDS,
        ))
        try:
            await asyncio.shield(join_task)
        except asyncio.CancelledError:
            await join_task
            raise
        if thread.is_alive():
            # Never wedge Core shutdown on supplemental quota polling. The
            # worker is daemonized; the adapter normally finishes cancellation
            # well inside this bound, while stale homes are covered by its next
            # startup cleanup pass if an OS/provider failure outlives the bound.
            LOGGER.error("codex_usage_worker_cleanup_timeout")


class CodexUsageProvider:
    provider = "codex"

    def __init__(self, adapter: CodexAppServerAdapter) -> None:
        self._adapter = adapter

    async def fetch(self) -> UsageBudgetSnapshot:
        try:
            if isinstance(self._adapter, CodexAppServerAdapter):
                workspace_policy = self._adapter.workspace_policy
                payload = await _run_cancellable_coroutine_in_worker(
                    lambda: CodexAppServerAdapter(workspace_policy).fetch_rate_limits()
                )
            else:
                # Lightweight fakes/custom providers remain directly awaitable;
                # only the native Windows app-server transport needs isolation.
                payload = await self._adapter.fetch_rate_limits()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise UsageBudgetFetchError("Codex usage data is unavailable") from exc
        return parse_codex_rate_limits(payload)


class CursorUsageProvider:
    provider = "cursor"

    def __init__(
        self,
        *,
        token_reader: Callable[[], str] | None = None,
        fetch_json: Callable[[str], Mapping[str, Any]] | None = None,
    ) -> None:
        self._token_reader = token_reader or _read_cursor_access_token
        self._fetch_json = fetch_json or _fetch_cursor_usage_json

    async def fetch(self) -> UsageBudgetSnapshot:
        try:
            payload = await asyncio.to_thread(self._fetch_payload)
        except asyncio.CancelledError:
            raise
        except UsageBudgetFetchError:
            raise
        except Exception as exc:
            raise UsageBudgetFetchError("Cursor usage data is unavailable") from exc
        return parse_cursor_usage_summary(payload)

    def _fetch_payload(self) -> Mapping[str, Any]:
        token = self._token_reader()
        if not token:
            raise UsageBudgetFetchError("Cursor local login state is unavailable")
        return self._fetch_json(token)


def _read_cursor_access_token() -> str:
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        raise UsageBudgetFetchError("Cursor local login state is unavailable")
    database = Path(appdata) / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    if not database.is_file():
        raise UsageBudgetFetchError("Cursor local login state is unavailable")
    try:
        connection = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=2.0)
        try:
            row = connection.execute(
                "SELECT value FROM ItemTable WHERE key = ?",
                ("cursorAuth/accessToken",),
            ).fetchone()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        raise UsageBudgetFetchError("Cursor local login state could not be read") from exc
    if row is None or not isinstance(row[0], str):
        raise UsageBudgetFetchError("Cursor local login state is unavailable")
    raw = row[0].strip()
    if not raw:
        raise UsageBudgetFetchError("Cursor local login state is unavailable")
    if raw.startswith('"'):
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise UsageBudgetFetchError("Cursor local login state is invalid") from exc
        if not isinstance(decoded, str) or not decoded.strip():
            raise UsageBudgetFetchError("Cursor local login state is invalid")
        return decoded.strip()
    return raw


def _fetch_cursor_usage_json(token: str) -> Mapping[str, Any]:
    session_value = token if token.startswith("::") else f"::{token}"
    request = Request(
        _CURSOR_USAGE_URL,
        headers={
            "Accept": "application/json",
            "User-Agent": "Nirai/0.1 Usage Monitor",
            "Cookie": f"WorkosCursorSessionToken={session_value}",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=10.0) as response:
            raw = response.read(_MAX_CURSOR_USAGE_BYTES + 1)
    except HTTPError as exc:
        raise UsageBudgetFetchError(f"Cursor usage endpoint returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise UsageBudgetFetchError("Cursor usage endpoint is unavailable") from exc
    if len(raw) > _MAX_CURSOR_USAGE_BYTES:
        raise UsageBudgetFetchError("Cursor usage response exceeded the size limit")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise UsageBudgetFetchError("Cursor usage response was invalid JSON") from exc
    if not isinstance(payload, Mapping):
        raise UsageBudgetFetchError("Cursor usage response had an invalid shape")
    return payload
