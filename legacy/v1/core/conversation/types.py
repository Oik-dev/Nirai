"""Conversation values and serialized contract, separate from durable storage."""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Literal


ConversationMode = Literal["talk", "brainstorm", "consult", "review"]
ConversationParticipantKind = Literal["resident", "provider"]
ConversationState = Literal["open", "closed"]
ConversationTurnState = Literal[
    "idle",
    "running",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]

CONVERSATION_MODES: frozenset[str] = frozenset({"talk", "brainstorm", "consult", "review"})
CONVERSATION_PARTICIPANT_KINDS: frozenset[str] = frozenset({"resident", "provider"})
CONVERSATION_TEXT_LIMIT = 32_000
CONVERSATION_MESSAGE_LIMIT = 100
CONVERSATION_FILE_LIMIT_BYTES = 2 * 1024 * 1024
CONVERSATION_ID_PREFIX = "CV-"
CONVERSATION_TURN_ID_PREFIX = "CT-"


class ConversationRuntimeError(RuntimeError):
    pass


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class ConversationMessage:
    seq: int
    ts: str
    role: Literal["holo", "participant"]
    sender: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "ts": self.ts,
            "role": self.role,
            "sender": self.sender,
            "text": self.text,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ConversationMessage:
        seq = value.get("seq")
        ts = value.get("ts")
        role = value.get("role")
        sender = value.get("sender")
        text = value.get("text")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
            raise ConversationRuntimeError("Conversation message seq is invalid")
        if not isinstance(ts, str) or not ts:
            raise ConversationRuntimeError("Conversation message timestamp is invalid")
        if role not in {"holo", "participant"}:
            raise ConversationRuntimeError("Conversation message role is invalid")
        if not isinstance(sender, str) or not sender.strip() or len(sender) > 200:
            raise ConversationRuntimeError("Conversation message sender is invalid")
        if not isinstance(text, str) or not text.strip() or len(text) > CONVERSATION_TEXT_LIMIT:
            raise ConversationRuntimeError("Conversation message text is invalid")
        return cls(seq=seq, ts=ts, role=role, sender=sender, text=text)


