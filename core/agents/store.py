from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .types import AgentEvent, AgentEventType, AgentSessionSnapshot, utc_now_iso


class AgentSessionStoreError(RuntimeError):
    pass


class AgentSessionStore:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.sessions_root = self.root / "runtime" / "agent_sessions"
        self.sessions_root.mkdir(parents=True, exist_ok=True)

    def create(self, snapshot: AgentSessionSnapshot) -> AgentSessionSnapshot:
        directory = self._session_dir(snapshot.agent_session_id)
        if directory.exists():
            raise AgentSessionStoreError(f"Agent session already exists: {snapshot.agent_session_id}")
        directory.mkdir(parents=True)
        self.save_snapshot(snapshot)
        return snapshot

    def save_snapshot(self, snapshot: AgentSessionSnapshot) -> None:
        directory = self._session_dir(snapshot.agent_session_id)
        try:
            directory.mkdir(parents=True, exist_ok=True)
            _atomic_write_json(directory / "session.json", snapshot.to_protocol())
        except OSError as exc:
            raise AgentSessionStoreError(
                f"Agent session could not be saved: {snapshot.agent_session_id}"
            ) from exc

    def load_snapshot(self, agent_session_id: str) -> AgentSessionSnapshot:
        path = self._session_dir(agent_session_id) / "session.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentSessionStoreError(f"Agent session could not be read: {agent_session_id}") from exc
        if not isinstance(value, dict):
            raise AgentSessionStoreError(f"Agent session is malformed: {agent_session_id}")
        try:
            snapshot = AgentSessionSnapshot.from_dict(value)
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentSessionStoreError(f"Agent session is malformed: {agent_session_id}") from exc

        # session.json already persists last_event_seq after normal appends. A
        # hard crash can leave only the newest event fsynced, so recovery needs
        # the durable tail sequence, not a full historical events.jsonl scan.
        events = self.read_event_tail(agent_session_id, limit=1)
        event_seq = max(
            (
                int(event["seq"])
                for event in events
                if isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool)
            ),
            default=0,
        )
        if event_seq > snapshot.last_event_seq:
            snapshot = snapshot.with_updates(last_event_seq=event_seq)
            self.save_snapshot(snapshot)
        return snapshot

    def list_snapshots(self) -> list[AgentSessionSnapshot]:
        snapshots: list[AgentSessionSnapshot] = []
        for directory in sorted(self.sessions_root.iterdir()):
            if not directory.is_dir():
                continue
            try:
                snapshots.append(self.load_snapshot(directory.name))
            except AgentSessionStoreError:
                continue
        return sorted(snapshots, key=lambda item: item.updated_at, reverse=True)

    def append_event(
        self,
        snapshot: AgentSessionSnapshot,
        event_type: AgentEventType,
        payload: dict[str, Any],
    ) -> tuple[AgentEvent, AgentSessionSnapshot]:
        path = self._session_dir(snapshot.agent_session_id) / "events.jsonl"
        persisted_seq = self._prepare_event_log_tail_for_append(path)
        event = AgentEvent(
            seq=max(snapshot.last_event_seq, persisted_seq) + 1,
            ts=utc_now_iso(),
            task_id=snapshot.task_id,
            agent_session_id=snapshot.agent_session_id,
            resident=snapshot.resident,
            provider=snapshot.provider,
            type=event_type,
            payload=dict(payload),
        )
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(event.to_protocol(), ensure_ascii=False, separators=(",", ":")))
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise AgentSessionStoreError("Agent event could not be persisted") from exc

        updated = snapshot.with_updates(last_event_seq=event.seq)
        self.save_snapshot(updated)
        return event, updated

    def read_events(self, agent_session_id: str) -> list[dict[str, Any]]:
        path = self._session_dir(agent_session_id) / "events.jsonl"
        if not path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            raw = path.read_bytes()
            lines = raw.splitlines(keepends=True)
            offset = 0
            for index, encoded_line in enumerate(lines):
                stripped = encoded_line.strip()
                if not stripped:
                    offset += len(encoded_line)
                    continue
                try:
                    line = stripped.decode("utf-8")
                    value = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    if index != len(lines) - 1:
                        raise AgentSessionStoreError("Agent event log is malformed before its tail") from exc
                    with path.open("r+b") as handle:
                        handle.seek(offset)
                        handle.truncate()
                        handle.flush()
                        os.fsync(handle.fileno())
                    break
                if isinstance(value, dict):
                    events.append(value)
                offset += len(encoded_line)
        except OSError as exc:
            raise AgentSessionStoreError("Agent event log could not be read") from exc
        return events

    def read_event_tail(self, agent_session_id: str, *, limit: int) -> list[dict[str, Any]]:
        """Read only the newest bounded event window from the durable JSONL.

        Normal World reconnect/snapshot traffic only needs the recent UI window;
        do not materialize an arbitrarily long session log for every snapshot.
        Full-log parsing remains available through ``read_events`` for startup
        recovery and explicit internal inspection.
        """
        bounded = max(0, int(limit))
        if bounded == 0:
            return []
        path = self._session_dir(agent_session_id) / "events.jsonl"
        if not path.is_file():
            return []
        try:
            size = path.stat().st_size
            if size <= 0:
                return []
            chunk_size = 64 * 1024
            position = size
            chunks: list[bytes] = []
            newline_count = 0
            # One extra newline guarantees the first selected line is complete
            # even when the read window begins in the middle of its predecessor.
            # Count each block once; join only after the bounded window is read.
            with path.open("rb") as handle:
                while position > 0 and newline_count < bounded + 1:
                    read_size = min(chunk_size, position)
                    position -= read_size
                    handle.seek(position)
                    chunk = handle.read(read_size)
                    chunks.append(chunk)
                    newline_count += chunk.count(b"\n")
            buffer = b"".join(reversed(chunks))
            chunks.clear()
            lines = buffer.splitlines()
            selected = lines[-bounded:]
            events: list[dict[str, Any]] = []
            for encoded_line in selected:
                stripped = encoded_line.strip()
                if not stripped:
                    continue
                try:
                    value = json.loads(stripped.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    # Corrupt/incomplete tails are exceptional. Reuse the full
                    # recovery parser once so it can quarantine/trim safely.
                    return self.read_events(agent_session_id)[-bounded:]
                if isinstance(value, dict):
                    events.append(value)
            return events
        except OSError as exc:
            raise AgentSessionStoreError("Agent event tail could not be read") from exc

    def _prepare_event_log_tail_for_append(self, path: Path) -> int:
        """Repair only the tail and return its last durable event sequence.

        Normal appends must be O(1) in session length. A crash can leave one
        incomplete JSON tail, while a valid final JSON may simply be missing its
        newline. Inspect only a bounded tail window, quarantine malformed partial
        bytes, and never rescan the full historical event log on every append.
        """
        if not path.is_file():
            return 0
        try:
            size = path.stat().st_size
            if size <= 0:
                return 0
            tail_window = min(size, 256 * 1024)
            with path.open("r+b") as handle:
                handle.seek(size - tail_window)
                tail = handle.read(tail_window)
                ends_with_newline = tail.endswith(b"\n")
                content = tail[:-1] if ends_with_newline else tail
                line_start_in_tail = content.rfind(b"\n") + 1
                last_line = content[line_start_in_tail:].strip()
                absolute_line_start = size - tail_window + line_start_in_tail
                if not last_line:
                    return 0
                try:
                    value = json.loads(last_line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    if ends_with_newline:
                        raise AgentSessionStoreError(
                            "Agent event log contains a malformed completed tail"
                        ) from exc
                    corrupt = path.with_name(f"{path.name}.corrupt-{uuid4().hex}")
                    corrupt.write_bytes(tail[line_start_in_tail:])
                    handle.seek(absolute_line_start)
                    handle.truncate()
                    handle.flush()
                    os.fsync(handle.fileno())
                    return self._prepare_event_log_tail_for_append(path)
                if not isinstance(value, dict):
                    raise AgentSessionStoreError("Agent event log tail is not an object")
                seq = value.get("seq")
                if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
                    raise AgentSessionStoreError("Agent event log tail sequence is invalid")
                if not ends_with_newline:
                    handle.seek(0, os.SEEK_END)
                    handle.write(b"\n")
                    handle.flush()
                    os.fsync(handle.fileno())
                return seq
        except OSError as exc:
            raise AgentSessionStoreError("Agent event log tail could not be prepared") from exc

    def _session_dir(self, agent_session_id: str) -> Path:
        candidate = (self.sessions_root / agent_session_id).resolve()
        try:
            candidate.relative_to(self.sessions_root.resolve())
        except ValueError as exc:
            raise AgentSessionStoreError("Agent session path escaped runtime root") from exc
        return candidate


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
