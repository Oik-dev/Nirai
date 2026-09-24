from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
import sqlite3
from time import perf_counter
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class MemoryEvaluationError(RuntimeError):
    pass


@dataclass(frozen=True)
class RawEntry:
    entry_id: str
    scope: str
    conversation_id: str
    speaker_id: str
    participants: tuple[str, ...]
    occurred_at: str
    text: str
    event_type: str = "utterance"
    resident_experience: tuple[str, ...] = ()
    importance: float = 0.5


@dataclass(frozen=True)
class StructuredFact:
    fact_id: str
    scope: str
    subject: str
    predicate: str
    value: str
    statement: str
    valid_from: str
    valid_to: str | None
    status: str
    source_entry_ids: tuple[str, ...]
    search_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    query_scope: str
    query_time: str
    question: str
    must_include_evidence: tuple[str, ...]
    must_not_use: tuple[str, ...] = ()
    should_abstain: bool = False
    tags: tuple[str, ...] = ()
    top_k: int = 5


@dataclass(frozen=True)
class RetrievalHit:
    entry_id: str
    text: str
    score: float
    source: str


@dataclass(frozen=True)
class EvaluationCaseResult:
    case_id: str
    passed: bool
    recall: float
    forbidden_hits: tuple[str, ...]
    returned_ids: tuple[str, ...]
    latency_ms: float
    context_chars: int
    tags: tuple[str, ...]


