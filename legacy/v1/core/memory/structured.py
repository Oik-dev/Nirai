from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import quote

try:
    import sqlite_vec
except ModuleNotFoundError:  # optional at import time; Store initialization fails soft
    sqlite_vec = None  # type: ignore[assignment]

from ..brains.gemini import (
    _extract_interaction_text,
    _request_json_async,
    load_gemini_api_key,
)


class StructuredMemoryProcessorError(RuntimeError):
    pass


@dataclass(frozen=True)
class StructuredMemoryCandidate:
    quote: str
    kind: str
    subject: str
    attribute: str
    value: str
    statement: str
    certainty: str
    explicit_correction: bool = False
    previous_value: str | None = None


GEMINI_MEMORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quote": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": ["fact", "promise", "preference", "relationship"],
                    },
                    "subject": {"type": "string", "enum": ["master", "resident", "other"]},
                    "attribute": {"type": "string"},
                    "value": {"type": "string"},
                    "statement": {"type": "string"},
                    "certainty": {"type": "string", "enum": ["confirmed", "hypothesis"]},
                    "explicit_correction": {"type": "boolean"},
                    "previous_value": {"type": ["string", "null"]},
                },
                "required": [
                    "quote",
                    "kind",
                    "subject",
                    "attribute",
                    "value",
                    "statement",
                    "certainty",
                    "explicit_correction",
                    "previous_value",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}


WORLD_MEMORY_EXTRACTION_INSTRUCTION = """
You are Nirai's background memory extractor. Extract only durable information explicitly supported by the single RAW utterance below.

Keep only:
- confirmed durable facts
- promises / future commitments
- sustained preferences
- sustained relationship changes
- uncertain but potentially durable plans only as certainty=hypothesis

Kind rules:
- preference = stable likes/dislikes or choices only
- promise = an explicit commitment/agreement to do something
- relationship = sustained relationship state/change
- fact = plans, status, identity, dates, codes, and other durable propositions, including uncertain plans when certainty=hypothesis

Do not keep:
- one-off mood, meal, weather, or incidental activity
- guesses or implications not stated in RAW

Rules:
1. Every candidate MUST contain quote copied verbatim as a contiguous substring of RAW.
2. subject must identify who the information is about. Do not swap speakers. The RAW speaker is provided separately.
3. certainty is confirmed or hypothesis. Words like "maybe", "might", "not decided" require hypothesis.
4. explicit_correction is true only when RAW explicitly corrects/replaces an earlier value.
5. previous_value is only the old value explicitly stated by RAW; otherwise null.
6. Preserve the RAW language and lexical values. Never translate values such as 赤→red or 青→blue.
7. If a sentence contains both a durable plan and a separate commitment, extracting both is allowed when both independently matter later.
8. If nothing deserves durable memory, return an empty candidates array.
9. Return only the schema-conforming result.
""".strip()


