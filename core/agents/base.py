from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

from .types import AgentEventType


class AgentRuntimeError(RuntimeError):
    pass


class AgentRuntimeUnavailableError(AgentRuntimeError):
    pass


class AgentRuntimeProtocolError(AgentRuntimeError):
    pass


@dataclass(frozen=True)
class AgentRunRequest:
    task_id: str
    agent_session_id: str
    resident: str
    provider: str
    prompt: str
    working_dir: Path
    model: str | None = None
    reasoning_effort: str | None = None


EmitEvent = Callable[[AgentEventType, dict[str, Any]], Awaitable[None]]
WaitForMaster = Callable[[str, str, dict[str, Any]], Awaitable[dict[str, Any]]]


class AgentRuntimeAdapter(Protocol):
    provider: str

    async def run(
        self,
        request: AgentRunRequest,
        *,
        emit: EmitEvent,
        wait_for_master: WaitForMaster,
    ) -> str | None: ...

    async def cancel(self, agent_session_id: str) -> bool: ...
