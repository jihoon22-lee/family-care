from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from uuid import UUID

import pytest
from familycare_api.clauses.dsl import RULE_SCHEMA_VERSION
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import (
    ClaimHistoryFact,
    DecisionReaders,
    FactValue,
    MedicalEvent,
    PolicySnapshot,
    Question,
    RuleEvaluation,
)
from familycare_api.decisions.engine import (
    aggregate_required_results,
    build_follow_up_questions,
    evaluate_event,
)

SCOPE_ID = UUID("00000000-0000-0000-0000-000000000001")
MEMBER_ID = UUID("00000000-0000-0000-0000-000000000002")
POLICY_ID = UUID("00000000-0000-0000-0000-000000000003")
RIDER_A = UUID("00000000-0000-0000-0000-000000000010")
RIDER_B = UUID("00000000-0000-0000-0000-000000000011")
RIDER_C = UUID("00000000-0000-0000-0000-000000000012")
RULE_A = UUID("00000000-0000-0000-0000-000000000020")
RULE_B = UUID("00000000-0000-0000-0000-000000000021")
RULE_C = UUID("00000000-0000-0000-0000-000000000022")
CANDIDATE_A = UUID("00000000-0000-0000-0000-000000000030")
CANDIDATE_B = UUID("00000000-0000-0000-0000-000000000031")
CANDIDATE_C = UUID("00000000-0000-0000-0000-000000000032")
EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000040")
OTHER_EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000041")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000050")
EXTRACTION_ID = UUID("00000000-0000-0000-0000-000000000051")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000060")
NOW = datetime(2026, 8, 25, tzinfo=UTC)
SCOPE = HouseholdScope(SCOPE_ID)


class FakeHistoryReader:
    def __init__(self, facts: tuple[ClaimHistoryFact, ...] = ()) -> None:
        self.facts = facts
        self.calls: list[tuple[UUID, UUID]] = []

    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]:
        self.calls.append((scope.household_space_id, family_member_id))
        return self.facts


class FakePolicyReader:
    def __init__(self, snapshots: Iterable[PolicySnapshot]) -> None:
        self.snapshots = tuple(snapshots)
        self.calls: list[tuple[UUID, UUID, date | None]] = []

    def for_event_date(
        self, scope: HouseholdScope, family_member_id: UUID, event_date: date | None
    ) -> tuple[PolicySnapshot, ...]:
        self.calls.append((scope.household_space_id, family_member_id, event_date))
        return self.snapshots


class FakeRuleReader:
    def __init__(
        self,
        rules: Mapping[UUID, tuple[CoverageRuleVersion, ...]],
        *,
        failures: Iterable[UUID] = (),
    ) -> None:
        self.rules = dict(rules)
        self.failures = frozenset(failures)
        self.calls: list[tuple[UUID, UUID]] = []

    def executable_for_rider(
        self, scope: HouseholdScope, rider_id: UUID
    ) -> tuple[CoverageRuleVersion, ...]:
        self.calls.append((scope.household_space_id, rider_id))
        if rider_id in self.failures:
            raise RuntimeError("synthetic rule reader failure")
        return self.rules.get(rider_id, ())


class FakeEvidenceRepository:
    def __init__(
        self,
        evidence: Mapping[UUID, EvidenceRef],
        *,
        missing: Iterable[UUID] = (),
        mismatch: bool = False,
    ) -> None:
        self.evidence = dict(evidence)
        self.missing = frozenset(missing)
        self.mismatch = mismatch
        self.calls: list[tuple[UUID, tuple[UUID, ...]]] = []

    def get_many(
        self, scope: HouseholdScope, evidence_ids: tuple[UUID, ...]
    ) -> tuple[EvidenceRef, ...]:
        self.calls.append((scope.household_space_id, evidence_ids))
        if self.mismatch and evidence_ids:
            return (self.evidence[OTHER_EVIDENCE_ID],)
        return tuple(
            self.evidence[evidence_id]
            for evidence_id in evidence_ids
            if evidence_id not in self.missing and evidence_id in self.evidence
        )


