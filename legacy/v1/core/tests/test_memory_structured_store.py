from __future__ import annotations

import asyncio
from pathlib import Path

from core.memory.structured import (
    StructuredMemoryCandidate,
    WorldMemoryBackgroundWorker,
    WorldStructuredMemoryStore,
)
from core.memory.world import WorldMemoryService


def public_entry(entry_id: str, text: str, ts: str) -> dict[str, str]:
    return {
        "entry_id": entry_id,
        "ts": ts,
        "kind": "say",
        "from": "master",
        "text": text,
        "session": "S-STRUCTURED",
    }


class FakeProcessor:
    def __init__(self, candidates_by_text: dict[str, list[StructuredMemoryCandidate]], *, fail: bool = False) -> None:
        self.candidates_by_text = candidates_by_text
        self.fail = fail

    async def embed_document(self, text: str) -> list[float]:
        if self.fail:
            raise RuntimeError("cloud offline")
        return [0.1, 0.2, 0.3]

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("cloud offline")
        return [[0.1, 0.2, 0.3] for _ in texts]

    async def extract(self, text: str, *, speaker_id: str) -> list[StructuredMemoryCandidate]:
        if self.fail:
            raise RuntimeError("cloud offline")
        assert speaker_id == "master"
        return self.candidates_by_text.get(text, [])


def candidate(
    *,
    quote: str,
    value: str,
    certainty: str = "confirmed",
    explicit_correction: bool = False,
    previous_value: str | None = None,
) -> StructuredMemoryCandidate:
    return StructuredMemoryCandidate(
        quote=quote,
        kind="preference",
        subject="master",
        attribute="favorite_color",
        value=value,
        statement=f"Masterの好きな色は{value}",
        certainty=certainty,
        explicit_correction=explicit_correction,
        previous_value=previous_value,
    )


