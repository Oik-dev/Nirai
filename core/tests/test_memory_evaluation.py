from __future__ import annotations

from pathlib import Path

from core.memory.evaluation import (
    EvaluationCase,
    GatedHybridBaseline,
    GeminiEmbeddingAdapter,
    GeminiSemanticBaseline,
    HybridBaseline,
    LexicalBaseline,
    MemoryEvaluationError,
    MemoryEvaluationHarness,
    OllamaBgeM3Embedder,
    RawEntry,
    SemanticBaseline,
    StructuredFactOverlayBaseline,
    load_fixture,
    load_structured_facts,
)


FIXTURE = Path(__file__).with_name("memory_eval_golden_v1.json")


def test_memory_eval_fixture_loads_required_scope_and_abstention_cases() -> None:
    entries, cases = load_fixture(FIXTURE)

    assert len(entries) >= 9
    assert len(cases) >= 10
    assert any(case.should_abstain for case in cases)
    assert any("privacy" in case.tags for case in cases)
    assert any("correction" in case.tags for case in cases)
    assert {entry.scope for entry in entries} >= {"public", "private:lapan", "private:kina"}


def test_lexical_baseline_never_crosses_private_scope() -> None:
    entries, cases = load_fixture(FIXTURE)
    baseline = LexicalBaseline()
    baseline.build(entries)
    privacy_case = next(case for case in cases if case.case_id == "private-cross-scope-001")

    hits = baseline.search(privacy_case)

    assert all(hit.entry_id != "raw-kina-secret" for hit in hits)


def test_harness_scores_abstention_as_failure_when_unrelated_memory_is_returned() -> None:
    entries = (
        RawEntry(
            entry_id="raw-1",
            scope="private:lapan",
            conversation_id="whisper:lapan",
            speaker_id="master",
            participants=("master", "resident:lapan"),
            occurred_at="2026-01-01T00:00:00+09:00",
            text="海の話",
        ),
    )
    cases = (
        EvaluationCase(
            case_id="abstain",
            query_scope="private:lapan",
            query_time="2026-09-06T00:00:00+09:00",
            question="犬の名前は？",
            must_include_evidence=(),
            should_abstain=True,
        ),
    )

    class BadBaseline:
        name = "bad"

        def build(self, entries):
            pass

        def search(self, case):
            from core.memory.evaluation import RetrievalHit

            return [RetrievalHit(entry_id="raw-1", text="海の話", score=1.0, source="bad")]

    report = MemoryEvaluationHarness(entries, cases).run(BadBaseline())

    assert report.passed == 0
    assert report.cases[0].recall == 0.0


def test_semantic_baseline_filters_by_scope_and_similarity_floor() -> None:
    vectors = {
        "海で落ち着く": [1.0, 0.0],
        "秘密の塔": [0.0, 1.0],
        "落ち着ける場所": [0.9, 0.1],
        "犬の名前": [-1.0, 0.0],
    }
    embedder = OllamaBgeM3Embedder(call_fn=lambda text: vectors[text])
    baseline = SemanticBaseline(embedder, similarity_floor=0.5)
    baseline.build(
        (
            RawEntry(
                entry_id="sea",
                scope="private:lapan",
                conversation_id="w:lapan",
                speaker_id="master",
                participants=("master", "resident:lapan"),
                occurred_at="2026-01-01T00:00:00+09:00",
                text="海で落ち着く",
            ),
            RawEntry(
                entry_id="secret",
                scope="private:kina",
                conversation_id="w:kina",
                speaker_id="master",
                participants=("master", "resident:kina"),
                occurred_at="2026-01-01T00:00:00+09:00",
                text="秘密の塔",
            ),
        )
    )

    hits = baseline.search(
        EvaluationCase(
            case_id="semantic",
            query_scope="private:lapan",
            query_time="2026-09-06T00:00:00+09:00",
            question="落ち着ける場所",
            must_include_evidence=("sea",),
        )
    )
    abstain_hits = baseline.search(
        EvaluationCase(
            case_id="unknown",
            query_scope="private:lapan",
            query_time="2026-09-06T00:00:00+09:00",
            question="犬の名前",
            must_include_evidence=(),
            should_abstain=True,
        )
    )

    assert [hit.entry_id for hit in hits] == ["sea"]
    assert abstain_hits == []
    assert embedder.request_count == 4


