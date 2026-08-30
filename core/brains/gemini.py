from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import logging
from pathlib import Path
import ssl
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import quote

from .base import BrainError, BrainResponse, BrainResponseError, BrainUnavailableError
from .talk_common import (
    TALK_JSON_SCHEMA,
    build_talk_prompt,
    build_whisper_prompt,
    parse_embedded_json,
    parse_talk_object,
)


GEMINI_TIMEOUT_SEC = 120.0
GEMINI_HTTP_TIMEOUT_SEC = 30.0
GEMINI_POLL_INTERVAL_SEC = 2.0
GEMINI_CANCEL_TIMEOUT_SEC = 5.0
GEMINI_DEFAULT_MODEL = "gemini-3.5-flash"
GEMINI_API_HOSTNAME = "generativelanguage.googleapis.com"
GEMINI_API_HOST = f"https://{GEMINI_API_HOSTNAME}/v1beta"
GEMINI_INTERACTIONS_REVISION = "2026-05-20"
GEMINI_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
LOGGER = logging.getLogger("nirai.core.brain.gemini")


def load_gemini_api_key(nirai_root: Path) -> str | None:
    path = nirai_root / "world" / ".env"
    if not path.is_file():
        return None
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "GEMINI_API_KEY" and value.strip():
            return value.strip().strip('"').strip("'")
    return None


