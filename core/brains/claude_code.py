from __future__ import annotations

import json
import logging
from pathlib import Path
import shutil
from typing import Any, Sequence

from .base import BrainError, BrainResponse, BrainResponseError, BrainUnavailableError
from .process_manager import CompletedInvocation, ProcessManager
from .talk_common import (
    TALK_JSON_SCHEMA,
    build_talk_prompt,
    build_whisper_prompt,
    extract_result_envelope,
)


CLAUDE_TIMEOUT_SEC = 120.0
LOGGER = logging.getLogger("nirai.core.brain.claude_code")


def resolve_claude_command() -> tuple[str, ...]:
    claude_path = (
        shutil.which("claude.exe")
        or shutil.which("claude.cmd")
        or shutil.which("claude")
    )
    if claude_path is None:
        raise BrainUnavailableError("Claude Code CLI was not found on PATH")

    path = Path(claude_path)
    if path.suffix.lower() == ".cmd":
        raise BrainUnavailableError(
            "Claude Code npm launcher is not supported by this Nirai build; install the native claude executable"
        )
    return (str(path),)


def _extract_cli_error(completed: CompletedInvocation) -> str:
    raw = completed.stdout.strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict):
            result = parsed.get("result")
            if isinstance(result, str) and result.strip():
                return result.strip()
            status = parsed.get("api_error_status")
            if isinstance(status, int):
                return f"Claude Code API error {status}"
    return completed.stderr.strip() or f"exit code {completed.returncode}"


def _is_unavailable_error(detail: str) -> bool:
    lowered = detail.casefold()
    return any(
        marker in lowered
        for marker in (
            "subscription access",
            "api key",
            "authentication",
            "not logged in",
            "login required",
            "unauthorized",
            "forbidden",
        )
    )


class ClaudeCodeDriver:
    def __init__(
        self,
        nirai_root: Path,
        *,
        process_manager: ProcessManager | None = None,
        command_prefix: Sequence[str] | None = None,
    ) -> None:
        self.nirai_root = nirai_root
        self.workspace = nirai_root / "runtime" / "brain_workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.process_manager = process_manager or ProcessManager()
        self.command_prefix = tuple(command_prefix) if command_prefix is not None else resolve_claude_command()
        self.schema_json = json.dumps(TALK_JSON_SCHEMA, ensure_ascii=False, separators=(",", ":"))

    async def think(
        self,
        invocation_id: str,
        mode: str,
        resident: dict[str, Any],
        context: dict[str, Any],
    ) -> BrainResponse:
        if mode == "talk":
            prompt = build_talk_prompt(resident, context)
        elif mode == "whisper":
            prompt = build_whisper_prompt(resident, context)
        else:
            raise BrainError(f"ClaudeCodeDriver does not support mode yet: {mode}")

        model_value = resident.get("brain_model")
        model = model_value.strip() if isinstance(model_value, str) and model_value.strip() else None
        for attempt in range(2):
            completed = await self._run_once(invocation_id, prompt, model)
            if completed.returncode != 0:
                detail = _extract_cli_error(completed)
                LOGGER.warning(
                    "claude_cli_failed invocation_id=%s returncode=%s detail=%s",
                    invocation_id,
                    completed.returncode,
                    detail[:500].replace("\r", "\\r").replace("\n", "\\n"),
                )
                if _is_unavailable_error(detail):
                    raise BrainUnavailableError(f"Claude Code is unavailable: {detail}")
                raise BrainError(f"Claude Code CLI failed: {detail}")
            try:
                return extract_result_envelope(completed.stdout, "Claude Code")
            except BrainResponseError as exc:
                LOGGER.warning(
                    "claude_parse_failed invocation_id=%s attempt=%s error=%s",
                    invocation_id,
                    attempt + 1,
                    exc,
                )
                if attempt == 1:
                    raise

        raise BrainResponseError("Claude Code response could not be parsed")

    async def cancel(self, invocation_id: str) -> bool:
        return await self.process_manager.cancel(invocation_id)

    async def _run_once(
        self,
        invocation_id: str,
        prompt: str,
        model: str | None,
    ) -> CompletedInvocation:
        argv = [
            *self.command_prefix,
            "-p",
            *(["--model", model] if model is not None else []),
            "--safe-mode",
            "--tools",
            "",
            "--permission-mode",
            "dontAsk",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            self.schema_json,
        ]
        return await self.process_manager.run(
            invocation_id,
            argv,
            cwd=self.workspace,
            timeout_sec=CLAUDE_TIMEOUT_SEC,
            stdin_text=prompt,
        )