def readers(
    snapshots: Iterable[PolicySnapshot],
    rules: Mapping[UUID, tuple[CoverageRuleVersion, ...]],
    *,
    history: FakeHistoryReader | None = None,
    evidence: FakeEvidenceRepository | None = None,
    failures: Iterable[UUID] = (),
) -> DecisionReaders:
    return DecisionReaders(
        policy=FakePolicyReader(snapshots),
        rules=FakeRuleReader(rules, failures=failures),
        evidence=evidence or FakeEvidenceRepository({EVIDENCE_ID: evidence_ref()}),
        history=history or FakeHistoryReader(),
    )


def event(
    *,
    event_date: date | None = date(2026, 8, 25),
    facts: Mapping[str, FactValue] | None = None,
    version: int = 1,
) -> MedicalEvent:
    return MedicalEvent(
        id=EVENT_ID,
        household_space_id=SCOPE_ID,
        family_member_id=MEMBER_ID,
        mode="post_treatment",
        event_date=event_date,
        visit_date=event_date,
        facts=facts or {},
        version=version,
    )


def evidence_ref(evidence_id: UUID = EVIDENCE_ID) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        document_version_id=DOCUMENT_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256="a" * 64,
        physical_page=1,
        bbox=None,
        review_state="AI_VERIFIED",
    )


def stale_evidence_ref() -> EvidenceRef:
    value = evidence_ref()
    object.__setattr__(value, "stale", True)
    return value


def snapshot(
    rider_id: UUID,
    *,
    status: str = "active",
    rider_status: str | None = "active",
    rider_type: str = "fixed",
    contract_start: date | None = date(2020, 1, 1),
    contract_end: date | None = date(2030, 12, 31),
    rider_coverage_start: date | None = date(2020, 1, 1),
    rider_coverage_end: date | None = date(2030, 12, 31),
    renewable: bool | None = None,
    status_checked_at: datetime | None = None,
) -> PolicySnapshot:
    return PolicySnapshot(
        policy_id=POLICY_ID,
        rider_id=rider_id,
        effective_status=status,
        evidence_ids=(EVIDENCE_ID,),
        rider_type=rider_type,
        contract_start=contract_start,
        contract_end=contract_end,
        rider_coverage_start=rider_coverage_start,
        rider_coverage_end=rider_coverage_end,
        rider_status=rider_status,
        insured_amount=Decimal("1000000"),
        currency="KRW",
        renewable=renewable,
        status_checked_at=status_checked_at,
    )


def rule(
    *,
    rule_id: UUID = RULE_A,
    candidate_id: UUID = CANDIDATE_A,
    required: bool = True,
    field: str = "MedicalEvent.classification",
    operator: str = "equals",
    value: object = "injury",
    executable: bool = True,
    review_state: str = "AI_VERIFIED",
    evidence: tuple[EvidenceRef, ...] | None = None,
) -> CoverageRuleVersion:
    expression: dict[str, object]
    if operator in {"equals", "in"}:
        expression = {"op": operator, "field": field, "value": value}
    elif operator == "count_before":
        expression = {
            "op": operator,
            "field": field,
            "value": value,
            "unit": "occurrences",
        }
    else:
        expression = {
            "op": operator,
            "field": field,
            "value": value,
            "unit": "days",
        }
    rule_document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": "eligibility",
        "required": required,
        "input_field_paths": [field],
        "expression": expression,
        "result_reason_code": "RULE_MATCH",
        "evidence_ids": [str(item.evidence_id) for item in evidence or (evidence_ref(),)],
    }
    return CoverageRuleVersion(
        id=rule_id,
        coverage_rule_id=UUID("00000000-0000-0000-0000-000000000070"),
        candidate_version_id=candidate_id,
        version_number=1,
        schema_version=RULE_SCHEMA_VERSION,
        rule_kind="eligibility",
        required=required,
        input_field_paths=(field,),
        rule_document=rule_document,
        result_reason_code="RULE_MATCH",
        review_state=cast(str, review_state),
        executable=executable,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=NOW if executable else None,
        evidence=evidence or (evidence_ref(),),
    )


