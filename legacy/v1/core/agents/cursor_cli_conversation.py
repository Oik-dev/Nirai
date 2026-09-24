from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Sequence

from ..brains.cursor import (
    CURSOR_TIMEOUT_SEC,
    _extract_cli_error,
    _is_unavailable_error,
    build_cursor_environment,
    resolve_cursor_command,
    resolve_cursor_workspace,
)
from ..brains.process_manager import ProcessManager
from .base import (
    AgentRunRequest,
    AgentRuntimeError,
    AgentRuntimeProtocolError,
    AgentRuntimeUnavailableError,
    EmitEvent,
    WaitForMaster,
)


class CursorCliConversationAdapter:
    """Read-only Cursor CLI transport for resumable Resident conversations.

    Cursor ACP is kept for Agent Runtime and review because it exposes structured
    permissions/tool events. Ordinary Resident conversation instead uses the
    Cursor CLI's native ``--resume <session_id>`` path so the exact CLI model id
    selected by the Resident (including xhigh non-fast variants) is preserved.
    """

    provider = "cursor"
    capabilities = frozenset()

    def __init__(
        self,
        nirai_root: Path,
        *,
        process_manager: ProcessManager | None = None,
        command_prefix: Sequence[str] | None = None,
    ) -> None:
        self.nirai_root = nirai_root.resolve()
        self.workspace = resolve_cursor_workspace()
        self.environment = build_cursor_environment(self.nirai_root)
        self.process_manager = process_manager or ProcessManager()
        self.command_prefix = tuple(command_prefix) if command_prefix is not None else resolve_cursor_command()

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None:
        del wait_for_master
        if not request.read_only or request.purpose != "resident_brain":
            raise AgentRuntimeError("Cursor CLI Conversation adapter is restricted to read-only Resident Brain turns")

        argv = [*self.command_prefix, "-p"]
        if request.provider_session_id is not None:
            argv.extend(["--resume", request.provider_session_id])
        if request.model:
            argv.extend(["--model", request.model])
        argv.extend([
            "--mode",
            "ask",
        ])
        # Cursor CLI sandbox mode is currently unavailable on Windows. Ask mode
        # is itself the provider's documented read-only Q&A mode there. On
        # platforms where sandbox exists, enable it as an additional boundary.
        if os.name != "nt":
            argv.extend(["--sandbox", "enabled"])
        argv.extend([
            "--trust",
            "--output-format",
            "json",
            "--workspace",
            str(self.workspace),
        ])

        completed = await self.process_manager.run(
            request.agent_session_id,
            argv,
            cwd=self.workspace,
            timeout_sec=CURSOR_TIMEOUT_SEC,
            stdin_text=request.prompt,
            env=self.environment,
        )
        if completed.returncode != 0:
            detail = _extract_cli_error(completed)
            if _is_unavailable_error(detail):
                raise AgentRuntimeUnavailableError(f"Cursor Agent is unavailable: {detail}")
            raise AgentRuntimeProtocolError(f"Cursor Agent CLI failed: {detail}")

        try:
            payload = json.loads(completed.stdout.strip())
        except json.JSONDecodeError as exc:
            raise AgentRuntimeProtocolError("Cursor Agent CLI returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise AgentRuntimeProtocolError("Cursor Agent CLI returned a non-object result")
        if payload.get("is_error") is True:
            detail = payload.get("result") or payload.get("error") or "Cursor Agent reported an error"
            raise AgentRuntimeProtocolError(str(detail))

        result = payload.get("result")
        session_id = payload.get("session_id")
        if not isinstance(result, str) or not result.strip():
            raise AgentRuntimeProtocolError("Cursor Agent CLI returned no Resident conversation text")
        if not isinstance(session_id, str) or not session_id.strip():
            raise AgentRuntimeProtocolError("Cursor Agent CLI returned no resumable session_id")
        cleaned_session_id = session_id.strip()
        if request.provider_session_id is not None and cleaned_session_id != request.provider_session_id:
            raise AgentRuntimeProtocolError("Cursor Agent --resume returned a different session_id")

        await emit("run_state", {
            "state": "running",
            "provider_session_id": cleaned_session_id,
        })
        await emit("status_message", {
            "kind": "provider_session_resumed" if request.provider_session_id else "provider_session_started",
            "text": "Cursor CLI conversation resumed" if request.provider_session_id else "Cursor CLI conversation started",
        })
        return result.strip()

    async def cancel(self, agent_session_id: str) -> bool:
        return await self.process_manager.cancel(agent_session_id)

    def discard_conversation_context(self, _conversation_id: str) -> None:
        # Cursor CLI chat ids are provider-side transport state. Forgetting the
        # durable Nirai mapping is sufficient to prevent future reuse; the CLI
        # currently exposes resume/list but no supported delete-by-id command.
        return
