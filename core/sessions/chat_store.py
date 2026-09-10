from __future__ import annotations

from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any
from uuid import uuid4


PUBLIC_CHAT_KINDS = ("say", "resident_say", "resident_chat", "holo_say", "task")


class ChatStoreError(RuntimeError):
    pass


def _now_iso() -> str:
    # Keep sub-second precision for display and legacy entry identity.
    # Pagination uses the indexed sequence, independent of wall-clock time.
    return datetime.now().astimezone().isoformat(timespec="microseconds")


def _today_key() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d")


def _memory_outbox_target(entry: dict[str, Any]) -> tuple[str | None, str | None]:
    kind = str(entry.get("kind", ""))
    if kind in PUBLIC_CHAT_KINDS:
        return "world", None
    if kind == "whisper" and isinstance(entry.get("to"), str):
        return "private", str(entry["to"])
    if kind == "resident_whisper" and isinstance(entry.get("from"), str):
        return "private", str(entry["from"])
    return None, None


class ChatStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.index_path = root / "index.json"
        self.entries_db_path = root / "entries.sqlite3"
        self.root.mkdir(parents=True, exist_ok=True)
        self._session_lock = threading.RLock()
        self._sessions = self._load_index_file()
        self._session_ids = {str(item["id"]) for item in self._sessions}
        self._ensure_entries_schema()
        self._overlay_session_metadata()

    def list_sessions(self) -> list[dict[str, Any]]:
        with self._session_lock:
            sessions = [dict(item) for item in self._sessions]
        return sorted(sessions, key=lambda item: item["updated_at"], reverse=True)

    def reconcile_raw_sessions(self) -> int:
        """Index every durable Chat JSONL tail before dependent recovery runs.

        JSONL fsync is the Chat commit barrier. A crash may happen before the
        rebuildable SQLite index/outbox sees that line, so Core startup must
        replay all indexed-session tails before Memory outbox reconciliation.
        """
        sessions = self._read_index()
        for session in sessions:
            self._ensure_session_indexed(str(session["id"]))
        return len(sessions)

    def has_session(self, session_id: str) -> bool:
        with self._session_lock:
            return session_id in self._session_ids

    def create_session(self) -> dict[str, Any]:
        with self._session_lock:
            previous_sessions = [dict(item) for item in self._sessions]
            sessions = [dict(item) for item in previous_sessions]
            # Session ids are durable foreign keys for World Memory and Agent origin
            # metadata. Never derive a new id from the currently-visible session list:
            # deleting a chat must not make its id reusable while long-term memory may
            # still reference it. Keep the date prefix for human diagnostics and add a
            # UUID-backed identity for collision-free lifetime uniqueness.
            session_id = f"S-{_today_key()}-U{uuid4().hex}"
            now = _now_iso()
            session = {
                "id": session_id,
                "title": "新しいチャット",
                "created_at": now,
                "updated_at": now,
            }
            path = self._session_path(session_id)
            raw_created = False
            index_committed = False
            try:
                # Create the Raw authority first. If a later metadata/index write
                # fails, this empty unique file is safe to remove. The reverse
                # ordering could expose a Session whose authoritative JSONL never
                # existed after a transient filesystem failure.
                path.touch(exist_ok=False)
                raw_created = True
                sessions.append(session)
                self._write_index(sessions)
                index_committed = True
                self._persist_session_metadata(session)
                return dict(session)
            except Exception as exc:
                rollback_errors: list[BaseException] = []
                if index_committed:
                    try:
                        self._write_index(previous_sessions)
                    except Exception as rollback_exc:
                        rollback_errors.append(rollback_exc)
                try:
                    with closing(self._connect_entries()) as connection:
                        connection.execute(
                            "DELETE FROM session_metadata WHERE session_id=?",
                            (session_id,),
                        )
                        connection.commit()
                except Exception as rollback_exc:
                    rollback_errors.append(rollback_exc)
                if raw_created:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as rollback_exc:
                        rollback_errors.append(rollback_exc)
                if rollback_errors:
                    detail = "; ".join(
                        str(error) or type(error).__name__
                        for error in rollback_errors
                    )
                    raise ChatStoreError(
                        f"chat session create failed and rollback was incomplete: {detail}"
                    ) from exc
                raise

    def append_entry(
        self,
        session_id: str,
        *,
        kind: str,
        sender: str,
        text: str,
        request_id: str | None = None,
        to: str | None = None,
        task_id: str | None = None,
        agent_session_id: str | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        with self._session_lock:
            return self._append_entry_locked(
                session_id,
                kind=kind,
                sender=sender,
                text=text,
                request_id=request_id,
                to=to,
                task_id=task_id,
                agent_session_id=agent_session_id,
                entry_id=entry_id,
            )

    def _append_entry_locked(
        self,
        session_id: str,
        *,
        kind: str,
        sender: str,
        text: str,
        request_id: str | None = None,
        to: str | None = None,
        task_id: str | None = None,
        agent_session_id: str | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        if not self.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")

        cleaned = text.strip()
        if not cleaned:
            raise ChatStoreError("chat entry text must not be empty")

        now = _now_iso()
        stable_entry_id = entry_id or f"CE-{uuid4()}"
        if not isinstance(stable_entry_id, str) or not stable_entry_id.strip():
            raise ChatStoreError("chat entry id must not be empty")
        entry: dict[str, Any] = {
            "entry_id": stable_entry_id,
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
        if task_id is not None:
            entry["task_id"] = task_id
        if agent_session_id is not None:
            entry["agent_session_id"] = agent_session_id

        path = self._session_path(session_id)
        with path.open("a+b") as handle:
            # Preserve a crash-truncated tail, but never concatenate the next
            # valid entry onto it. The indexer skips malformed complete lines.
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write((json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        # Raw JSONL is the durable commit barrier. Hot metadata lives in one
        # SQLite row instead of rewriting the complete index.json on every
        # message. index.json remains the low-frequency Session existence
        # snapshot and is refreshed by create/delete operations.
        self._update_cached_session_metadata(
            session_id,
            updated_at=now,
            kind=kind,
            sender=sender,
            text=cleaned,
        )

        # One tail-import path for ordinary writes, metadata, legacy logs and recovery.
        # Advance the checkpoint only after every complete raw line is indexed.
        self._ensure_session_indexed(session_id)
        return entry

    def delete_session(self, session_id: str) -> None:
        with self._session_lock:
            self._delete_session_locked(session_id)

    def _delete_session_locked(self, session_id: str) -> None:
        sessions = self._read_index()
        if not any(item["id"] == session_id for item in sessions):
            raise ChatStoreError(f"unknown chat session: {session_id}")

        # Keep the pre-delete outbox identity so a failed cross-store delete can
        # rebuild the indexed Chat view without accidentally re-queuing already
        # synced historical entries for Memory replay.
        with closing(self._connect_entries()) as connection:
            original_outbox_entry_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT entry_id FROM memory_outbox WHERE session_id=?",
                    (session_id,),
                ).fetchall()
            }

        path = self._session_path(session_id)
        staged = path.with_name(f".{path.name}.{uuid4()}.deleting")
        raw_staged = False
        try:
            if path.is_file():
                os.replace(path, staged)
                raw_staged = True

            self._write_index([item for item in sessions if item["id"] != session_id])
            with closing(self._connect_entries()) as connection:
                # Ordinary chat deletion must not discard the only authoritative
                # indexed source for an unsynced Memory outbox row. Keep just
                # those source entries hidden until Memory replay succeeds;
                # synced rows are deleted immediately with the visible chat.
                connection.execute(
                    """
                    DELETE FROM chat_entries
                    WHERE session_id=?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM memory_outbox AS o
                          WHERE o.entry_id=chat_entries.entry_id
                      )
                    """,
                    (session_id,),
                )
                connection.execute("DELETE FROM chat_index_state WHERE session_id=?", (session_id,))
                connection.execute("DELETE FROM session_metadata WHERE session_id=?", (session_id,))
                connection.commit()

            if raw_staged:
                staged.unlink()
        except Exception as exc:
            rollback_errors: list[BaseException] = []
            if raw_staged and staged.exists():
                try:
                    os.replace(staged, path)
                except OSError as rollback_exc:
                    rollback_errors.append(rollback_exc)
            try:
                self._write_index(sessions)
            except OSError as rollback_exc:
                rollback_errors.append(rollback_exc)
            try:
                self._rebuild_session_index_after_delete_rollback(
                    session_id,
                    original_outbox_entry_ids,
                )
            except Exception as rollback_exc:
                rollback_errors.append(rollback_exc)
            if rollback_errors:
                detail = "; ".join(
                    str(error) or type(error).__name__
                    for error in rollback_errors
                )
                raise ChatStoreError(
                    f"chat session delete failed and rollback was incomplete: {detail}"
                ) from exc
            raise

    def _rebuild_session_index_after_delete_rollback(
        self,
        session_id: str,
        original_outbox_entry_ids: set[str],
    ) -> None:
        """Restore derived Chat rows while preserving the pre-delete outbox set."""
        with closing(self._connect_entries()) as connection:
            connection.execute("DELETE FROM chat_entries WHERE session_id=?", (session_id,))
            connection.execute("DELETE FROM chat_index_state WHERE session_id=?", (session_id,))
            connection.commit()

        self._ensure_session_indexed(session_id)

        with closing(self._connect_entries()) as connection:
            current_outbox_ids = {
                str(row[0])
                for row in connection.execute(
                    "SELECT entry_id FROM memory_outbox WHERE session_id=?",
                    (session_id,),
                ).fetchall()
            }
            for entry_id in current_outbox_ids - original_outbox_entry_ids:
                connection.execute(
                    "DELETE FROM memory_outbox WHERE entry_id=? AND session_id=?",
                    (entry_id, session_id),
                )
            connection.commit()

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

        self._ensure_session_indexed(session_id)
        with closing(self._connect_entries()) as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) FROM chat_entries WHERE session_id=?",
                (session_id,),
            ).fetchone()
            end_index = int(row[0]) if row is not None else 0
            if before is not None:
                try:
                    end_index = int(before)
                except ValueError as exc:
                    raise ChatStoreError("invalid history cursor") from exc
                max_seq = int(row[0]) if row is not None else 0
                if end_index < 0 or end_index > max_seq:
                    raise ChatStoreError("history cursor is out of range")
            if end_index == 0:
                return [], None
            rows = connection.execute(
                """
                SELECT payload_json, seq
                FROM chat_entries
                WHERE session_id=? AND seq <= ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (session_id, end_index, limit),
            ).fetchall()
        rows = list(reversed(rows))
        entries = [json.loads(str(row[0])) for row in rows]
        first_seq = int(rows[0][1]) if rows else 0
        next_before = str(first_seq - 1) if first_seq > 1 else None
        return entries, next_before

    def read_entries_after(
        self,
        session_id: str,
        after_entry_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return all entries after a stable chat entry id."""
        return self._read_entries_after_where(session_id, after_entry_id)

    def read_public_entries_after(
        self,
        session_id: str,
        after_entry_id: str | None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return only public-channel entries after a stable marker.

        Channel filtering happens in SQLite before payload decoding, so an old
        native-context marker is not forced to materialize years of unrelated
        Whisper traffic merely to discard it in Python.
        """
        placeholders = ",".join("?" for _ in PUBLIC_CHAT_KINDS)
        return self._read_entries_after_where(
            session_id,
            after_entry_id,
            where_sql=f"kind IN ({placeholders})",
            where_params=PUBLIC_CHAT_KINDS,
            limit=limit,
        )

    def read_whisper_entries_after(
        self,
        session_id: str,
        resident_name: str,
        after_entry_id: str | None,
    ) -> list[dict[str, Any]]:
        """Return only one Resident's private channel after a stable marker."""
        return self._read_entries_after_where(
            session_id,
            after_entry_id,
            where_sql=(
                "((kind='whisper' AND recipient=?) "
                "OR (kind='resident_whisper' AND sender=?))"
            ),
            where_params=(resident_name, resident_name),
        )

    def _read_entries_after_where(
        self,
        session_id: str,
        after_entry_id: str | None,
        *,
        where_sql: str | None = None,
        where_params: tuple[Any, ...] = (),
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Indexed continuation primitive with optional SQL-side filtering."""
        if not self.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")
        self._ensure_session_indexed(session_id)
        with closing(self._connect_entries()) as connection:
            after_seq = 0
            if after_entry_id is not None:
                marker = connection.execute(
                    "SELECT seq FROM chat_entries WHERE session_id=? AND entry_id=?",
                    (session_id, after_entry_id),
                ).fetchone()
                if marker is None:
                    raise ChatStoreError(
                        f"chat continuation marker was not found: {session_id}/{after_entry_id}"
                    )
                after_seq = int(marker[0])
            filter_clause = f" AND {where_sql}" if where_sql else ""
            limit_clause = " LIMIT ?" if limit is not None else ""
            params: tuple[Any, ...] = (session_id, after_seq, *where_params)
            if limit is not None:
                if limit <= 0:
                    return []
                params = (*params, int(limit))
            rows = connection.execute(
                "SELECT payload_json FROM chat_entries "
                f"WHERE session_id=? AND seq>?{filter_clause} ORDER BY seq{limit_clause}",
                params,
            ).fetchall()
        return [json.loads(str(row[0])) for row in rows]

    def read_channel_history(
        self,
        session_id: str,
        *,
        resident_name: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Read a bounded public tail, or only the named Resident's whispers.

        Limit each indexed kind before merging: unrelated channels cannot evict
        context, and long histories do not require a full-session scan or sort.
        """
        if not self.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")
        if limit <= 0:
            return []
        self._ensure_session_indexed(session_id)
        rows: list[tuple[int, str]] = []
        with closing(self._connect_entries()) as connection:
            if resident_name is None:
                for kind in PUBLIC_CHAT_KINDS:
                    rows.extend(connection.execute(
                        "SELECT seq, payload_json FROM chat_entries "
                        "WHERE session_id=? AND kind=? ORDER BY seq DESC LIMIT ?",
                        (session_id, kind, limit),
                    ).fetchall())
            else:
                # Field names are fixed code; all caller values stay parameters.
                for kind, field in (("whisper", "recipient"), ("resident_whisper", "sender")):
                    rows.extend(connection.execute(
                        "SELECT seq, payload_json FROM chat_entries "
                        f"WHERE session_id=? AND kind=? AND {field}=? ORDER BY seq DESC LIMIT ?",
                        (session_id, kind, resident_name, limit),
                    ).fetchall())
        return [json.loads(payload) for _, payload in sorted(rows, key=lambda row: row[0])[-limit:]]

    def find_task_entry(self, session_id: str, agent_session_id: str) -> dict[str, Any] | None:
        if not self.has_session(session_id):
            raise ChatStoreError(f"unknown chat session: {session_id}")
        self._ensure_session_indexed(session_id)
        with closing(self._connect_entries()) as connection:
            row = connection.execute(
                """
                SELECT payload_json
                FROM chat_entries
                WHERE session_id=? AND kind='task' AND agent_session_id=?
                ORDER BY seq DESC
                LIMIT 1
                """,
                (session_id, agent_session_id),
            ).fetchone()
        return json.loads(str(row[0])) if row is not None else None

    def _connect_entries(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.entries_db_path, timeout=5.0)
        # journal_mode is persistent database state and is established once by
        # schema initialization. Reissuing PRAGMA journal_mode=WAL on every hot
        # connection is surprisingly expensive on Windows.
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _overlay_session_metadata(self) -> None:
        try:
            with closing(self._connect_entries()) as connection:
                rows = connection.execute(
                    "SELECT session_id, title, created_at, updated_at FROM session_metadata"
                ).fetchall()
        except sqlite3.Error as exc:
            raise ChatStoreError("chat session metadata could not be read") from exc
        metadata = {
            str(row[0]): {
                "title": str(row[1]),
                "created_at": str(row[2]),
                "updated_at": str(row[3]),
            }
            for row in rows
        }
        with self._session_lock:
            for session in self._sessions:
                current = metadata.get(str(session["id"]))
                if current is not None:
                    session.update(current)

    def _update_cached_session_metadata(
        self,
        session_id: str,
        *,
        updated_at: str,
        kind: str,
        sender: str,
        text: str,
    ) -> dict[str, Any]:
        with self._session_lock:
            for session in self._sessions:
                if session["id"] != session_id:
                    continue
                session["updated_at"] = updated_at
                if (
                    session["title"] == "新しいチャット"
                    and kind == "say"
                    and sender == "master"
                ):
                    title = " ".join(text.split())
                    session["title"] = title[:30] if title else "新しいチャット"
                return dict(session)
        raise ChatStoreError(f"unknown chat session: {session_id}")

    def _persist_session_metadata(self, session: dict[str, Any]) -> None:
        try:
            with closing(self._connect_entries()) as connection:
                connection.execute(
                    """
                    INSERT INTO session_metadata(session_id, title, created_at, updated_at)
                    VALUES(?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        title=excluded.title,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        str(session["id"]),
                        str(session["title"]),
                        str(session["created_at"]),
                        str(session["updated_at"]),
                    ),
                )
                connection.commit()
        except sqlite3.Error as exc:
            raise ChatStoreError("chat session metadata could not be saved") from exc

    def _ensure_session_metadata_row(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> None:
        existing = connection.execute(
            "SELECT 1 FROM session_metadata WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if existing is not None:
            return
        with self._session_lock:
            seed = next(
                (dict(item) for item in self._sessions if item["id"] == session_id),
                None,
            )
        if seed is None:
            return
        connection.execute(
            """
            INSERT INTO session_metadata(session_id, title, created_at, updated_at)
            VALUES(?, ?, ?, ?)
            """,
            (
                session_id,
                str(seed["title"]),
                str(seed["created_at"]),
                str(seed["updated_at"]),
            ),
        )

    @staticmethod
    def _update_indexed_session_metadata(
        connection: sqlite3.Connection,
        session_id: str,
        entry: dict[str, Any],
    ) -> None:
        row = connection.execute(
            "SELECT title, updated_at FROM session_metadata WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        title = str(row[0])
        kind = str(entry.get("kind", ""))
        sender = str(entry.get("from", ""))
        text = str(entry.get("text", "")).strip()
        if title == "新しいチャット" and kind == "say" and sender == "master" and text:
            compact = " ".join(text.split())
            title = compact[:30] if compact else title
        occurred_at = entry.get("ts")
        updated_at = str(occurred_at) if isinstance(occurred_at, str) and occurred_at else str(row[1])
        connection.execute(
            "UPDATE session_metadata SET title=?, updated_at=? WHERE session_id=?",
            (title, updated_at, session_id),
        )

    def _sync_cached_session_metadata(
        self,
        connection: sqlite3.Connection,
        session_id: str,
    ) -> None:
        row = connection.execute(
            "SELECT title, created_at, updated_at FROM session_metadata WHERE session_id=?",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        with self._session_lock:
            for session in self._sessions:
                if session["id"] == session_id:
                    session["title"] = str(row[0])
                    session["created_at"] = str(row[1])
                    session["updated_at"] = str(row[2])
                    return

    def _ensure_entries_schema(self) -> None:
        with closing(self._connect_entries()) as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_entries (
                    session_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    entry_id TEXT,
                    ts TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT,
                    request_id TEXT,
                    task_id TEXT,
                    agent_session_id TEXT,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY(session_id, seq)
                )
                """
            )
            connection.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS chat_entries_entry_id ON chat_entries(session_id, entry_id) WHERE entry_id IS NOT NULL"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chat_entries_agent_session ON chat_entries(session_id, agent_session_id, seq)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chat_entries_channel ON chat_entries(session_id, kind, seq)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chat_entries_recipient ON chat_entries(session_id, kind, recipient, seq)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS chat_entries_sender ON chat_entries(session_id, kind, sender, seq)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS chat_index_state (
                    session_id TEXT PRIMARY KEY,
                    indexed_bytes INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_metadata (
                    session_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            for session in self._sessions:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO session_metadata(
                        session_id, title, created_at, updated_at
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (
                        str(session["id"]),
                        str(session["title"]),
                        str(session["created_at"]),
                        str(session["updated_at"]),
                    ),
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_outbox (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    resident_name TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_outbox_quarantine (
                    entry_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    resident_name TEXT,
                    payload_json TEXT,
                    indexed_payload_json TEXT,
                    error TEXT NOT NULL,
                    quarantined_at TEXT NOT NULL
                )
                """
            )
            user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if user_version == 0:
                # Pre-v1 checkpoints could skip raw entries after an append.
                # Invalidate derived rows once; lazily reimport each session on
                # its next read/write. The raw JSONL and session list stay intact.
                connection.execute("DELETE FROM chat_entries")
                connection.execute("DELETE FROM chat_index_state")
                user_version = 1
            if user_version < 2:
                # Existing indexed chat is authoritative even if a previous
                # second write to World/Private Memory failed. Seed a durable
                # idempotent outbox so startup can reconcile both stores.
                public_placeholders = ",".join("?" for _ in PUBLIC_CHAT_KINDS)
                connection.execute(
                    "INSERT OR IGNORE INTO memory_outbox(entry_id, session_id, scope, resident_name, payload_json) "
                    "SELECT entry_id, session_id, 'world', NULL, payload_json FROM chat_entries "
                    f"WHERE entry_id IS NOT NULL AND kind IN ({public_placeholders})",
                    PUBLIC_CHAT_KINDS,
                )
                connection.execute(
                    "INSERT OR IGNORE INTO memory_outbox(entry_id, session_id, scope, resident_name, payload_json) "
                    "SELECT entry_id, session_id, 'private', recipient, payload_json FROM chat_entries "
                    "WHERE entry_id IS NOT NULL AND kind='whisper' AND recipient IS NOT NULL"
                )
                connection.execute(
                    "INSERT OR IGNORE INTO memory_outbox(entry_id, session_id, scope, resident_name, payload_json) "
                    "SELECT entry_id, session_id, 'private', sender, payload_json FROM chat_entries "
                    "WHERE entry_id IS NOT NULL AND kind='resident_whisper'"
                )
                user_version = 2
            if user_version < 3:
                # Older builds could derive the always-visible Session title from
                # a private Master Whisper. Recompute this presentation metadata
                # from the first indexed public Master Say only. Raw JSONL remains
                # untouched; a not-yet-indexed Say will populate the title during
                # the normal startup tail reconciliation.
                session_ids = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT session_id FROM session_metadata"
                    ).fetchall()
                ]
                for session_id in session_ids:
                    title = "新しいチャット"
                    row = connection.execute(
                        """
                        SELECT payload_json
                        FROM chat_entries
                        WHERE session_id=? AND kind='say' AND sender='master'
                        ORDER BY seq
                        LIMIT 1
                        """,
                        (session_id,),
                    ).fetchone()
                    if row is not None:
                        try:
                            payload = json.loads(str(row[0]))
                        except json.JSONDecodeError:
                            payload = None
                        if isinstance(payload, dict):
                            text = payload.get("text")
                            if isinstance(text, str):
                                compact = " ".join(text.split())
                                if compact:
                                    title = compact[:30]
                    connection.execute(
                        "UPDATE session_metadata SET title=? WHERE session_id=?",
                        (title, session_id),
                    )
                user_version = 3
            connection.execute(f"PRAGMA user_version={user_version}")
            connection.commit()

    def _ensure_session_indexed(self, session_id: str) -> None:
        path = self._session_path(session_id)
        if not path.exists():
            return
        file_size = path.stat().st_size
        with closing(self._connect_entries()) as connection:
            self._ensure_session_metadata_row(connection, session_id)
            row = connection.execute(
                "SELECT indexed_bytes FROM chat_index_state WHERE session_id=?",
                (session_id,),
            ).fetchone()
            indexed_bytes = int(row[0]) if row is not None else 0
            truncated = file_size < indexed_bytes
            if row is None or truncated:
                # No trustworthy incremental checkpoint means metadata must be
                # replayed from Raw too. Titles are auto-derived from the first
                # public Master Say, so reset derived fields before full reindex.
                connection.execute(
                    """
                    UPDATE session_metadata
                    SET title='新しいチャット', updated_at=created_at
                    WHERE session_id=?
                    """,
                    (session_id,),
                )
            if truncated:
                connection.execute("DELETE FROM chat_entries WHERE session_id=?", (session_id,))
                indexed_bytes = 0
            if file_size == indexed_bytes and not truncated:
                connection.commit()
                self._sync_cached_session_metadata(connection, session_id)
                return
            next_seq_row = connection.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM chat_entries WHERE session_id=?",
                (session_id,),
            ).fetchone()
            next_seq = int(next_seq_row[0]) if next_seq_row is not None else 1
            last_complete_offset = indexed_bytes
            with path.open("rb") as handle:
                handle.seek(indexed_bytes)
                while True:
                    encoded = handle.readline()
                    if not encoded:
                        break
                    if not encoded.endswith(b"\n"):
                        break
                    last_complete_offset = handle.tell()
                    stripped = encoded.strip()
                    if not stripped:
                        continue
                    try:
                        parsed = json.loads(stripped.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not isinstance(parsed, dict):
                        continue
                    self._insert_indexed_entry(connection, session_id, next_seq, parsed)
                    self._update_indexed_session_metadata(connection, session_id, parsed)
                    next_seq += 1
            connection.execute(
                """
                INSERT INTO chat_index_state(session_id, indexed_bytes)
                VALUES(?, ?)
                ON CONFLICT(session_id) DO UPDATE SET indexed_bytes=excluded.indexed_bytes
                """,
                (session_id, last_complete_offset),
            )
            connection.commit()
            self._sync_cached_session_metadata(connection, session_id)

    @staticmethod
    def _insert_indexed_entry(
        connection: sqlite3.Connection,
        session_id: str,
        seq: int,
        entry: dict[str, Any],
    ) -> None:
        payload_json = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
        connection.execute(
            """
            INSERT OR IGNORE INTO chat_entries(
                session_id, seq, entry_id, ts, kind, sender, recipient,
                request_id, task_id, agent_session_id, payload_json
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                seq,
                entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None,
                str(entry.get("ts", "")),
                str(entry.get("kind", "")),
                str(entry.get("from", "")),
                entry.get("to") if isinstance(entry.get("to"), str) else None,
                entry.get("request_id") if isinstance(entry.get("request_id"), str) else None,
                entry.get("task_id") if isinstance(entry.get("task_id"), str) else None,
                entry.get("agent_session_id") if isinstance(entry.get("agent_session_id"), str) else None,
                payload_json,
            ),
        )
        entry_id = entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None
        scope, resident_name = _memory_outbox_target(entry)
        if entry_id is not None and scope is not None:
            connection.execute(
                """
                INSERT OR IGNORE INTO memory_outbox(
                    entry_id, session_id, scope, resident_name, payload_json
                ) VALUES(?, ?, ?, ?, ?)
                """,
                (entry_id, session_id, scope, resident_name, payload_json),
            )

    def pending_memory_sync(self, *, limit: int = 500) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 5000))
        try:
            with closing(self._connect_entries()) as connection:
                rows = connection.execute(
                    """
                    SELECT o.entry_id, o.session_id, o.scope, o.resident_name,
                           o.payload_json, c.payload_json
                    FROM memory_outbox AS o
                    LEFT JOIN chat_entries AS c
                      ON c.session_id=o.session_id AND c.entry_id=o.entry_id
                    ORDER BY o.rowid
                    LIMIT ?
                    """,
                    (bounded,),
                ).fetchall()
        except sqlite3.Error as exc:
            raise ChatStoreError("memory outbox could not be read") from exc

        result: list[dict[str, Any]] = []
        repaired_sessions: set[str] = set()
        for entry_id, session_id, scope, resident_name, outbox_payload, indexed_payload in rows:
            stable_entry_id = str(entry_id)
            stable_session_id = str(session_id)
            if stable_session_id in repaired_sessions:
                # A previous broken row from this same Session rebuilt the whole
                # derived Chat index. Refresh this row from SQLite instead of
                # judging the stale values captured by the original batch query.
                with closing(self._connect_entries()) as connection:
                    refreshed = connection.execute(
                        """
                        SELECT o.scope, o.resident_name, o.payload_json, c.payload_json
                        FROM memory_outbox AS o
                        LEFT JOIN chat_entries AS c
                          ON c.session_id=o.session_id AND c.entry_id=o.entry_id
                        WHERE o.entry_id=?
                        """,
                        (stable_entry_id,),
                    ).fetchone()
                if refreshed is None:
                    continue
                scope, resident_name, outbox_payload, indexed_payload = refreshed
            try:
                entry, payload_mismatch = self._authoritative_outbox_payload(
                    outbox_payload,
                    indexed_payload,
                )
                expected_scope, expected_resident = _memory_outbox_target(entry)
                if expected_scope is None:
                    raise ChatStoreError("memory outbox indexed source is not memory-eligible")
            except ChatStoreError as first_error:
                # Chat JSONL is the authority. If the visible raw session still
                # exists, rebuild its derived SQLite index once while preserving
                # the exact pre-repair outbox set, then retry this row.
                if stable_session_id not in repaired_sessions and self._session_path(stable_session_id).is_file():
                    with closing(self._connect_entries()) as connection:
                        original_outbox_entry_ids = {
                            str(row[0])
                            for row in connection.execute(
                                "SELECT entry_id FROM memory_outbox WHERE session_id=?",
                                (stable_session_id,),
                            ).fetchall()
                        }
                    self._rebuild_session_index_after_delete_rollback(
                        stable_session_id,
                        original_outbox_entry_ids,
                    )
                    repaired_sessions.add(stable_session_id)
                    with closing(self._connect_entries()) as connection:
                        refreshed = connection.execute(
                            """
                            SELECT o.scope, o.resident_name, o.payload_json, c.payload_json
                            FROM memory_outbox AS o
                            LEFT JOIN chat_entries AS c
                              ON c.session_id=o.session_id AND c.entry_id=o.entry_id
                            WHERE o.entry_id=?
                            """,
                            (stable_entry_id,),
                        ).fetchone()
                    if refreshed is not None:
                        scope, resident_name, outbox_payload, indexed_payload = refreshed
                        try:
                            entry, payload_mismatch = self._authoritative_outbox_payload(
                                outbox_payload,
                                indexed_payload,
                            )
                            expected_scope, expected_resident = _memory_outbox_target(entry)
                            if expected_scope is None:
                                raise ChatStoreError(
                                    "memory outbox indexed source is not memory-eligible"
                                )
                        except ChatStoreError as retry_error:
                            self._quarantine_memory_outbox_row(
                                stable_entry_id,
                                stable_session_id,
                                str(scope),
                                str(resident_name) if resident_name is not None else None,
                                outbox_payload,
                                indexed_payload,
                                retry_error,
                            )
                            continue
                    else:
                        self._quarantine_memory_outbox_row(
                            stable_entry_id,
                            stable_session_id,
                            str(scope),
                            str(resident_name) if resident_name is not None else None,
                            outbox_payload,
                            indexed_payload,
                            first_error,
                        )
                        continue
                else:
                    self._quarantine_memory_outbox_row(
                        stable_entry_id,
                        stable_session_id,
                        str(scope),
                        str(resident_name) if resident_name is not None else None,
                        outbox_payload,
                        indexed_payload,
                        first_error,
                    )
                    continue

            current_resident = str(resident_name) if resident_name is not None else None
            metadata_mismatch = str(scope) != expected_scope or current_resident != expected_resident
            if payload_mismatch or metadata_mismatch:
                encoded = json.dumps(entry, ensure_ascii=False, separators=(",", ":"))
                try:
                    with closing(self._connect_entries()) as connection:
                        connection.execute(
                            """
                            UPDATE memory_outbox
                            SET scope=?, resident_name=?, payload_json=?
                            WHERE entry_id=?
                            """,
                            (expected_scope, expected_resident, encoded, stable_entry_id),
                        )
                        connection.commit()
                except sqlite3.Error as exc:
                    raise ChatStoreError("memory outbox repair could not update derived row") from exc
            result.append({
                "entry_id": stable_entry_id,
                "session_id": stable_session_id,
                "scope": expected_scope,
                "resident_name": expected_resident,
                "entry": entry,
            })
        return result

    def _quarantine_memory_outbox_row(
        self,
        entry_id: str,
        session_id: str,
        scope: str,
        resident_name: str | None,
        outbox_payload: object,
        indexed_payload: object,
        error: BaseException,
    ) -> None:
        try:
            with closing(self._connect_entries()) as connection:
                connection.execute(
                    """
                    INSERT OR REPLACE INTO memory_outbox_quarantine(
                        entry_id, session_id, scope, resident_name, payload_json,
                        indexed_payload_json, error, quarantined_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        entry_id,
                        session_id,
                        scope,
                        resident_name,
                        None if outbox_payload is None else str(outbox_payload),
                        None if indexed_payload is None else str(indexed_payload),
                        f"{type(error).__name__}: {error}"[:2000],
                        _now_iso(),
                    ),
                )
                connection.execute("DELETE FROM memory_outbox WHERE entry_id=?", (entry_id,))
                connection.commit()
        except sqlite3.Error as exc:
            raise ChatStoreError("memory outbox quarantine could not be persisted") from exc

    def quarantined_memory_sync_count(self) -> int:
        try:
            with closing(self._connect_entries()) as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM memory_outbox_quarantine"
                ).fetchone()
        except sqlite3.Error as exc:
            raise ChatStoreError("memory outbox quarantine count could not be read") from exc
        return int(row[0]) if row is not None else 0

    @staticmethod
    def _authoritative_outbox_payload(
        outbox_payload: object,
        indexed_payload: object,
    ) -> tuple[dict[str, Any], bool]:
        if indexed_payload is None:
            raise ChatStoreError("memory outbox payload has no indexed source entry")
        try:
            indexed = json.loads(str(indexed_payload))
        except json.JSONDecodeError as exc:
            raise ChatStoreError("memory outbox indexed source payload is invalid") from exc
        if not isinstance(indexed, dict):
            raise ChatStoreError("memory outbox indexed source payload is not an object")
        try:
            outbox = json.loads(str(outbox_payload))
        except json.JSONDecodeError:
            outbox = None
        return indexed, not isinstance(outbox, dict) or outbox != indexed

    def mark_memory_synced(self, entry_id: str) -> None:
        try:
            with closing(self._connect_entries()) as connection:
                row = connection.execute(
                    "SELECT session_id FROM memory_outbox WHERE entry_id=?",
                    (entry_id,),
                ).fetchone()
                connection.execute("DELETE FROM memory_outbox WHERE entry_id=?", (entry_id,))
                if row is not None:
                    session_id = str(row[0])
                    # A deleted chat may have retained this one indexed source
                    # solely so Memory replay could finish. Once synced, remove
                    # that hidden source too; live sessions keep their index row.
                    if not self._session_path(session_id).is_file():
                        connection.execute(
                            "DELETE FROM chat_entries WHERE entry_id=?",
                            (entry_id,),
                        )
                connection.commit()
        except sqlite3.Error as exc:
            raise ChatStoreError("memory outbox entry could not be marked synced") from exc

    def pending_memory_sync_count(self) -> int:
        try:
            with closing(self._connect_entries()) as connection:
                row = connection.execute("SELECT COUNT(*) FROM memory_outbox").fetchone()
        except sqlite3.Error as exc:
            raise ChatStoreError("memory outbox count could not be read") from exc
        return int(row[0]) if row is not None else 0

    def _session_path(self, session_id: str) -> Path:
        return self.root / f"{session_id}.jsonl"

    def _read_index(self) -> list[dict[str, Any]]:
        with self._session_lock:
            return [dict(item) for item in self._sessions]

    def _load_index_file(self) -> list[dict[str, Any]]:
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
        self._sessions = [dict(item) for item in sessions]
        self._session_ids = {str(item["id"]) for item in self._sessions}