def _request_json(api_key: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Synchronous request used only by the background Model Catalog loader."""
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{GEMINI_API_HOST}{path}",
        data=data,
        method="GET" if payload is None else "POST",
        headers={
            "x-goog-api-key": api_key,
            "content-type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=GEMINI_TIMEOUT_SEC) as response:
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        if exc.code in {401, 403, 429}:
            raise BrainUnavailableError(f"Gemini API is unavailable: HTTP {exc.code}: {detail}") from exc
        raise BrainError(f"Gemini API failed: HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise BrainUnavailableError(f"Gemini API is unavailable: {exc.reason}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrainResponseError("Gemini API response was not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise BrainResponseError("Gemini API response must be a JSON object")
    return parsed


async def _read_http_response_body(
    reader: asyncio.StreamReader,
    headers: dict[str, str],
) -> bytes:
    transfer_encoding = headers.get("transfer-encoding", "").casefold()
    if "chunked" in transfer_encoding:
        chunks: list[bytes] = []
        total = 0
        while True:
            size_line = await reader.readline()
            if not size_line:
                raise BrainResponseError("Gemini API ended a chunked response unexpectedly")
            try:
                size = int(size_line.split(b";", 1)[0].strip(), 16)
            except ValueError as exc:
                raise BrainResponseError("Gemini API returned an invalid chunk size") from exc
            if size == 0:
                while True:
                    trailer = await reader.readline()
                    if trailer in {b"", b"\r\n"}:
                        break
                break
            total += size
            if total > GEMINI_MAX_RESPONSE_BYTES:
                raise BrainResponseError("Gemini API response exceeded 4MB")
            try:
                chunks.append(await reader.readexactly(size))
                terminator = await reader.readexactly(2)
            except asyncio.IncompleteReadError as exc:
                raise BrainUnavailableError(
                    "Gemini API connection closed before the response body completed"
                ) from exc
            if terminator != b"\r\n":
                raise BrainResponseError("Gemini API returned an invalid chunk terminator")
        return b"".join(chunks)

    content_length = headers.get("content-length")
    if content_length is not None:
        try:
            size = int(content_length)
        except ValueError as exc:
            raise BrainResponseError("Gemini API returned an invalid Content-Length") from exc
        if size > GEMINI_MAX_RESPONSE_BYTES:
            raise BrainResponseError("Gemini API response exceeded 4MB")
        try:
            return await reader.readexactly(size)
        except asyncio.IncompleteReadError as exc:
            raise BrainUnavailableError(
                "Gemini API connection closed before the response body completed"
            ) from exc

    body = await reader.read(GEMINI_MAX_RESPONSE_BYTES + 1)
    if len(body) > GEMINI_MAX_RESPONSE_BYTES:
        raise BrainResponseError("Gemini API response exceeded 4MB")
    return body


async def _request_json_async(
    api_key: str,
    path: str,
    payload: dict[str, Any] | None = None,
    *,
    method: str | None = None,
) -> dict[str, Any]:
    """Cancellable stdlib HTTPS transport for Interactions API calls."""
    actual_method = method or ("GET" if payload is None else "POST")
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    ssl_context = ssl.create_default_context()
    writer: asyncio.StreamWriter | None = None

    async def execute() -> dict[str, Any]:
        nonlocal writer
        reader, writer = await asyncio.open_connection(
            GEMINI_API_HOSTNAME,
            443,
            ssl=ssl_context,
            server_hostname=GEMINI_API_HOSTNAME,
        )
        headers = [
            f"{actual_method} /v1beta{path} HTTP/1.1",
            f"Host: {GEMINI_API_HOSTNAME}",
            f"x-goog-api-key: {api_key}",
            "Accept: application/json",
            "Accept-Encoding: identity",
            "Connection: close",
        ]
        if path.startswith("/interactions"):
            headers.append(f"Api-Revision: {GEMINI_INTERACTIONS_REVISION}")
        if body is not None:
            headers.extend([
                "Content-Type: application/json",
                f"Content-Length: {len(body)}",
            ])
        request_head = ("\r\n".join(headers) + "\r\n\r\n").encode("ascii")
        writer.write(request_head)
        if body is not None:
            writer.write(body)
        await writer.drain()

        status_line = await reader.readline()
        parts = status_line.decode("ascii", errors="replace").strip().split(" ", 2)
        if len(parts) < 2:
            raise BrainResponseError("Gemini API returned an invalid HTTP status line")
        try:
            status = int(parts[1])
        except ValueError as exc:
            raise BrainResponseError("Gemini API returned an invalid HTTP status") from exc

        response_headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in {b"", b"\r\n"}:
                break
            key, separator, value = line.partition(b":")
            if not separator:
                raise BrainResponseError("Gemini API returned an invalid HTTP header")
            response_headers[key.decode("ascii", errors="ignore").strip().casefold()] = value.decode(
                "latin-1", errors="replace"
            ).strip()

        raw_bytes = await _read_http_response_body(reader, response_headers)
        raw = raw_bytes.decode("utf-8", errors="replace")
        if status >= 400:
            detail = raw[:1000]
            if status in {401, 403, 429}:
                raise BrainUnavailableError(
                    f"Gemini API is unavailable: HTTP {status}: {detail}"
                )
            raise BrainError(f"Gemini API failed: HTTP {status}: {detail}")
        if not raw.strip():
            return {}
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise BrainResponseError("Gemini API response was not valid JSON") from exc
        if not isinstance(parsed, dict):
            raise BrainResponseError("Gemini API response must be a JSON object")
        return parsed

    try:
        return await asyncio.wait_for(execute(), timeout=GEMINI_HTTP_TIMEOUT_SEC)
    except asyncio.TimeoutError as exc:
        raise BrainUnavailableError("Gemini API timed out") from exc
    except OSError as exc:
        raise BrainUnavailableError(f"Gemini API is unavailable: {exc}") from exc
    finally:
        if writer is not None:
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()


def list_gemini_models(nirai_root: Path) -> list[dict[str, str]]:
    api_key = load_gemini_api_key(nirai_root)
    if api_key is None:
        raise BrainUnavailableError("GEMINI_API_KEY was not found in world/.env")
    payload = _request_json(api_key, "/models?pageSize=1000")
    result: list[dict[str, str]] = []
    excluded = ("image", "tts", "transcribe", "robotics", "computer-use", "omni")
    allowed_prefixes = ("gemini-", "antigravity-")
    for raw_model in payload.get("models", []):
        if not isinstance(raw_model, dict):
            continue
        name = raw_model.get("name")
        methods = raw_model.get("supportedGenerationMethods")
        if not isinstance(name, str) or not isinstance(methods, list) or "generateContent" not in methods:
            continue
        model_id = name.removeprefix("models/")
        lowered = model_id.casefold()
        if not model_id.startswith(allowed_prefixes) or any(marker in lowered for marker in excluded):
            continue
        display_name = raw_model.get("displayName")
        result.append({
            "id": model_id,
            "display_name": display_name if isinstance(display_name, str) and display_name else model_id,
        })
    return result


def _extract_interaction_text(payload: dict[str, Any]) -> str:
    steps = payload.get("steps")
    if not isinstance(steps, list):
        raise BrainResponseError("Gemini interaction contained no steps")
    parts: list[str] = []
    for step in steps:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        content = step.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str):
                parts.append(item["text"])
    text = "".join(parts).strip()
    if not text:
        raise BrainResponseError("Gemini interaction contained no model text")
    return text


def _interaction_error(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if isinstance(error, dict):
        message = error.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return f"interaction status={payload.get('status')}"


def _is_antigravity(model: str) -> bool:
    return model.casefold().startswith("antigravity-")


class GeminiDriver:
    def __init__(self, nirai_root: Path) -> None:
        self.nirai_root = nirai_root
        self.api_key = load_gemini_api_key(nirai_root)
        if self.api_key is None:
            raise BrainUnavailableError("GEMINI_API_KEY was not found in world/.env")
        self._active: dict[str, asyncio.Task[dict[str, Any]]] = {}
        self._interaction_ids: dict[str, str] = {}

    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> BrainResponse:
        model_value = resident.get("brain_model")
        model = model_value.strip() if isinstance(model_value, str) and model_value.strip() else GEMINI_DEFAULT_MODEL
        allow_web_search = _is_antigravity(model)

        if mode == "talk":
            prompt = build_talk_prompt(resident, context, allow_web_search=allow_web_search)
        elif mode == "whisper":
            prompt = build_whisper_prompt(resident, context, allow_web_search=allow_web_search)
        else:
            raise BrainError(f"GeminiDriver does not support mode yet: {mode}")

        for attempt in range(2):
            task = asyncio.create_task(self._run_interaction(invocation_id, model, prompt))
            self._active[invocation_id] = task
            try:
                response_payload = await task
            except asyncio.CancelledError:
                raise
            finally:
                if self._active.get(invocation_id) is task:
                    self._active.pop(invocation_id, None)
                self._interaction_ids.pop(invocation_id, None)

            try:
                raw_text = _extract_interaction_text(response_payload)
                return parse_talk_object(parse_embedded_json(raw_text, "Gemini"), "Gemini")
            except BrainResponseError as exc:
                LOGGER.warning(
                    "gemini_parse_failed invocation_id=%s attempt=%s model=%s error=%s",
                    invocation_id,
                    attempt + 1,
                    model,
                    exc,
                )
                if attempt == 1:
                    raise

        raise BrainResponseError("Gemini response could not be parsed")

    async def _run_interaction(
        self,
        invocation_id: str,
        model: str,
        prompt: str,
    ) -> dict[str, Any]:
        if _is_antigravity(model):
            payload: dict[str, Any] = {
                "agent": model,
                "input": prompt,
                "environment": "remote",
                "background": True,
                "tools": [
                    {"type": "google_search"},
                    {"type": "url_context"},
                ],
            }
        else:
            payload = {
                "model": model,
                "input": prompt,
                "response_format": {
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": TALK_JSON_SCHEMA,
                },
            }

        async def execute() -> dict[str, Any]:
            response = await _request_json_async(self.api_key, "/interactions", payload)
            interaction_id = response.get("id")
            if isinstance(interaction_id, str) and interaction_id:
                self._interaction_ids[invocation_id] = interaction_id

            while True:
                status = response.get("status")
                if status == "completed":
                    return response
                if status not in {"queued", "in_progress"}:
                    raise BrainError(f"Gemini interaction failed: {_interaction_error(response)}")
                if not isinstance(interaction_id, str) or not interaction_id:
                    raise BrainResponseError("Gemini interaction did not return an id")
                await asyncio.sleep(GEMINI_POLL_INTERVAL_SEC)
                response = await _request_json_async(
                    self.api_key,
                    f"/interactions/{quote(interaction_id, safe='')}",
                    method="GET",
                )

        try:
            return await asyncio.wait_for(execute(), timeout=GEMINI_TIMEOUT_SEC)
        except asyncio.TimeoutError as exc:
            interaction_id = self._interaction_ids.get(invocation_id)
            if _is_antigravity(model) and interaction_id:
                await self._cancel_remote_interaction(invocation_id, interaction_id)
            raise BrainUnavailableError("Gemini interaction timed out") from exc

    async def _cancel_remote_interaction(self, invocation_id: str, interaction_id: str) -> None:
        try:
            await asyncio.wait_for(
                _request_json_async(
                    self.api_key,
                    f"/interactions/{quote(interaction_id, safe='')}/cancel",
                    method="POST",
                ),
                timeout=GEMINI_CANCEL_TIMEOUT_SEC,
            )
        except (asyncio.TimeoutError, BrainError) as exc:
            LOGGER.warning(
                "gemini_interaction_cancel_failed invocation_id=%s interaction_id=%s error=%s",
                invocation_id,
                interaction_id,
                str(exc)[:500],
            )

    async def cancel(self, invocation_id: str) -> bool:
        task = self._active.get(invocation_id)
        if task is None or task.done():
            return False

        interaction_id = self._interaction_ids.get(invocation_id)
        if interaction_id:
            await self._cancel_remote_interaction(invocation_id, interaction_id)

        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        return True
