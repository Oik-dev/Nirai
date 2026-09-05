from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


TASK_QUEUE_FORMAT_VERSION = 1
TASK_QUEUE_TEXT_LIMIT = 32_000
TASK_QUEUE_PENDING_LIMIT = 32
TASK_QUEUE_FILE_SIZE_LIMIT = 8 * 1024 * 1024


class TaskQueueStoreError(Exception):
    pass


@dataclass(frozen=True)
class QueuedTaskRecord:
    task_id: str
    text: str
    message_id: str | None
    origin_session_id: str
    working_dir: str
    task_metadata_dir: str
    target_name: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "text": self.text,
            "message_id": self.message_id,
            "origin_session_id": self.origin_session_id,
            "working_dir": self.working_dir,
            "task_metadata_dir": self.task_metadata_dir,
            "target_name": self.target_name,
        }

    @classmethod
    def from_json(cls, payload: object) -> "QueuedTaskRecord":
        if not isinstance(payload, dict):
            raise TaskQueueStoreError("Task Queue entry must be an object")

        def required_string(key: str) -> str:
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise TaskQueueStoreError(f"Task Queue entry {key} must be a non-empty string")
            return value

        text = required_string("text")
        if len(text) > TASK_QUEUE_TEXT_LIMIT:
            raise TaskQueueStoreError("Task Queue entry text exceeds the persisted limit")

        message_id = payload.get("message_id")
        if message_id is not None and not isinstance(message_id, str):
            raise TaskQueueStoreError("Task Queue entry message_id must be a string or null")
        target_name = payload.get("target_name")
        if target_name is not None and (not isinstance(target_name, str) or not target_name.strip()):
            raise TaskQueueStoreError("Task Queue entry target_name must be a non-empty string or null")

        return cls(
            task_id=required_string("task_id"),
            text=text,
            message_id=message_id,
            origin_session_id=required_string("origin_session_id"),
            working_dir=required_string("working_dir"),
            task_metadata_dir=required_string("task_metadata_dir"),
            target_name=target_name,
        )


@dataclass(frozen=True)
class TaskQueueState:
    active: QueuedTaskRecord | None
    pending: tuple[QueuedTaskRecord, ...]


class TaskQueueStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "runtime" / "task_queue.json"

    def load(self) -> TaskQueueState:
        if not self.path.exists():
            return TaskQueueState(active=None, pending=())
        try:
            if self.path.stat().st_size > TASK_QUEUE_FILE_SIZE_LIMIT:
                raise TaskQueueStoreError("Task Queue state exceeds the persisted file size limit")
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except TaskQueueStoreError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
            raise TaskQueueStoreError("Task Queue state could not be read") from exc
        if not isinstance(raw, dict) or raw.get("format_version") != TASK_QUEUE_FORMAT_VERSION:
            raise TaskQueueStoreError("Task Queue state has an unsupported format")
        raw_active = raw.get("active")
        active = None if raw_active is None else QueuedTaskRecord.from_json(raw_active)
        raw_pending = raw.get("pending")
        if not isinstance(raw_pending, list):
            raise TaskQueueStoreError("Task Queue pending state must be an array")
        if len(raw_pending) > TASK_QUEUE_PENDING_LIMIT:
            raise TaskQueueStoreError("Task Queue pending state exceeds the configured limit")
        pending = tuple(QueuedTaskRecord.from_json(item) for item in raw_pending)
        ids = [record.task_id for record in pending]
        if active is not None:
            ids.append(active.task_id)
        if len(ids) != len(set(ids)):
            raise TaskQueueStoreError("Task Queue contains duplicate task_id values")
        return TaskQueueState(active=active, pending=pending)

    def save(
        self,
        *,
        active: QueuedTaskRecord | None,
        pending: Iterable[QueuedTaskRecord],
    ) -> None:
        pending_tuple = tuple(
            QueuedTaskRecord.from_json(record.to_json())
            for record in pending
        )
        active = None if active is None else QueuedTaskRecord.from_json(active.to_json())
        if len(pending_tuple) > TASK_QUEUE_PENDING_LIMIT:
            raise TaskQueueStoreError("Task Queue pending state exceeds the configured limit")
        ids = [record.task_id for record in pending_tuple]
        if active is not None:
            ids.append(active.task_id)
        if len(ids) != len(set(ids)):
            raise TaskQueueStoreError("Task Queue contains duplicate task_id values")
        payload = {
            "format_version": TASK_QUEUE_FORMAT_VERSION,
            "active": active.to_json() if active is not None else None,
            "pending": [record.to_json() for record in pending_tuple],
        }
        try:
            serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
            serialized_size = len(serialized.encode("utf-8"))
        except UnicodeError as exc:
            raise TaskQueueStoreError("Task Queue state could not be encoded") from exc
        if serialized_size > TASK_QUEUE_FILE_SIZE_LIMIT:
            raise TaskQueueStoreError("Task Queue state exceeds the persisted file size limit")
        temporary = self.path.with_name(f"{self.path.name}.{uuid4()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(
                serialized,
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            raise TaskQueueStoreError("Task Queue state could not be saved") from exc
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