class GeminiWorldMemoryProcessor:
    """Cloud processor for public World Memory derived data.

    This class never owns Raw Memory and never mutates continuity state. It only
    returns candidate derivations; local storage/validation remains authoritative.
    """

    def __init__(
        self,
        nirai_root: Path,
        *,
        extraction_model: str = "gemini-3.5-flash-lite",
        embedding_model: str = "gemini-embedding-2",
        embedding_dim: int = 768,
        timeout_sec: float = 60.0,
        poll_interval_sec: float = 0.5,
    ) -> None:
        self.root = nirai_root.resolve()
        self.api_key = load_gemini_api_key(self.root)
        self.extraction_model = extraction_model
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self.timeout_sec = timeout_sec
        self.poll_interval_sec = poll_interval_sec
        if self.api_key is None:
            raise StructuredMemoryProcessorError("GEMINI_API_KEY was not found in world/.env")

    async def extract(
        self,
        text: str,
        *,
        speaker_id: str,
    ) -> list[StructuredMemoryCandidate]:
        payload = {
            "model": self.extraction_model,
            "system_instruction": WORLD_MEMORY_EXTRACTION_INSTRUCTION,
            "input": f"RAW SPEAKER: {speaker_id}\nRAW:\n{text}",
            "store": False,
            "generation_config": {
                "thinking_level": "minimal",
                "seed": 1,
            },
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": GEMINI_MEMORY_SCHEMA,
            },
        }

        async def execute() -> dict[str, Any]:
            response = await _request_json_async(self.api_key or "", "/interactions", payload)
            interaction_id = response.get("id")
            while True:
                status = response.get("status")
                if status == "completed":
                    return response
                if status not in {"queued", "in_progress"}:
                    raise StructuredMemoryProcessorError(
                        f"Gemini extraction interaction failed: status={status}"
                    )
                if not isinstance(interaction_id, str) or not interaction_id:
                    raise StructuredMemoryProcessorError("Gemini extraction returned no interaction id")
                await asyncio.sleep(self.poll_interval_sec)
                response = await _request_json_async(
                    self.api_key or "",
                    f"/interactions/{quote(interaction_id, safe='')}",
                    method="GET",
                )

        try:
            response = await asyncio.wait_for(execute(), timeout=self.timeout_sec)
        except asyncio.TimeoutError as exc:
            raise StructuredMemoryProcessorError("Gemini extraction timed out") from exc
        try:
            parsed = json.loads(_extract_interaction_text(response))
        except json.JSONDecodeError as exc:
            raise StructuredMemoryProcessorError("Gemini extraction output was not valid JSON") from exc
        raw_candidates = parsed.get("candidates") if isinstance(parsed, dict) else None
        if not isinstance(raw_candidates, list):
            raise StructuredMemoryProcessorError("Gemini extraction candidates must be a list")
        return [_candidate(item) for item in raw_candidates]

    async def embed_document(self, text: str) -> list[float]:
        return await self._embed(text, task_type="RETRIEVAL_DOCUMENT")

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if len(texts) == 1:
            return [await self.embed_document(texts[0])]
        payload = {
            "requests": [
                {
                    "model": f"models/{self.embedding_model}",
                    "content": {"parts": [{"text": text}]},
                    "embedContentConfig": {
                        "taskType": "RETRIEVAL_DOCUMENT",
                        "outputDimensionality": self.embedding_dim,
                        "autoTruncate": False,
                    },
                }
                for text in texts
            ]
        }
        try:
            parsed = await _request_json_async(
                self.api_key or "",
                f"/models/{self.embedding_model}:batchEmbedContents",
                payload,
            )
        except Exception as exc:  # noqa: BLE001
            raise StructuredMemoryProcessorError(f"Gemini batch embedding failed: {exc}") from exc
        raw_embeddings = parsed.get("embeddings") if isinstance(parsed, dict) else None
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            raise StructuredMemoryProcessorError("Gemini batch embedding response count mismatch")
        result: list[list[float]] = []
        for raw_embedding in raw_embeddings:
            values = raw_embedding.get("values") if isinstance(raw_embedding, dict) else None
            if not isinstance(values, list) or len(values) != self.embedding_dim:
                raise StructuredMemoryProcessorError("Gemini batch embedding response contained an invalid vector")
            result.append([float(value) for value in values])
        return result

    async def embed_query(self, text: str) -> list[float]:
        return await self._embed(text, task_type="RETRIEVAL_QUERY")

    async def _embed(self, text: str, *, task_type: str) -> list[float]:
        payload = {
            "model": f"models/{self.embedding_model}",
            "content": {"parts": [{"text": text}]},
            "embedContentConfig": {
                "taskType": task_type,
                "outputDimensionality": self.embedding_dim,
                "autoTruncate": False,
            },
        }
        try:
            parsed = await _request_json_async(
                self.api_key or "",
                f"/models/{self.embedding_model}:embedContent",
                payload,
            )
        except Exception as exc:  # noqa: BLE001
            raise StructuredMemoryProcessorError(f"Gemini embedding failed: {exc}") from exc
        embedding = parsed.get("embedding") if isinstance(parsed, dict) else None
        values = embedding.get("values") if isinstance(embedding, dict) else None
        if not isinstance(values, list) or not values:
            raise StructuredMemoryProcessorError("Gemini embedding response contained no vector")
        return [float(value) for value in values]


