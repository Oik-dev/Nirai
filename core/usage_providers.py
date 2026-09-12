"""Provider-specific Usage Budget fetch adapters.

Only this module knows provider transport details. It returns generic snapshots
and never stores provider authentication values in Nirai state.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping
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


class CodexUsageProvider:
    provider = "codex"

    def __init__(self, adapter: CodexAppServerAdapter) -> None:
        self._adapter = adapter

    async def fetch(self) -> UsageBudgetSnapshot:
        try:
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