def calculation_rule() -> CoverageRuleVersion:
    rule_document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": "rate_amount",
        "required": True,
        "input_field_paths": ["Rider.insured_amount"],
        "calculation": {
            "op": "multiply",
            "args": [
                {"field": "Rider.insured_amount"},
                {"value": Decimal("0.5")},
            ],
        },
        "result_reason_code": "SYNTHETIC_RATE_AMOUNT",
        "evidence_ids": [str(EVIDENCE_ID)],
    }
    return CoverageRuleVersion(
        id=RULE_B,
        coverage_rule_id=UUID("00000000-0000-0000-0000-000000000071"),
        candidate_version_id=CANDIDATE_B,
        version_number=1,
        schema_version=RULE_SCHEMA_VERSION,
        rule_kind="rate_amount",
        required=True,
        input_field_paths=("Rider.insured_amount",),
        rule_document=rule_document,
        result_reason_code="SYNTHETIC_RATE_AMOUNT",
        review_state="AI_VERIFIED",
        executable=True,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=NOW,
        evidence=(evidence_ref(),),
    )


def evaluation(
    *,
    rider_id: UUID = RIDER_A,
    rule_version_id: UUID = RULE_A,
    result: str = "UNKNOWN",
    required: bool = True,
    missing_fields: tuple[str, ...] = (),
    conflicting_fields: tuple[str, ...] = (),
) -> RuleEvaluation:
    return RuleEvaluation(
        rider_id=rider_id,
        rule_version_id=rule_version_id,
        result=cast(str, result),
        required=required,
        reason_code="SYNTHETIC_REASON",
        missing_fields=missing_fields,
        conflicting_fields=conflicting_fields,
    )


def run(
    snapshots: Iterable[PolicySnapshot],
    rules: Mapping[UUID, tuple[CoverageRuleVersion, ...]],
    *,
    medical_event: MedicalEvent | None = None,
    history: FakeHistoryReader | None = None,
    evidence: FakeEvidenceRepository | None = None,
    failures: Iterable[UUID] = (),
):
    return evaluate_event(
        SCOPE,
        medical_event
        or event(facts={"MedicalEvent.classification": FactValue("injury", "user", ())}),
        readers(
            snapshots,
            rules,
            history=history,
            evidence=evidence,
            failures=failures,
        ),
    )


def test_required_aggregation_precedence_is_no_match_then_unknown_then_match() -> None:
    assert (
        aggregate_required_results((evaluation(result="MATCH"), evaluation(result="UNKNOWN")))
        == "UNKNOWN"
    )
    assert (
        aggregate_required_results((evaluation(result="UNKNOWN"), evaluation(result="NO_MATCH")))
        == "NO_MATCH"
    )
    assert aggregate_required_results((evaluation(result="MATCH"),)) == "MATCH"


def test_optional_rule_never_overrides_required_result() -> None:
    assert (
        aggregate_required_results(
            (evaluation(result="MATCH"), evaluation(result="NO_MATCH", required=False))
        )
        == "MATCH"
    )
    assert (
        aggregate_required_results(
            (evaluation(result="UNKNOWN"), evaluation(result="NO_MATCH", required=False))
        )
        == "UNKNOWN"
    )


def test_follow_up_questions_are_deduplicated_in_evaluation_and_field_order() -> None:
    questions = build_follow_up_questions(
        (
            evaluation(
                missing_fields=("Rider.status", "MedicalEvent.event_date"),
                conflicting_fields=("PolicyContract.contract_end",),
            ),
            evaluation(
                missing_fields=("Rider.status", "ClaimHistory.counted_occurrence"),
                conflicting_fields=("PolicyContract.contract_end",),
            ),
        )
    )

    assert tuple(item.field_path for item in questions) == (
        "Rider.status",
        "MedicalEvent.event_date",
        "PolicyContract.contract_end",
        "ClaimHistory.counted_occurrence",
    )
    assert all(isinstance(item, Question) for item in questions)


def test_only_actual_subscribed_riders_become_candidates() -> None:
    result = run(
        (snapshot(RIDER_B),),
        {
            RIDER_A: (rule(),),
            RIDER_B: (rule(rule_id=RULE_B, candidate_id=CANDIDATE_B),),
        },
    )

    assert tuple(candidate.rider_id for candidate in result.candidates) == (RIDER_B,)


