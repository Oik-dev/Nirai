from __future__ import annotations

import asyncio
from pathlib import Path

from core.memory.private import PrivateMemoryService
from core.memory.structured import WorldStructuredMemoryStore
from core.memory.world import WorldMemoryService
from core.memory.private_semantic import (
    PrivateMemoryBackgroundWorker,
    PrivateMemoryHybridRetriever,
    PrivateSemanticMemoryError,
    PrivateVectorStore,
)


def make_resident(root: Path, name: str = "Lapan") -> None:
    resident_dir = root / "residents" / name
    resident_dir.mkdir(parents=True, exist_ok=True)
    (resident_dir / "config.toml").write_text('brain = "codex"\n', encoding="utf-8")


class FakeProcessor:
    def __init__(self) -> None:
        self.documents: list[str] = []
        self.queries: list[str] = []
        self.fail_queries = False

    async def embed_document(self, text: str) -> list[float]:
        self.documents.append(text)
        if "海" in text or "静か" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]

    async def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        if self.fail_queries:
            raise PrivateSemanticMemoryError("busy")
        if "気分転換" in text or "頭の中" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]


def test_private_background_embedding_and_semantic_recall_stay_resident_scoped(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path, "Lapan")
        make_resident(tmp_path, "Kina")
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-LAPAN",
            sender="master",
            recipient="Lapan",
            text="疲れた時は海を眺めると気持ちが静かになって落ち着く",
            entry_id="CE-LAPAN-SEA",
        )
        memory.append_whisper(
            "Kina",
            session_id="S-KINA",
            sender="master",
            recipient="Kina",
            text="Kinaだけの秘密は北の塔",
            entry_id="CE-KINA-TOWER",
        )
        store = PrivateVectorStore(memory, vector_dim=2)
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(store, processor)  # type: ignore[arg-type]

        summary = await worker.process_pending(["Lapan", "Kina"], limit_total=2)
        assert summary.processed == 2
        assert summary.failed == 0
        assert store.vector_count("Lapan") == 1
        assert store.vector_count("Kina") == 1

        retriever = PrivateMemoryHybridRetriever(
            memory,
            store=store,
            processor=processor,  # type: ignore[arg-type]
        )
        hits = await retriever.search("Lapan", "頭の中が騒がしい日に気分転換するならどこ？")
        assert [hit.entry_id for hit in hits] == ["CE-LAPAN-SEA"]
        assert hits[0].source == "private-gemini-embedding-2"
        assert all(hit.entry_id != "CE-KINA-TOWER" for hit in hits)

    asyncio.run(scenario())


def test_private_semantic_failure_degrades_to_conservative_local_exact(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-CODE",
            sender="master",
            recipient="Lapan",
            text="保守用合言葉はZX-7419",
            entry_id="CE-CODE",
        )
        store = PrivateVectorStore(memory, vector_dim=2)
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(store, processor)  # type: ignore[arg-type]
        await worker.process_pending(["Lapan"])
        processor.fail_queries = True
        retriever = PrivateMemoryHybridRetriever(
            memory,
            store=store,
            processor=processor,  # type: ignore[arg-type]
        )

        reasons: list[str] = []

        async def capture(reason: str) -> None:
            reasons.append(reason)

        exact = await retriever.search("Lapan", "ZX-7419って何だっけ？", on_fallback=capture)
        unrelated = await retriever.search("Lapan", "昔飼っていた犬の名前は？", on_fallback=capture)
        assert [hit.entry_id for hit in exact] == ["CE-CODE"]
        assert unrelated == []
        assert reasons == ["semantic_query_failed"]

    asyncio.run(scenario())


def test_private_current_query_prefers_latest_semantic_evidence_without_requiring_structured_fact(
    tmp_path: Path,
) -> None:
    class CurrentProcessor:
        async def embed_document(self, _text: str) -> list[float]:
            return [0.0, 1.0]

        async def embed_query(self, _text: str) -> list[float]:
            return [0.0, 1.0]

    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-OLD",
            sender="master",
            recipient="Lapan",
            text="赤が一番好き",
            ts="2026-03-01T10:00:00+09:00",
            entry_id="CE-COLOR-OLD",
        )
        memory.append_whisper(
            "Lapan",
            session_id="S-NEW",
            sender="master",
            recipient="Lapan",
            text="最近は青のほうを選ぶことが増えた",
            ts="2026-08-20T10:00:00+09:00",
            entry_id="CE-COLOR-NEW",
        )
        store = PrivateVectorStore(memory, vector_dim=2)
        processor = CurrentProcessor()
        worker = PrivateMemoryBackgroundWorker(store, processor)  # type: ignore[arg-type]
        await worker.process_pending(["Lapan"], limit_total=1)
        await worker.process_pending(["Lapan"], limit_total=1)
        retriever = PrivateMemoryHybridRetriever(
            memory,
            store=store,
            processor=processor,  # type: ignore[arg-type]
        )

        hits = await retriever.search("Lapan", "今はどの色を選びそう？")

        assert [hit.entry_id for hit in hits] == ["CE-COLOR-NEW"]

    asyncio.run(scenario())


def test_private_temporal_query_does_not_return_ambiguous_lexical_hits_when_semantic_is_unavailable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-OLD",
            sender="master",
            recipient="Lapan",
            text="今いちばん好きな色は赤かな",
            entry_id="CE-COLOR-OLD",
        )
        memory.append_whisper(
            "Lapan",
            session_id="S-NEW",
            sender="master",
            recipient="Lapan",
            text="前に好きな色は赤って言ったけど、今は青のほうが好き。これは訂正ね",
            entry_id="CE-COLOR-NEW",
        )
        retriever = PrivateMemoryHybridRetriever(memory)

        hits = await retriever.search("Lapan", "今いちばん好きな色は何色？")

        assert hits == []

    asyncio.run(scenario())


