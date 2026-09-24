from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import re
import sqlite3
from typing import Any, Awaitable, Callable

try:
    import sqlite_vec
except ModuleNotFoundError:  # optional at import time; semantic layer fails soft
    sqlite_vec = None  # type: ignore[assignment]

from .private import PrivateMemoryHit, PrivateMemoryService
from .structured import (
    GeminiWorldMemoryProcessor,
    StructuredMemoryProcessorError,
    WorldStructuredMemoryStore,
)


class PrivateSemanticMemoryError(RuntimeError):
    pass


@dataclass(frozen=True)
class PrivateEmbeddingJob:
    resident_name: str
    raw_id: str
    entry_id: str | None
    text: str
    attempts: int


@dataclass(frozen=True)
class PrivateEmbeddingSummary:
    processed: int
    failed: int
    budget_exhausted: bool = False


class GeminiPrivateEmbeddingProcessor:
    """Gemini Embedding adapter for Private Whisper semantic retrieval.

    Private Raw Memory remains resident-local in Nirai. Only embedding requests
    are sent to the configured Gemini API; no local Ollama/BGE runtime is used.
    """

    def __init__(
        self,
        root: Path,
        *,
        embedding_model: str = "gemini-embedding-2",
        vector_dim: int = 768,
    ) -> None:
        self.embedding_model = embedding_model
        self.vector_dim = vector_dim
        self._delegate = GeminiWorldMemoryProcessor(
            root,
            embedding_model=embedding_model,
            embedding_dim=vector_dim,
        )

    async def embed_query(self, text: str) -> list[float]:
        try:
            return await self._delegate.embed_query(text)
        except StructuredMemoryProcessorError as exc:
            raise PrivateSemanticMemoryError(str(exc)) from exc

    async def embed_document(self, text: str) -> list[float]:
        try:
            return await self._delegate.embed_document(text)
        except StructuredMemoryProcessorError as exc:
            raise PrivateSemanticMemoryError(str(exc)) from exc

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        try:
            return await self._delegate.embed_documents(texts)
        except StructuredMemoryProcessorError as exc:
            raise PrivateSemanticMemoryError(str(exc)) from exc


