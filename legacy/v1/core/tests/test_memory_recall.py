from __future__ import annotations

import asyncio
from pathlib import Path

from core.memory.recall import WorldMemoryHybridRetriever
from core.memory.structured import (
    StructuredMemoryCandidate,
    WorldStructuredJob,
    WorldStructuredMemoryStore,
)
from core.memory.world import WorldMemoryService


class FakeQueryProcessor:
    def __init__(self, query_vectors: dict[str, list[float]]) -> None:
        self.query_vectors = query_vectors
        self.query_calls: list[str] = []

    async def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return list(self.query_vectors[text])


def entry(entry_id: str, text: str, *, session: str = "S-RECALL", ts: str = "2026-09-01T10:00:00+09:00") -> dict[str, str]:
    return {
        "entry_id": entry_id,
        "ts": ts,
        "kind": "say",
        "from": "master",
        "text": text,
        "session": session,
        "request_id": f"REQ-{entry_id}",
    }


def commit_vector(
    store: WorldStructuredMemoryStore,
    raw: WorldMemoryService,
    raw_entry: dict[str, str],
    vector: list[float],
    candidates: list[StructuredMemoryCandidate] | None = None,
) -> None:
    raw_id = raw.raw_entry_id(raw_entry)
    jobs = {job.raw_id: job for job in store.pending_jobs(limit=50)}
    job = jobs[raw_id]
    store.commit_job(job, embedding=vector, candidates=list(candidates or []))


