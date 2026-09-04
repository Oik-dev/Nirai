from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any, Sequence

from .base import BrainError, BrainResponse, BrainResponseError, BrainUnavailableError
from .process_manager import CompletedInvocation, ProcessManager
from .talk_common import (
    build_consult_prompt,
    build_talk_prompt,
    build_whisper_prompt,
    extract_consult_result_envelope,
    extract_result_envelope,
)


CURSOR_TIMEOUT_SEC = 120.0
LOGGER = logging.getLogger("nirai.core.brain.cursor")


def _windows_subprocess_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def _latest_cursor_runtime(launcher_dir: Path) -> tuple[str, ...] | None:
    direct_node = launcher_dir / "node.exe"
    direct_index = launcher_dir / "index.js"
    if direct_node.is_file() and direct_index.is_file():
        return (str(direct_node), str(direct_index))

    versions_dir = launcher_dir / "versions"
    if not versions_dir.is_dir():
        return None
    candidates = sorted(
        (candidate for candidate in versions_dir.iterdir() if candidate.is_dir()),
        key=lambda candidate: candidate.name,
        reverse=True,
    )
    for candidate in candidates:
        node_path = candidate / "node.exe"
        index_path = candidate / "index.js"
        if node_path.is_file() and index_path.is_file():
            return (str(node_path), str(index_path))
    return None


def resolve_cursor_command() -> tuple[str, ...]:
    cursor_path = (
        shutil.which("cursor-agent.exe")
        or shutil.which("cursor-agent.cmd")
        or shutil.which("cursor-agent")
    )
    if cursor_path is None:
        raise BrainUnavailableError("Cursor Agent CLI was not found on PATH")

    path = Path(cursor_path)
    if path.suffix.lower() == ".cmd":
        runtime = _latest_cursor_runtime(path.parent)
        if runtime is None:
            raise BrainUnavailableError("Cursor Agent runtime could not be resolved from its launcher")
        return runtime
    return (str(path),)


def _normalize_cursor_model_display_name(model_id: str, display_name: str) -> str:
    lowered = model_id.casefold()
    if lowered.endswith("-high-fast") and " high" not in display_name.casefold():
        return display_name.removesuffix(" Fast") + " High Fast"
    if lowered.endswith("-high") and " high" not in display_name.casefold():
        return display_name + " High"
    return display_name


def build_cursor_environment(nirai_root: Path) -> dict[str, str]:
    profile_root = nirai_root / "runtime" / "cursor_profile"
    config_dir = profile_root / ".cursor"
    profile_root.mkdir(parents=True, exist_ok=True)
    config_dir.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["USERPROFILE"] = str(profile_root)
    env["HOME"] = str(profile_root)
    env["CURSOR_CONFIG_DIR"] = str(config_dir)
    return env


def resolve_cursor_workspace() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise BrainUnavailableError("LOCALAPPDATA is unavailable for the isolated Cursor workspace")
    workspace = Path(local_app_data) / "Nirai" / "cursor_brain_workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return workspace


def list_cursor_models(nirai_root: Path | None = None) -> list[dict[str, str]]:
    command = resolve_cursor_command()
    env = build_cursor_environment(nirai_root) if nirai_root is not None else None
    try:
        completed = subprocess.run(
            [*command, "models"],
            cwd=str(Path.cwd()),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
            env=env,
            creationflags=_windows_subprocess_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BrainUnavailableError(f"Cursor model catalog could not be loaded: {exc}") from exc
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise BrainUnavailableError(f"Cursor model catalog could not be loaded: {detail}")
    models: list[dict[str, str]] = []
    for line in completed.stdout.splitlines():
        if " - " not in line:
            continue
        model_id, display_name = line.split(" - ", 1)
        model_id = model_id.strip()
        display_name = display_name.strip()
        if model_id and display_name:
            models.append({
                "id": model_id,
                "display_name": _normalize_cursor_model_display_name(model_id, display_name),
            })
    return models


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
            error = parsed.get("error")
            if isinstance(error, str) and error.strip():
                return error.strip()
    return completed.stderr.strip() or f"exit code {completed.returncode}"


def _is_unavailable_error(detail: str) -> bool:
    lowered = detail.casefold()
    return any(
        marker in lowered
        for marker in (
            "not logged in",
            "authentication",
            "unauthorized",
            "forbidden",
            "api key",
            "usage limit",
            "quota",
        )
    )


class CursorDriver:
    def __init__(
        self,
        nirai_root: Path,
        *,
        process_manager: ProcessManager | None = None,
        command_prefix: Sequence[str] | None = None,
    ) -> None:
        self.nirai_root = nirai_root
        self.workspace = resolve_cursor_workspace()
        self.environment = build_cursor_environment(nirai_root)
        self.process_manager = process_manager or ProcessManager()
        self.command_prefix = tuple(command_prefix) if command_prefix is not None else resolve_cursor_command()

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
        elif mode == "consult":
            prompt = build_consult_prompt(resident, context)
        else:
            raise BrainError(f"CursorDriver does not support mode yet: {mode}")

        model_value = resident.get("brain_model")
        model = model_value.strip() if isinstance(model_value, str) and model_value.strip() else None
        for attempt in range(2):
            completed = await self._run_once(invocation_id, prompt, model)
            if completed.returncode != 0:
                detail = _extract_cli_error(completed)
                LOGGER.warning(
                    "cursor_cli_failed invocation_id=%s returncode=%s detail=%s",
                    invocation_id,
                    completed.returncode,
                    detail[:500].replace("\r", "\\r").replace("\n", "\\n"),
                )
                if _is_unavailable_error(detail):
                    raise BrainUnavailableError(f"Cursor Agent is unavailable: {detail}")
                raise BrainError(f"Cursor Agent CLI failed: {detail}")
            try:
                if mode == "consult":
                    return extract_consult_result_envelope(completed.stdout, "Cursor Agent")
                return extract_result_envelope(completed.stdout, "Cursor Agent")
            except BrainResponseError as exc:
                LOGGER.warning(
                    "cursor_parse_failed invocation_id=%s attempt=%s error=%s",
                    invocation_id,
                    attempt + 1,
                    exc,
                )
                if attempt == 1:
                    raise

        raise BrainResponseError("Cursor Agent response could not be parsed")

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
            "--mode",
            "ask",
            "--trust",
            "--output-format",
            "json",
            "--workspace",
            str(self.workspace),
        ]
        return await self.process_manager.run(
            invocation_id,
            argv,
            cwd=self.workspace,
            timeout_sec=CURSOR_TIMEOUT_SEC,
            stdin_text=prompt,
            env=self.environment,
        )