def test_background_worker_applies_explicit_correction_but_not_unmarked_conflict(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    red_text = "好きな色は赤。"
    blue_text = "前に赤と言ったけど、今は青が好き。"
    green_text = "最近は緑もいいと思う。"
    raw.record_public_entry(public_entry("CE-RED", red_text, "2026-01-01T10:00:00+09:00"))
    raw.record_public_entry(public_entry("CE-BLUE", blue_text, "2026-02-01T10:00:00+09:00"))
    raw.record_public_entry(public_entry("CE-GREEN", green_text, "2026-03-01T10:00:00+09:00"))

    processor = FakeProcessor({
        red_text: [candidate(quote="好きな色は赤", value="赤")],
        blue_text: [candidate(
            quote="前に赤と言ったけど、今は青が好き",
            value="青",
            explicit_correction=True,
            previous_value="赤",
        )],
        green_text: [candidate(quote="最近は緑もいいと思う", value="緑")],
    })
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    worker = WorldMemoryBackgroundWorker(store, processor)  # type: ignore[arg-type]

    summary = asyncio.run(worker.process_pending(limit=10))

    assert summary.processed == 3
    assert summary.failed == 0
    current = store.current_fact("master", "favorite_color")
    assert current is not None
    assert current["value"] == "青"
    with store._connect() as connection:  # noqa: SLF001 - test-only inspection
        rows = connection.execute(
            "SELECT value, status FROM atomic_memories ORDER BY valid_from"
        ).fetchall()
    assert [(row["value"], row["status"]) for row in rows] == [
        ("赤", "superseded"),
        ("青", "active"),
        ("緑", "hypothesis"),
    ]


def test_background_worker_rejects_unsupported_quote_but_keeps_job_and_vector_consistent(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    text = "今日は公開で白い砂の話をした。"
    entry = public_entry("CE-QUOTE", text, "2026-04-01T10:00:00+09:00")
    raw.record_public_entry(entry)
    fake = StructuredMemoryCandidate(
        quote="原文に存在しない引用",
        kind="fact",
        subject="master",
        attribute="invented",
        value="捏造",
        statement="存在しない事実",
        certainty="confirmed",
    )
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    worker = WorldMemoryBackgroundWorker(store, FakeProcessor({text: [fake]}))  # type: ignore[arg-type]

    summary = asyncio.run(worker.process_pending())

    assert summary.processed == 1
    assert summary.candidates_committed == 0
    state = store.job_state(WorldMemoryService.raw_entry_id(entry))
    assert state is not None and state["status"] == "done"
    with store._connect() as connection:  # noqa: SLF001
        assert connection.execute("SELECT COUNT(*) FROM atomic_memories").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM raw_vec").fetchone()[0] == 1


def test_forget_session_removes_raw_structured_current_fact_and_vector(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    text = "好きな場所は海辺。"
    entry = public_entry("CE-FORGET-ALL", text, "2026-04-10T10:00:00+09:00")
    raw.record_public_entry(entry)
    memory = StructuredMemoryCandidate(
        quote="好きな場所は海辺",
        kind="preference",
        subject="master",
        attribute="favorite_place",
        value="海辺",
        statement="Masterの好きな場所は海辺",
        certainty="confirmed",
    )
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    worker = WorldMemoryBackgroundWorker(store, FakeProcessor({text: [memory]}))  # type: ignore[arg-type]
    assert asyncio.run(worker.process_pending()).processed == 1
    assert store.current_fact("master", "favorite_place") is not None

    assert raw.forget_session("S-STRUCTURED") == 1

    assert raw.raw_entries_for_session("S-STRUCTURED") == []
    assert store.current_fact("master", "favorite_place") is None
    with store._connect() as connection:  # noqa: SLF001
        assert connection.execute("SELECT COUNT(*) FROM atomic_memories").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM raw_vec").fetchone()[0] == 0


def test_background_worker_failure_backs_off_without_losing_raw(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    entry = public_entry("CE-FAIL", "クラウド障害でも原文は残す。", "2026-05-01T10:00:00+09:00")
    raw.record_public_entry(entry)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    worker = WorldMemoryBackgroundWorker(store, FakeProcessor({}, fail=True))  # type: ignore[arg-type]

    summary = asyncio.run(worker.process_pending())

    assert summary.processed == 0
    assert summary.failed == 1
    assert raw.raw_entries_for_session("S-STRUCTURED") == [entry]
    state = store.job_state(WorldMemoryService.raw_entry_id(entry))
    assert state is not None
    assert state["status"] == "pending"
    assert state["attempts"] == 1
    assert state["next_attempt_at"] is not None


def test_embedding_budget_is_shared_between_background_and_query_calls(tmp_path: Path) -> None:
    WorldMemoryService(tmp_path)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)

    assert store.reserve_background_slot(daily_limit=2, embedding_daily_budget=2) is True
    assert store.reserve_query_embedding_slot(daily_limit=2, embedding_daily_budget=2) is True
    assert store.reserve_background_slot(daily_limit=2, embedding_daily_budget=2) is False
    assert store.reserve_query_embedding_slot(daily_limit=2, embedding_daily_budget=2) is False


def test_embedding_budget_uses_rolling_24h_window_not_local_calendar_day(tmp_path: Path) -> None:
    WorldMemoryService(tmp_path)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    with store._connect() as connection:  # noqa: SLF001
        connection.execute(
            "INSERT INTO cloud_usage_events(used_at, kind) VALUES(?, 'background')",
            ("2000-01-01T00:00:00+00:00",),
        )
        connection.commit()

    assert store.reserve_background_slot(daily_limit=1, embedding_daily_budget=1) is True
    assert store.reserve_query_embedding_slot(daily_limit=1, embedding_daily_budget=1) is False


def test_embedding_model_change_rebuilds_only_vectors_and_keeps_structured_facts(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    text = "好きな場所は海辺。"
    entry = public_entry("CE-MODEL-MIGRATE", text, "2026-05-20T10:00:00+09:00")
    raw.record_public_entry(entry)
    memory = StructuredMemoryCandidate(
        quote="好きな場所は海辺",
        kind="preference",
        subject="master",
        attribute="favorite_place",
        value="海辺",
        statement="Masterの好きな場所は海辺",
        certainty="confirmed",
    )
    old = WorldStructuredMemoryStore(tmp_path, vector_dim=3, embedding_model="embedding-old")
    worker = WorldMemoryBackgroundWorker(old, FakeProcessor({text: [memory]}))  # type: ignore[arg-type]
    assert asyncio.run(worker.process_pending()).processed == 1
    assert old.vector_count() == 1
    assert old.current_fact("master", "favorite_place") is not None

    migrated = WorldStructuredMemoryStore(tmp_path, vector_dim=3, embedding_model="embedding-new")

    assert migrated.vector_count() == 0
    assert migrated.vector_rebuild_pending_count() == 1
    current = migrated.current_fact("master", "favorite_place")
    assert current is not None and current["value"] == "海辺"
    assert migrated.job_state(WorldMemoryService.raw_entry_id(entry))["status"] == "done"

    summary = asyncio.run(
        WorldMemoryBackgroundWorker(migrated, FakeProcessor({})).process_pending(limit=1)  # type: ignore[arg-type]
    )
    assert summary.processed == 0
    assert summary.vectors_rebuilt == 1
    assert migrated.vector_count() == 1
    assert migrated.vector_rebuild_pending_count() == 0
    assert migrated.current_fact("master", "favorite_place")["value"] == "海辺"


def test_resident_current_facts_are_isolated_by_speaker_identity(tmp_path: Path) -> None:
    raw = WorldMemoryService(tmp_path)
    lapan = {
        "entry_id": "CE-LAPAN-PREF",
        "ts": "2026-06-01T10:00:00+09:00",
        "kind": "resident_say",
        "from": "Lapan",
        "text": "私は青が好き。",
        "session": "S-RESIDENT-PREF",
    }
    kina = {
        "entry_id": "CE-KINA-PREF",
        "ts": "2026-06-01T10:01:00+09:00",
        "kind": "resident_say",
        "from": "Kina",
        "text": "私は赤が好き。",
        "session": "S-RESIDENT-PREF",
    }
    raw.record_public_entry(lapan)
    raw.record_public_entry(kina)
    store = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    jobs = {job.raw_id: job for job in store.pending_jobs(limit=10)}
    store.commit_job(
        jobs[raw.raw_entry_id(lapan)],
        embedding=[1.0, 0.0, 0.0],
        candidates=[StructuredMemoryCandidate(
            quote="私は青が好き",
            kind="preference",
            subject="resident",
            attribute="favorite_color",
            value="青",
            statement="Lapanの好きな色は青",
            certainty="confirmed",
        )],
    )
    store.commit_job(
        jobs[raw.raw_entry_id(kina)],
        embedding=[0.0, 1.0, 0.0],
        candidates=[StructuredMemoryCandidate(
            quote="私は赤が好き",
            kind="preference",
            subject="resident",
            attribute="favorite_color",
            value="赤",
            statement="Kinaの好きな色は赤",
            certainty="confirmed",
        )],
    )

    lapan_current = store.current_fact("resident:Lapan", "favorite_color")
    kina_current = store.current_fact("resident:Kina", "favorite_color")
    assert lapan_current is not None and lapan_current["value"] == "青"
    assert kina_current is not None and kina_current["value"] == "赤"
    rows = store.structured_rows()
    assert {row["subject"] for row in rows} == {"resident:Lapan", "resident:Kina"}
    assert all(row["is_current"] == 1 for row in rows)

    # Re-open after simulating the early derived-row representation. Schema
    # initialization must repair it from Raw speaker provenance.
    with store._connect() as connection:  # noqa: SLF001
        connection.execute("UPDATE atomic_memories SET subject='resident' WHERE subject='resident:Lapan'")
        connection.commit()
    repaired = WorldStructuredMemoryStore(tmp_path, vector_dim=3)
    repaired_rows = repaired.structured_rows()
    assert {row["subject"] for row in repaired_rows} == {"resident:Lapan", "resident:Kina"}
    assert all(row["is_current"] == 1 for row in repaired_rows)
