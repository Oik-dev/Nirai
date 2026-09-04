from __future__ import annotations

from typing import Any

from .chat_store import ChatStore, ChatStoreError


class SessionManager:
    def __init__(self, store: ChatStore) -> None:
        self.store = store
        sessions = self.store.list_sessions()
        if sessions:
            self.active_session_id = sessions[0]["id"]
        else:
            self.active_session_id = self.store.create_session()["id"]

    def list_sessions(self) -> list[dict[str, Any]]:
        return self.store.list_sessions()

    def create_session(self) -> dict[str, Any]:
        session = self.store.create_session()
        self.active_session_id = session["id"]
        return session

    def select_session(self, session_id: str) -> None:
        if not self.store.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")
        self.active_session_id = session_id

    def append_master_say(self, text: str, request_id: str) -> dict[str, Any]:
        return self.store.append_entry(
            self.active_session_id,
            kind="say",
            sender="master",
            text=text,
            request_id=request_id,
        )

    def append_master_whisper(self, resident_name: str, text: str, request_id: str) -> dict[str, Any]:
        return self.store.append_entry(
            self.active_session_id,
            kind="whisper",
            sender="master",
            to=resident_name,
            text=text,
            request_id=request_id,
        )

    def append_resident_say(
        self,
        session_id: str,
        resident_name: str,
        text: str,
        request_id: str,
    ) -> dict[str, Any]:
        return self.store.append_entry(
            session_id,
            kind="resident_say",
            sender=resident_name,
            text=text,
            request_id=request_id,
        )

    def append_resident_whisper(
        self,
        session_id: str,
        resident_name: str,
        text: str,
        request_id: str,
    ) -> dict[str, Any]:
        return self.store.append_entry(
            session_id,
            kind="resident_whisper",
            sender=resident_name,
            to="master",
            text=text,
            request_id=request_id,
        )

    def append_resident_chat(
        self,
        session_id: str,
        sender_name: str,
        recipient_name: str | None,
        text: str,
    ) -> dict[str, Any]:
        return self.store.append_entry(
            session_id,
            kind="resident_chat",
            sender=sender_name,
            to=recipient_name,
            text=text,
        )

    def find_task_entry(
        self,
        session_id: str,
        agent_session_id: str,
    ) -> dict[str, Any] | None:
        for entry in reversed(self.store.read_history(session_id, limit=100000)):
            if (
                entry.get("kind") == "task"
                and entry.get("agent_session_id") == agent_session_id
            ):
                return entry
        return None

    def append_task(
        self,
        session_id: str,
        resident_name: str,
        text: str,
        *,
        task_id: str,
        agent_session_id: str,
    ) -> dict[str, Any]:
        return self.store.append_entry(
            session_id,
            kind="task",
            sender=resident_name,
            text=text,
            task_id=task_id,
            agent_session_id=agent_session_id,
        )

    def append_holo_say(
        self,
        text: str,
        *,
        to: str | None = None,
        sender: str = "Holo",
    ) -> dict[str, Any]:
        return self.store.append_entry(
            self.active_session_id,
            kind="holo_say",
            sender=sender,
            to=to,
            text=text,
        )

    def delete_session(self, session_id: str) -> str:
        self.store.delete_session(session_id)
        sessions = self.store.list_sessions()
        if not sessions:
            return self.create_session()["id"]
        if self.active_session_id == session_id:
            self.active_session_id = sessions[0]["id"]
        return self.active_session_id

    def history(
        self,
        session_id: str | None = None,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        target = session_id or self.active_session_id
        return self.store.read_history(target, before=before, limit=limit)

    def history_page(
        self,
        session_id: str | None = None,
        *,
        before: str | None = None,
        limit: int = 50,
    ) -> tuple[list[dict[str, Any]], str | None]:
        target = session_id or self.active_session_id
        return self.store.read_history_page(target, before=before, limit=limit)

    def public_history(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        entries = self.store.read_history(session_id, limit=max(limit * 4, limit))
        public = [
            entry
            for entry in entries
            if entry.get("kind") in {"say", "resident_say", "resident_chat", "holo_say", "task"}
        ]
        return public[-limit:]

    def whisper_history(
        self,
        session_id: str,
        resident_name: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        entries = self.store.read_history(session_id, limit=max(limit * 4, limit))
        whispers = [
            entry for entry in entries
            if (entry.get("kind") == "whisper" and entry.get("to") == resident_name)
            or (entry.get("kind") == "resident_whisper" and entry.get("from") == resident_name)
        ]
        return whispers[-limit:]