@dataclass(frozen=True)
class WorldStructuredJob:
    raw_id: str
    text: str
    speaker_id: str
    occurred_at: str
    attempts: int


@dataclass(frozen=True)
class WorldMemoryProcessingSummary:
    processed: int
    failed: int
    candidates_committed: int
    budget_exhausted: bool = False
    vectors_rebuilt: int = 0


class WorldStructuredMemoryStore:
    """Local authority for derived public Structured Memory.

    Raw entries remain authoritative in `raw_entries`. Gemini output is accepted
    only after exact-quote and scope checks, then committed transactionally with
    job state. The vector table is derived and rebuildable.
    """

    def __init__(
        self,
        root: Path,
        *,
        vector_dim: int = 768,
        embedding_model: str = "gemini-embedding-2",
    ) -> None:
        self.root = root.resolve()
        self.db_path = self.root / "world_memory" / "world_memory.sqlite3"
        self.vector_dim = vector_dim
        self.embedding_model = embedding_model
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        if sqlite_vec is None:
            raise StructuredMemoryProcessorError("sqlite-vec is not installed")
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            raw_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_entries'"
            ).fetchone()
            if raw_table is None:
                raise StructuredMemoryProcessorError(
                    "World Raw Memory schema is missing; initialize WorldMemoryService first"
                )
            job_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(structured_jobs)").fetchall()
            }
            if "next_attempt_at" not in job_columns:
                connection.execute("ALTER TABLE structured_jobs ADD COLUMN next_attempt_at TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS atomic_memories (
                    memory_id TEXT PRIMARY KEY,
                    raw_id TEXT NOT NULL REFERENCES raw_entries(raw_id) ON DELETE CASCADE,
                    scope TEXT NOT NULL DEFAULT 'public',
                    kind TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    attribute TEXT NOT NULL,
                    value TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    certainty TEXT NOT NULL,
                    explicit_correction INTEGER NOT NULL DEFAULT 0,
                    previous_value TEXT,
                    evidence_quote TEXT NOT NULL,
                    status TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            # Early development builds stored the generic subject label
            # `resident` in atomic_memories while current_facts already used a
            # resident-specific key. Normalize derived rows from Raw provenance
            # so Structured Recall and Current Fact use the same identity key.
            connection.execute(
                """
                UPDATE atomic_memories
                SET subject = CASE
                    WHEN lower((SELECT sender FROM raw_entries WHERE raw_id = atomic_memories.raw_id)) = 'master'
                        THEN 'resident:unknown'
                    ELSE 'resident:' || (SELECT sender FROM raw_entries WHERE raw_id = atomic_memories.raw_id)
                END
                WHERE subject = 'resident'
                  AND EXISTS(SELECT 1 FROM raw_entries WHERE raw_id = atomic_memories.raw_id)
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS atomic_scope_subject_attr ON atomic_memories(scope, subject, attribute, status)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS current_facts (
                    scope TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    attribute TEXT NOT NULL,
                    memory_id TEXT NOT NULL REFERENCES atomic_memories(memory_id) ON DELETE CASCADE,
                    value TEXT NOT NULL,
                    PRIMARY KEY(scope, subject, attribute)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS derived_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS vector_rebuild_jobs (
                    raw_id TEXT PRIMARY KEY REFERENCES raw_entries(raw_id) ON DELETE CASCADE,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    next_attempt_at TEXT
                )
                """
            )
            vector_row = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_vec'"
            ).fetchone()
            vector_sql = str(vector_row["sql"]) if vector_row is not None and vector_row["sql"] is not None else ""
            model_row = connection.execute(
                "SELECT value FROM derived_meta WHERE key='embedding_model'"
            ).fetchone()
            stored_model = str(model_row["value"]) if model_row is not None else None
            vector_incompatible = bool(vector_sql) and f"float[{self.vector_dim}]" not in vector_sql
            model_incompatible = stored_model is not None and stored_model != self.embedding_model
            if vector_incompatible or model_incompatible:
                connection.execute("DROP TABLE IF EXISTS raw_vec")
                connection.execute("DELETE FROM vector_rebuild_jobs")
                connection.execute(
                    "INSERT OR IGNORE INTO vector_rebuild_jobs(raw_id) SELECT raw_id FROM raw_entries"
                )
            connection.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS raw_vec USING vec0(
                    raw_id TEXT PRIMARY KEY,
                    embedding float[{self.vector_dim}] distance_metric=cosine
                )
                """
            )
            connection.execute(
                "INSERT INTO derived_meta(key, value) VALUES('embedding_model', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (self.embedding_model,),
            )
            connection.execute(
                "INSERT INTO derived_meta(key, value) VALUES('embedding_dim', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(self.vector_dim),),
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cloud_usage (
                    usage_day TEXT PRIMARY KEY,
                    background_jobs INTEGER NOT NULL DEFAULT 0,
                    query_embeddings INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            usage_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(cloud_usage)").fetchall()
            }
            if "query_embeddings" not in usage_columns:
                connection.execute(
                    "ALTER TABLE cloud_usage ADD COLUMN query_embeddings INTEGER NOT NULL DEFAULT 0"
                )
            # RPD resets on the provider's Pacific-time calendar day. A local
            # rolling 24h guard is intentionally stricter and avoids depending
            # on OS timezone data while still preventing quota overshoot across
            # mismatched UTC/JST/provider day boundaries.
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS cloud_usage_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    used_at TEXT NOT NULL,
                    kind TEXT NOT NULL CHECK(kind IN ('background', 'query'))
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS cloud_usage_events_time_kind ON cloud_usage_events(used_at, kind)"
            )
            connection.commit()

    def reserve_background_slot(
        self,
        *,
        daily_limit: int,
        embedding_daily_budget: int,
    ) -> bool:
        return self._reserve_cloud_slot(
            kind="background",
            kind_limit=daily_limit,
            embedding_daily_budget=embedding_daily_budget,
        )

    def reserve_query_embedding_slot(
        self,
        *,
        daily_limit: int,
        embedding_daily_budget: int,
    ) -> bool:
        return self._reserve_cloud_slot(
            kind="query",
            kind_limit=daily_limit,
            embedding_daily_budget=embedding_daily_budget,
        )

    def _reserve_cloud_slot(
        self,
        *,
        kind: str,
        kind_limit: int,
        embedding_daily_budget: int,
    ) -> bool:
        if kind not in {"background", "query"}:
            raise StructuredMemoryProcessorError(f"unsupported cloud usage kind: {kind}")
        if kind_limit <= 0 or embedding_daily_budget <= 0:
            return False
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(hours=24)
        prune_before = now - timedelta(hours=48)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM cloud_usage_events WHERE used_at < ?",
                (prune_before.isoformat(),),
            )
            rows = connection.execute(
                """
                SELECT kind, COUNT(*) AS count
                FROM cloud_usage_events
                WHERE used_at >= ?
                GROUP BY kind
                """,
                (cutoff.isoformat(),),
            ).fetchall()
            counts = {str(row["kind"]): int(row["count"]) for row in rows}
            kind_count = counts.get(kind, 0)
            total = counts.get("background", 0) + counts.get("query", 0)
            if kind_count >= kind_limit or total >= embedding_daily_budget:
                connection.rollback()
                return False
            connection.execute(
                "INSERT INTO cloud_usage_events(used_at, kind) VALUES(?, ?)",
                (now.isoformat(), kind),
            )
            connection.commit()
        return True

    def pending_jobs(self, *, limit: int = 5) -> list[WorldStructuredJob]:
        bounded = max(1, min(int(limit), 50))
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT r.raw_id, r.text, r.sender, r.occurred_at, j.attempts
                FROM raw_entries AS r
                JOIN structured_jobs AS j ON j.raw_id = r.raw_id
                WHERE j.status = 'pending'
                  AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= ?)
                ORDER BY r.occurred_at, r.rowid
                LIMIT ?
                """,
                (now, bounded),
            ).fetchall()
        return [
            WorldStructuredJob(
                raw_id=str(row["raw_id"]),
                text=str(row["text"]),
                speaker_id=str(row["sender"]),
                occurred_at=str(row["occurred_at"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]

    def commit_job(
        self,
        job: WorldStructuredJob,
        *,
        embedding: list[float],
        candidates: list[StructuredMemoryCandidate],
    ) -> int:
        if len(embedding) != self.vector_dim:
            raise StructuredMemoryProcessorError(
                f"embedding dimension mismatch: expected {self.vector_dim}, got {len(embedding)}"
            )
        accepted = self._validate_candidates(job, candidates)
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT 1
                FROM raw_entries AS r
                JOIN structured_jobs AS j ON j.raw_id = r.raw_id
                WHERE r.raw_id=? AND j.status='pending'
                LIMIT 1
                """,
                (job.raw_id,),
            ).fetchone()
            if current is None:
                # Forget/deletion may have invalidated this cloud result while
                # extraction/embedding was in flight. Never recreate derived
                # vectors or atomic memories for a Raw row that no longer exists.
                connection.rollback()
                return 0
            connection.execute("DELETE FROM raw_vec WHERE raw_id = ?", (job.raw_id,))
            connection.execute(
                "INSERT INTO raw_vec(raw_id, embedding) VALUES(?, ?)",
                (job.raw_id, sqlite_vec.serialize_float32(embedding)),
            )
            committed = 0
            for candidate in accepted:
                status = self._commit_candidate(connection, job, candidate)
                if status is not None:
                    committed += 1
            connection.execute(
                "UPDATE structured_jobs SET status='done', last_error=NULL, next_attempt_at=NULL WHERE raw_id=?",
                (job.raw_id,),
            )
            connection.execute("DELETE FROM vector_rebuild_jobs WHERE raw_id=?", (job.raw_id,))
            connection.commit()
        return committed

    def fail_job(self, job: WorldStructuredJob, error: str) -> None:
        attempts = job.attempts + 1
        delay_seconds = min(6 * 60 * 60, 60 * (2 ** min(attempts - 1, 8)))
        next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE structured_jobs
                SET status='pending', attempts=?, last_error=?, next_attempt_at=?
                WHERE raw_id=?
                """,
                (attempts, error[:1000], next_attempt.isoformat(), job.raw_id),
            )
            connection.commit()

    def pending_vector_rebuild_jobs(self, *, limit: int = 32) -> list[WorldStructuredJob]:
        bounded = max(1, min(int(limit), 64))
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT r.raw_id, r.text, r.sender, r.occurred_at, j.attempts
                FROM raw_entries AS r
                JOIN vector_rebuild_jobs AS j ON j.raw_id = r.raw_id
                WHERE j.next_attempt_at IS NULL OR j.next_attempt_at <= ?
                ORDER BY r.occurred_at, r.rowid
                LIMIT ?
                """,
                (now, bounded),
            ).fetchall()
        return [
            WorldStructuredJob(
                raw_id=str(row["raw_id"]),
                text=str(row["text"]),
                speaker_id=str(row["sender"]),
                occurred_at=str(row["occurred_at"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]

    def commit_vector_rebuild(self, job: WorldStructuredJob, embedding: list[float]) -> None:
        if len(embedding) != self.vector_dim:
            raise StructuredMemoryProcessorError(
                f"embedding dimension mismatch: expected {self.vector_dim}, got {len(embedding)}"
            )
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT 1
                FROM raw_entries AS r
                JOIN vector_rebuild_jobs AS j ON j.raw_id = r.raw_id
                WHERE r.raw_id=?
                LIMIT 1
                """,
                (job.raw_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                return
            connection.execute("DELETE FROM raw_vec WHERE raw_id=?", (job.raw_id,))
            connection.execute(
                "INSERT INTO raw_vec(raw_id, embedding) VALUES(?, ?)",
                (job.raw_id, sqlite_vec.serialize_float32(embedding)),
            )
            connection.execute("DELETE FROM vector_rebuild_jobs WHERE raw_id=?", (job.raw_id,))
            connection.commit()

    def fail_vector_rebuild(self, job: WorldStructuredJob, error: str) -> None:
        attempts = job.attempts + 1
        delay_seconds = min(6 * 60 * 60, 60 * (2 ** min(attempts - 1, 8)))
        next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                UPDATE vector_rebuild_jobs
                SET attempts=?, last_error=?, next_attempt_at=?
                WHERE raw_id=?
                """,
                (attempts, error[:1000], next_attempt.isoformat(), job.raw_id),
            )
            connection.commit()

    def vector_rebuild_pending_count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM vector_rebuild_jobs").fetchone()
        return int(row["count"]) if row is not None else 0

    def _validate_candidates(
        self,
        job: WorldStructuredJob,
        candidates: list[StructuredMemoryCandidate],
    ) -> list[StructuredMemoryCandidate]:
        accepted: list[StructuredMemoryCandidate] = []
        for candidate in candidates:
            if not candidate.quote or candidate.quote not in job.text:
                continue
            if candidate.kind not in {"fact", "promise", "preference", "relationship"}:
                continue
            if candidate.subject not in {"master", "resident", "other"}:
                continue
            if candidate.certainty not in {"confirmed", "hypothesis"}:
                continue
            if not candidate.attribute.strip() or not candidate.value.strip() or not candidate.statement.strip():
                continue
            if candidate.explicit_correction and not candidate.previous_value:
                continue
            accepted.append(candidate)
        return accepted

    def _commit_candidate(
        self,
        connection: sqlite3.Connection,
        job: WorldStructuredJob,
        candidate: StructuredMemoryCandidate,
    ) -> str | None:
        subject_key = candidate.subject
        current_fact_eligible = True
        if candidate.subject == "resident":
            if job.speaker_id.casefold() == "master":
                subject_key = "resident:unknown"
                current_fact_eligible = False
            else:
                subject_key = f"resident:{job.speaker_id}"
        elif candidate.subject == "other":
            subject_key = "other"
            current_fact_eligible = False

        fingerprint = "\x1f".join(
            [
                job.raw_id,
                candidate.kind,
                subject_key,
                candidate.attribute,
                candidate.value,
                candidate.quote,
            ]
        )
        memory_id = f"WM-{sha256(fingerprint.encode('utf-8')).hexdigest()[:24]}"
        if connection.execute(
            "SELECT 1 FROM atomic_memories WHERE memory_id=?",
            (memory_id,),
        ).fetchone() is not None:
            return None

        current = None
        if current_fact_eligible:
            current = connection.execute(
                """
                SELECT c.memory_id, c.value, a.status
                FROM current_facts AS c
                JOIN atomic_memories AS a ON a.memory_id = c.memory_id
                WHERE c.scope='public' AND c.subject=? AND c.attribute=?
                """,
                (subject_key, candidate.attribute),
            ).fetchone()

        status = "hypothesis" if candidate.certainty == "hypothesis" else "active"
        replace_current = False
        if status == "active" and current_fact_eligible:
            if candidate.explicit_correction:
                if current is None:
                    replace_current = True
                elif str(current["value"]) == str(candidate.previous_value):
                    connection.execute(
                        "UPDATE atomic_memories SET status='superseded', valid_to=? WHERE memory_id=?",
                        (job.occurred_at, str(current["memory_id"])),
                    )
                    replace_current = True
                else:
                    # Explicit correction did not match local continuity state.
                    # Keep it as a hypothesis rather than silently overwriting.
                    status = "hypothesis"
            elif current is None:
                replace_current = True
            elif str(current["value"]) == candidate.value:
                replace_current = True
            else:
                # A conflicting confirmed proposition without explicit
                # correction is not allowed to replace current truth.
                status = "hypothesis"

        now = datetime.now(timezone.utc).isoformat()
        connection.execute(
            """
            INSERT INTO atomic_memories(
                memory_id, raw_id, scope, kind, subject, attribute, value,
                statement, certainty, explicit_correction, previous_value,
                evidence_quote, status, valid_from, valid_to, created_at
            ) VALUES(?, ?, 'public', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                memory_id,
                job.raw_id,
                candidate.kind,
                subject_key,
                candidate.attribute,
                candidate.value,
                candidate.statement,
                candidate.certainty,
                1 if candidate.explicit_correction else 0,
                candidate.previous_value,
                candidate.quote,
                status,
                job.occurred_at,
                now,
            ),
        )
        if status == "active" and replace_current:
            connection.execute(
                """
                INSERT INTO current_facts(scope, subject, attribute, memory_id, value)
                VALUES('public', ?, ?, ?, ?)
                ON CONFLICT(scope, subject, attribute)
                DO UPDATE SET memory_id=excluded.memory_id, value=excluded.value
                """,
                (subject_key, candidate.attribute, memory_id, candidate.value),
            )
        return status

    def vector_count(self) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM raw_vec").fetchone()
        return int(row["count"]) if row is not None else 0

    def nearest_raw(
        self,
        embedding: list[float],
        *,
        k: int,
    ) -> list[dict[str, Any]]:
        if len(embedding) != self.vector_dim:
            raise StructuredMemoryProcessorError(
                f"query embedding dimension mismatch: expected {self.vector_dim}, got {len(embedding)}"
            )
        bounded = max(1, min(int(k), 100))
        blob = sqlite_vec.serialize_float32(embedding)
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT r.raw_id, r.entry_id, r.session_id, r.text, r.occurred_at,
                       v.distance AS distance
                FROM raw_vec AS v
                JOIN raw_entries AS r ON r.raw_id = v.raw_id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (blob, bounded),
            ).fetchall()
        return [dict(row) for row in rows]

    def structured_rows(self) -> list[dict[str, Any]]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT a.*, r.entry_id, r.session_id, r.text AS raw_text, r.occurred_at,
                       CASE WHEN c.memory_id = a.memory_id THEN 1 ELSE 0 END AS is_current
                FROM atomic_memories AS a
                JOIN raw_entries AS r ON r.raw_id = a.raw_id
                LEFT JOIN current_facts AS c
                  ON c.scope = a.scope
                 AND c.subject = a.subject
                 AND c.attribute = a.attribute
                WHERE a.scope='public' AND a.status IN ('active', 'superseded')
                ORDER BY a.valid_from, a.created_at
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def structured_rows_for_raw_ids(
        self,
        raw_ids: list[str],
        *,
        family_limit: int = 2,
    ) -> list[dict[str, Any]]:
        ordered_raw_ids = list(dict.fromkeys(raw_id for raw_id in raw_ids if raw_id))[:100]
        if not ordered_raw_ids:
            return []
        bounded_families = max(1, min(int(family_limit), 8))
        raw_rank = {raw_id: index for index, raw_id in enumerate(ordered_raw_ids)}
        placeholders = ",".join("?" for _ in ordered_raw_ids)
        with closing(self._connect()) as connection:
            family_rows = connection.execute(
                f"""
                SELECT raw_id, subject, attribute
                FROM atomic_memories
                WHERE scope='public'
                  AND status IN ('active', 'superseded')
                  AND raw_id IN ({placeholders})
                """,
                ordered_raw_ids,
            ).fetchall()
            family_rank: dict[tuple[str, str], int] = {}
            for row in family_rows:
                key = (str(row["subject"]), str(row["attribute"]))
                family_rank[key] = min(
                    family_rank.get(key, 10**9),
                    raw_rank.get(str(row["raw_id"]), 10**9),
                )
            selected = [
                key
                for key, _rank in sorted(family_rank.items(), key=lambda item: item[1])[:bounded_families]
            ]
            if not selected:
                return []
            family_clause = " OR ".join("(a.subject=? AND a.attribute=?)" for _ in selected)
            params = [value for subject, attribute in selected for value in (subject, attribute)]
            rows = connection.execute(
                f"""
                SELECT a.*, r.entry_id, r.session_id, r.text AS raw_text, r.occurred_at,
                       CASE WHEN c.memory_id = a.memory_id THEN 1 ELSE 0 END AS is_current
                FROM atomic_memories AS a
                JOIN raw_entries AS r ON r.raw_id = a.raw_id
                LEFT JOIN current_facts AS c
                  ON c.scope = a.scope
                 AND c.subject = a.subject
                 AND c.attribute = a.attribute
                WHERE a.scope='public'
                  AND a.status IN ('active', 'superseded')
                  AND ({family_clause})
                ORDER BY a.valid_from, a.created_at
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def current_fact(self, subject: str, attribute: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT a.*
                FROM current_facts AS c
                JOIN atomic_memories AS a ON a.memory_id = c.memory_id
                WHERE c.scope='public' AND c.subject=? AND c.attribute=?
                """,
                (subject, attribute),
            ).fetchone()
        return dict(row) if row is not None else None

    def job_state(self, raw_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM structured_jobs WHERE raw_id=?",
                (raw_id,),
            ).fetchone()
        return dict(row) if row is not None else None


class WorldMemoryBackgroundWorker:
    def __init__(
        self,
        store: WorldStructuredMemoryStore,
        processor: GeminiWorldMemoryProcessor,
        *,
        daily_limit: int = 400,
        embedding_daily_budget: int = 900,
    ) -> None:
        self.store = store
        self.processor = processor
        self.daily_limit = daily_limit
        self.embedding_daily_budget = embedding_daily_budget

    async def process_pending(self, *, limit: int = 5) -> WorldMemoryProcessingSummary:
        jobs = self.store.pending_jobs(limit=limit)
        processed = 0
        failed = 0
        candidates_committed = 0
        vectors_rebuilt = 0
        budget_exhausted = False
        for job in jobs:
            if not self.store.reserve_background_slot(
                daily_limit=self.daily_limit,
                embedding_daily_budget=self.embedding_daily_budget,
            ):
                budget_exhausted = True
                break
            try:
                embedding, candidates = await asyncio.gather(
                    self.processor.embed_document(job.text),
                    self.processor.extract(job.text, speaker_id=job.speaker_id),
                )
                candidates_committed += self.store.commit_job(
                    job,
                    embedding=embedding,
                    candidates=candidates,
                )
                processed += 1
            except Exception as exc:  # noqa: BLE001
                self.store.fail_job(job, str(exc) or type(exc).__name__)
                failed += 1

        # Provider/dimension migrations rebuild only the derived vector index.
        # Structured facts stay intact and a single batch request can restore
        # multiple Raw documents without spending one RPD per historical row.
        if not budget_exhausted:
            rebuild_jobs = self.store.pending_vector_rebuild_jobs(limit=32)
            if rebuild_jobs:
                if not self.store.reserve_background_slot(
                    daily_limit=self.daily_limit,
                    embedding_daily_budget=self.embedding_daily_budget,
                ):
                    budget_exhausted = True
                else:
                    try:
                        embeddings = await self.processor.embed_documents([job.text for job in rebuild_jobs])
                        if len(embeddings) != len(rebuild_jobs):
                            raise StructuredMemoryProcessorError("Vector rebuild batch response count mismatch")
                        for job, embedding in zip(rebuild_jobs, embeddings, strict=True):
                            self.store.commit_vector_rebuild(job, embedding)
                            vectors_rebuilt += 1
                    except Exception as exc:  # noqa: BLE001
                        message = str(exc) or type(exc).__name__
                        for job in rebuild_jobs:
                            self.store.fail_vector_rebuild(job, message)
                            failed += 1
        return WorldMemoryProcessingSummary(
            processed=processed,
            failed=failed,
            candidates_committed=candidates_committed,
            budget_exhausted=budget_exhausted,
            vectors_rebuilt=vectors_rebuilt,
        )


def _candidate(value: Any) -> StructuredMemoryCandidate:
    if not isinstance(value, dict):
        raise StructuredMemoryProcessorError("Structured memory candidate must be an object")
    previous = value.get("previous_value")
    return StructuredMemoryCandidate(
        quote=str(value.get("quote", "")),
        kind=str(value.get("kind", "")),
        subject=str(value.get("subject", "")),
        attribute=str(value.get("attribute", "")),
        value=str(value.get("value", "")),
        statement=str(value.get("statement", "")),
        certainty=str(value.get("certainty", "")),
        explicit_correction=value.get("explicit_correction") is True,
        previous_value=str(previous) if previous is not None else None,
    )
