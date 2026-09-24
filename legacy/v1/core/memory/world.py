from __future__ import annotations

from contextlib import closing
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any

from .lexical import raw_search_terms


class WorldMemoryError(RuntimeError):
    pass


class WorldMemoryService:
    PUBLIC_KINDS = {"say", "resident_say", "resident_chat", "holo_say", "task"}

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.memory_root = self.root / "world_memory"
        self.episodes_root = self.memory_root / "episodes"
        self.db_path = self.memory_root / "world_memory.sqlite3"
        self.episodes_root.mkdir(parents=True, exist_ok=True)
        self._ensure_raw_schema()

    def record_public_entry(self, entry: dict[str, Any]) -> None:
        if entry.get("kind") not in self.PUBLIC_KINDS:
            return
        session_id = entry.get("session")
        sender = entry.get("from")
        text = entry.get("text")
        ts = entry.get("ts")
        if not all(isinstance(value, str) and value for value in (session_id, sender, text, ts)):
            raise WorldMemoryError("public entry is missing required fields")

        inserted = self._append_raw_entry(entry)
        if not inserted and self.is_session_forgotten(session_id):
            return

        # Compatibility-derived Episode view. Raw SQLite above is the durable
        # source. Ordinary new entries remain append-only; duplicate replay is
        # the recovery path after a crash between Raw commit and Episode append.
        # On that path only, verify the stable marker before deciding the derived
        # view is already repaired.
        path = self._episode_path(session_id)
        marker = self.entry_marker(entry)
        # New inserts stay append-only. Full-file reads are only for the
        # duplicate-replay path after a crash between Raw commit and Episode
        # append, including UTF-8 tails that would otherwise fail Core start.
        if not inserted and path.is_file():
            episode_text = self._legacy_episode_text(path)
            if episode_text is None:
                if self.is_session_forgotten(session_id):
                    try:
                        path.unlink(missing_ok=True)
                    except OSError as exc:
                        raise WorldMemoryError("forgotten World Memory Episode could not be removed") from exc
                    return
                self._rebuild_legacy_episode_from_raw(session_id)
                return
            if marker in episode_text:
                return
        if not path.exists():
            try:
                path.write_text(
                    f"# World Memory Episode\n\n"
                    f"session_id: {session_id}\n"
                    f"episode_id: {session_id}-E001\n\n"
                    "## 公開会話\n",
                    encoding="utf-8",
                    newline="\n",
                )
            except OSError as exc:
                raise WorldMemoryError("legacy World Memory Episode could not be created") from exc
        label = "Master" if sender == "master" else sender
        clean_text = " ".join(text.split())[:240]
        try:
            with path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"{marker}\n- {ts} {label}: {clean_text}\n")
        except OSError as exc:
            raise WorldMemoryError("legacy World Memory Episode could not be appended") from exc
        # Forget may have committed after Raw insertion but before this derived
        # append. Tombstone wins; never leave a compatibility Episode behind.
        if self.is_session_forgotten(session_id):
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                raise WorldMemoryError("forgotten World Memory Episode could not be removed") from exc

    def _legacy_episode_text(self, path: Path) -> str | None:
        if not path.is_file():
            return None
        try:
            return path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            return None
        except OSError as exc:
            raise WorldMemoryError("legacy World Memory Episode could not be verified") from exc

    def _rebuild_legacy_episode_from_raw(self, session_id: str) -> None:
        path = self._episode_path(session_id)
        entries = self.raw_entries_for_session(session_id)
        lines = [
            "# World Memory Episode",
            "",
            f"session_id: {session_id}",
            f"episode_id: {session_id}-E001",
            "",
            "## 公開会話",
        ]
        for entry in entries:
            sender = str(entry.get("from") or "")
            text = str(entry.get("text") or "")
            ts = str(entry.get("ts") or "")
            label = "Master" if sender == "master" else sender
            clean_text = " ".join(text.split())[:240]
            lines.append(self.entry_marker(entry))
            lines.append(f"- {ts} {label}: {clean_text}")
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        except OSError as exc:
            raise WorldMemoryError("legacy World Memory Episode could not be repaired") from exc

    def is_session_forgotten(self, session_id: str) -> bool:
        self._validate_session_id(session_id)
        with closing(self._connect()) as connection:
            return connection.execute(
                "SELECT 1 FROM forgotten_sessions WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone() is not None

    def raw_entries_for_session(self, session_id: str) -> list[dict[str, Any]]:
        self._validate_session_id(session_id)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT payload_json FROM raw_entries WHERE session_id = ? ORDER BY occurred_at, rowid",
                (session_id,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for (payload_json,) in rows:
            try:
                parsed = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                result.append(parsed)
        return result

    def has_raw_session(self, session_id: str) -> bool:
        self._validate_session_id(session_id)
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT 1 FROM raw_entries WHERE session_id = ? LIMIT 1",
                (session_id,),
            ).fetchone()
        return row is not None

    def pending_raw_entries(self, *, limit: int = 50) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 500))
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT r.payload_json
                FROM raw_entries AS r
                JOIN structured_jobs AS j ON j.raw_id = r.raw_id
                WHERE j.status = 'pending'
                ORDER BY r.occurred_at, r.rowid
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for (payload_json,) in rows:
            try:
                parsed = json.loads(payload_json)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(parsed, dict):
                result.append(parsed)
        return result

    def _ensure_raw_schema(self) -> None:
        self.memory_root.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_entries (
                    raw_id TEXT PRIMARY KEY,
                    entry_id TEXT,
                    session_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT,
                    text TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS raw_entries_session_time ON raw_entries(session_id, occurred_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS structured_jobs (
                    raw_id TEXT PRIMARY KEY REFERENCES raw_entries(raw_id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS structured_jobs_status_raw ON structured_jobs(status, raw_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forgotten_sessions (
                    session_id TEXT PRIMARY KEY,
                    forgotten_at TEXT NOT NULL
                )
                """
            )
            self._ensure_raw_fts(connection)
            connection.commit()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _append_raw_entry(self, entry: dict[str, Any]) -> bool:
        raw_id = self.raw_entry_id(entry)
        entry_id = entry.get("entry_id")
        session_id = str(entry["session"])
        recipient = entry.get("to")
        payload_json = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        with closing(self._connect()) as connection:
            try:
                # Serialize against forget_session. Once the tombstone commits,
                # no late/outbox replay may recreate Raw or derived rows.
                connection.execute("BEGIN IMMEDIATE")
                forgotten = connection.execute(
                    "SELECT 1 FROM forgotten_sessions WHERE session_id = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                if forgotten is not None:
                    connection.commit()
                    return False
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO raw_entries(
                        raw_id, entry_id, session_id, kind, sender, recipient,
                        text, occurred_at, payload_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        raw_id,
                        entry_id if isinstance(entry_id, str) and entry_id else None,
                        session_id,
                        str(entry["kind"]),
                        str(entry["from"]),
                        recipient if isinstance(recipient, str) and recipient else None,
                        str(entry["text"]),
                        str(entry["ts"]),
                        payload_json,
                    ),
                )
                inserted = bool(cursor.rowcount)
                if inserted:
                    connection.execute(
                        "INSERT OR IGNORE INTO structured_jobs(raw_id, status) VALUES(?, 'pending')",
                        (raw_id,),
                    )
                    connection.execute(
                        "INSERT INTO raw_fts(raw_id, text, search_text) VALUES(?, ?, ?)",
                        (raw_id, str(entry["text"]), self.raw_search_text(str(entry["text"]))),
                    )
                connection.commit()
                return inserted
            except sqlite3.Error as exc:
                connection.rollback()
                raise WorldMemoryError("World Memory Raw entry could not be committed") from exc

    def _ensure_raw_fts(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS raw_fts USING fts5(
                raw_id UNINDEXED,
                text UNINDEXED,
                search_text,
                tokenize='unicode61'
            )
            """
        )
        raw_count = int(connection.execute("SELECT COUNT(*) FROM raw_entries").fetchone()[0])
        fts_count = int(connection.execute("SELECT COUNT(*) FROM raw_fts").fetchone()[0])
        if raw_count == fts_count:
            return
        connection.execute("DELETE FROM raw_fts")
        for raw_id, text in connection.execute(
            "SELECT raw_id, text FROM raw_entries ORDER BY occurred_at, rowid"
        ).fetchall():
            connection.execute(
                "INSERT INTO raw_fts(raw_id, text, search_text) VALUES(?, ?, ?)",
                (str(raw_id), str(text), self.raw_search_text(str(text))),
            )

    @staticmethod
    def raw_search_terms(text: str) -> list[str]:
        return raw_search_terms(text)

    @classmethod
    def raw_search_text(cls, text: str) -> str:
        return " ".join(cls.raw_search_terms(text))

    @classmethod
    def raw_entry_id(cls, entry: dict[str, Any]) -> str:
        entry_id = entry.get("entry_id")
        if isinstance(entry_id, str) and entry_id:
            return f"entry:{entry_id}"
        marker = cls.entry_marker(entry)
        return f"legacy:{marker.removeprefix('<!-- entry:').removesuffix(' -->')}"

    @staticmethod
    def entry_marker(entry: dict[str, Any]) -> str:
        entry_id = entry.get("entry_id")
        if isinstance(entry_id, str) and entry_id:
            fingerprint_source = f"entry_id\x1f{entry_id}"
        else:
            # Legacy entries predate stable entry_id. Timestamp is part of the
            # fallback identity so a repeated sentence at a later time remains
            # a distinct fact while replay of the same stored entry dedupes.
            fingerprint_source = "\x1f".join([
                str(entry.get("session", "")),
                str(entry.get("kind", "")),
                str(entry.get("from", "")),
                str(entry.get("text", "")),
                str(entry.get("ts", "")),
                str(entry.get("request_id", "")),
                str(entry.get("task_id", "")),
                str(entry.get("agent_session_id", "")),
            ])
        return f"<!-- entry:{sha256(fingerprint_source.encode('utf-8')).hexdigest()[:20]} -->"

    def forget_session(self, session_id: str) -> int:
        self._validate_session_id(session_id)
        with closing(self._connect()) as connection:
            has_raw_vec = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_vec'"
            ).fetchone() is not None
            if has_raw_vec:
                try:
                    import sqlite_vec
                except ModuleNotFoundError as exc:
                    raise WorldMemoryError(
                        "World Memory Forget cannot verify/delete the vector index because sqlite-vec is unavailable"
                    ) from exc
                connection.enable_load_extension(True)
                try:
                    sqlite_vec.load(connection)
                finally:
                    connection.enable_load_extension(False)
            try:
                connection.execute("BEGIN IMMEDIATE")
                raw_ids = [
                    str(row[0])
                    for row in connection.execute(
                        "SELECT raw_id FROM raw_entries WHERE session_id = ?",
                        (session_id,),
                    ).fetchall()
                ]
                # Tombstone and deletion share one transaction. Even if Core
                # dies before Chat JSONL is removed, startup/outbox replay cannot
                # resurrect this public-memory session.
                connection.execute(
                    "INSERT OR IGNORE INTO forgotten_sessions(session_id, forgotten_at) VALUES(?, ?)",
                    (session_id, datetime.now().astimezone().isoformat(timespec="seconds")),
                )
                if has_raw_vec:
                    for raw_id in raw_ids:
                        connection.execute("DELETE FROM raw_vec WHERE raw_id = ?", (raw_id,))
                for raw_id in raw_ids:
                    connection.execute("DELETE FROM raw_fts WHERE raw_id = ?", (raw_id,))
                connection.execute("DELETE FROM raw_entries WHERE session_id = ?", (session_id,))
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise WorldMemoryError("World Memory Forget failed before all durable/derived rows were removed") from exc

        deleted_episodes = 0
        for path in self.episodes_root.glob(f"{session_id}-E*.md"):
            path.unlink(missing_ok=True)
            deleted_episodes += 1
        return deleted_episodes

    def episodes_for_session(self, session_id: str) -> list[Path]:
        self._validate_session_id(session_id)
        return sorted(self.episodes_root.glob(f"{session_id}-E*.md"))

    def _episode_path(self, session_id: str) -> Path:
        self._validate_session_id(session_id)
        return self.episodes_root / f"{session_id}-E001.md"

    @staticmethod
    def _validate_session_id(session_id: str) -> None:
        if not session_id or any(character in session_id for character in '/\\:*?"<>|'):
            raise WorldMemoryError("invalid session id")