@dataclass(frozen=True)
class ConversationRecord:
    conversation_id: str
    participant_kind: ConversationParticipantKind
    participant: str
    mode: ConversationMode
    state: ConversationState
    turn_state: ConversationTurnState
    created_at: str
    updated_at: str
    messages: tuple[ConversationMessage, ...] = ()
    next_message_seq: int = 1
    target_name: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    provider_session_id: str | None = None
    active_turn_id: str | None = None
    active_agent_session_id: str | None = None
    active_invocation_id: str | None = None
    last_turn_id: str | None = None
    last_agent_session_id: str | None = None
    last_error: str | None = None
    verdict: str | None = None
    public_session_id: str | None = None

    def with_updates(self, **changes: Any) -> ConversationRecord:
        changes.setdefault("updated_at", utc_now_iso())
        return replace(self, **changes)

    def with_cleared_active_turn(self, **changes: Any) -> ConversationRecord:
        """Clear the turn and both execution handles in the same value update."""
        return self.with_updates(
            active_turn_id=None,
            active_agent_session_id=None,
            active_invocation_id=None,
            **changes,
        )

    def to_protocol(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "participant_kind": self.participant_kind,
            "participant": self.participant,
            "mode": self.mode,
            "state": self.state,
            "turn_state": self.turn_state,
            "target": self.target_name,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "provider_session_id": self.provider_session_id,
            "active_turn_id": self.active_turn_id,
            "active_agent_session_id": self.active_agent_session_id,
            "last_turn_id": self.last_turn_id,
            "last_agent_session_id": self.last_agent_session_id,
            "last_error": self.last_error,
            "verdict": self.verdict,
            "public_session_id": self.public_session_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": [message.to_dict() for message in self.messages],
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1,
            **self.to_protocol(),
            "active_invocation_id": self.active_invocation_id,
            "next_message_seq": self.next_message_seq,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> ConversationRecord:
        if value.get("version") != 1:
            raise ConversationRuntimeError("Unsupported Conversation record version")
        conversation_id = value.get("conversation_id")
        participant_kind = value.get("participant_kind")
        participant = value.get("participant")
        mode = value.get("mode")
        state = value.get("state")
        turn_state = value.get("turn_state")
        created_at = value.get("created_at")
        updated_at = value.get("updated_at")
        raw_messages = value.get("messages")
        next_message_seq = value.get("next_message_seq")
        if not isinstance(conversation_id, str) or not conversation_id.startswith(CONVERSATION_ID_PREFIX):
            raise ConversationRuntimeError("Conversation id is invalid")
        if participant_kind not in CONVERSATION_PARTICIPANT_KINDS:
            raise ConversationRuntimeError("Conversation participant kind is invalid")
        if not isinstance(participant, str) or not participant.strip() or len(participant) > 200:
            raise ConversationRuntimeError("Conversation participant is invalid")
        if mode not in CONVERSATION_MODES:
            raise ConversationRuntimeError("Conversation mode is invalid")
        if state not in {"open", "closed"}:
            raise ConversationRuntimeError("Conversation state is invalid")
        if turn_state not in {"idle", "running", "completed", "failed", "cancelled", "interrupted"}:
            raise ConversationRuntimeError("Conversation turn state is invalid")
        if not isinstance(created_at, str) or not created_at or not isinstance(updated_at, str) or not updated_at:
            raise ConversationRuntimeError("Conversation timestamps are invalid")
        if not isinstance(raw_messages, list) or len(raw_messages) > CONVERSATION_MESSAGE_LIMIT:
            raise ConversationRuntimeError("Conversation messages are invalid")
        messages = tuple(
            ConversationMessage.from_dict(item)
            for item in raw_messages
            if isinstance(item, dict)
        )
        if len(messages) != len(raw_messages):
            raise ConversationRuntimeError("Conversation messages contain an invalid item")
        if any(current.seq >= following.seq for current, following in zip(messages, messages[1:])):
            raise ConversationRuntimeError("Conversation message sequence is invalid")
        if not isinstance(next_message_seq, int) or isinstance(next_message_seq, bool) or next_message_seq < 1:
            raise ConversationRuntimeError("Conversation next_message_seq is invalid")
        if messages and next_message_seq <= messages[-1].seq:
            raise ConversationRuntimeError("Conversation next_message_seq is stale")

        return cls(
            conversation_id=conversation_id,
            participant_kind=participant_kind,
            participant=participant.strip(),
            mode=mode,
            state=state,
            turn_state=turn_state,
            created_at=created_at,
            updated_at=updated_at,
            messages=messages,
            next_message_seq=next_message_seq,
            target_name=_optional_text(value.get("target"), 200),
            model=_optional_text(value.get("model"), 200),
            reasoning_effort=_optional_text(value.get("reasoning_effort"), 50),
            provider_session_id=_optional_text(value.get("provider_session_id"), 500),
            active_turn_id=_optional_text(value.get("active_turn_id"), 200),
            active_agent_session_id=_optional_text(value.get("active_agent_session_id"), 200),
            active_invocation_id=_optional_text(value.get("active_invocation_id"), 200),
            last_turn_id=_optional_text(value.get("last_turn_id"), 200),
            last_agent_session_id=_optional_text(value.get("last_agent_session_id"), 200),
            last_error=_optional_text(value.get("last_error"), 2_000),
            verdict=_optional_text(value.get("verdict"), 50),
            public_session_id=_optional_text(value.get("public_session_id"), 200),
        )


def _clean_optional(value: str | None, limit: int, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConversationRuntimeError(f"{label} must be a string")
    cleaned = value.strip()
    if not cleaned:
        return None
    if len(cleaned) > limit or any(ord(character) < 32 for character in cleaned):
        raise ConversationRuntimeError(f"{label} is invalid")
    return cleaned


def _optional_text(value: object, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ConversationRuntimeError("Conversation optional text field is invalid")
    return value
