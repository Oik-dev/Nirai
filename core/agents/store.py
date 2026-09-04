from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

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
        directory.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(directory / "session.json", snapshot.to_protocol())

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

        events = self.read_events(agent_session_id)
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
        persisted_seq = max(
            (
                int(event["seq"])
                for event in self.read_events(snapshot.agent_session_id)
                if isinstance(event.get("seq"), int) and not isinstance(event.get("seq"), bool)
            ),
            default=0,
        )
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
        path = self._session_dir(snapshot.agent_session_id) / "events.jsonl"
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