def test_private_temporal_query_falls_back_to_one_strong_local_hit_when_semantic_is_unavailable(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-CODE",
            sender="master",
            recipient="Lapan",
            text="保守用合言葉はZX-7419",
            entry_id="CE-TEMPORAL-CODE",
        )
        retriever = PrivateMemoryHybridRetriever(memory)

        hits = await retriever.search("Lapan", "今の保守用合言葉ZX-7419って何だっけ？")

        assert [hit.entry_id for hit in hits] == ["CE-TEMPORAL-CODE"]

    asyncio.run(scenario())


def test_private_embedding_shares_gemini_background_budget_with_public_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        WorldMemoryService(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-BUDGET",
            sender="master",
            recipient="Lapan",
            text="Gemini shared budget test",
            entry_id="CE-BUDGET",
        )
        quota = WorldStructuredMemoryStore(tmp_path, vector_dim=2)
        assert quota.reserve_background_slot(daily_limit=1, embedding_daily_budget=1) is True
        store = PrivateVectorStore(memory, vector_dim=2)
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(
            store,
            processor,  # type: ignore[arg-type]
            quota_store=quota,
            daily_limit=1,
            embedding_daily_budget=1,
        )

        summary = await worker.process_pending(["Lapan"])

        assert summary.processed == 0
        assert summary.failed == 0
        assert summary.budget_exhausted is True
        assert processor.documents == []
        assert store.vector_count("Lapan") == 0

    asyncio.run(scenario())


def test_private_query_shares_gemini_query_budget_with_public_memory(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        WorldMemoryService(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-Q-BUDGET",
            sender="master",
            recipient="Lapan",
            text="疲れた時は海を眺めると気持ちが静かになる",
            entry_id="CE-Q-BUDGET",
        )
        store = PrivateVectorStore(memory, vector_dim=2)
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(store, processor)  # type: ignore[arg-type]
        await worker.process_pending(["Lapan"])
        quota = WorldStructuredMemoryStore(tmp_path, vector_dim=2)
        assert quota.reserve_query_embedding_slot(daily_limit=1, embedding_daily_budget=1) is True
        processor.queries.clear()
        retriever = PrivateMemoryHybridRetriever(
            memory,
            store=store,
            processor=processor,  # type: ignore[arg-type]
            quota_store=quota,
            query_embedding_daily_limit=1,
            embedding_daily_budget=1,
        )

        hits = await retriever.search("Lapan", "疲れた時は海を眺めると静かになるって話、最近も覚えてる？")

        assert [hit.entry_id for hit in hits] == ["CE-Q-BUDGET"]
        assert processor.queries == []

    asyncio.run(scenario())


def test_private_vector_dimension_change_rebuilds_derived_index_from_raw(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-MIGRATE",
            sender="master",
            recipient="Lapan",
            text="provider migration memory",
            entry_id="CE-MIGRATE",
        )
        old_store = PrivateVectorStore(memory, vector_dim=2)
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(old_store, processor)  # type: ignore[arg-type]
        await worker.process_pending(["Lapan"])
        assert old_store.vector_count("Lapan") == 1

        new_store = PrivateVectorStore(memory, vector_dim=3)

        assert new_store.vector_count("Lapan") == 0
        pending = new_store.pending_jobs("Lapan")
        assert [job.entry_id for job in pending] == ["CE-MIGRATE"]
        assert pending[0].attempts == 0

    asyncio.run(scenario())


def test_private_embedding_model_change_rebuilds_same_dimension_index_from_raw(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-MODEL-MIGRATE",
            sender="master",
            recipient="Lapan",
            text="same dimension model migration memory",
            entry_id="CE-MODEL-MIGRATE",
        )
        old_store = PrivateVectorStore(
            memory,
            vector_dim=2,
            embedding_model="embedding-model-a",
        )
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(old_store, processor)  # type: ignore[arg-type]
        await worker.process_pending(["Lapan"])
        assert old_store.vector_count("Lapan") == 1

        new_store = PrivateVectorStore(
            memory,
            vector_dim=2,
            embedding_model="embedding-model-b",
        )

        assert new_store.vector_count("Lapan") == 0
        pending = new_store.pending_jobs("Lapan")
        assert [job.entry_id for job in pending] == ["CE-MODEL-MIGRATE"]
        assert pending[0].attempts == 0

    asyncio.run(scenario())


def test_private_forget_removes_semantic_vector_and_pending_job(tmp_path: Path) -> None:
    async def scenario() -> None:
        make_resident(tmp_path)
        memory = PrivateMemoryService(tmp_path)
        memory.append_whisper(
            "Lapan",
            session_id="S-FORGET",
            sender="master",
            recipient="Lapan",
            text="忘れる対象の秘密",
            entry_id="CE-FORGET-VEC",
        )
        store = PrivateVectorStore(memory, vector_dim=2)
        processor = FakeProcessor()
        worker = PrivateMemoryBackgroundWorker(store, processor)  # type: ignore[arg-type]
        await worker.process_pending(["Lapan"])
        assert store.vector_count("Lapan") == 1

        assert memory.forget_entry("Lapan", "CE-FORGET-VEC") is True
        assert store.vector_count("Lapan") == 0
        assert store.pending_jobs("Lapan") == []

    asyncio.run(scenario())
