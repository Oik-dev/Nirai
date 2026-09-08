from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from .evaluation import RawEntry, StructuredFact


@dataclass(frozen=True)
class FactMutationProposal:
    mutation_id: str
    fact_id: str
    scope: str
    subject: str
    predicate: str
    value: str
    statement: str
    valid_from: str
    source_entry_ids: tuple[str, ...]
    evidence_quotes: tuple[str, ...]
    supersedes_fact_ids: tuple[str, ...] = ()
    explicit_correction: bool = False
    uncertain: bool = False


@dataclass(frozen=True)
class MutationDecision:
    accepted: bool
    status: str | None
    supersede_fact_ids: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class MutationApplyResult:
    facts: tuple[StructuredFact, ...]
    decision: MutationDecision
    applied: bool


class MutationLedger:
    """Evaluation-only idempotency ledger.

    It models the invariant that durable background retry must not duplicate a
    Structured Fact. It is not a product persistence implementation.
    """

    def __init__(self) -> None:
        self._applied: dict[str, MutationApplyResult] = {}

    def apply(
        self,
        proposal: FactMutationProposal,
        *,
        raw_entries: Iterable[RawEntry],
        existing_facts: Iterable[StructuredFact],
    ) -> MutationApplyResult:
        previous = self._applied.get(proposal.mutation_id)
        if previous is not None:
            return replace(previous, applied=False)
        result = apply_fact_mutation(
            proposal,
            raw_entries=tuple(raw_entries),
            existing_facts=tuple(existing_facts),
        )
        if result.applied:
            self._applied[proposal.mutation_id] = result
        return result


def validate_fact_mutation(
    proposal: FactMutationProposal,
    *,
    raw_entries: Iterable[RawEntry],
    existing_facts: Iterable[StructuredFact],
) -> MutationDecision:
    raw_by_id = {entry.entry_id: entry for entry in raw_entries}
    facts_by_id = {fact.fact_id: fact for fact in existing_facts}

    if not proposal.mutation_id.strip() or not proposal.fact_id.strip():
        return MutationDecision(False, None, (), "mutation/fact id is required")
    if not proposal.source_entry_ids:
        return MutationDecision(False, None, (), "structured fact requires raw provenance")
    if not proposal.evidence_quotes:
        return MutationDecision(False, None, (), "structured fact requires exact evidence quote")

    sources: list[RawEntry] = []
    for entry_id in proposal.source_entry_ids:
        source = raw_by_id.get(entry_id)
        if source is None:
            return MutationDecision(False, None, (), f"missing raw provenance: {entry_id}")
        if source.scope != proposal.scope:
            return MutationDecision(False, None, (), "raw provenance crosses privacy scope")
        sources.append(source)

    source_text = "\n".join(entry.text for entry in sources)
    for quote in proposal.evidence_quotes:
        cleaned = quote.strip()
        if not cleaned or cleaned not in source_text:
            return MutationDecision(False, None, (), "evidence quote is not present in raw provenance")

    if proposal.fact_id in facts_by_id:
        existing = facts_by_id[proposal.fact_id]
        if (
            existing.scope != proposal.scope
            or existing.subject != proposal.subject
            or existing.predicate != proposal.predicate
            or existing.value != proposal.value
        ):
            return MutationDecision(False, None, (), "fact id collides with a different fact")

    supersede_targets: list[StructuredFact] = []
    for fact_id in proposal.supersedes_fact_ids:
        target = facts_by_id.get(fact_id)
        if target is None:
            return MutationDecision(False, None, (), f"supersede target does not exist: {fact_id}")
        if target.scope != proposal.scope:
            return MutationDecision(False, None, (), "supersede target crosses privacy scope")
        if target.subject != proposal.subject or target.predicate != proposal.predicate:
            return MutationDecision(False, None, (), "supersede target subject/predicate mismatch")
        supersede_targets.append(target)

    if supersede_targets and not proposal.explicit_correction:
        return MutationDecision(False, None, (), "supersede requires explicit correction evidence")

    active_conflict = next(
        (
            fact
            for fact in facts_by_id.values()
            if fact.scope == proposal.scope
            and fact.subject == proposal.subject
            and fact.predicate == proposal.predicate
            and fact.status == "active"
            and fact.value != proposal.value
            and fact.fact_id not in proposal.supersedes_fact_ids
        ),
        None,
    )

    if proposal.explicit_correction:
        if not supersede_targets:
            return MutationDecision(False, None, (), "explicit correction must name the fact it corrects")
        status = "active"
    elif proposal.uncertain or active_conflict is not None:
        # Conservative rule: ambiguity or an unresolved contradiction is not a
        # license to silently change current truth.
        status = "hypothesis"
    else:
        status = "active"

    return MutationDecision(
        True,
        status,
        tuple(fact.fact_id for fact in supersede_targets),
        "accepted",
    )


