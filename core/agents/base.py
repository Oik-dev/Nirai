from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any, Awaitable, Callable, Protocol

from .types import AgentEventType


class AgentRuntimeError(RuntimeError):
    pass


class AgentRuntimeUnavailableError(AgentRuntimeError):
    pass


class AgentRuntimeProtocolError(AgentRuntimeError):
    pass


class AgentReviewTargetChangedError(AgentRuntimeError):
    """A read-only review finished against a source tree that changed meanwhile."""


class AgentProviderLimitError(AgentRuntimeError):
    """Structured provider capacity stop that may be resumed or rerouted safely."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        partial_work_path: str | None = None,
    ) -> None:
        if code not in {"provider_quota_exhausted", "provider_rate_limit", "provider_resource_exhausted"}:
            raise ValueError(f"Unsupported provider limit code: {code}")
        super().__init__(message)
        self.code = code
        self.partial_work_path = partial_work_path

    def with_partial_work(self, path: str | None) -> "AgentProviderLimitError":
        return AgentProviderLimitError(
            self.code,
            str(self),
            partial_work_path=path,
        )


def classify_provider_limit(value: object) -> AgentProviderLimitError | None:
    """Classify only strong quota/rate-limit evidence; ambiguous failures stay generic."""
    structured_values: list[str] = []
    if isinstance(value, dict):
        for key in ("code", "type", "kind", "reason"):
            raw = value.get(key)
            if isinstance(raw, str) and raw.strip():
                structured_values.append(raw.strip().casefold())
    quota_codes = {
        "usage_limit_reached",
        "usage_limit_exceeded",
        "quota_exceeded",
        "quota_exhausted",
        "insufficient_quota",
    }
    rate_codes = {"rate_limit", "rate_limit_exceeded", "too_many_requests"}
    if any(item in quota_codes for item in structured_values):
        return AgentProviderLimitError("provider_quota_exhausted", "Provider usage quota is exhausted")
    if any(item in rate_codes for item in structured_values):
        return AgentProviderLimitError("provider_rate_limit", "Provider rate limit is active")

    if isinstance(value, dict):
        raw_message = value.get("message")
        text = raw_message if isinstance(raw_message, str) else ""
    elif isinstance(value, str):
        text = value
    else:
        text = ""
    folded = " ".join(text.casefold().split())
    if any(marker in folded for marker in (
        "usage limit",
        "quota exceeded",
        "quota exhausted",
        "weekly limit",
        "5-hour limit",
        "5 hour limit",
    )):
        return AgentProviderLimitError("provider_quota_exhausted", "Provider usage quota is exhausted")
    if any(marker in folded for marker in ("rate limit", "too many requests")):
        return AgentProviderLimitError("provider_rate_limit", "Provider rate limit is active")
    # Capacity exhaustion does not prove the account quota was consumed. Keep a
    # distinct reason while using the same partial-work preservation and handoff.
    if "resource_exhausted" in structured_values or re.search(r"\bresource_exhausted\b", folded):
        return AgentProviderLimitError("provider_resource_exhausted", "Provider resources are exhausted")
    return None


@dataclass(frozen=True)
class AgentRunResult:
    """Explicit Adapter outcome metadata that must not be inferred from return timing."""

    summary: str | None
    work_committed: bool = False


@dataclass(frozen=True)
class AgentRunRequest:
    task_id: str
    agent_session_id: str
    resident: str
    provider: str
    prompt: str
    working_dir: Path
    resident_persona: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    read_only: bool = False
    purpose: str = "work"
    conversation_id: str | None = None
    provider_session_id: str | None = None


RESIDENT_TASK_PERSONA_LIMIT = 16_000


def resident_task_identity_instruction(request: AgentRunRequest) -> str:
    """Keep one Resident identity across chat and Agent work without roleplaying code."""
    if request.purpose not in {"work", "integrated_audit"}:
        return ""
    persona = request.resident_persona.strip() if isinstance(request.resident_persona, str) else ""
    if not persona:
        return ""
    persona = persona[:RESIDENT_TASK_PERSONA_LIMIT]
    return (
        f"Nirai Resident identity: You are Resident {request.resident}. "
        "Remain consistent with this Resident when writing human-facing progress, questions, and final reports. "
        "Do not apply character speech to source code, identifiers, commands, file contents, test names, or technical facts. "
        "Persona never overrides the Master task, safety boundaries, or completion requirements.\n"
        f"Resident persona:\n{persona}"
    )


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
    ) -> str | None | AgentRunResult: ...

    async def cancel(self, agent_session_id: str) -> bool: ...
