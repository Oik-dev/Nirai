from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Literal


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
    pending_request_id: str | None = None
    pending_request_kind: str | None = None
    origin_chat_session_id: str | None = None
    task_phase: str | None = None
    result_reported: bool = False
    result_notified: bool = False
    last_event_seq: int = 0
    final_summary: str | None = None

    def with_updates(self, **changes: Any) -> AgentSessionSnapshot:
        changes.setdefault("updated_at", utc_now_iso())
        return replace(self, **changes)

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
            "pending_request_id": self.pending_request_id,
            "pending_request_kind": self.pending_request_kind,
            "origin_chat_session_id": self.origin_chat_session_id,
            "task_phase": self.task_phase,
            "result_reported": self.result_reported,
            "result_notified": self.result_notified,
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
            pending_request_id=_optional_str(value.get("pending_request_id")),
            pending_request_kind=_optional_str(value.get("pending_request_kind")),
            origin_chat_session_id=_optional_str(value.get("origin_chat_session_id")),
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
            last_event_seq=int(value.get("last_event_seq", 0)),
            final_summary=_optional_str(value.get("final_summary")),
        )


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