def apply_fact_mutation(
    proposal: FactMutationProposal,
    *,
    raw_entries: tuple[RawEntry, ...],
    existing_facts: tuple[StructuredFact, ...],
) -> MutationApplyResult:
    decision = validate_fact_mutation(
        proposal,
        raw_entries=raw_entries,
        existing_facts=existing_facts,
    )
    if not decision.accepted or decision.status is None:
        return MutationApplyResult(existing_facts, decision, applied=False)

    if any(fact.fact_id == proposal.fact_id for fact in existing_facts):
        # Same identity + same semantic fields was validated above. Treat this
        # as an idempotent replay, not a second fact.
        return MutationApplyResult(existing_facts, decision, applied=False)

    updated: list[StructuredFact] = []
    supersede_ids = set(decision.supersede_fact_ids)
    for fact in existing_facts:
        if fact.fact_id in supersede_ids:
            updated.append(
                replace(
                    fact,
                    status="superseded",
                    valid_to=proposal.valid_from,
                )
            )
        else:
            updated.append(fact)
    updated.append(
        StructuredFact(
            fact_id=proposal.fact_id,
            scope=proposal.scope,
            subject=proposal.subject,
            predicate=proposal.predicate,
            value=proposal.value,
            statement=proposal.statement,
            valid_from=proposal.valid_from,
            valid_to=None,
            status=decision.status,
            source_entry_ids=proposal.source_entry_ids,
        )
    )
    return MutationApplyResult(tuple(updated), decision, applied=True)


@dataclass(frozen=True)
class ForgetSnapshot:
    raw_entry_ids: frozenset[str]
    structured_provenance: dict[str, frozenset[str]]
    lexical_entry_ids: frozenset[str]
    vector_entry_ids: frozenset[str]
    delivery_cache_entry_ids: frozenset[str]


@dataclass(frozen=True)
class ForgetAuditResult:
    passed: bool
    residues: tuple[str, ...]
    collateral_missing: tuple[str, ...]


def audit_forget_transition(
    *,
    before: ForgetSnapshot,
    after: ForgetSnapshot,
    forgotten_entry_ids: Iterable[str],
) -> ForgetAuditResult:
    forgotten = frozenset(forgotten_entry_ids)
    residues: list[str] = []

    for entry_id in sorted(forgotten & after.raw_entry_ids):
        residues.append(f"raw:{entry_id}")
    for fact_id, provenance in sorted(after.structured_provenance.items()):
        leaked = sorted(forgotten & provenance)
        if leaked:
            residues.append(f"structured:{fact_id}:{','.join(leaked)}")
    for label, ids in (
        ("lexical", after.lexical_entry_ids),
        ("vector", after.vector_entry_ids),
        ("delivery-cache", after.delivery_cache_entry_ids),
    ):
        for entry_id in sorted(forgotten & ids):
            residues.append(f"{label}:{entry_id}")

    before_unrelated = before.raw_entry_ids - forgotten
    collateral_missing = tuple(sorted(before_unrelated - after.raw_entry_ids))
    return ForgetAuditResult(
        passed=not residues and not collateral_missing,
        residues=tuple(residues),
        collateral_missing=collateral_missing,
    )
