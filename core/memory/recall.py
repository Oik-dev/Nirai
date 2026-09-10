from __future__ import annotations

from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
import sqlite3
from typing import Any, Awaitable, Callable

from .lexical import (
    has_distinctive_anchor_match as _has_distinctive_anchor_match,
    normalize_lexical_text as _normalize_lexical_text,
)
from .structured import (
    GeminiWorldMemoryProcessor,
    StructuredMemoryProcessorError,
    WorldStructuredMemoryStore,
)
from .world import WorldMemoryService


class WorldMemoryRecallError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorldMemoryRecallHit:
    raw_id: str
    entry_id: str | None
    session_id: str
    excerpt: str
    score: float
    source: str
    occurred_at: str

    def to_context(self) -> dict[str, object]:
        label = self.entry_id or self.raw_id
        return {
            "episode_id": label,
            "session_id": self.session_id,
            "path": f"world_memory/world_memory.sqlite3#{self.raw_id}",
            "excerpt": self.excerpt,
            "source": self.source,
            "occurred_at": self.occurred_at,
        }


@dataclass(frozen=True)
class _LexicalHit:
    hit: WorldMemoryRecallHit
    coverage: float
    distinctive_anchor: bool


class WorldMemoryHybridRetriever:
    """Public World Memory recall over lossless Raw + Structured + Gemini vectors.

    Retrieval is abstention-oriented:
    - Structured current/history resolution wins when a matching continuity fact exists.
    - Strong lexical matches stay fully local and do not spend a Gemini query embedding.
    - Weak/semantic queries use Gemini Embedding only when vectors exist and budget allows.
    - Gemini failure or quota exhaustion degrades to gated local FTS rather than failing chat.
    """

    DEFAULT_TOP_K = 4
    MAX_TOP_K = 10
    SEMANTIC_SIMILARITY_FLOOR = 0.70
    LEXICAL_COVERAGE_FLOOR = 0.18
    LOCAL_FALLBACK_COVERAGE_FLOOR = 0.22
    STRONG_LEXICAL_COVERAGE_FLOOR = 0.42
    RRF_K = 60
    _HISTORICAL_MARKERS = ("前は", "以前", "昔", "当時", "訂正する前", "前に")

    def __init__(
        self,
        root: Path,
        *,
        store: WorldStructuredMemoryStore | None = None,
        processor: GeminiWorldMemoryProcessor | None = None,
        query_embedding_daily_limit: int = 500,
        embedding_daily_budget: int = 900,
    ) -> None:
        self.root = root.resolve()
        self.db_path = self.root / "world_memory" / "world_memory.sqlite3"
        self.store = store
        self.processor = processor
        self.query_embedding_daily_limit = query_embedding_daily_limit
        self.embedding_daily_budget = embedding_daily_budget

    async def search(
        self,
        query: str,
        *,
        top_k: int = DEFAULT_TOP_K,
        exclude_raw_ids: set[str] | None = None,
        on_fallback: Callable[[str], Awaitable[None]] | None = None,
    ) -> list[WorldMemoryRecallHit]:
        cleaned = " ".join(query.split()).strip()
        if not cleaned:
            return []
        bounded_top_k = max(1, min(int(top_k), self.MAX_TOP_K))
        excluded = set(exclude_raw_ids or ())

        try:
            lexical = self._lexical_candidates(
                cleaned,
                top_k=bounded_top_k,
                exclude_raw_ids=excluded,
            )
            gated_candidates = [
                item
                for item in lexical
                if item.coverage >= self.LEXICAL_COVERAGE_FLOOR or item.distinctive_anchor
            ]
            structured_hits, family_raw_ids, structured_resolved = self._structured_overlay(
                cleaned,
                candidate_raw_ids=[item.hit.raw_id for item in gated_candidates],
                top_k=bounded_top_k,
                exclude_raw_ids=excluded,
            )
        except (sqlite3.Error, OSError, StructuredMemoryProcessorError) as exc:
            raise WorldMemoryRecallError("World Memory local recall failed") from exc

        gated_lexical = [
            item
            for item in gated_candidates
            if item.hit.raw_id not in family_raw_ids
        ]

        if structured_resolved:
            return self._dedupe_hits(
                structured_hits + [item.hit for item in gated_lexical],
                top_k=bounded_top_k,
            )

        strong_lexical = [
            item
            for item in gated_lexical
            if item.coverage >= self.STRONG_LEXICAL_COVERAGE_FLOOR or item.distinctive_anchor
        ]
        if strong_lexical:
            return self._dedupe_hits(
                [item.hit for item in strong_lexical],
                top_k=bounded_top_k,
            )

        if self.store is None or self.processor is None:
            if on_fallback is not None:
                await on_fallback("semantic_unavailable")
            return self._local_fallback(gated_lexical, top_k=bounded_top_k)

        try:
            if self.store.vector_count() <= 0:
                if on_fallback is not None:
                    await on_fallback("vector_index_empty")
                return self._local_fallback(gated_lexical, top_k=bounded_top_k)
            if not self.store.reserve_query_embedding_slot(
                daily_limit=self.query_embedding_daily_limit,
                embedding_daily_budget=self.embedding_daily_budget,
            ):
                if on_fallback is not None:
                    await on_fallback("query_embedding_budget_exhausted")
                return self._local_fallback(gated_lexical, top_k=bounded_top_k)
            query_vector = await self.processor.embed_query(cleaned)
            semantic_hits = self._semantic_candidates(
                query_vector,
                top_k=bounded_top_k,
                exclude_raw_ids=excluded,
            )
            semantic_structured, semantic_family_raw_ids, semantic_resolved = self._structured_overlay(
                cleaned,
                candidate_raw_ids=[hit.raw_id for hit in semantic_hits],
                top_k=bounded_top_k,
                exclude_raw_ids=excluded,
            )
            if semantic_resolved:
                lexical_for_fusion = [
                    item.hit for item in gated_lexical
                    if item.hit.raw_id not in semantic_family_raw_ids
                ]
                semantic_for_fusion = [
                    hit for hit in semantic_hits
                    if hit.raw_id not in semantic_family_raw_ids
                ]
                return self._dedupe_hits(
                    semantic_structured
                    + self._rrf_fuse(
                        lexical_for_fusion,
                        semantic_for_fusion,
                        top_k=bounded_top_k,
                    ),
                    top_k=bounded_top_k,
                )
        except (StructuredMemoryProcessorError, OSError, sqlite3.Error):
            if on_fallback is not None:
                await on_fallback("semantic_query_failed")
            return self._local_fallback(gated_lexical, top_k=bounded_top_k)

        return self._rrf_fuse(
            [item.hit for item in gated_lexical],
            semantic_hits,
            top_k=bounded_top_k,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _lexical_candidates(
        self,
        query: str,
        *,
        top_k: int,
        exclude_raw_ids: set[str],
    ) -> list[_LexicalHit]:
        terms = WorldMemoryService.raw_search_terms(query)
        if not terms or not self.db_path.is_file():
            return []
        match_query = " OR ".join(f'"{term}"' for term in terms[:64])
        candidate_limit = max(20, top_k * 6)
        with closing(self._connect()) as connection:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='raw_fts'"
            ).fetchone()
            if exists is None:
                return []
            rows = connection.execute(
                """
                SELECT r.raw_id, r.entry_id, r.session_id, r.text, r.occurred_at,
                       bm25(raw_fts) AS rank
                FROM raw_fts
                JOIN raw_entries AS r ON r.raw_id = raw_fts.raw_id
                WHERE raw_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (match_query, candidate_limit),
            ).fetchall()

        results: list[_LexicalHit] = []
        for row in rows:
            raw_id = str(row["raw_id"])
            if raw_id in exclude_raw_ids:
                continue
            text = str(row["text"])
            coverage = _lexical_coverage(query, text)
            anchor = _has_distinctive_anchor_match(query, text)
            results.append(
                _LexicalHit(
                    hit=WorldMemoryRecallHit(
                        raw_id=raw_id,
                        entry_id=str(row["entry_id"]) if row["entry_id"] is not None else None,
                        session_id=str(row["session_id"]),
                        excerpt=text,
                        score=coverage,
                        source="raw-fts5",
                        occurred_at=str(row["occurred_at"]),
                    ),
                    coverage=coverage,
                    distinctive_anchor=anchor,
                )
            )
        results.sort(key=lambda item: (item.coverage, item.distinctive_anchor), reverse=True)
        return results[: max(top_k * 4, 12)]

    def _semantic_candidates(
        self,
        query_vector: list[float],
        *,
        top_k: int,
        exclude_raw_ids: set[str],
    ) -> list[WorldMemoryRecallHit]:
        assert self.store is not None
        rows = self.store.nearest_raw(query_vector, k=max(20, top_k * 6))
        hits: list[WorldMemoryRecallHit] = []
        for row in rows:
            raw_id = str(row["raw_id"])
            if raw_id in exclude_raw_ids:
                continue
            similarity = 1.0 - float(row["distance"])
            if similarity < self.SEMANTIC_SIMILARITY_FLOOR:
                continue
            hits.append(
                WorldMemoryRecallHit(
                    raw_id=raw_id,
                    entry_id=str(row["entry_id"]) if row.get("entry_id") is not None else None,
                    session_id=str(row["session_id"]),
                    excerpt=str(row["text"]),
                    score=similarity,
                    source="gemini-embedding-2",
                    occurred_at=str(row["occurred_at"]),
                )
            )
            if len(hits) >= top_k:
                break
        return hits

    def _structured_overlay(
        self,
        query: str,
        *,
        candidate_raw_ids: list[str],
        top_k: int,
        exclude_raw_ids: set[str],
    ) -> tuple[list[WorldMemoryRecallHit], set[str], bool]:
        if self.store is None or not candidate_raw_ids:
            return [], set(), False
        rows = self.store.structured_rows_for_raw_ids(
            candidate_raw_ids,
            family_limit=2,
        )
        if not rows:
            return [], set(), False

        candidate_order = {raw_id: index for index, raw_id in enumerate(candidate_raw_ids)}
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        family_rank: dict[tuple[str, str], int] = {}
        for row in rows:
            key = (str(row["subject"]), str(row["attribute"]))
            grouped.setdefault(key, []).append(row)
            raw_id = str(row["raw_id"])
            if raw_id in candidate_order:
                family_rank[key] = min(family_rank.get(key, 10**9), candidate_order[raw_id])
        if not family_rank:
            return [], set(), False
        selected_families = [
            key
            for key, _ in sorted(family_rank.items(), key=lambda item: item[1])[:2]
        ]
        historical = any(marker in query for marker in self._HISTORICAL_MARKERS)
        hits: list[WorldMemoryRecallHit] = []
        suppressed_family_raw_ids: set[str] = set()

        for key in selected_families:
            family = grouped[key]
            family_raw_ids = {str(row["raw_id"]) for row in family}
            if historical:
                selected = family
            else:
                selected = [row for row in family if int(row.get("is_current") or 0) == 1]
                if not selected:
                    continue
                suppressed_family_raw_ids.update(family_raw_ids)
                for row in selected:
                    suppressed_family_raw_ids.discard(str(row["raw_id"]))
            for row in selected:
                raw_id = str(row["raw_id"])
                if raw_id in exclude_raw_ids:
                    continue
                hits.append(
                    WorldMemoryRecallHit(
                        raw_id=raw_id,
                        entry_id=str(row["entry_id"]) if row.get("entry_id") is not None else None,
                        session_id=str(row["session_id"]),
                        excerpt=str(row["raw_text"]),
                        score=2.0,
                        source="structured-history" if historical else "structured-current",
                        occurred_at=str(row["occurred_at"]),
                    )
                )

        if not hits:
            return [], set(), False
        hits.sort(key=lambda item: item.occurred_at if historical else item.score, reverse=not historical)
        return self._dedupe_hits(hits, top_k=top_k), suppressed_family_raw_ids, True

    def _local_fallback(
        self,
        lexical: list[_LexicalHit],
        *,
        top_k: int,
    ) -> list[WorldMemoryRecallHit]:
        # Semantic confirmation is unavailable. Permit only one unambiguous
        # lexical candidate with enough query coverage. Multiple plausible
        # candidates mean the local evidence is ambiguous, so abstain.
        eligible = [
            item
            for item in lexical
            if item.coverage >= self.LOCAL_FALLBACK_COVERAGE_FLOOR
            or item.distinctive_anchor
        ]
        if len(eligible) != 1:
            return []
        return self._dedupe_hits([eligible[0].hit], top_k=top_k)

    def _rrf_fuse(
        self,
        lexical_hits: list[WorldMemoryRecallHit],
        semantic_hits: list[WorldMemoryRecallHit],
        *,
        top_k: int,
    ) -> list[WorldMemoryRecallHit]:
        scores: dict[str, float] = {}
        payloads: dict[str, WorldMemoryRecallHit] = {}
        sources: dict[str, set[str]] = {}
        for hits in (lexical_hits, semantic_hits):
            for rank, hit in enumerate(hits, start=1):
                scores[hit.raw_id] = scores.get(hit.raw_id, 0.0) + 1.0 / (self.RRF_K + rank)
                payloads[hit.raw_id] = hit
                sources.setdefault(hit.raw_id, set()).add(hit.source)
        ordered = sorted(scores, key=scores.__getitem__, reverse=True)[:top_k]
        result: list[WorldMemoryRecallHit] = []
        for raw_id in ordered:
            hit = payloads[raw_id]
            result.append(
                WorldMemoryRecallHit(
                    raw_id=hit.raw_id,
                    entry_id=hit.entry_id,
                    session_id=hit.session_id,
                    excerpt=hit.excerpt,
                    score=scores[raw_id],
                    source="+".join(sorted(sources[raw_id])),
                    occurred_at=hit.occurred_at,
                )
            )
        return result

    @staticmethod
    def _dedupe_hits(
        hits: list[WorldMemoryRecallHit],
        *,
        top_k: int,
    ) -> list[WorldMemoryRecallHit]:
        seen: set[str] = set()
        result: list[WorldMemoryRecallHit] = []
        for hit in hits:
            if hit.raw_id in seen:
                continue
            seen.add(hit.raw_id)
            result.append(hit)
            if len(result) >= top_k:
                break
        return result



def _character_ngrams(text: str, size: int) -> list[str]:
    compact = _normalize_lexical_text(text)
    if len(compact) < size:
        return [compact] if len(compact) >= 2 else []
    return list(dict.fromkeys(compact[index : index + size] for index in range(len(compact) - size + 1)))


def _lexical_coverage(question: str, text: str) -> float:
    query_grams = set(_character_ngrams(question, 3))
    if not query_grams:
        query_grams = set(_character_ngrams(question, 2))
    if not query_grams:
        return 0.0
    text_grams = set(_character_ngrams(text, 3))
    if not text_grams:
        text_grams = set(_character_ngrams(text, 2))
    return len(query_grams & text_grams) / len(query_grams)
