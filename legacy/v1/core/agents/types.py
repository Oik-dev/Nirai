from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal, get_args


AgentEventType = Literal[
    "assistant_message",
    "status_message",
    "tool_call",
    "command_execution",
    "file_change",
    "diff",
    "approval_request",
    "question_request",
    "plan",
    "todo_update",
    "subagent_update",
    "artifact",
    "run_state",
    "error",
]

AgentRunState = Literal[
    "queued",
    "starting",
    "running",
    "waiting_for_master",
    "cancelling",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]

AGENT_RUN_STATES: frozenset[str] = frozenset(get_args(AgentRunState))

TERMINAL_RUN_STATES: frozenset[str] = frozenset({"completed", "failed", "cancelled", "interrupted"})


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class AgentEvent:
    seq: int
    ts: str
    task_id: str
    agent_session_id: str
    resident: str
    provider: str
    type: AgentEventType
    payload: dict[str, Any] = field(default_factory=dict)

    def to_protocol(self) -> dict[str, Any]:
        return {
            "event_id": f"AE-{self.agent_session_id}-{self.seq:06d}",
            "seq": self.seq,
            "ts": self.ts,
            "task_id": self.task_id,
            "agent_session_id": self.agent_session_id,
            "resident": self.resident,
            "provider": self.provider,
            "type": self.type,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True)
class AgentSessionSnapshot:
    task_id: str
    agent_session_id: str
    resident: str
    provider: str
    working_dir: str
    run_state: AgentRunState
    started_at: str
    updated_at: str
    provider_session_id: str | None = None
    provider_turn_id: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    read_only: bool = False
    purpose: str = "work"
    conversation_id: str | None = None
    pending_request_id: str | None = None
    pending_request_kind: str | None = None
    pending_request_payload: dict[str, Any] | None = None
    origin_chat_session_id: str | None = None
    workflow_id: str | None = None
    task_phase: str | None = None
    result_reported: bool = False
    result_notified: bool = False
    recovered_by_agent_session_id: str | None = None
    recovery_source_agent_session_id: str | None = None
    recovery_action: str | None = None
    interruption_reason: str | None = None
    partial_work_path: str | None = None
    last_event_seq: int = 0
    final_summary: str | None = None

    def with_updates(self, **changes: Any) -> AgentSessionSnapshot:
        changes.setdefault("updated_at", utc_now_iso())
        return replace(self, **changes)

    def with_cleared_pending_request(self, **changes: Any) -> AgentSessionSnapshot:
        """Close all three durable Master-input fields in one value update."""
        return self.with_updates(
            pending_request_id=None,
            pending_request_kind=None,
            pending_request_payload=None,
            **changes,
        )

    def to_protocol(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "agent_session_id": self.agent_session_id,
            "resident": self.resident,
            "provider": self.provider,
            "working_dir": self.working_dir,
            "run_state": self.run_state,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "provider_session_id": self.provider_session_id,
            "provider_turn_id": self.provider_turn_id,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "read_only": self.read_only,
            "purpose": self.purpose,
            "conversation_id": self.conversation_id,
            "pending_request_id": self.pending_request_id,
            "pending_request_kind": self.pending_request_kind,
            "pending_request_payload": (
                dict(self.pending_request_payload)
                if self.pending_request_payload is not None
                else None
            ),
            "origin_chat_session_id": self.origin_chat_session_id,
            "workflow_id": self.workflow_id,
            "task_phase": self.task_phase,
            "result_reported": self.result_reported,
            "result_notified": self.result_notified,
            "recovered_by_agent_session_id": self.recovered_by_agent_session_id,
            "recovery_source_agent_session_id": self.recovery_source_agent_session_id,
            "recovery_action": self.recovery_action,
            "interruption_reason": self.interruption_reason,
            "partial_work_path": self.partial_work_path,
            "last_event_seq": self.last_event_seq,
            "final_summary": self.final_summary,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> AgentSessionSnapshot:
        return cls(
            task_id=str(value["task_id"]),
            agent_session_id=str(value["agent_session_id"]),
            resident=str(value["resident"]),
            provider=str(value["provider"]),
            working_dir=str(value["working_dir"]),
            run_state=value["run_state"],
            started_at=str(value["started_at"]),
            updated_at=str(value["updated_at"]),
            provider_session_id=_optional_str(value.get("provider_session_id")),
            provider_turn_id=_optional_str(value.get("provider_turn_id")),
            model=_optional_str(value.get("model")),
            reasoning_effort=_optional_str(value.get("reasoning_effort")),
            read_only=value.get("read_only") is True,
            purpose=_agent_purpose_from_dict(value),
            conversation_id=_optional_str(value.get("conversation_id")),
            pending_request_id=_optional_str(value.get("pending_request_id")),
            pending_request_kind=_optional_str(value.get("pending_request_kind")),
            pending_request_payload=_optional_dict(value.get("pending_request_payload")),
            origin_chat_session_id=_optional_str(value.get("origin_chat_session_id")),
            workflow_id=_optional_str(value.get("workflow_id")),
            task_phase=_optional_str(value.get("task_phase")),
            result_reported=value.get("result_reported") is True,
            # Pre-field snapshots were produced before durable World-notification
            # tracking existed. Treat already-reported legacy terminal results as
            # already notified to avoid replaying historical tasks after upgrade.
            result_notified=(
                value.get("result_notified") is True
                if "result_notified" in value
                else value.get("result_reported") is True
            ),
            recovered_by_agent_session_id=_optional_str(value.get("recovered_by_agent_session_id")),
            recovery_source_agent_session_id=_optional_str(value.get("recovery_source_agent_session_id")),
            recovery_action=_optional_str(value.get("recovery_action")),
            interruption_reason=_optional_str(value.get("interruption_reason")),
            partial_work_path=_optional_str(value.get("partial_work_path")),
            last_event_seq=int(value.get("last_event_seq", 0)),
            final_summary=_optional_str(value.get("final_summary")),
        )


def _agent_purpose_from_dict(value: dict[str, Any]) -> str:
    explicit = _optional_str(value.get("purpose"))
    if explicit is not None:
        return explicit
    if value.get("read_only") is not True:
        return "work"
    task_id = str(value.get("task_id", ""))
    return "review" if task_id.startswith("HR-") else "consult"


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _optional_dict(value: object) -> dict[str, Any] | None:
    return dict(value) if isinstance(value, dict) else None