class PrivateVectorStore:
    def __init__(
        self,
        memory: PrivateMemoryService,
        *,
        vector_dim: int = 768,
        embedding_model: str = "gemini-embedding-2",
    ) -> None:
        if sqlite_vec is None:
            raise PrivateSemanticMemoryError("sqlite-vec is not installed")
        self.memory = memory
        self.vector_dim = vector_dim
        self.embedding_model = embedding_model
        self._ready_residents: set[str] = set()

    def _connect(self, resident_name: str) -> sqlite3.Connection:
        db_path = self.memory.private_db_path(resident_name)
        connection = sqlite3.connect(db_path, timeout=5.0)
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _ensure_schema(self, resident_name: str) -> None:
        if resident_name in self._ready_residents:
            return
        with closing(self._connect(resident_name)) as connection:
            existing = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='raw_vec'"
            ).fetchone()
            existing_sql = str(existing["sql"]) if existing is not None and existing["sql"] is not None else ""
            expected_dimension = f"float[{self.vector_dim}]"
            stored_model_row = connection.execute(
                "SELECT value FROM migration_meta WHERE key='private_embedding_model'"
            ).fetchone()
            stored_dim_row = connection.execute(
                "SELECT value FROM migration_meta WHERE key='private_embedding_dim'"
            ).fetchone()
            stored_model = str(stored_model_row["value"]) if stored_model_row is not None else None
            stored_dim = str(stored_dim_row["value"]) if stored_dim_row is not None else None
            incompatible = bool(existing_sql) and (
                expected_dimension not in existing_sql
                or stored_model != self.embedding_model
                or stored_dim != str(self.vector_dim)
            )
            if incompatible:
                # Vector indexes are derived data. Model changes matter even
                # when dimensions happen to match: vectors from two embedding
                # spaces must never be compared by cosine distance.
                connection.execute("DROP TABLE raw_vec")
                connection.execute(
                    """
                    UPDATE embedding_jobs
                    SET status='pending', attempts=0, last_error=NULL, next_attempt_at=NULL
                    """
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
                """
                INSERT INTO migration_meta(key, value) VALUES('private_embedding_model', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (self.embedding_model,),
            )
            connection.execute(
                """
                INSERT INTO migration_meta(key, value) VALUES('private_embedding_dim', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (str(self.vector_dim),),
            )
            connection.commit()
        self._ready_residents.add(resident_name)

    def pending_jobs(self, resident_name: str, *, limit: int = 1) -> list[PrivateEmbeddingJob]:
        self._ensure_schema(resident_name)
        bounded = max(1, min(int(limit), 20))
        now = datetime.now(timezone.utc).isoformat()
        with closing(self._connect(resident_name)) as connection:
            rows = connection.execute(
                """
                SELECT r.raw_id, r.entry_id, r.text, j.attempts
                FROM raw_entries AS r
                JOIN embedding_jobs AS j ON j.raw_id = r.raw_id
                WHERE j.status='pending'
                  AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= ?)
                ORDER BY r.rowid
                LIMIT ?
                """,
                (now, bounded),
            ).fetchall()
        return [
            PrivateEmbeddingJob(
                resident_name=resident_name,
                raw_id=str(row["raw_id"]),
                entry_id=str(row["entry_id"]) if row["entry_id"] is not None else None,
                text=str(row["text"]),
                attempts=int(row["attempts"]),
            )
            for row in rows
        ]

    def commit_job(self, job: PrivateEmbeddingJob, embedding: list[float]) -> None:
        if len(embedding) != self.vector_dim:
            raise PrivateSemanticMemoryError(
                f"private embedding dimension mismatch: expected {self.vector_dim}, got {len(embedding)}"
            )
        self._ensure_schema(job.resident_name)
        with closing(self._connect(job.resident_name)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """
                SELECT r.entry_id, j.status
                FROM raw_entries AS r
                JOIN embedding_jobs AS j ON j.raw_id = r.raw_id
                WHERE r.raw_id=?
                """,
                (job.raw_id,),
            ).fetchone()
            forgotten = False
            if job.entry_id is not None:
                forgotten = connection.execute(
                    "SELECT 1 FROM forgotten_entries WHERE entry_id=? LIMIT 1",
                    (job.entry_id,),
                ).fetchone() is not None
            if current is None or forgotten or str(current["status"]) != "pending":
                # The async embedding was computed from a job that Forget or a
                # newer worker invalidated while the provider call was in flight.
                # Never resurrect a derived vector after its Raw authority died.
                connection.execute("DELETE FROM raw_vec WHERE raw_id=?", (job.raw_id,))
                connection.commit()
                return
            connection.execute("DELETE FROM raw_vec WHERE raw_id=?", (job.raw_id,))
            connection.execute(
                "INSERT INTO raw_vec(raw_id, embedding) VALUES(?, ?)",
                (job.raw_id, sqlite_vec.serialize_float32(embedding)),
            )
            connection.execute(
                "UPDATE embedding_jobs SET status='done', last_error=NULL, next_attempt_at=NULL WHERE raw_id=?",
                (job.raw_id,),
            )
            connection.commit()

    def fail_job(self, job: PrivateEmbeddingJob, error: str) -> None:
        attempts = job.attempts + 1
        delay_seconds = min(6 * 60 * 60, 60 * (2 ** min(attempts - 1, 8)))
        next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        with closing(self._connect(job.resident_name)) as connection:
            connection.execute(
                """
                UPDATE embedding_jobs
                SET status='pending', attempts=?, last_error=?, next_attempt_at=?
                WHERE raw_id=?
                """,
                (attempts, error[:1000], next_attempt.isoformat(), job.raw_id),
            )
            connection.commit()

    def vector_count(self, resident_name: str) -> int:
        self._ensure_schema(resident_name)
        with closing(self._connect(resident_name)) as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM raw_vec").fetchone()
        return int(row["count"]) if row is not None else 0

    def nearest_raw(
        self,
        resident_name: str,
        embedding: list[float],
        *,
        k: int,
        exclude_entry_ids: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        if len(embedding) != self.vector_dim:
            raise PrivateSemanticMemoryError(
                f"query embedding dimension mismatch: expected {self.vector_dim}, got {len(embedding)}"
            )
        self._ensure_schema(resident_name)
        bounded = max(1, min(int(k), 100))
        excluded = set(exclude_entry_ids or ())
        blob = sqlite_vec.serialize_float32(embedding)
        with closing(self._connect(resident_name)) as connection:
            rows = connection.execute(
                """
                SELECT r.raw_id, r.entry_id, r.public_session_id, r.text, r.occurred_at,
                       v.distance AS distance
                FROM raw_vec AS v
                JOIN raw_entries AS r ON r.raw_id = v.raw_id
                WHERE v.embedding MATCH ? AND k = ?
                ORDER BY v.distance
                """,
                (blob, bounded),
            ).fetchall()
        return [
            dict(row)
            for row in rows
            if row["entry_id"] is None or str(row["entry_id"]) not in excluded
        ]


class PrivateMemoryBackgroundWorker:
    def __init__(
        self,
        store: PrivateVectorStore,
        processor: GeminiPrivateEmbeddingProcessor,
        *,
        quota_store: WorldStructuredMemoryStore | None = None,
        daily_limit: int = 400,
        embedding_daily_budget: int = 900,
    ) -> None:
        self.store = store
        self.processor = processor
        self.quota_store = quota_store
        self.daily_limit = daily_limit
        self.embedding_daily_budget = embedding_daily_budget
        self._resident_cursor = 0

    async def process_pending(
        self,
        resident_names: list[str],
        *,
        limit_total: int = 32,
    ) -> PrivateEmbeddingSummary:
        processed = 0
        failed = 0
        if not resident_names or limit_total <= 0:
            return PrivateEmbeddingSummary(processed=0, failed=0)
        ordered = resident_names[self._resident_cursor :] + resident_names[: self._resident_cursor]
        self._resident_cursor = (self._resident_cursor + 1) % len(resident_names)
        remaining = max(1, min(int(limit_total), 64))
        jobs: list[PrivateEmbeddingJob] = []
        for resident_name in ordered:
            if remaining <= 0:
                break
            try:
                resident_jobs = self.store.pending_jobs(resident_name, limit=remaining)
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            jobs.extend(resident_jobs)
            remaining -= len(resident_jobs)
        if not jobs:
            return PrivateEmbeddingSummary(processed=0, failed=failed)
        if self.quota_store is not None and not self.quota_store.reserve_background_slot(
            daily_limit=self.daily_limit,
            embedding_daily_budget=self.embedding_daily_budget,
        ):
            return PrivateEmbeddingSummary(processed=0, failed=failed, budget_exhausted=True)
        try:
            batch = getattr(self.processor, "embed_documents", None)
            if callable(batch):
                embeddings = await batch([job.text for job in jobs])
            else:
                embeddings = [await self.processor.embed_document(job.text) for job in jobs]
            if len(embeddings) != len(jobs):
                raise PrivateSemanticMemoryError("Private embedding batch response count mismatch")
            for job, embedding in zip(jobs, embeddings, strict=True):
                self.store.commit_job(job, embedding)
                processed += 1
        except Exception as exc:  # noqa: BLE001
            message = str(exc) or type(exc).__name__
            for job in jobs:
                self.store.fail_job(job, message)
                failed += 1
        return PrivateEmbeddingSummary(
            processed=processed,
            failed=failed,
            budget_exhausted=False,
        )


class PrivateMemoryHybridRetriever:
    DEFAULT_TOP_K = 4
    MAX_TOP_K = 10
    SEMANTIC_SIMILARITY_FLOOR = 0.70
    STRONG_LEXICAL_FLOOR = 0.42
    LOCAL_FALLBACK_FLOOR = 0.22
    _CURRENT_QUERY_MARKERS = ("今", "いま", "現在", "最近")
    _HISTORICAL_QUERY_MARKERS = ("前は", "以前", "昔", "当時", "訂正する前", "前に")

    def __init__(
        self,
        memory: PrivateMemoryService,
        *,
        store: PrivateVectorStore | None = None,
        processor: GeminiPrivateEmbeddingProcessor | None = None,
        quota_store: WorldStructuredMemoryStore | None = None,
        query_embedding_daily_limit: int = 500,
        embedding_daily_budget: int = 900,
    ) -> None:
        self.memory = memory
        self.store = store
        self.processor = processor
        self.quota_store = quota_store
        self.query_embedding_daily_limit = query_embedding_daily_limit
        self.embedding_daily_budget = embedding_daily_budget

    async def search(
        self,
        resident_name: str,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        exclude_entry_ids: set[str] | None = None,
        on_fallback: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[PrivateMemoryHit]:
        bounded = max(1, min(int(top_k), self.MAX_TOP_K))
        lexical = self.memory.search(
            resident_name,
            query,
            top_k=max(bounded * 3, 8),
            exclude_entry_ids=exclude_entry_ids,
        )
        current_query = any(marker in query for marker in self._CURRENT_QUERY_MARKERS)
        historical_query = any(marker in query for marker in self._HISTORICAL_QUERY_MARKERS)
        temporal_query = current_query or historical_query
        strong = [hit for hit in lexical if hit.score >= self.STRONG_LEXICAL_FLOOR]
        if strong and not temporal_query:
            return strong[:bounded]

        if self.store is None or self.processor is None:
            if on_fallback is not None:
                await on_fallback("semantic_unavailable")
            return self._local_fallback(lexical, top_k=bounded)
        try:
            if self.store.vector_count(resident_name) <= 0:
                if on_fallback is not None:
                    await on_fallback("vector_index_empty")
                return self._local_fallback(lexical, top_k=bounded)
            if self.quota_store is not None and not self.quota_store.reserve_query_embedding_slot(
                daily_limit=self.query_embedding_daily_limit,
                embedding_daily_budget=self.embedding_daily_budget,
            ):
                if on_fallback is not None:
                    await on_fallback("query_embedding_budget_exhausted")
                return self._local_fallback(lexical, top_k=bounded)
            query_vector = await self.processor.embed_query(query)
            rows = self.store.nearest_raw(
                resident_name,
                query_vector,
                k=max(20, bounded * 6),
                exclude_entry_ids=exclude_entry_ids,
            )
        except (PrivateSemanticMemoryError, OSError, sqlite3.Error):
            if on_fallback is not None:
                await on_fallback("semantic_query_failed")
            return self._local_fallback(lexical, top_k=bounded)

        semantic: list[PrivateMemoryHit] = []
        for row in rows:
            similarity = 1.0 - float(row["distance"])
            if similarity < self.SEMANTIC_SIMILARITY_FLOOR:
                continue
            semantic.append(
                PrivateMemoryHit(
                    raw_id=str(row["raw_id"]),
                    entry_id=str(row["entry_id"]) if row.get("entry_id") is not None else None,
                    public_session_id=str(row["public_session_id"]),
                    excerpt=str(row["text"]),
                    score=similarity,
                    occurred_at=str(row["occurred_at"]),
                    source=f"private-{getattr(self.processor, 'embedding_model', 'gemini-embedding-2')}",
                )
            )
            if len(semantic) >= bounded:
                break
        if semantic:
            if current_query:
                corrections = [hit for hit in semantic if self._is_explicit_correction_text(hit.excerpt)]
                candidates = corrections or semantic
                candidates.sort(key=lambda hit: hit.occurred_at, reverse=True)
                return candidates[:1]
            return semantic
        return self._local_fallback(lexical, top_k=bounded)

    @staticmethod
    def _is_explicit_correction_text(text: str) -> bool:
        if "訂正" in text:
            return True
        compact = " ".join(text.split())
        return bool(
            re.search(r"(?:前に|以前).{0,80}(?:けど|が).{0,80}(?:今|現在)", compact)
        )

    def _local_fallback(self, hits: list[PrivateMemoryHit], *, top_k: int) -> list[PrivateMemoryHit]:
        eligible = [hit for hit in hits if hit.score >= self.LOCAL_FALLBACK_FLOOR]
        if len(eligible) != 1:
            return []
        return eligible[:top_k]
