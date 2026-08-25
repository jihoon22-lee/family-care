"""Deterministic Rider-level coverage evaluation and tri-state aggregation."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from uuid import UUID, uuid4

from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import (
    ClaimCandidate,
    ClaimHistoryFact,
    DecisionReaders,
    DecisionRunResult,
    FactConfirmation,
    FactContext,
    FactValue,
    MedicalEvent,
    PolicySnapshot,
    Question,
    RuleEvaluation,
    TriState,
)
from familycare_api.decisions.rule_runtime import RuleRuntimeError, evaluate_rule

ENGINE_VERSION = "decision-engine-v1"
_APPROVED_EVIDENCE_STATES = frozenset({"AI_VERIFIED", "USER_CONFIRMED"})
_INACTIVE_STATUSES = frozenset({"inactive", "expired", "cancelled"})
_RULE_KIND_ORDER = {
    "eligibility": 10,
    "classification": 20,
    "temporal": 30,
    "exclusion": 40,
    "frequency": 50,
    "indemnity_eligibility": 60,
    "fixed_amount": 70,
    "rate_amount": 70,
    "deductible": 70,
    "limit": 70,
    "required_document": 80,
}


def aggregate_required_results(evaluations: Sequence[RuleEvaluation]) -> TriState:
    """Aggregate only required rules, with decisive mismatch precedence."""

    required = tuple(item for item in evaluations if item.required)
    if any(item.result == "NO_MATCH" for item in required):
        return "NO_MATCH"
    if any(item.result == "UNKNOWN" for item in required):
        return "UNKNOWN"
    return "MATCH"


def build_follow_up_questions(
    evaluations: Sequence[RuleEvaluation],
) -> tuple[Question, ...]:
    """Return stable, de-duplicated questions without generating medical text."""

    questions: list[Question] = []
    seen: set[str] = set()
    for evaluation in evaluations:
        for field_path in (*evaluation.missing_fields, *evaluation.conflicting_fields):
            if field_path in seen:
                continue
            seen.add(field_path)
            questions.append(Question(field_path=field_path, reason_code=evaluation.reason_code))
    return tuple(questions)


class DeterministicCoverageDecisionEngine:
    """Pure orchestration over scoped readers; no AI or external provider calls."""

    def __init__(
        self,
        readers: DecisionReaders,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.readers = readers
        self.id_factory = id_factory
        self.clock = clock or (lambda: datetime.now(UTC))

    def evaluate(self, scope: HouseholdScope, event: MedicalEvent) -> DecisionRunResult:
        if event.household_space_id != scope.household_space_id:
            raise ValueError("medical event scope mismatch")

        policy_snapshot_at = self.clock()
        stale = False
        try:
            snapshots = self.readers.policy.for_event_date(
                scope,
                event.family_member_id,
                event.event_date,
            )
        except Exception:
            snapshots = ()
            stale = True

        try:
            history = self.readers.history.for_family_member(scope, event.family_member_id)
        except Exception:
            history = ()
            stale = True

        candidates: list[ClaimCandidate] = []
        evaluations: list[RuleEvaluation] = []
        rule_version_ids: list[UUID] = []

        grouped = _group_snapshots(snapshots)
        for rider_id in sorted(grouped, key=str):
            rider_snapshots = grouped[rider_id]
            if len(rider_snapshots) != 1:
                snapshot = rider_snapshots[0]
                candidates.append(
                    self._unknown_candidate(
                        snapshot,
                        reason_code="CONFLICTING_POLICY_SNAPSHOT",
                        questions=(Question("Rider.status", "CONFLICTING_POLICY_SNAPSHOT"),),
                    )
                )
                stale = True
                continue
            snapshot = rider_snapshots[0]
            candidate, rider_evaluations, rider_rules, rider_stale = self._evaluate_snapshot(
                scope,
                event,
                snapshot,
                history,
            )
            candidates.append(candidate)
            evaluations.extend(rider_evaluations)
            rule_version_ids.extend(item.id for item in rider_rules)
            stale = stale or rider_stale

        return DecisionRunResult(
            run_id=self.id_factory(),
            medical_event_id=event.id,
            event_version=event.version,
            engine_version=ENGINE_VERSION,
            rule_set_version=_rule_set_version(rule_version_ids),
            policy_snapshot_at=policy_snapshot_at,
            candidates=tuple(candidates),
            evaluations=tuple(evaluations),
            stale=stale,
        )

    def _evaluate_snapshot(
        self,
        scope: HouseholdScope,
        event: MedicalEvent,
        snapshot: PolicySnapshot,
        history: tuple[ClaimHistoryFact, ...],
    ) -> tuple[
        ClaimCandidate,
        tuple[RuleEvaluation, ...],
        tuple[CoverageRuleVersion, ...],
        bool,
    ]:
        try:
            rules = tuple(
                sorted(
                    self.readers.rules.executable_for_rider(scope, snapshot.rider_id),
                    key=_rule_order,
                )
            )
        except Exception:
            return (
                self._unknown_candidate(snapshot, reason_code="RULE_READER_UNAVAILABLE"),
                (),
                (),
                True,
            )

        context = _fact_context(event, snapshot, history)
        precondition, precondition_reason, precondition_questions = _policy_precondition(
            event,
            snapshot,
        )
        rider_evaluations: list[RuleEvaluation] = []
        evidence_invalid = False
        decision_rules = tuple(
            item for item in rules if item.rule_document.get("calculation") is None
        )

        for rule in decision_rules:
            try:
                evaluation = evaluate_rule(rule, context, rider_id=snapshot.rider_id)
            except RuleRuntimeError:
                evaluation = RuleEvaluation(
                    rider_id=snapshot.rider_id,
                    rule_version_id=rule.id,
                    result="UNKNOWN",
                    required=rule.required,
                    reason_code="RULE_RUNTIME_INVALID",
                    evidence=rule.evidence,
                )
            evaluation = replace(evaluation, id=self.id_factory())
            evaluation, valid = self._validated_evidence(
                scope,
                snapshot,
                rule,
                evaluation,
            )
            evidence_invalid = evidence_invalid or not valid
            if valid and precondition != "MATCH":
                evaluation = replace(
                    evaluation,
                    result=precondition,
                    reason_code=precondition_reason,
                    missing_fields=_unique_strings(
                        (*evaluation.missing_fields, *_question_fields(precondition_questions))
                        if precondition == "UNKNOWN"
                        else evaluation.missing_fields
                    ),
                )
            rider_evaluations.append(evaluation)

        if not decision_rules:
            return (
                self._unknown_candidate(
                    snapshot,
                    reason_code=("NO_EXECUTABLE_DECISION_RULE" if rules else "NO_EXECUTABLE_RULE"),
                    questions=precondition_questions,
                ),
                (),
                rules,
                precondition != "MATCH",
            )

        effective_precondition = "UNKNOWN" if evidence_invalid else precondition
        effective_precondition_reason = (
            "EVIDENCE_UNAVAILABLE" if evidence_invalid else precondition_reason
        )
        aggregate = aggregate_required_results(rider_evaluations)
        if effective_precondition == "NO_MATCH":
            aggregate = "NO_MATCH"
        elif effective_precondition == "UNKNOWN" and aggregate != "NO_MATCH":
            aggregate = "UNKNOWN"
        questions = _unique_questions(
            (*precondition_questions, *build_follow_up_questions(rider_evaluations))
        )
        required = tuple(item for item in rider_evaluations if item.required)
        reasons = _unique_strings(
            item.reason_code for item in rider_evaluations if item.result != "MATCH"
        )
        if effective_precondition != "MATCH":
            reasons = _unique_strings((effective_precondition_reason, *reasons))
        candidate = ClaimCandidate(
            id=self.id_factory(),
            rider_id=snapshot.rider_id,
            rider_type=snapshot.rider_type,
            rider_label=snapshot.rider_label,
            aggregate_result=aggregate,
            evaluations=tuple(rider_evaluations),
            questions=questions,
            hold_reason_codes=reasons,
            required_match_count=sum(item.result == "MATCH" for item in required),
            required_unknown_count=sum(item.result == "UNKNOWN" for item in required),
            required_no_match_count=sum(item.result == "NO_MATCH" for item in required),
        )
        return candidate, tuple(rider_evaluations), rules, evidence_invalid

    def _validated_evidence(
        self,
        scope: HouseholdScope,
        snapshot: PolicySnapshot,
        rule: CoverageRuleVersion,
        evaluation: RuleEvaluation,
    ) -> tuple[RuleEvaluation, bool]:
        requested_ids = _unique_ids(
            (*snapshot.evidence_ids, *(item.evidence_id for item in rule.evidence))
        )
        try:
            evidence = self.readers.evidence.get_many(scope, requested_ids)
        except Exception:
            evidence = ()
        received_ids = tuple(item.evidence_id for item in evidence)
        valid = (
            bool(requested_ids)
            and frozenset(received_ids) == frozenset(requested_ids)
            and len(received_ids) == len(frozenset(received_ids))
            and all(_evidence_is_current(item) for item in evidence)
        )
        if not valid:
            return (
                replace(
                    evaluation,
                    result="UNKNOWN",
                    reason_code="EVIDENCE_UNAVAILABLE",
                ),
                False,
            )
        return (
            replace(
                evaluation,
                evidence=tuple(evidence),
                evidence_ids=received_ids,
            ),
            True,
        )

    def _unknown_candidate(
        self,
        snapshot: PolicySnapshot,
        *,
        reason_code: str,
        questions: tuple[Question, ...] = (),
    ) -> ClaimCandidate:
        return ClaimCandidate(
            id=self.id_factory(),
            rider_id=snapshot.rider_id,
            rider_type=snapshot.rider_type,
            rider_label=snapshot.rider_label,
            aggregate_result="UNKNOWN",
            questions=questions,
            hold_reason_codes=(reason_code,),
        )


def evaluate_event(
    scope: HouseholdScope,
    event: MedicalEvent,
    readers: DecisionReaders,
) -> DecisionRunResult:
    """Evaluate one event using the default deterministic engine version."""

    return DeterministicCoverageDecisionEngine(readers).evaluate(scope, event)


def _fact_context(
    event: MedicalEvent,
    snapshot: PolicySnapshot,
    history: tuple[ClaimHistoryFact, ...],
) -> FactContext:
    event_facts = dict(event.facts)
    if "MedicalEvent.event_date" not in event_facts and "event_date" not in event_facts:
        event_facts["event_date"] = FactValue(event.event_date, "user", ())
    if "MedicalEvent.visit_date" not in event_facts and "visit_date" not in event_facts:
        event_facts["visit_date"] = FactValue(event.visit_date, "user", ())

    policy_confirmation: FactConfirmation = (
        "ai_structured" if snapshot.evidence_ids else "unconfirmed"
    )
    status_confirmation: FactConfirmation = (
        policy_confirmation
        if snapshot.effective_status != "unknown" and snapshot.rider_status is not None
        else "unconfirmed"
    )
    policy = {
        "contract_start": FactValue(
            snapshot.contract_start,
            policy_confirmation,
            snapshot.evidence_ids,
        ),
        "contract_end": FactValue(
            snapshot.contract_end,
            policy_confirmation,
            snapshot.evidence_ids,
        ),
    }
    rider = {
        "status": FactValue(
            snapshot.rider_status,
            status_confirmation,
            snapshot.evidence_ids,
        ),
        "insured_amount": FactValue(
            snapshot.insured_amount,
            policy_confirmation,
            snapshot.evidence_ids,
        ),
    }
    claim_history = {
        "counted_occurrence": FactValue(
            sum(item.counted_occurrence for item in history) if history else None,
            "ai_structured" if history else "unconfirmed",
            (),
        )
    }
    return FactContext(
        medical_event=event_facts,
        policy=policy,
        rider=rider,
        claim_history=claim_history,
        as_of_date=event.event_date,
    )


def _policy_precondition(
    event: MedicalEvent,
    snapshot: PolicySnapshot,
) -> tuple[TriState, str, tuple[Question, ...]]:
    if not snapshot.evidence_ids:
        return "UNKNOWN", "POLICY_EVIDENCE_MISSING", ()
    if (
        snapshot.effective_status in _INACTIVE_STATUSES
        or snapshot.rider_status in _INACTIVE_STATUSES
    ):
        return "NO_MATCH", "RIDER_INACTIVE", ()
    if snapshot.effective_status != "active" or snapshot.rider_status != "active":
        return (
            "UNKNOWN",
            "RIDER_STATUS_UNCONFIRMED",
            (Question("Rider.status", "RIDER_STATUS_UNCONFIRMED"),),
        )
    if snapshot.renewable is True and snapshot.status_checked_at is None:
        return (
            "UNKNOWN",
            "RENEWAL_STATUS_UNCONFIRMED",
            (Question("Rider.status", "RENEWAL_STATUS_UNCONFIRMED"),),
        )
    if event.event_date is None:
        return (
            "UNKNOWN",
            "EVENT_DATE_REQUIRED",
            (Question("MedicalEvent.event_date", "EVENT_DATE_REQUIRED"),),
        )
    if snapshot.contract_start is None or snapshot.contract_end is None:
        missing = (
            "PolicyContract.contract_start"
            if snapshot.contract_start is None
            else "PolicyContract.contract_end"
        )
        return (
            "UNKNOWN",
            "POLICY_PERIOD_UNCONFIRMED",
            (Question(missing, "POLICY_PERIOD_UNCONFIRMED"),),
        )
    if not snapshot.contract_start <= event.event_date <= snapshot.contract_end:
        return "NO_MATCH", "EVENT_OUTSIDE_POLICY_PERIOD", ()
    rider_start = snapshot.rider_coverage_start or snapshot.contract_start
    rider_end = snapshot.rider_coverage_end or snapshot.contract_end
    if not rider_start <= event.event_date <= rider_end:
        return "NO_MATCH", "EVENT_OUTSIDE_RIDER_PERIOD", ()
    return "MATCH", "POLICY_PRECONDITIONS_MATCH", ()


def _rule_order(rule: CoverageRuleVersion) -> tuple[int, str, int, str]:
    return (
        _RULE_KIND_ORDER.get(rule.rule_kind, 999),
        str(rule.coverage_rule_id),
        rule.version_number,
        str(rule.id),
    )


def _group_snapshots(
    snapshots: Iterable[PolicySnapshot],
) -> dict[UUID, tuple[PolicySnapshot, ...]]:
    grouped: dict[UUID, list[PolicySnapshot]] = {}
    for snapshot in snapshots:
        grouped.setdefault(snapshot.rider_id, []).append(snapshot)
    return {key: tuple(value) for key, value in grouped.items()}


def _rule_set_version(rule_version_ids: Iterable[UUID]) -> str:
    values = sorted({str(item) for item in rule_version_ids})
    if not values:
        return "rules-none"
    digest = sha256("\n".join(values).encode()).hexdigest()[:24]
    return f"rules-{digest}"


def _evidence_is_current(evidence: EvidenceRef) -> bool:
    return (
        evidence.review_state in _APPROVED_EVIDENCE_STATES
        and not bool(getattr(evidence, "stale", False))
        and not bool(getattr(evidence, "evidence_stale", False))
    )


def _question_fields(questions: Sequence[Question]) -> tuple[str, ...]:
    return tuple(item.field_path for item in questions)


def _unique_questions(questions: Iterable[Question]) -> tuple[Question, ...]:
    values: list[Question] = []
    seen: set[str] = set()
    for question in questions:
        if question.field_path in seen:
            continue
        seen.add(question.field_path)
        values.append(question)
    return tuple(values)


def _unique_strings(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


def _unique_ids(values: Iterable[UUID]) -> tuple[UUID, ...]:
    result: list[UUID] = []
    for value in values:
        if value not in result:
            result.append(value)
    return tuple(result)


__all__ = [
    "DeterministicCoverageDecisionEngine",
    "ENGINE_VERSION",
    "aggregate_required_results",
    "build_follow_up_questions",
    "evaluate_event",
]
