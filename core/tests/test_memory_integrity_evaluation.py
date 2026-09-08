from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from core.memory.evaluation import RawEntry, StructuredFact, load_fixture, load_structured_facts
from core.memory.evaluation_integrity import (
    FactMutationProposal,
    ForgetSnapshot,
    MutationLedger,
    apply_fact_mutation,
    audit_forget_transition,
    validate_fact_mutation,
)


FIXTURE = Path(__file__).with_name("memory_eval_golden_v1.json")


def _fixture():
    entries, _cases = load_fixture(FIXTURE)
    facts = load_structured_facts(FIXTURE)
    return entries, facts


def _blue_correction() -> FactMutationProposal:
    return FactMutationProposal(
        mutation_id="mut-color-blue",
        fact_id="fact-lapan-color-blue-v2",
        scope="private:lapan",
        subject="master",
        predicate="favorite_color",
        value="青",
        statement="Masterの現在の好きな色は青",
        valid_from="2026-08-20T10:00:00+09:00",
        source_entry_ids=("raw-lapan-color-new",),
        evidence_quotes=("前に好きな色は赤って言ったけど、今は青のほうが好き。これは訂正ね。",),
        supersedes_fact_ids=("fact-lapan-color-red",),
        explicit_correction=True,
    )


def test_explicit_correction_supersedes_only_named_same_predicate_fact() -> None:
    entries, facts = _fixture()
    old_only = tuple(fact for fact in facts if fact.fact_id == "fact-lapan-color-red")

    result = apply_fact_mutation(
        _blue_correction(),
        raw_entries=entries,
        existing_facts=old_only,
    )

    assert result.applied is True
    by_id = {fact.fact_id: fact for fact in result.facts}
    assert by_id["fact-lapan-color-red"].status == "superseded"
    assert by_id["fact-lapan-color-red"].valid_to == "2026-08-20T10:00:00+09:00"
    assert by_id["fact-lapan-color-blue-v2"].status == "active"
    assert by_id["fact-lapan-color-blue-v2"].source_entry_ids == ("raw-lapan-color-new",)


def test_uncertain_conflicting_update_becomes_hypothesis_and_keeps_current_fact() -> None:
    raw = RawEntry(
        entry_id="raw-job-maybe",
        scope="private:lapan",
        conversation_id="whisper:lapan",
        speaker_id="master",
        participants=("master", "resident:lapan"),
        occurred_at="2026-09-01T12:00:00+09:00",
        text="今の会社を辞めるかもしれない。まだ決めてはいない。",
    )
    current = StructuredFact(
        fact_id="fact-job-current",
        scope="private:lapan",
        subject="master",
        predicate="employment_status",
        value="在職中",
        statement="Masterは現在の会社に在職中",
        valid_from="2026-05-11T00:00:00+09:00",
        valid_to=None,
        status="active",
        source_entry_ids=("raw-job-start",),
    )
    proposal = FactMutationProposal(
        mutation_id="mut-job-maybe",
        fact_id="fact-job-maybe",
        scope="private:lapan",
        subject="master",
        predicate="employment_status",
        value="退職を検討中",
        statement="Masterは退職を検討している",
        valid_from=raw.occurred_at,
        source_entry_ids=(raw.entry_id,),
        evidence_quotes=("辞めるかもしれない。まだ決めてはいない。",),
        uncertain=True,
    )

    result = apply_fact_mutation(proposal, raw_entries=(raw,), existing_facts=(current,))

    assert result.applied is True
    by_id = {fact.fact_id: fact for fact in result.facts}
    assert by_id["fact-job-current"].status == "active"
    assert by_id["fact-job-maybe"].status == "hypothesis"


def test_semantic_similarity_is_not_enough_to_auto_supersede() -> None:
    entries, facts = _fixture()
    old_only = tuple(fact for fact in facts if fact.fact_id == "fact-lapan-color-red")
    proposal = replace(
        _blue_correction(),
        mutation_id="mut-no-explicit",
        fact_id="fact-blue-no-explicit",
        explicit_correction=False,
    )

    decision = validate_fact_mutation(proposal, raw_entries=entries, existing_facts=old_only)

    assert decision.accepted is False
    assert "explicit correction" in decision.reason


def test_supersede_rejects_subject_or_predicate_mismatch() -> None:
    entries, facts = _fixture()
    wrong = StructuredFact(
        fact_id="fact-car-color",
        scope="private:lapan",
        subject="master-car",
        predicate="color",
        value="赤",
        statement="車の色は赤",
        valid_from="2026-03-01T10:00:00+09:00",
        valid_to=None,
        status="active",
        source_entry_ids=("raw-lapan-color-old",),
    )
    proposal = replace(
        _blue_correction(),
        mutation_id="mut-wrong-target",
        fact_id="fact-blue-wrong-target",
        supersedes_fact_ids=(wrong.fact_id,),
    )

    decision = validate_fact_mutation(proposal, raw_entries=entries, existing_facts=(*facts, wrong))

    assert decision.accepted is False
    assert "subject/predicate mismatch" in decision.reason


