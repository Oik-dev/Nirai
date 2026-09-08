from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4


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

    def with_updates(self, **changes: Any) -> ConversationRecord:
        changes.setdefault("updated_at", utc_now_iso())
        return replace(self, **changes)

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
        )


class ConversationStore:
    """Durable Nirai-owned conversation state.

    Provider sessions are deliberately not durable conversation identity. A
    provider may be started afresh for every turn; this store is the source of
    truth that lets a conversation continue across provider process exits and
    Core restarts.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.conversations_root = (self.root / "runtime" / "conversations").resolve()
        try:
            self.conversations_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConversationRuntimeError("Conversation store could not be prepared") from exc
        self.recover_interrupted_turns()

    def create(
        self,
        *,
        participant_kind: str,
        participant: str,
        mode: str,
        target_name: str | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> ConversationRecord:
        if participant_kind not in CONVERSATION_PARTICIPANT_KINDS:
            raise ConversationRuntimeError("Conversation participant_kind must be resident or provider")
        cleaned_participant = participant.strip()
        if not cleaned_participant or len(cleaned_participant) > 200:
            raise ConversationRuntimeError("Conversation participant is invalid")
        if mode not in CONVERSATION_MODES:
            raise ConversationRuntimeError("Conversation mode is invalid")
        now = utc_now_iso()
        record = ConversationRecord(
            conversation_id=f"{CONVERSATION_ID_PREFIX}{uuid4()}",
            participant_kind=participant_kind,  # type: ignore[arg-type]
            participant=cleaned_participant,
            mode=mode,  # type: ignore[arg-type]
            state="open",
            turn_state="idle",
            created_at=now,
            updated_at=now,
            target_name=_clean_optional(target_name, 200, "Conversation target"),
            model=_clean_optional(model, 200, "Conversation model"),
            reasoning_effort=_clean_optional(reasoning_effort, 50, "Conversation reasoning effort"),
        )
        self.save(record)
        return record

    def load(self, conversation_id: str) -> ConversationRecord:
        path = self._path(conversation_id)
        try:
            size = path.stat().st_size
            if size > CONVERSATION_FILE_LIMIT_BYTES:
                raise ConversationRuntimeError("Conversation record exceeds the safe file-size limit")
            raw = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ConversationRuntimeError(f"Unknown Conversation: {conversation_id}") from exc
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise ConversationRuntimeError(f"Conversation record could not be read: {conversation_id}") from exc
        if not isinstance(raw, dict):
            raise ConversationRuntimeError("Conversation record root must be an object")
        record = ConversationRecord.from_dict(raw)
        if record.conversation_id != conversation_id:
            raise ConversationRuntimeError("Conversation record id does not match its filename")
        return record

    def save(self, record: ConversationRecord) -> None:
        path = self._path(record.conversation_id)
        payload = json.dumps(record.to_dict(), ensure_ascii=False, indent=2) + "\n"
        encoded = payload.encode("utf-8")
        if len(encoded) > CONVERSATION_FILE_LIMIT_BYTES:
            raise ConversationRuntimeError("Conversation record exceeds the safe file-size limit")
        temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
        try:
            with temporary.open("wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ConversationRuntimeError("Conversation record could not be saved") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def append_message(
        self,
        record: ConversationRecord,
        *,
        role: Literal["holo", "participant"],
        sender: str,
        text: str,
    ) -> ConversationRecord:
        updated, journal_checkpoint = self._stage_message(
            record,
            role=role,
            sender=sender,
            text=text,
        )
        try:
            self.save(updated)
        except Exception:
            self._rollback_staged_journal(record.conversation_id, journal_checkpoint)
            raise
        return updated

    def full_messages(self, record: ConversationRecord) -> tuple[ConversationMessage, ...]:
        persisted_count = record.next_message_seq - 1
        if persisted_count <= 0:
            return ()
        by_seq: dict[int, ConversationMessage] = {}
        journal_path = self._journal_path(record.conversation_id)
        try:
            if journal_path.is_file():
                with journal_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        if not line.endswith("\n"):
                            # An incomplete append tail is not a committed
                            # message and can be ignored after a crash.
                            break
                        stripped = line.strip()
                        if not stripped:
                            continue
                        value = json.loads(stripped)
                        if not isinstance(value, dict):
                            raise ConversationRuntimeError("Conversation journal item is invalid")
                        message = ConversationMessage.from_dict(value)
                        if message.seq < record.next_message_seq:
                            by_seq[message.seq] = message
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConversationRuntimeError("Conversation journal could not be read") from exc
        for message in record.messages:
            if message.seq < record.next_message_seq:
                by_seq[message.seq] = message
        expected = list(range(1, record.next_message_seq))
        if sorted(by_seq) != expected:
            raise ConversationRuntimeError(
                "Full Conversation recovery transcript is not available from the durable journal"
            )
        return tuple(by_seq[seq] for seq in expected)

    def clear_provider_session(self, record: ConversationRecord) -> ConversationRecord:
        if record.provider_session_id is None:
            return record
        updated = record.with_updates(provider_session_id=None)
        self.save(updated)
        return updated

    def start_turn(
        self,
        record: ConversationRecord,
        *,
        holo_sender: str,
        text: str,
    ) -> ConversationRecord:
        if record.state != "open":
            raise ConversationRuntimeError("Conversation is closed")
        if record.turn_state == "running":
            raise ConversationRuntimeError("Conversation already has a running turn")
        updated, journal_checkpoint = self._stage_message(
            record,
            role="holo",
            sender=holo_sender,
            text=text,
        )
        try:
            turn_id = f"{CONVERSATION_TURN_ID_PREFIX}{uuid4()}"
            updated = updated.with_updates(
                turn_state="running",
                active_turn_id=turn_id,
                active_agent_session_id=None,
                active_invocation_id=None,
                last_error=None,
                verdict=None,
            )
            # Message + running state become visible in one atomic snapshot
            # replace. The journal append is write-ahead evidence only until
            # this save commits.
            self.save(updated)
        except Exception:
            self._rollback_staged_journal(record.conversation_id, journal_checkpoint)
            raise
        return updated

    def set_active_agent_session(
        self,
        record: ConversationRecord,
        agent_session_id: str,
    ) -> ConversationRecord:
        if record.turn_state != "running":
            raise ConversationRuntimeError("Conversation turn is no longer running")
        updated = record.with_updates(active_agent_session_id=agent_session_id)
        self.save(updated)
        return updated

    def set_active_invocation(
        self,
        record: ConversationRecord,
        invocation_id: str,
    ) -> ConversationRecord:
        if record.turn_state != "running":
            raise ConversationRuntimeError("Conversation turn is no longer running")
        updated = record.with_updates(active_invocation_id=invocation_id)
        self.save(updated)
        return updated

    def finish_turn(
        self,
        record: ConversationRecord,
        *,
        sender: str,
        text: str,
        agent_session_id: str | None = None,
        provider_session_id: str | None = None,
        verdict: str | None = None,
    ) -> ConversationRecord:
        if record.turn_state != "running":
            raise ConversationRuntimeError("Conversation turn is no longer running")
        updated, journal_checkpoint = self._stage_message(
            record,
            role="participant",
            sender=sender,
            text=text,
        )
        try:
            updated = updated.with_updates(
                turn_state="completed",
                last_turn_id=record.active_turn_id,
                last_agent_session_id=agent_session_id or record.active_agent_session_id,
                provider_session_id=(
                    _clean_optional(provider_session_id, 500, "Provider session id")
                    or record.provider_session_id
                ),
                active_turn_id=None,
                active_agent_session_id=None,
                active_invocation_id=None,
                last_error=None,
                verdict=_clean_optional(verdict, 50, "Conversation verdict"),
            )
            # Reply + completed state commit together; a crash before this
            # replace leaves only an uncommitted journal tail, repaired at boot.
            self.save(updated)
        except Exception:
            self._rollback_staged_journal(record.conversation_id, journal_checkpoint)
            raise
        return updated

    def finish_without_message(
        self,
        record: ConversationRecord,
        *,
        agent_session_id: str | None = None,
        provider_session_id: str | None = None,
        verdict: str | None = None,
    ) -> ConversationRecord:
        if record.turn_state != "running":
            raise ConversationRuntimeError("Conversation turn is no longer running")
        updated = record.with_updates(
            turn_state="completed",
            last_turn_id=record.active_turn_id,
            last_agent_session_id=agent_session_id or record.active_agent_session_id,
            provider_session_id=(
                _clean_optional(provider_session_id, 500, "Provider session id")
                or record.provider_session_id
            ),
            active_turn_id=None,
            active_agent_session_id=None,
            active_invocation_id=None,
            last_error=None,
            verdict=_clean_optional(verdict, 50, "Conversation verdict"),
        )
        self.save(updated)
        return updated

    def end_turn(
        self,
        record: ConversationRecord,
        turn_state: Literal["failed", "cancelled", "interrupted"],
        *,
        error: str | None = None,
        agent_session_id: str | None = None,
    ) -> ConversationRecord:
        if turn_state not in {"failed", "cancelled", "interrupted"}:
            raise ConversationRuntimeError("Conversation terminal turn state is invalid")
        updated = record.with_updates(
            turn_state=turn_state,
            last_turn_id=record.active_turn_id,
            last_agent_session_id=agent_session_id or record.active_agent_session_id,
            active_turn_id=None,
            active_agent_session_id=None,
            active_invocation_id=None,
            last_error=_clean_optional(error, 2_000, "Conversation error"),
            verdict=None,
        )
        self.save(updated)
        return updated

    def close(self, record: ConversationRecord) -> ConversationRecord:
        if record.turn_state == "running":
            raise ConversationRuntimeError("Running Conversation must be cancelled before close")
        if record.state == "closed":
            return record
        updated = record.with_updates(state="closed")
        self.save(updated)
        return updated

    def recover_interrupted_turns(self) -> int:
        recovered = 0
        try:
            paths = tuple(self.conversations_root.glob(f"{CONVERSATION_ID_PREFIX}*.json"))
        except OSError:
            return 0
        for path in paths:
            try:
                record = self.load(path.stem)
            except ConversationRuntimeError:
                continue
            try:
                self._trim_uncommitted_journal_tail(record)
            except ConversationRuntimeError:
                # The snapshot remains authoritative for turn recovery. A
                # malformed historical journal will still be reported if full
                # transcript reconstruction is later requested.
                pass
            if record.turn_state != "running":
                continue
            try:
                recovered_record = self.end_turn(
                    record,
                    "interrupted",
                    error="Core restarted before the Conversation turn completed.",
                )
                if recovered_record.participant_kind == "provider":
                    # A provider may have consumed some or all of the in-flight
                    # prompt before Core died. Its native context is ambiguous;
                    # force the next turn to rebuild from Nirai's journal.
                    self.clear_provider_session(recovered_record)
            except ConversationRuntimeError:
                continue
            recovered += 1
        return recovered

    def _ensure_journal_baseline(self, record: ConversationRecord) -> None:
        path = self._journal_path(record.conversation_id)
        try:
            if path.is_file() and path.stat().st_size > 0:
                return
        except OSError as exc:
            raise ConversationRuntimeError("Conversation journal could not be inspected") from exc
        temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                for message in record.messages:
                    json.dump(message.to_dict(), handle, ensure_ascii=False, separators=(",", ":"))
                    handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        except OSError as exc:
            raise ConversationRuntimeError("Conversation journal baseline could not be saved") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    def _stage_message(
        self,
        record: ConversationRecord,
        *,
        role: Literal["holo", "participant"],
        sender: str,
        text: str,
    ) -> tuple[ConversationRecord, int]:
        cleaned = text.strip()
        if not cleaned:
            raise ConversationRuntimeError("Conversation message must not be empty")
        if len(cleaned) > CONVERSATION_TEXT_LIMIT:
            raise ConversationRuntimeError(
                f"Conversation message exceeds the {CONVERSATION_TEXT_LIMIT} character limit"
            )
        cleaned_sender = sender.strip()
        if not cleaned_sender or len(cleaned_sender) > 200:
            raise ConversationRuntimeError("Conversation message sender is invalid")
        message = ConversationMessage(
            seq=record.next_message_seq,
            ts=utc_now_iso(),
            role=role,
            sender=cleaned_sender,
            text=cleaned,
        )
        self._ensure_journal_baseline(record)
        journal_checkpoint = self._append_journal_message(record.conversation_id, message)
        messages = (*record.messages, message)
        if len(messages) > CONVERSATION_MESSAGE_LIMIT:
            messages = messages[-CONVERSATION_MESSAGE_LIMIT:]
        return (
            record.with_updates(
                messages=tuple(messages),
                next_message_seq=record.next_message_seq + 1,
            ),
            journal_checkpoint,
        )

    def _append_journal_message(self, conversation_id: str, message: ConversationMessage) -> int:
        path = self._journal_path(conversation_id)
        payload = (json.dumps(message.to_dict(), ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        try:
            with path.open("ab") as handle:
                checkpoint = handle.tell()
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            return checkpoint
        except OSError as exc:
            raise ConversationRuntimeError("Conversation journal message could not be saved") from exc

    def _rollback_staged_journal(self, conversation_id: str, checkpoint: int) -> None:
        path = self._journal_path(conversation_id)
        try:
            with path.open("r+b") as handle:
                handle.truncate(checkpoint)
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            # Startup reconciliation trims any write-ahead tail not referenced
            # by next_message_seq, so do not mask the original snapshot error.
            return

    def _trim_uncommitted_journal_tail(self, record: ConversationRecord) -> None:
        path = self._journal_path(record.conversation_id)
        if not path.is_file():
            return
        try:
            with path.open("r+b") as handle:
                while True:
                    line_start = handle.tell()
                    encoded = handle.readline()
                    if not encoded:
                        return
                    if not encoded.endswith(b"\n"):
                        handle.seek(line_start)
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
                        return
                    stripped = encoded.strip()
                    if not stripped:
                        continue
                    try:
                        value = json.loads(stripped.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise ConversationRuntimeError("Conversation journal is malformed before its tail") from exc
                    if not isinstance(value, dict):
                        raise ConversationRuntimeError("Conversation journal item is invalid")
                    message = ConversationMessage.from_dict(value)
                    if message.seq >= record.next_message_seq:
                        handle.seek(line_start)
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
                        return
        except OSError as exc:
            raise ConversationRuntimeError("Conversation journal could not be reconciled") from exc

    def _journal_path(self, conversation_id: str) -> Path:
        return self._path(conversation_id).with_suffix(".jsonl")

    def _path(self, conversation_id: str) -> Path:
        cleaned = conversation_id.strip()
        if (
            not cleaned.startswith(CONVERSATION_ID_PREFIX)
            or len(cleaned) > 200
            or any(character in cleaned for character in "/\\")
            or cleaned in {".", ".."}
        ):
            raise ConversationRuntimeError("Conversation id is invalid")
        candidate = (self.conversations_root / f"{cleaned}.json").resolve()
        try:
            candidate.relative_to(self.conversations_root)
        except ValueError as exc:
            raise ConversationRuntimeError("Conversation path escaped its runtime root") from exc
        return candidate


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