@dataclass(frozen=True)
class EvaluationReport:
    baseline: str
    cases: tuple[EvaluationCaseResult, ...]

    @property
    def passed(self) -> int:
        return sum(1 for item in self.cases if item.passed)

    @property
    def total(self) -> int:
        return len(self.cases)

    @property
    def accuracy(self) -> float:
        return self.passed / self.total if self.total else 0.0

    @property
    def false_recall_cases(self) -> int:
        return sum(1 for item in self.cases if item.forbidden_hits)

    @property
    def p50_latency_ms(self) -> float:
        values = sorted(item.latency_ms for item in self.cases)
        if not values:
            return 0.0
        return values[(len(values) - 1) // 2]

    @property
    def p95_latency_ms(self) -> float:
        values = sorted(item.latency_ms for item in self.cases)
        if not values:
            return 0.0
        index = max(0, math.ceil(len(values) * 0.95) - 1)
        return values[index]

    @property
    def mean_context_chars(self) -> float:
        return (
            sum(item.context_chars for item in self.cases) / len(self.cases)
            if self.cases
            else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline,
            "passed": self.passed,
            "total": self.total,
            "accuracy": round(self.accuracy, 4),
            "false_recall_cases": self.false_recall_cases,
            "p50_latency_ms": round(self.p50_latency_ms, 3),
            "p95_latency_ms": round(self.p95_latency_ms, 3),
            "mean_context_chars": round(self.mean_context_chars, 1),
            "cases": [
                {
                    "case_id": item.case_id,
                    "passed": item.passed,
                    "recall": round(item.recall, 4),
                    "forbidden_hits": list(item.forbidden_hits),
                    "returned_ids": list(item.returned_ids),
                    "latency_ms": round(item.latency_ms, 3),
                    "context_chars": item.context_chars,
                    "tags": list(item.tags),
                }
                for item in self.cases
            ],
        }


class RetrievalBaseline:
    name = "baseline"

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        raise NotImplementedError

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        raise NotImplementedError


class LexicalBaseline(RetrievalBaseline):
    """SQLite FTS5 trigram raw-evidence baseline.

    This is intentionally small and deterministic. It is an evaluation adapter,
    not the final Nirai product retriever.
    """

    name = "lexical-fts5"

    def __init__(self) -> None:
        self._connection = sqlite3.connect(":memory:")
        self._entries: dict[str, RawEntry] = {}
        self._tokenizer = "trigram"
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            self._connection.execute(
                """
                CREATE VIRTUAL TABLE raw_fts USING fts5(
                    entry_id UNINDEXED,
                    scope UNINDEXED,
                    text,
                    tokenize='trigram'
                )
                """
            )
        except sqlite3.OperationalError:
            self._tokenizer = "unicode61"
            self._connection.execute(
                """
                CREATE VIRTUAL TABLE raw_fts USING fts5(
                    entry_id UNINDEXED,
                    scope UNINDEXED,
                    text,
                    tokenize='unicode61'
                )
                """
            )

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        self._connection.execute("DELETE FROM raw_fts")
        self._entries = {entry.entry_id: entry for entry in entries}
        self._connection.executemany(
            "INSERT INTO raw_fts(entry_id, scope, text) VALUES(?, ?, ?)",
            [(entry.entry_id, entry.scope, self._search_text(entry.text)) for entry in entries],
        )
        self._connection.commit()

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        query = self._query(case.question)
        if not query:
            return []
        rows = self._connection.execute(
            """
            SELECT entry_id, bm25(raw_fts) AS rank
            FROM raw_fts
            WHERE raw_fts MATCH ? AND scope = ?
            ORDER BY rank ASC
            LIMIT ?
            """,
            (query, case.query_scope, max(case.top_k * 4, 12)),
        ).fetchall()
        hits: list[RetrievalHit] = []
        for entry_id, rank in rows:
            entry = self._entries[str(entry_id)]
            hits.append(
                RetrievalHit(
                    entry_id=entry.entry_id,
                    text=entry.text,
                    score=1.0 / (1.0 + max(0.0, float(rank) + 10.0)),
                    source="fts5",
                )
            )
            if len(hits) >= case.top_k:
                break
        return hits

    def _query(self, text: str) -> str:
        cleaned = " ".join(text.split())
        if self._tokenizer == "trigram":
            grams = _character_ngrams(cleaned, 3)
            if not grams:
                return ""
            # OR favors exact lexical anchors without forcing all conversational
            # filler tokens to match. Scope filtering and Golden false-recall
            # cases keep this honest.
            return " OR ".join(f'"{gram}"' for gram in grams[:64])
        tokens = [token for token in re.findall(r"[\w一-龥ぁ-んァ-ヴー]+", cleaned) if len(token) >= 2]
        return " OR ".join(f'"{token}"' for token in tokens[:32])

    def _search_text(self, text: str) -> str:
        return " ".join(text.split())


class OllamaBgeM3Embedder:
    """Small stdlib adapter mirroring Serina's shared CPU BGE-M3 contract."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        model: str = "bge-m3",
        timeout_sec: float = 20.0,
        call_fn: Callable[[str], list[float]] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_sec = timeout_sec
        self._call_fn = call_fn
        self.request_count = 0
        self._cache: dict[str, list[float]] = {}

    def embed(self, text: str) -> list[float]:
        cached = self._cache.get(text)
        if cached is not None:
            return list(cached)
        self.request_count += 1
        if self._call_fn is not None:
            vector = self._call_fn(text)
            if not vector:
                raise MemoryEvaluationError("embedding adapter returned an empty vector")
            normalized = [float(value) for value in vector]
            self._cache[text] = normalized
            return list(normalized)
        payload = json.dumps(
            {
                "model": self.model,
                "prompt": text,
                "options": {"num_gpu": 0},
                "keep_alive": -1,
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/embeddings",
            data=payload,
            method="POST",
            headers={"content-type": "application/json"},
        )
        try:
            with urlopen(request, timeout=self.timeout_sec) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise MemoryEvaluationError(f"Ollama bge-m3 unavailable: {exc}") from exc
        vector = parsed.get("embedding") if isinstance(parsed, dict) else None
        if not isinstance(vector, list) or not vector:
            raise MemoryEvaluationError("Ollama response did not contain an embedding")
        normalized = [float(value) for value in vector]
        self._cache[text] = normalized
        return list(normalized)


class GeminiEmbeddingAdapter:
    """Evaluation adapter for Gemini retrieval embeddings.

    Documents and queries use their dedicated task types. Vectors are cached in
    memory so a benchmark run never re-embeds the same text/role pair.
    """

    def __init__(
        self,
        nirai_root: Path,
        *,
        model: str = "gemini-embedding-2",
        output_dimensionality: int = 768,
        call_fn: Callable[[str, str], list[float]] | None = None,
    ) -> None:
        from core.brains.gemini import load_gemini_api_key

        self.model = model
        self.output_dimensionality = output_dimensionality
        self.api_key = load_gemini_api_key(nirai_root)
        self._call_fn = call_fn
        self.request_count = 0
        self._cache: dict[tuple[str, str], list[float]] = {}
        if self.api_key is None and self._call_fn is None:
            raise MemoryEvaluationError("GEMINI_API_KEY was not found in world/.env")

    def embed_document(self, text: str) -> list[float]:
        return self._embed(text, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text, "RETRIEVAL_QUERY")

    def _embed(self, text: str, task_type: str) -> list[float]:
        cache_key = (task_type, text)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return list(cached)
        self.request_count += 1
        if self._call_fn is not None:
            vector = self._call_fn(text, task_type)
        else:
            from core.brains.gemini import _request_json

            payload = {
                "model": f"models/{self.model}",
                "content": {"parts": [{"text": text}]},
                "embedContentConfig": {
                    "taskType": task_type,
                    "outputDimensionality": self.output_dimensionality,
                    "autoTruncate": True,
                },
            }
            try:
                parsed = _request_json(
                    self.api_key or "",
                    f"/models/{self.model}:embedContent",
                    payload,
                )
            except Exception as exc:  # noqa: BLE001
                raise MemoryEvaluationError(f"Gemini embedding unavailable: {exc}") from exc
            embedding = parsed.get("embedding") if isinstance(parsed, dict) else None
            vector = embedding.get("values") if isinstance(embedding, dict) else None
        if not isinstance(vector, list) or not vector:
            raise MemoryEvaluationError("Gemini embedding response contained no vector")
        normalized = [float(value) for value in vector]
        self._cache[cache_key] = normalized
        return list(normalized)


class GeminiSemanticBaseline(RetrievalBaseline):
    name = "semantic-gemini"

    def __init__(
        self,
        embedder: GeminiEmbeddingAdapter,
        *,
        similarity_floor: float = 0.50,
    ) -> None:
        self.embedder = embedder
        self.similarity_floor = similarity_floor
        self.name = f"semantic-{embedder.model}"
        self._entries: tuple[RawEntry, ...] = ()
        self._vectors: dict[str, list[float]] = {}

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        self._entries = entries
        self._vectors = {
            entry.entry_id: self.embedder.embed_document(entry.text)
            for entry in entries
        }

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        query_vector = self.embedder.embed_query(case.question)
        candidates: list[RetrievalHit] = []
        for entry in self._entries:
            if entry.scope != case.query_scope:
                continue
            similarity = _cosine_similarity(query_vector, self._vectors[entry.entry_id])
            if similarity < self.similarity_floor:
                continue
            candidates.append(
                RetrievalHit(
                    entry_id=entry.entry_id,
                    text=entry.text,
                    score=similarity,
                    source=self.embedder.model,
                )
            )
        candidates.sort(key=lambda hit: hit.score, reverse=True)
        return candidates[: case.top_k]


class SemanticBaseline(RetrievalBaseline):
    name = "semantic-bge-m3"

    def __init__(
        self,
        embedder: OllamaBgeM3Embedder,
        *,
        similarity_floor: float = 0.50,
    ) -> None:
        self.embedder = embedder
        self.similarity_floor = similarity_floor
        self._entries: tuple[RawEntry, ...] = ()
        self._vectors: dict[str, list[float]] = {}

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        self._entries = entries
        self._vectors = {entry.entry_id: self.embedder.embed(entry.text) for entry in entries}

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        query_vector = self.embedder.embed(case.question)
        candidates: list[RetrievalHit] = []
        for entry in self._entries:
            if entry.scope != case.query_scope:
                continue
            similarity = _cosine_similarity(query_vector, self._vectors[entry.entry_id])
            if similarity < self.similarity_floor:
                continue
            candidates.append(
                RetrievalHit(
                    entry_id=entry.entry_id,
                    text=entry.text,
                    score=similarity,
                    source="bge-m3",
                )
            )
        candidates.sort(key=lambda hit: hit.score, reverse=True)
        return candidates[: case.top_k]


class HybridBaseline(RetrievalBaseline):
    """Simple RRF fusion. No reranker, graph, or agentic planner."""

    name = "hybrid-rrf"

    def __init__(
        self,
        lexical: LexicalBaseline,
        semantic: SemanticBaseline,
        *,
        rrf_k: int = 60,
        allow_lexical_fallback: bool = True,
    ) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.rrf_k = rrf_k
        self.allow_lexical_fallback = allow_lexical_fallback
        self.semantic_available = True
        self.name = "hybrid-rrf"

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        self.lexical.build(entries)
        try:
            self.semantic.build(entries)
            self.semantic_available = True
            self.name = "hybrid-rrf"
        except MemoryEvaluationError:
            if not self.allow_lexical_fallback:
                raise
            self.semantic_available = False
            self.name = "hybrid-rrf[fts-fallback]"

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        lexical_hits = self.lexical.search(case)
        semantic_hits = self.semantic.search(case) if self.semantic_available else []
        scores: dict[str, float] = {}
        texts: dict[str, str] = {}
        sources: dict[str, set[str]] = {}
        for source_hits, source_name in ((lexical_hits, "fts5"), (semantic_hits, "bge-m3")):
            for rank, hit in enumerate(source_hits, start=1):
                scores[hit.entry_id] = scores.get(hit.entry_id, 0.0) + 1.0 / (self.rrf_k + rank)
                texts[hit.entry_id] = hit.text
                sources.setdefault(hit.entry_id, set()).add(source_name)
        ordered = sorted(scores, key=scores.__getitem__, reverse=True)[: case.top_k]
        return [
            RetrievalHit(
                entry_id=entry_id,
                text=texts[entry_id],
                score=scores[entry_id],
                source="+".join(sorted(sources[entry_id])),
            )
            for entry_id in ordered
        ]


class GatedHybridBaseline(RetrievalBaseline):
    """Hybrid retrieval with an abstention-oriented lexical gate.

    Semantic hits are accepted above the SemanticBaseline floor. A lexical-only
    hit must have enough direct query coverage; weak surface overlap does not
    get resurrected merely because RRF can rank it.
    """

    name = "hybrid-gated"

    def __init__(
        self,
        lexical: LexicalBaseline,
        semantic: SemanticBaseline,
        *,
        lexical_coverage_floor: float = 0.18,
        rrf_k: int = 60,
        allow_lexical_fallback: bool = True,
    ) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.lexical_coverage_floor = lexical_coverage_floor
        self.rrf_k = rrf_k
        self.allow_lexical_fallback = allow_lexical_fallback
        self.semantic_available = True
        self.name = "hybrid-gated"

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        self.lexical.build(entries)
        try:
            self.semantic.build(entries)
            self.semantic_available = True
            self.name = "hybrid-gated"
        except MemoryEvaluationError:
            if not self.allow_lexical_fallback:
                raise
            self.semantic_available = False
            self.name = "hybrid-gated[fts-fallback]"

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        lexical_hits = self.lexical.search(case)
        semantic_hits = self.semantic.search(case) if self.semantic_available else []
        semantic_ids = {hit.entry_id for hit in semantic_hits}
        lexical_hits = [
            hit
            for hit in lexical_hits
            if hit.entry_id in semantic_ids
            or _lexical_coverage(case.question, hit.text) >= self.lexical_coverage_floor
            or _has_distinctive_anchor_match(case.question, hit.text)
        ]

        scores: dict[str, float] = {}
        texts: dict[str, str] = {}
        sources: dict[str, set[str]] = {}
        for source_hits, source_name in ((lexical_hits, "fts5"), (semantic_hits, "bge-m3")):
            for rank, hit in enumerate(source_hits, start=1):
                scores[hit.entry_id] = scores.get(hit.entry_id, 0.0) + 1.0 / (self.rrf_k + rank)
                texts[hit.entry_id] = hit.text
                sources.setdefault(hit.entry_id, set()).add(source_name)
        ordered = sorted(scores, key=scores.__getitem__, reverse=True)[: case.top_k]
        return [
            RetrievalHit(
                entry_id=entry_id,
                text=texts[entry_id],
                score=scores[entry_id],
                source="+".join(sorted(sources[entry_id])),
            )
            for entry_id in ordered
        ]


class StructuredFactOverlayBaseline(RetrievalBaseline):
    """Conservative temporal-fact overlay on top of a raw retriever.

    Evaluation-only adapter. It models the value of explicit active/superseded
    state without pretending that semantic similarity alone can decide updates.
    """

    name = "structured-fact-overlay"
    _HISTORICAL_MARKERS = ("前は", "以前", "昔", "当時", "訂正する前", "前に")

    def __init__(
        self,
        raw_baseline: RetrievalBaseline,
        facts: tuple[StructuredFact, ...],
        entries: tuple[RawEntry, ...],
    ) -> None:
        self.raw_baseline = raw_baseline
        self.facts = facts
        self._entries = {entry.entry_id: entry for entry in entries}

    def build(self, entries: tuple[RawEntry, ...]) -> None:
        self.raw_baseline.build(entries)

    def search(self, case: EvaluationCase) -> list[RetrievalHit]:
        raw_hits = self.raw_baseline.search(case)
        matching = [
            fact
            for fact in self.facts
            if fact.scope == case.query_scope and self._matches(case.question, fact)
        ]
        if not matching:
            return raw_hits

        historical = any(marker in case.question for marker in self._HISTORICAL_MARKERS)
        selected = matching if historical else [fact for fact in matching if fact.status == "active"]
        if not selected:
            return raw_hits

        family_source_ids = {
            entry_id
            for fact in matching
            for entry_id in fact.source_entry_ids
        }
        selected_source_ids: list[str] = []
        for fact in sorted(selected, key=lambda item: item.valid_from):
            for entry_id in fact.source_entry_ids:
                if entry_id not in selected_source_ids:
                    selected_source_ids.append(entry_id)

        hits: list[RetrievalHit] = []
        for entry_id in selected_source_ids:
            entry = self._entries.get(entry_id)
            if entry is None:
                continue
            hits.append(
                RetrievalHit(
                    entry_id=entry.entry_id,
                    text=entry.text,
                    score=2.0,
                    source="structured-fact",
                )
            )
        for hit in raw_hits:
            if hit.entry_id in family_source_ids or hit.entry_id in selected_source_ids:
                continue
            hits.append(hit)
            if len(hits) >= case.top_k:
                break
        return hits[: case.top_k]

    @staticmethod
    def _matches(question: str, fact: StructuredFact) -> bool:
        if fact.search_terms:
            return all(term in question for term in fact.search_terms)
        combined = f"{fact.subject} {fact.predicate} {fact.value} {fact.statement}"
        question_grams = set(_character_ngrams(question, 2))
        fact_grams = set(_character_ngrams(combined, 2))
        return bool(question_grams and len(question_grams & fact_grams) >= 2)


class MemoryEvaluationHarness:
    def __init__(self, entries: tuple[RawEntry, ...], cases: tuple[EvaluationCase, ...]) -> None:
        self.entries = entries
        self.cases = cases

    def run(self, baseline: RetrievalBaseline) -> EvaluationReport:
        baseline.build(self.entries)
        results: list[EvaluationCaseResult] = []
        for case in self.cases:
            started = perf_counter()
            hits = baseline.search(case)
            latency_ms = (perf_counter() - started) * 1000.0
            returned = tuple(hit.entry_id for hit in hits)
            expected = set(case.must_include_evidence)
            forbidden = tuple(entry_id for entry_id in returned if entry_id in set(case.must_not_use))
            if case.should_abstain:
                recall = 1.0 if not hits else 0.0
                passed = not hits and not forbidden
            else:
                recalled = len(expected.intersection(returned))
                recall = recalled / len(expected) if expected else 1.0
                passed = recalled == len(expected) and not forbidden
            results.append(
                EvaluationCaseResult(
                    case_id=case.case_id,
                    passed=passed,
                    recall=recall,
                    forbidden_hits=forbidden,
                    returned_ids=returned,
                    latency_ms=latency_ms,
                    context_chars=sum(len(hit.text) for hit in hits),
                    tags=case.tags,
                )
            )
        return EvaluationReport(baseline=baseline.name, cases=tuple(results))


def load_fixture(path: Path) -> tuple[tuple[RawEntry, ...], tuple[EvaluationCase, ...]]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise MemoryEvaluationError("fixture root must be an object")
    entries = tuple(_raw_entry(item) for item in parsed.get("entries", []))
    cases = tuple(_evaluation_case(item) for item in parsed.get("cases", []))
    if not entries or not cases:
        raise MemoryEvaluationError("fixture must contain entries and cases")
    return entries, cases


def load_structured_facts(path: Path) -> tuple[StructuredFact, ...]:
    parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, dict):
        raise MemoryEvaluationError("fixture root must be an object")
    raw_facts = parsed.get("facts", [])
    if not isinstance(raw_facts, list):
        raise MemoryEvaluationError("fixture facts must be a list")
    return tuple(_structured_fact(item) for item in raw_facts)


def _structured_fact(value: Any) -> StructuredFact:
    if not isinstance(value, dict):
        raise MemoryEvaluationError("structured fact must be an object")
    valid_to = value.get("valid_to")
    if valid_to is not None and not isinstance(valid_to, str):
        raise MemoryEvaluationError("structured fact valid_to must be a string or null")
    return StructuredFact(
        fact_id=_required_str(value, "fact_id"),
        scope=_required_str(value, "scope"),
        subject=_required_str(value, "subject"),
        predicate=_required_str(value, "predicate"),
        value=_required_str(value, "value"),
        statement=_required_str(value, "statement"),
        valid_from=_required_str(value, "valid_from"),
        valid_to=valid_to,
        status=_required_str(value, "status"),
        source_entry_ids=tuple(_string_list(value.get("source_entry_ids"))),
        search_terms=tuple(_string_list(value.get("search_terms"))),
    )


def _raw_entry(value: Any) -> RawEntry:
    if not isinstance(value, dict):
        raise MemoryEvaluationError("raw entry must be an object")
    return RawEntry(
        entry_id=_required_str(value, "entry_id"),
        scope=_required_str(value, "scope"),
        conversation_id=_required_str(value, "conversation_id"),
        speaker_id=_required_str(value, "speaker_id"),
        participants=tuple(_string_list(value.get("participants"))),
        occurred_at=_required_str(value, "occurred_at"),
        text=_required_str(value, "text"),
        event_type=str(value.get("event_type") or "utterance"),
        resident_experience=tuple(_string_list(value.get("resident_experience"))),
        importance=float(value.get("importance", 0.5)),
    )


def _evaluation_case(value: Any) -> EvaluationCase:
    if not isinstance(value, dict):
        raise MemoryEvaluationError("evaluation case must be an object")
    expected = value.get("expected")
    if not isinstance(expected, dict):
        raise MemoryEvaluationError("evaluation case expected must be an object")
    return EvaluationCase(
        case_id=_required_str(value, "case_id"),
        query_scope=_required_str(value, "query_scope"),
        query_time=_required_str(value, "query_time"),
        question=_required_str(value, "question"),
        must_include_evidence=tuple(_string_list(expected.get("must_include_evidence"))),
        must_not_use=tuple(_string_list(expected.get("must_not_use"))),
        should_abstain=expected.get("should_abstain") is True,
        tags=tuple(_string_list(value.get("tags"))),
        top_k=int(value.get("top_k", 5)),
    )


def _required_str(value: dict[str, Any], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str) or not result.strip():
        raise MemoryEvaluationError(f"fixture field {key} must be a non-empty string")
    return result.strip()


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise MemoryEvaluationError("fixture list field must contain only strings")
    return [item for item in value if item]


def _character_ngrams(text: str, size: int) -> list[str]:
    compact = re.sub(r"\s+", "", text)
    if len(compact) < size:
        return [compact] if len(compact) >= 2 else []
    seen: set[str] = set()
    result: list[str] = []
    for index in range(len(compact) - size + 1):
        gram = compact[index : index + size]
        if gram not in seen:
            seen.add(gram)
            result.append(gram)
    return result


def _has_distinctive_anchor_match(question: str, text: str) -> bool:
    query_tokens = {
        token.casefold()
        for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", question)
        if len(token) >= 2
    }
    if not query_tokens:
        return False
    haystack = text.casefold()
    return any(token in haystack for token in query_tokens)


def _lexical_coverage(question: str, text: str) -> float:
    query_grams = set(_character_ngrams(_normalize_lexical_text(question), 3))
    if not query_grams:
        return 0.0
    text_grams = set(_character_ngrams(_normalize_lexical_text(text), 3))
    return len(query_grams & text_grams) / len(query_grams)


def _normalize_lexical_text(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z一-龥ぁ-んァ-ヴー]+", "", text)


def _cosine_similarity(left: Iterable[float], right: Iterable[float]) -> float:
    left_values = list(left)
    right_values = list(right)
    if len(left_values) != len(right_values) or not left_values:
        raise MemoryEvaluationError("embedding dimensions do not match")
    dot = sum(a * b for a, b in zip(left_values, right_values))
    left_norm = math.sqrt(sum(value * value for value in left_values))
    right_norm = math.sqrt(sum(value * value for value in right_values))
    if left_norm == 0.0 or right_norm == 0.0:
        return 0.0
    return dot / (left_norm * right_norm)
