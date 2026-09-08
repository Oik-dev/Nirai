from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from pathlib import Path
import re
import sqlite3
from typing import Any


class PrivateMemoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrivateMemoryHit:
    raw_id: str
    entry_id: str | None
    public_session_id: str
    excerpt: str
    score: float
    occurred_at: str
    source: str = "private-fts5"

    def to_context(self, resident_name: str) -> dict[str, object]:
        label = self.entry_id or self.raw_id
        return {
            "memory_id": label,
            "resident": resident_name,
            "path": f"residents/{resident_name}/private/private_memory.sqlite3#{self.raw_id}",
            "excerpt": self.excerpt,
            "source": self.source,
            "occurred_at": self.occurred_at,
        }


class PrivateMemoryService:
    """Resident-scoped Private Memory authority.

    `private_memory.sqlite3` is the active Raw/FTS store. Legacy
    `whispers.jsonl` is kept as a compatibility log and imported idempotently,
    but normal recent/delta/retrieval paths never parse the whole JSONL file.
    """

    DEFAULT_TOP_K = 4
    MAX_TOP_K = 10
    LEXICAL_COVERAGE_FLOOR = 0.18
    STRONG_LEXICAL_COVERAGE_FLOOR = 0.42

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.residents_root = self.root / "residents"
        self._ready_residents: set[str] = set()

    def append_whisper(
        self,
        resident_name: str,
        *,
        session_id: str,
        sender: str,
        recipient: str,
        text: str,
        request_id: str | None = None,
        ts: str | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        cleaned = text.strip()
        if not cleaned:
            raise PrivateMemoryError("whisper text must not be empty")
        private_dir = self._ensure_store(resident_name)
        entry: dict[str, Any] = {
            "ts": ts or datetime.now().astimezone().isoformat(timespec="seconds"),
            "session": session_id,
            "from": sender,
            "to": recipient,
            "text": cleaned,
        }
        if entry_id is not None:
            if not isinstance(entry_id, str) or not entry_id.strip():
                raise PrivateMemoryError("whisper entry_id must be a non-empty string")
            entry["entry_id"] = entry_id.strip()
        if request_id is not None:
            entry["request_id"] = request_id

        inserted = self._insert_raw_entry(private_dir, entry)
        if inserted:
            whispers_path = private_dir / "whispers.jsonl"
            with whispers_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
            self._record_legacy_signature(private_dir)
        self._refresh_context(resident_name)
        return entry

    def recent_whispers(self, resident_name: str, limit: int = 20) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        private_dir = self._ensure_store(resident_name)
        bounded = max(1, min(int(limit), 500))
        with closing(self._connect(private_dir)) as connection:
            rows = connection.execute(
                """
                SELECT payload_json
                FROM raw_entries
                ORDER BY rowid DESC
                LIMIT ?
                """,
                (bounded,),
            ).fetchall()
        result = [self._parse_payload(row[0]) for row in reversed(rows)]
        return [entry for entry in result if entry is not None]

    def whispers_after(
        self,
        resident_name: str,
        after_entry_id: str | None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        private_dir = self._ensure_store(resident_name)
        with closing(self._connect(private_dir)) as connection:
            if after_entry_id is None:
                if limit is None:
                    rows = connection.execute(
                        "SELECT payload_json FROM raw_entries ORDER BY rowid"
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT payload_json FROM raw_entries ORDER BY rowid LIMIT ?",
                        (max(0, int(limit)),),
                    ).fetchall()
            else:
                marker = connection.execute(
                    "SELECT rowid FROM raw_entries WHERE entry_id = ? LIMIT 1",
                    (after_entry_id,),
                ).fetchone()
                if marker is None:
                    raise PrivateMemoryError(
                        f"private whisper continuation marker was not found: {resident_name}/{after_entry_id}"
                    )
                if limit is None:
                    rows = connection.execute(
                        "SELECT payload_json FROM raw_entries WHERE rowid > ? ORDER BY rowid",
                        (int(marker[0]),),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT payload_json FROM raw_entries WHERE rowid > ? ORDER BY rowid LIMIT ?",
                        (int(marker[0]), max(0, int(limit))),
                    ).fetchall()
        result = [self._parse_payload(row[0]) for row in rows]
        return [entry for entry in result if entry is not None]

    def context_for_brain(self, resident_name: str, session_id: str) -> dict[str, Any]:
        private_dir = self._ensure_store(resident_name)
        context_path = private_dir / "context.md"
        context_text = context_path.read_text(encoding="utf-8") if context_path.exists() else ""
        recent = self.recent_whispers(resident_name, 20)
        current_session = [entry for entry in recent if entry.get("session") == session_id]
        return {
            "private_context": context_text,
            "recent_whispers": recent,
            "current_whisper_history": current_session,
        }

    def search(
        self,
        resident_name: str,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        exclude_entry_ids: set[str] | None = None,
    ) -> list[PrivateMemoryHit]:
        cleaned = " ".join(query.split()).strip()
        if not cleaned:
            return []
        terms = self.raw_search_terms(cleaned)
        if not terms:
            return []
        private_dir = self._ensure_store(resident_name)
        excluded = set(exclude_entry_ids or ())
        bounded_top_k = max(1, min(int(top_k), self.MAX_TOP_K))
        match_query = " OR ".join(f'"{term}"' for term in terms[:64])
        candidate_limit = max(20, bounded_top_k * 6)
        with closing(self._connect(private_dir)) as connection:
            rows = connection.execute(
                """
                SELECT r.raw_id, r.entry_id, r.public_session_id, r.text, r.occurred_at,
                       bm25(raw_fts) AS rank
                FROM raw_fts
                JOIN raw_entries AS r ON r.raw_id = raw_fts.raw_id
                WHERE raw_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match_query, candidate_limit),
            ).fetchall()

        hits: list[PrivateMemoryHit] = []
        for row in rows:
            entry_id = str(row["entry_id"]) if row["entry_id"] is not None else None
            if entry_id is not None and entry_id in excluded:
                continue
            text = str(row["text"])
            coverage = self._lexical_coverage(cleaned, text)
            anchor = self._has_distinctive_anchor_match(cleaned, text)
            if coverage < self.LEXICAL_COVERAGE_FLOOR and not anchor:
                continue
            hits.append(
                PrivateMemoryHit(
                    raw_id=str(row["raw_id"]),
                    entry_id=entry_id,
                    public_session_id=str(row["public_session_id"]),
                    excerpt=text,
                    score=max(coverage, 1.0 if anchor else 0.0),
                    occurred_at=str(row["occurred_at"]),
                )
            )
        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:bounded_top_k]

    def raw_count(self, resident_name: str) -> int:
        private_dir = self._ensure_store(resident_name)
        with closing(self._connect(private_dir)) as connection:
            return int(connection.execute("SELECT COUNT(*) FROM raw_entries").fetchone()[0])

    def private_db_path(self, resident_name: str) -> Path:
        return self._ensure_store(resident_name) / "private_memory.sqlite3"

    def forget_entry(self, resident_name: str, entry_id: str) -> bool:
        cleaned_entry_id = entry_id.strip()
        if not cleaned_entry_id:
            raise PrivateMemoryError("private forget entry_id must not be empty")
        private_dir = self._ensure_store(resident_name)
        removed = False
        with closing(self._connect(private_dir)) as connection:
            row = connection.execute(
                "SELECT raw_id FROM raw_entries WHERE entry_id = ? LIMIT 1",
                (cleaned_entry_id,),
            ).fetchone()
            raw_id = str(row[0]) if row is not None else None
            has_raw_vec = raw_id is not None and connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_vec'"
            ).fetchone() is not None
            if has_raw_vec:
                try:
                    import sqlite_vec
                except ModuleNotFoundError as exc:
                    raise PrivateMemoryError(
                        "Private Memory Forget cannot verify/delete the vector index because sqlite-vec is unavailable"
                    ) from exc
                connection.enable_load_extension(True)
                try:
                    sqlite_vec.load(connection)
                finally:
                    connection.enable_load_extension(False)
            try:
                connection.execute("BEGIN IMMEDIATE")
                # The tombstone is authoritative and commits in the same SQLite
                # transaction as deletion. If Core dies before compatibility
                # files are rebuilt, a later legacy import cannot resurrect the
                # forgotten entry and first Private access repairs those files.
                connection.execute(
                    "INSERT OR IGNORE INTO forgotten_entries(entry_id, forgotten_at) VALUES(?, ?)",
                    (cleaned_entry_id, datetime.now().astimezone().isoformat(timespec="seconds")),
                )
                if raw_id is not None:
                    if has_raw_vec:
                        connection.execute("DELETE FROM raw_vec WHERE raw_id = ?", (raw_id,))
                    connection.execute("DELETE FROM raw_fts WHERE raw_id = ?", (raw_id,))
                    connection.execute("DELETE FROM raw_entries WHERE raw_id = ?", (raw_id,))
                    removed = True
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise PrivateMemoryError(
                    "Private Memory Forget failed before all durable/derived rows were removed"
                ) from exc
        self._rewrite_legacy_jsonl(private_dir)
        self._refresh_context(resident_name)
        return removed

    def _ensure_store(self, resident_name: str) -> Path:
        private_dir = self._private_dir(resident_name)
        private_dir.mkdir(parents=True, exist_ok=True)
        if resident_name not in self._ready_residents:
            self._ensure_schema(private_dir)
            self._import_legacy_jsonl_if_changed(private_dir)
            self._ready_residents.add(resident_name)
            # Compatibility/context files are derived from SQLite. Rebuild once
            # on first access so a crash after a committed Forget cannot leave
            # stale private text visible to Brain context or on disk indefinitely.
            self._rewrite_legacy_jsonl(private_dir)
            self._refresh_context(resident_name)
        return private_dir

    def _ensure_schema(self, private_dir: Path) -> None:
        with closing(self._connect(private_dir)) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS raw_entries (
                    raw_id TEXT PRIMARY KEY,
                    entry_id TEXT UNIQUE,
                    public_session_id TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    text TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    request_id TEXT,
                    payload_json TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS raw_entries_time ON raw_entries(occurred_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS raw_entries_public_session ON raw_entries(public_session_id)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS migration_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS forgotten_entries (
                    entry_id TEXT PRIMARY KEY,
                    forgotten_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS embedding_jobs (
                    raw_id TEXT PRIMARY KEY REFERENCES raw_entries(raw_id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_attempt_at TEXT
                )
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO embedding_jobs(raw_id, status)
                SELECT raw_id, 'pending' FROM raw_entries
                """
            )
            self._ensure_fts(connection)
            connection.commit()

    def _connect(self, private_dir: Path) -> sqlite3.Connection:
        connection = sqlite3.connect(private_dir / "private_memory.sqlite3", timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_fts(self, connection: sqlite3.Connection) -> None:
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
        for row in connection.execute("SELECT raw_id, text FROM raw_entries ORDER BY rowid").fetchall():
            connection.execute(
                "INSERT INTO raw_fts(raw_id, text, search_text) VALUES(?, ?, ?)",
                (str(row[0]), str(row[1]), self.raw_search_text(str(row[1]))),
            )

    def _insert_raw_entry(
        self,
        private_dir: Path,
        entry: dict[str, Any],
        *,
        connection: sqlite3.Connection | None = None,
    ) -> bool:
        own_connection = connection is None
        active = connection or self._connect(private_dir)
        try:
            entry_id = entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None
            if entry_id is not None:
                forgotten = active.execute(
                    "SELECT 1 FROM forgotten_entries WHERE entry_id = ? LIMIT 1",
                    (entry_id,),
                ).fetchone()
                if forgotten is not None:
                    return False
            raw_id = self.raw_entry_id(entry)
            payload_json = json.dumps(entry, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
            cursor = active.execute(
                """
                INSERT OR IGNORE INTO raw_entries(
                    raw_id, entry_id, public_session_id, sender, recipient,
                    text, occurred_at, request_id, payload_json
                ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    raw_id,
                    entry.get("entry_id") if isinstance(entry.get("entry_id"), str) else None,
                    str(entry.get("session", "")),
                    str(entry.get("from", "")),
                    str(entry.get("to", "")),
                    str(entry.get("text", "")),
                    str(entry.get("ts", "")),
                    entry.get("request_id") if isinstance(entry.get("request_id"), str) else None,
                    payload_json,
                ),
            )
            inserted = bool(cursor.rowcount)
            if inserted:
                active.execute(
                    "INSERT INTO raw_fts(raw_id, text, search_text) VALUES(?, ?, ?)",
                    (raw_id, str(entry.get("text", "")), self.raw_search_text(str(entry.get("text", "")))),
                )
                active.execute(
                    "INSERT OR IGNORE INTO embedding_jobs(raw_id, status) VALUES(?, 'pending')",
                    (raw_id,),
                )
            if own_connection:
                active.commit()
            return inserted
        finally:
            if own_connection:
                active.close()

    def _import_legacy_jsonl_if_changed(self, private_dir: Path) -> None:
        path = private_dir / "whispers.jsonl"
        if not path.exists():
            return
        stat = path.stat()
        signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        with closing(self._connect(private_dir)) as connection:
            previous = connection.execute(
                "SELECT value FROM migration_meta WHERE key='legacy_jsonl_signature'"
            ).fetchone()
            if previous is not None and str(previous[0]) == signature:
                return
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    parsed = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not self._valid_raw_entry(parsed):
                    continue
                self._insert_raw_entry(private_dir, parsed, connection=connection)
            connection.execute(
                """
                INSERT INTO migration_meta(key, value) VALUES('legacy_jsonl_signature', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (signature,),
            )
            connection.commit()

    def _record_legacy_signature(self, private_dir: Path) -> None:
        path = private_dir / "whispers.jsonl"
        if not path.exists():
            return
        stat = path.stat()
        signature = f"{stat.st_size}:{stat.st_mtime_ns}"
        with closing(self._connect(private_dir)) as connection:
            connection.execute(
                """
                INSERT INTO migration_meta(key, value) VALUES('legacy_jsonl_signature', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (signature,),
            )
            connection.commit()

    def _rewrite_legacy_jsonl(self, private_dir: Path) -> None:
        path = private_dir / "whispers.jsonl"
        with closing(self._connect(private_dir)) as connection:
            rows = connection.execute("SELECT payload_json FROM raw_entries ORDER BY rowid").fetchall()
        payload = "".join(f"{row[0]}\n" for row in rows)
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        self._record_legacy_signature(private_dir)

    def _refresh_context(self, resident_name: str) -> None:
        entries = self.recent_whispers(resident_name, 12)
        lines = ["# Private Context", "", "## 前回までのWhisper"]
        if not entries:
            lines.append("- まだWhisperはありません。")
        else:
            for entry in entries:
                sender = entry.get("from") if isinstance(entry.get("from"), str) else "?"
                recipient = entry.get("to") if isinstance(entry.get("to"), str) else "?"
                text = entry.get("text") if isinstance(entry.get("text"), str) else ""
                ts = entry.get("ts") if isinstance(entry.get("ts"), str) else ""
                excerpt = " ".join(text.split())[:240]
                lines.append(f"- {ts} {sender} → {recipient}: {excerpt}")
        payload = "\n".join(lines).strip() + "\n"
        if len(payload) > 4096:
            payload = payload[-4096:]
            payload = "# Private Context\n\n## 前回までのWhisper\n" + payload.split("\n", 2)[-1]
        path = self._private_dir(resident_name) / "context.md"
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)

    def _private_dir(self, resident_name: str) -> Path:
        resident_dir = (self.residents_root / resident_name).resolve()
        try:
            resident_dir.relative_to(self.residents_root.resolve())
        except ValueError as exc:
            raise PrivateMemoryError("Resident path escaped residents root") from exc
        if not (resident_dir / "config.toml").is_file():
            raise PrivateMemoryError(f"Resident not found: {resident_name}")
        return resident_dir / "private"

    @staticmethod
    def _valid_raw_entry(value: Any) -> bool:
        if not isinstance(value, dict):
            return False
        return all(
            isinstance(value.get(key), str) and bool(str(value.get(key)).strip())
            for key in ("ts", "session", "from", "to", "text")
        )

    @staticmethod
    def _parse_payload(value: Any) -> dict[str, Any] | None:
        try:
            parsed = json.loads(str(value))
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    @classmethod
    def raw_entry_id(cls, entry: dict[str, Any]) -> str:
        entry_id = entry.get("entry_id")
        if isinstance(entry_id, str) and entry_id:
            return f"entry:{entry_id}"
        fingerprint = "\x1f".join(
            [
                str(entry.get("session", "")),
                str(entry.get("from", "")),
                str(entry.get("to", "")),
                str(entry.get("text", "")),
                str(entry.get("ts", "")),
                str(entry.get("request_id", "")),
            ]
        )
        return f"legacy:{sha256(fingerprint.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def raw_search_terms(text: str) -> list[str]:
        runs = [
            match.group(0).casefold()
            for match in re.finditer(r"[0-9A-Za-z_\u3040-\u30ff\u3400-\u9fff]+", text)
            if match.group(0)
        ]
        terms: list[str] = []
        for run in runs:
            if len(run) < 2:
                continue
            terms.extend(run[index : index + 2] for index in range(len(run) - 1))
        return list(dict.fromkeys(terms))

    @classmethod
    def raw_search_text(cls, text: str) -> str:
        return " ".join(cls.raw_search_terms(text))

    @staticmethod
    def _normalize_lexical_text(text: str) -> str:
        return re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ヴー]+", "", text).casefold()

    @classmethod
    def _character_ngrams(cls, text: str, size: int) -> list[str]:
        compact = cls._normalize_lexical_text(text)
        if len(compact) < size:
            return [compact] if len(compact) >= 2 else []
        return list(dict.fromkeys(compact[index : index + size] for index in range(len(compact) - size + 1)))

    @classmethod
    def _lexical_coverage(cls, question: str, text: str) -> float:
        query_grams = set(cls._character_ngrams(question, 3))
        if not query_grams:
            query_grams = set(cls._character_ngrams(question, 2))
        if not query_grams:
            return 0.0
        text_grams = set(cls._character_ngrams(text, 3))
        if not text_grams:
            text_grams = set(cls._character_ngrams(text, 2))
        return len(query_grams & text_grams) / len(query_grams)

    @staticmethod
    def _has_distinctive_anchor_match(question: str, text: str) -> bool:
        query_tokens = {
            token.casefold()
            for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", question)
            if len(token) >= 2
            and (len(token) >= 4 or any(char.isdigit() for char in token) or any(char in "._:/-" for char in token))
        }
        if not query_tokens:
            return False
        haystack = text.casefold()
        return any(token in haystack for token in query_tokens)