def test_calculation_rules_do_not_turn_a_matched_eligibility_into_unknown() -> None:
    result = run(
        (snapshot(RIDER_A),),
        {RIDER_A: (rule(), calculation_rule())},
    )

    assert result.candidates[0].aggregate_result == "MATCH"
    assert tuple(item.rule_version_id for item in result.evaluations) == (RULE_A,)


def test_missing_event_date_still_discovers_candidates_as_unknown() -> None:
    policy_reader = FakePolicyReader((snapshot(RIDER_A),))
    result = evaluate_event(
        SCOPE,
        event(event_date=None, facts={}),
        DecisionReaders(
            policy=policy_reader,
            rules=FakeRuleReader({RIDER_A: (rule(),)}),
            evidence=FakeEvidenceRepository({EVIDENCE_ID: evidence_ref()}),
            history=FakeHistoryReader(),
        ),
    )

    assert policy_reader.calls == [(SCOPE_ID, MEMBER_ID, None)]
    assert result.candidates[0].aggregate_result == "UNKNOWN"


@pytest.mark.parametrize("status", ["inactive", "expired", "cancelled"])
def test_confirmed_inactive_rider_is_deterministic_no_match(status: str) -> None:
    result = run((snapshot(RIDER_A, status=status, rider_status=status),), {RIDER_A: (rule(),)})

    assert result.candidates[0].aggregate_result == "NO_MATCH"
    assert result.evaluations[0].result == "NO_MATCH"


def test_unconfirmed_rider_status_is_unknown_not_no_match() -> None:
    result = run(
        (snapshot(RIDER_A, status="unknown", rider_status=None),),
        {RIDER_A: (rule(field="Rider.status", value="active"),)},
    )

    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.evaluations[0].result == "UNKNOWN"


def test_policy_period_boundary_is_inclusive_and_outside_period_is_no_match() -> None:
    inside = run(
        (
            snapshot(
                RIDER_A,
                contract_start=date(2026, 8, 25),
                contract_end=date(2026, 8, 25),
            ),
        ),
        {RIDER_A: (rule(),)},
    )
    outside = run(
        (
            snapshot(
                RIDER_A,
                contract_start=date(2026, 8, 26),
                contract_end=date(2026, 9, 25),
            ),
        ),
        {RIDER_A: (rule(),)},
    )

    assert inside.candidates[0].aggregate_result == "MATCH"
    assert outside.candidates[0].aggregate_result == "NO_MATCH"


def test_event_outside_subscribed_rider_period_is_no_match() -> None:
    result = run(
        (
            snapshot(
                RIDER_A,
                rider_coverage_start=date(2026, 8, 26),
                rider_coverage_end=date(2030, 12, 31),
            ),
        ),
        {RIDER_A: (rule(),)},
    )

    assert result.candidates[0].aggregate_result == "NO_MATCH"
    assert result.evaluations[0].reason_code == "EVENT_OUTSIDE_RIDER_PERIOD"


def test_renewable_rider_without_current_status_check_is_unknown() -> None:
    result = run(
        (snapshot(RIDER_A, renewable=True, status_checked_at=None),),
        {RIDER_A: (rule(),)},
    )

    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.evaluations[0].reason_code == "RENEWAL_STATUS_UNCONFIRMED"
    assert result.candidates[0].questions == (
        Question("Rider.status", "RENEWAL_STATUS_UNCONFIRMED"),
    )


def test_conflicting_policy_snapshots_are_not_arbitrarily_selected() -> None:
    result = run(
        (snapshot(RIDER_A), snapshot(RIDER_A, status="unknown")),
        {RIDER_A: (rule(),)},
    )

    assert len(result.candidates) == 1
    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.candidates[0].hold_reason_codes == ("CONFLICTING_POLICY_SNAPSHOT",)
    assert result.evaluations == ()
    assert result.stale is True