def test_strong_lexical_recall_skips_gemini_query_embedding(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    target = entry("CE-CODE", "Niraiの保守用合言葉はZX-7419。型番まで正確に覚えておいて。")
    raw.record_public_entry(target)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    commit_vector(store, raw, target, [1.0, 0.0, 0.0])
    processor = FakeQueryProcessor({})
    retriever = WorldMemoryHybridRetriever(
        tmp_path,
        store=store,
        processor=processor,  # type: ignore[arg-type]
    )

    hits = asyncio.run(retriever.search("保守用の合言葉ZX-7419は何だっけ？"))

    assert [hit.entry_id for hit in hits] == ["CE-CODE"]
    assert hits[0].source == "raw-fts5"
    assert processor.query_calls == []


def test_semantic_fallback_reports_when_gemini_layer_is_unavailable(tmp_path: Path) -> None:
    retriever = WorldMemoryHybridRetriever(tmp_path)
    reasons: list[str] = []

    async def capture(reason: str) -> None:
        reasons.append(reason)

    async def scenario() -> None:
        await retriever.search(
            "意味検索が必要な質問",
            on_fallback=capture,
        )

    asyncio.run(scenario())

    assert reasons == ["semantic_unavailable"]


def test_semantic_recall_uses_gemini_only_when_local_match_is_not_strong(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    sea = entry("CE-SEA", "疲れた時は海を眺めていると気持ちが静かになって落ち着く。人混みより海辺を選びたい。")
    bread = entry("CE-BREAD", "夕食は焼きたてのパンとスープにしようと話した。", ts="2026-09-02T10:00:00+09:00")
    raw.record_public_entry(sea)
    raw.record_public_entry(bread)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    commit_vector(store, raw, sea, [1.0, 0.0, 0.0])
    commit_vector(store, raw, bread, [0.0, 1.0, 0.0])
    question = "頭の中が騒がしい日に、気分転換先として自然に選びそうなのはどこ？"
    processor = FakeQueryProcessor({question: [1.0, 0.0, 0.0]})
    retriever = WorldMemoryHybridRetriever(
        tmp_path,
        store=store,
        processor=processor,  # type: ignore[arg-type]
    )

    hits = asyncio.run(retriever.search(question))

    assert hits[0].entry_id == "CE-SEA"
    assert "gemini-embedding-2" in hits[0].source
    assert processor.query_calls == [question]


def test_near_miss_abstains_instead_of_returning_related_but_unanswered_memory(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    sea = entry("CE-SEA", "疲れた時は海を眺めていると気持ちが静かになって落ち着く。人混みより海辺を選びたい。")
    raw.record_public_entry(sea)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    commit_vector(
        store,
        raw,
        sea,
        [1.0, 0.0, 0.0],
        [StructuredMemoryCandidate(
            quote="人混みより海辺を選びたい",
            kind="preference",
            subject="master",
            attribute="preferred_place",
            value="海辺",
            statement="Masterは人混みより海辺を選びたい",
            certainty="confirmed",
        )],
    )
    question = "海を眺めた日の駐車料金はいくらだった？"
    processor = FakeQueryProcessor({question: [0.0, 1.0, 0.0]})
    retriever = WorldMemoryHybridRetriever(
        tmp_path,
        store=store,
        processor=processor,  # type: ignore[arg-type]
    )

    hits = asyncio.run(retriever.search(question))

    assert hits == []
    assert processor.query_calls == [question]


def test_structured_current_fact_suppresses_superseded_raw_without_cloud_query(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    old = entry("CE-COLOR-OLD", "今いちばん好きな色は赤かな。", ts="2026-03-01T10:00:00+09:00")
    new = entry(
        "CE-COLOR-NEW",
        "前に好きな色は赤って言ったけど、今は青のほうが好き。これは訂正ね。",
        ts="2026-08-20T10:00:00+09:00",
    )
    raw.record_public_entry(old)
    raw.record_public_entry(new)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    commit_vector(
        store,
        raw,
        old,
        [1.0, 0.0, 0.0],
        [
            StructuredMemoryCandidate(
                quote="好きな色は赤",
                kind="preference",
                subject="master",
                attribute="favorite_color",
                value="赤",
                statement="Masterの好きな色は赤",
                certainty="confirmed",
            )
        ],
    )
    commit_vector(
        store,
        raw,
        new,
        [1.0, 0.0, 0.0],
        [
            StructuredMemoryCandidate(
                quote="好きな色は赤って言ったけど、今は青のほうが好き",
                kind="preference",
                subject="master",
                attribute="favorite_color",
                value="青",
                statement="Masterの現在の好きな色は青",
                certainty="confirmed",
                explicit_correction=True,
                previous_value="赤",
            )
        ],
    )
    processor = FakeQueryProcessor({})
    retriever = WorldMemoryHybridRetriever(
        tmp_path,
        store=store,
        processor=processor,  # type: ignore[arg-type]
    )

    hits = asyncio.run(retriever.search("今いちばん好きな色は何色？"))

    assert hits[0].entry_id == "CE-COLOR-NEW"
    assert "CE-COLOR-OLD" not in [hit.entry_id for hit in hits]
    assert hits[0].source == "structured-current"
    assert processor.query_calls == []


def test_structured_historical_query_returns_old_and_new_evidence(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    old = entry("CE-COLOR-OLD", "今いちばん好きな色は赤かな。", ts="2026-03-01T10:00:00+09:00")
    new = entry(
        "CE-COLOR-NEW",
        "前に好きな色は赤って言ったけど、今は青のほうが好き。これは訂正ね。",
        ts="2026-08-20T10:00:00+09:00",
    )
    raw.record_public_entry(old)
    raw.record_public_entry(new)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    commit_vector(
        store,
        raw,
        old,
        [1.0, 0.0, 0.0],
        [StructuredMemoryCandidate("好きな色は赤", "preference", "master", "favorite_color", "赤", "Masterの好きな色は赤", "confirmed")],
    )
    commit_vector(
        store,
        raw,
        new,
        [1.0, 0.0, 0.0],
        [StructuredMemoryCandidate("好きな色は赤って言ったけど、今は青のほうが好き", "preference", "master", "favorite_color", "青", "Masterの現在の好きな色は青", "confirmed", True, "赤")],
    )
    question = "訂正する前は好きな色を何色だと言っていた？"
    processor = FakeQueryProcessor({question: [1.0, 0.0, 0.0]})
    retriever = WorldMemoryHybridRetriever(
        tmp_path,
        store=store,
        processor=processor,  # type: ignore[arg-type]
    )

    hits = asyncio.run(retriever.search(question))

    assert [hit.entry_id for hit in hits[:2]] == ["CE-COLOR-OLD", "CE-COLOR-NEW"]
    assert all(hit.source == "structured-history" for hit in hits[:2])
    assert processor.query_calls == [question]
