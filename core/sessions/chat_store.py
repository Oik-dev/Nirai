from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


class ChatStoreError(RuntimeError):
    pass


def _now_iso() -> str:
    # History pagination uses the timestamp as its cursor. Keep sub-second
    # precision so rapid consecutive messages do not collapse onto one cursor.
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _today_key() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d")


class ChatStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index_path = root / "index.json"
        self.root.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[dict[str, Any]]:
        sessions = self._read_index()
        return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)

    def has_session(self, session_id: str) -> bool:
        return any(item["id"] == session_id for item in self._read_index())

    def create_session(self) -> dict[str, Any]:
        sessions = self._read_index()
        date_key = _today_key()
        prefix = f"S-{date_key}-"
        used = [
            int(item["id"].removeprefix(prefix))
            for item in sessions
            if isinstance(item.get("id"), str)
            and item["id"].startswith(prefix)
            and item["id"].removeprefix(prefix).isdigit()
        ]
        sequence = max(used, default=0) + 1
        session_id = f"{prefix}{sequence:03d}"
        now = _now_iso()
        session = {
            "id": session_id,
            "title": "新しいチャット",
            "created_at": now,
            "updated_at": now,
        }
        sessions.append(session)
        self._write_index(sessions)
        self._session_path(session_id).touch(exist_ok=False)
        return session

    def append_entry(
        self,
        session_id: str,
        *,
        kind: str,
        sender: str,
        text: str,
        request_id: str | None = None,
        to: str | None = None,
    ) -> dict[str, Any]:
        if not self.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")

        cleaned = text.strip()
        if not cleaned:
            raise ChatStoreError("chat entry text must not be empty")

        now = _now_iso()
        entry: dict[str, Any] = {
            "ts": now,
            "kind": kind,
            "from": sender,
            "text": cleaned,
            "session": session_id,
        }
        if to is not None:
            entry["to"] = to
        if request_id is not None:
            entry["request_id"] = request_id

        with self._session_path(session_id).open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

        sessions = self._read_index()
        for session in sessions:
            if session["id"] != session_id:
                continue
            session["updated_at"] = now
            if session["title"] == "新しいチャット" and sender == "master":
                title = " ".join(cleaned.split())
                session["title"] = title[:30] if title else "新しいチャット"
            break
        self._write_index(sessions)
        return entry

    def delete_session(self, session_id: str) -> None:
        sessions = self._read_index()
        if not any(item["id"] == session_id for item in sessions):
            raise ChatStoreError(f"unknown chat session: {session_id}")
        self._write_index([item for item in sessions if item["id"] != session_id])
        self._session_path(session_id).unlink(missing_ok=True)

    def read_history(
        self,
        session_id: str,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        entries, _ = self.read_history_page(session_id, before=before, limit=limit)
        return entries

    def read_history_page(
        self,
        session_id: str,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], str | None]:
        if not self.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")
        if limit <= 0:
            return [], before

        entries = self._read_entries(session_id)
        end_index = len(entries)
        if before is not None:
            try:
                end_index = int(before)
            except ValueError as exc:
                raise ChatStoreError("invalid history cursor") from exc
            if end_index < 0 or end_index > len(entries):
                raise ChatStoreError("history cursor is out of range")

        start_index = max(0, end_index - limit)
        next_before = str(start_index) if start_index > 0 else None
        return entries[start_index:end_index], next_before

    def _read_entries(self, session_id: str) -> list[dict[str, Any]]:
        path = self._session_path(session_id)
        if not path.exists():
            return []

        entries: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def _read_index(self) -> list[dict[str, Any]]:
        if not self.index_path.exists():
            return []
        try:
            parsed = json.loads(self.index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ChatStoreError(f"chat session index could not be read: {exc}") from exc
        if not isinstance(parsed, list):
            raise ChatStoreError("chat session index must be a JSON array")

        sessions: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                raise ChatStoreError("chat session index contains a non-object entry")
            required = ("id", "title", "created_at", "updated_at")
            if any(not isinstance(item.get(key), str) for key in required):
                raise ChatStoreError("chat session index contains an invalid entry")
            sessions.append(dict(item))
        return sessions

    def _write_index(self, sessions: list[dict[str, Any]]) -> None:
        temp_path = self.index_path.with_suffix(".json.tmp")
        payload = json.dumps(sessions, ensure_ascii=False, indent=2) + "\n"
        temp_path.write_text(payload, encoding="utf-8")
        temp_path.replace(self.index_path)