def test_waiting_period_and_confirmed_classification_mismatch_are_no_match() -> None:
    waiting = run(
        (
            snapshot(
                RIDER_A,
                contract_start=date(2026, 8, 20),
                rider_coverage_start=date(2026, 8, 20),
            ),
        ),
        {
            RIDER_A: (
                rule(
                    field="PolicyContract.contract_start",
                    operator="days_since",
                    value=10,
                ),
            )
        },
    )
    classification = run(
        (snapshot(RIDER_A),),
        {RIDER_A: (rule(value="illness"),)},
    )

    assert waiting.candidates[0].aggregate_result == "NO_MATCH"
    assert waiting.evaluations[0].reason_code == "DAYS_THRESHOLD_NOT_MET"
    assert classification.candidates[0].aggregate_result == "NO_MATCH"
    assert classification.evaluations[0].reason_code == "DETERMINISTIC_VALUE_MISMATCH"


def test_no_executable_rule_produces_unknown_and_does_not_call_ai() -> None:
    result = run(
        (snapshot(RIDER_A),),
        {
            RIDER_A: (
                rule(
                    executable=False,
                    review_state="NEEDS_REVIEW",
                ),
            )
        },
    )

    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.evaluations[0].reason_code in {"RULE_NOT_EXECUTABLE", "UNSUPPORTED_DSL"}


def test_missing_history_is_unknown_not_zero_occurrences() -> None:
    result = run(
        (snapshot(RIDER_A),),
        {
            RIDER_A: (
                rule(
                    field="ClaimHistory.counted_occurrence",
                    operator="count_before",
                    value=1,
                ),
            )
        },
        history=FakeHistoryReader(()),
    )

    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.evaluations[0].result == "UNKNOWN"


@pytest.mark.parametrize(
    "evidence_repository",
    [
        FakeEvidenceRepository({}),
        FakeEvidenceRepository(
            {EVIDENCE_ID: evidence_ref(), OTHER_EVIDENCE_ID: evidence_ref(OTHER_EVIDENCE_ID)},
            mismatch=True,
        ),
        FakeEvidenceRepository({EVIDENCE_ID: stale_evidence_ref()}),
    ],
)
def test_missing_mismatched_or_stale_evidence_is_unknown(
    evidence_repository: FakeEvidenceRepository,
) -> None:
    result = run(
        (snapshot(RIDER_A),),
        {RIDER_A: (rule(),)},
        evidence=evidence_repository,
    )

    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.evaluations[0].result == "UNKNOWN"


def test_inactive_status_without_current_evidence_is_unknown_not_no_match() -> None:
    result = run(
        (snapshot(RIDER_A, status="inactive", rider_status="inactive"),),
        {RIDER_A: (rule(),)},
        evidence=FakeEvidenceRepository({}),
    )

    assert result.candidates[0].aggregate_result == "UNKNOWN"
    assert result.evaluations[0].result == "UNKNOWN"
    assert result.evaluations[0].reason_code == "EVIDENCE_UNAVAILABLE"


def test_reader_failure_isolated_to_one_rider() -> None:
    result = run(
        (snapshot(RIDER_B), snapshot(RIDER_A)),
        {
            RIDER_A: (rule(),),
            RIDER_B: (rule(rule_id=RULE_B, candidate_id=CANDIDATE_B),),
        },
        failures=(RIDER_A,),
    )

    candidates = {candidate.rider_id: candidate for candidate in result.candidates}
    assert candidates[RIDER_A].aggregate_result == "UNKNOWN"
    assert candidates[RIDER_B].aggregate_result == "MATCH"


def test_results_have_stable_order_and_versions_without_ai_or_amount() -> None:
    snapshots = (snapshot(RIDER_B), snapshot(RIDER_A))
    rules = {
        RIDER_A: (rule(),),
        RIDER_B: (rule(rule_id=RULE_B, candidate_id=CANDIDATE_B),),
    }
    first = run(snapshots, rules)
    second = run(snapshots, rules)

    assert tuple(item.rider_id for item in first.candidates) == tuple(
        item.rider_id for item in second.candidates
    )
    assert first.event_version == second.event_version == 1
    assert first.engine_version == second.engine_version
    assert first.rule_set_version == second.rule_set_version
    assert first.stale is False and second.stale is False
    assert not hasattr(first.candidates[0], "amount")