def test_hybrid_rrf_combines_lexical_and_semantic_without_duplicate_evidence() -> None:
    entries = (
        RawEntry(
            entry_id="target",
            scope="private:lapan",
            conversation_id="w:lapan",
            speaker_id="master",
            participants=("master", "resident:lapan"),
            occurred_at="2026-01-01T00:00:00+09:00",
            text="青い真珠を海辺で見つけた",
        ),
        RawEntry(
            entry_id="other",
            scope="private:lapan",
            conversation_id="w:lapan",
            speaker_id="master",
            participants=("master", "resident:lapan"),
            occurred_at="2026-01-02T00:00:00+09:00",
            text="夕食はパンだった",
        ),
    )
    vectors = {
        "青い真珠を海辺で見つけた": [1.0, 0.0],
        "夕食はパンだった": [0.0, 1.0],
        "海辺の青い真珠": [1.0, 0.0],
    }
    hybrid = HybridBaseline(
        LexicalBaseline(),
        SemanticBaseline(OllamaBgeM3Embedder(call_fn=lambda text: vectors[text]), similarity_floor=0.5),
    )
    hybrid.build(entries)

    hits = hybrid.search(
        EvaluationCase(
            case_id="hybrid",
            query_scope="private:lapan",
            query_time="2026-09-06T00:00:00+09:00",
            question="海辺の青い真珠",
            must_include_evidence=("target",),
            top_k=3,
        )
    )

    assert [hit.entry_id for hit in hits].count("target") == 1
    assert hits[0].entry_id == "target"


def test_hybrid_degrades_to_lexical_when_bge_m3_is_unavailable() -> None:
    entries, cases = load_fixture(FIXTURE)

    def unavailable(_text: str) -> list[float]:
        raise MemoryEvaluationError("ollama offline")

    hybrid = HybridBaseline(
        LexicalBaseline(),
        SemanticBaseline(OllamaBgeM3Embedder(call_fn=unavailable)),
        allow_lexical_fallback=True,
    )
    report = MemoryEvaluationHarness(entries, cases).run(hybrid)

    assert hybrid.semantic_available is False
    assert report.baseline == "hybrid-rrf[fts-fallback]"
    assert report.total == len(cases)


def test_gemini_embedding_uses_document_and_query_task_types() -> None:
    entries, cases = load_fixture(FIXTURE)
    calls: list[tuple[str, str]] = []

    def fake_embed(text: str, task_type: str) -> list[float]:
        calls.append((text, task_type))
        if "海" in text or "気分転換" in text:
            return [1.0, 0.0]
        return [0.0, 1.0]

    embedder = GeminiEmbeddingAdapter(
        FIXTURE.parent,
        call_fn=fake_embed,
        output_dimensionality=2,
    )
    baseline = GeminiSemanticBaseline(embedder, similarity_floor=0.9)
    target_case = next(item for item in cases if item.case_id == "semantic-calm-001")
    baseline.build(entries)
    hits = baseline.search(target_case)

    assert hits[0].entry_id == "raw-lapan-sea"
    assert any(task_type == "RETRIEVAL_DOCUMENT" for _, task_type in calls)
    assert any(task_type == "RETRIEVAL_QUERY" for _, task_type in calls)


def test_gated_hybrid_fallback_drops_weak_lexical_near_miss() -> None:
    entries, cases = load_fixture(FIXTURE)

    def unavailable(_text: str) -> list[float]:
        raise MemoryEvaluationError("ollama offline")

    baseline = GatedHybridBaseline(
        LexicalBaseline(),
        SemanticBaseline(OllamaBgeM3Embedder(call_fn=unavailable)),
        allow_lexical_fallback=True,
    )
    report = MemoryEvaluationHarness(entries, cases).run(baseline)
    near_miss = next(item for item in report.cases if item.case_id == "abstain-near-miss-001")
    exact = next(item for item in report.cases if item.case_id == "exact-code-001")

    assert near_miss.passed is True
    assert near_miss.returned_ids == ()
    assert exact.passed is True


def test_structured_fact_overlay_resolves_current_correction_but_keeps_history() -> None:
    entries, cases = load_fixture(FIXTURE)
    facts = load_structured_facts(FIXTURE)
    baseline = StructuredFactOverlayBaseline(LexicalBaseline(), facts, entries)
    report = MemoryEvaluationHarness(entries, cases).run(baseline)

    current = next(item for item in report.cases if item.case_id == "correction-current-001")
    history = next(item for item in report.cases if item.case_id == "correction-history-001")

    assert current.passed is True
    assert current.returned_ids[0] == "raw-lapan-color-new"
    assert "raw-lapan-color-old" not in current.returned_ids
    assert history.passed is True
    assert set(history.returned_ids[:2]) == {"raw-lapan-color-old", "raw-lapan-color-new"}


def test_lexical_golden_report_exposes_known_correction_gap_instead_of_hiding_it() -> None:
    entries, cases = load_fixture(FIXTURE)
    report = MemoryEvaluationHarness(entries, cases).run(LexicalBaseline())
    correction = next(item for item in report.cases if item.case_id == "correction-current-001")

    assert report.total == len(cases)
    assert correction.passed is False