def test_mutation_rejects_cross_scope_raw_provenance() -> None:
    entries, facts = _fixture()
    proposal = FactMutationProposal(
        mutation_id="mut-cross-scope",
        fact_id="fact-cross-scope",
        scope="private:lapan",
        subject="dragon",
        predicate="location",
        value="北の塔",
        statement="秘密の竜は北の塔にいる",
        valid_from="2026-09-06T00:00:00+09:00",
        source_entry_ids=("raw-kina-secret",),
        evidence_quotes=("秘密の竜は北の塔にいる",),
    )

    decision = validate_fact_mutation(proposal, raw_entries=entries, existing_facts=facts)

    assert decision.accepted is False
    assert "privacy scope" in decision.reason


def test_mutation_rejects_quote_not_present_in_raw() -> None:
    entries, facts = _fixture()
    proposal = replace(
        _blue_correction(),
        mutation_id="mut-hallucinated-quote",
        fact_id="fact-hallucinated-quote",
        evidence_quotes=("Masterは紫が好きだと言った",),
    )

    decision = validate_fact_mutation(proposal, raw_entries=entries, existing_facts=facts)

    assert decision.accepted is False
    assert "not present" in decision.reason


def test_mutation_ledger_retry_is_idempotent() -> None:
    entries, facts = _fixture()
    old_only = tuple(fact for fact in facts if fact.fact_id == "fact-lapan-color-red")
    ledger = MutationLedger()

    first = ledger.apply(_blue_correction(), raw_entries=entries, existing_facts=old_only)
    retry = ledger.apply(_blue_correction(), raw_entries=entries, existing_facts=old_only)

    assert first.applied is True
    assert retry.applied is False
    assert retry.facts == first.facts
    assert sum(fact.fact_id == "fact-lapan-color-blue-v2" for fact in retry.facts) == 1


def test_forget_audit_detects_residue_in_structured_vector_and_delivery_cache() -> None:
    before = ForgetSnapshot(
        raw_entry_ids=frozenset({"raw-target", "raw-keep"}),
        structured_provenance={"fact-target": frozenset({"raw-target"})},
        lexical_entry_ids=frozenset({"raw-target", "raw-keep"}),
        vector_entry_ids=frozenset({"raw-target", "raw-keep"}),
        delivery_cache_entry_ids=frozenset({"raw-target"}),
    )
    bad_after = ForgetSnapshot(
        raw_entry_ids=frozenset({"raw-keep"}),
        structured_provenance={"fact-target": frozenset({"raw-target"})},
        lexical_entry_ids=frozenset({"raw-keep"}),
        vector_entry_ids=frozenset({"raw-target", "raw-keep"}),
        delivery_cache_entry_ids=frozenset({"raw-target"}),
    )

    audit = audit_forget_transition(
        before=before,
        after=bad_after,
        forgotten_entry_ids=("raw-target",),
    )

    assert audit.passed is False
    assert any(item.startswith("structured:") for item in audit.residues)
    assert "vector:raw-target" in audit.residues
    assert "delivery-cache:raw-target" in audit.residues


def test_forget_audit_requires_zero_residue_without_collateral_deletion() -> None:
    before = ForgetSnapshot(
        raw_entry_ids=frozenset({"raw-target", "raw-keep"}),
        structured_provenance={
            "fact-target": frozenset({"raw-target"}),
            "fact-keep": frozenset({"raw-keep"}),
        },
        lexical_entry_ids=frozenset({"raw-target", "raw-keep"}),
        vector_entry_ids=frozenset({"raw-target", "raw-keep"}),
        delivery_cache_entry_ids=frozenset({"raw-target", "raw-keep"}),
    )
    clean_after = ForgetSnapshot(
        raw_entry_ids=frozenset({"raw-keep"}),
        structured_provenance={"fact-keep": frozenset({"raw-keep"})},
        lexical_entry_ids=frozenset({"raw-keep"}),
        vector_entry_ids=frozenset({"raw-keep"}),
        delivery_cache_entry_ids=frozenset({"raw-keep"}),
    )

    audit = audit_forget_transition(
        before=before,
        after=clean_after,
        forgotten_entry_ids=("raw-target",),
    )

    assert audit.passed is True
    assert audit.residues == ()
    assert audit.collateral_missing == ()
