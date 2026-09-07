"""Document-based guidance remains useful without inventing status or money."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.knowledge_domain import KnowledgeFact, KnowledgeStatusInterval
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.models import LocalGuidanceResponse

from apps.api.tests.test_private_knowledge_engine import (
    HOUSEHOLD_ID,
    _calculation,
    _context,
    _coverage,
    _event,
    _expression_rule,
    _id,
)


def test_documented_coverage_without_latest_status_returns_candidate_formula_and_assumption() -> (
    None
):
    coverage = replace(
        _coverage(1, "100"),
        current_confirmation_decision=None,
        current_confirmed_status=None,
        status_intervals=(),
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.group == "PRIMARY"
    assert candidate.enrollment == "DOCUMENTED"
    assert candidate.freshness == "DOCUMENT_CONTINUITY"
    assert candidate.condition_result == "MATCH"
    assert candidate.estimate.kind == "POINT"
    assert candidate.estimate.amount == "100"
    assert candidate.estimate.formula is not None
    assert candidate.estimate.evidence
    assert "DOCUMENT_CONTINUITY_ASSUMED" in candidate.assumptions
    assert candidate.ref.coverage_id == coverage.knowledge_coverage_id
    assert coverage.current_confirmation_decision is None
    assert coverage.status_intervals == ()
    assert result.versions.assumption_policy == "document-continuity-v1"


@pytest.mark.parametrize("enrollment", ["NO_MATCH", "UNKNOWN"])
def test_terms_presence_does_not_create_an_enrolled_candidate(enrollment: str) -> None:
    coverage = replace(_coverage(1, "100"), enrollment_decision=enrollment)
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    assert not result.candidates
    assert result.support.total_coverages == 1


def test_unrelated_event_is_not_added_to_either_candidate_group() -> None:
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event("unrelated_category"), _context(_coverage(1, "100"))
    )
    assert not result.candidates
    assert result.outcome == "NO_RELEVANT_COVERAGE"


def test_insured_amount_without_a_formula_never_becomes_an_estimate() -> None:
    coverage = replace(_coverage(1, "900000"), calculation=None)
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    candidate = result.candidates[0]
    assert candidate.group == "PRIMARY"
    assert candidate.estimate.kind == "UNAVAILABLE"
    assert candidate.estimate.amount is None
    assert candidate.estimate.reason_code == "CALCULATION_NOT_PUBLISHED"


def test_unaligned_certificate_amount_keeps_formula_without_inventing_the_base() -> None:
    coverage = replace(
        _coverage(1, "100"),
        certificate_amount_decision="UNKNOWN",
        certificate_amount_evidence_state="REVIEW_REQUIRED",
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    assert result.candidates[0].estimate.kind == "FORMULA"
    assert result.candidates[0].estimate.amount is None
    assert "Rider.insured_amount" in result.candidates[0].estimate.missing_inputs


def test_unparsed_event_is_distinct_from_no_relevant_coverage() -> None:
    event = replace(_event(), facts={}, situation="synthetic unrecognized input")
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), event, _context(_coverage(1, "100"))
    )
    assert not result.candidates
    assert result.outcome == "INPUT_UNRESOLVED"


def test_missing_calculation_input_preserves_relevant_candidate_and_formula() -> None:
    calculation = _calculation(
        1,
        kind="FIXED",
        document_kind="fixed_amount",
        input_field_paths=("MedicalEvent.admission_days",),
        calculation={
            "op": "multiply",
            "args": [{"field": "MedicalEvent.admission_days"}, {"value": 25}],
        },
    )
    coverage = _coverage(1, "100", calculation=calculation)
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    candidate = result.candidates[0]
    assert candidate.group == "PRIMARY"
    assert candidate.estimate.kind == "FORMULA"
    assert candidate.estimate.amount is None
    assert "MedicalEvent.admission_days" in candidate.estimate.missing_inputs
    assert candidate.estimate.formula is not None
    assert [question.field_path for question in candidate.questions] == [
        "MedicalEvent.admission_days"
    ]


def test_missing_claim_history_limits_its_condition_without_erasing_candidate() -> None:
    frequency = _expression_rule(
        2,
        kind="frequency",
        expression={
            "op": "count_before",
            "field": "ClaimHistory.counted_occurrence",
            "value": 1,
            "unit": "occurrences",
        },
        input_field_paths=("ClaimHistory.counted_occurrence",),
        reason_code="SYNTHETIC_FREQUENCY",
    )
    coverage = _coverage(1, "100")
    coverage = replace(coverage, rules=(*coverage.rules, frequency))
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL"
    assert candidate.condition_result == "UNKNOWN"
    assert candidate.estimate.amount == "100"
    assert "CONDITIONS_REMAIN" in candidate.estimate.assumptions


def test_event_date_status_takes_precedence_over_current_termination() -> None:
    coverage = replace(
        _coverage(1, "100"),
        current_confirmation_decision="MATCH",
        current_confirmed_status="terminated",
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    assert result.candidates[0].freshness == "CONFIRMED_AT_EVENT"

    inactive = KnowledgeStatusInterval(
        effective_from=date(2026, 1, 1),
        effective_through=date(2026, 12, 31),
        decision="MATCH",
        confirmed_status="terminated",
        authority="REVIEWED_STATUS_DOCUMENT",
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
        _context(replace(coverage, status_intervals=(inactive,))),
    )
    assert not result.candidates


def test_current_termination_without_effective_date_is_not_a_past_event_rejection() -> None:
    coverage = replace(
        _coverage(1, "100"), current_confirmed_status="terminated", status_intervals=()
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL"
    assert candidate.freshness == "STATUS_UNRESOLVED"
    assert "DOCUMENT_CONTINUITY_ASSUMED" not in candidate.assumptions
    assert "EVENT_STATUS_UNRESOLVED" in candidate.estimate.assumptions


def test_rule_status_input_uses_the_same_event_time_evidence_as_freshness() -> None:
    status_rule = _expression_rule(
        3,
        kind="temporal",
        expression={"op": "equals", "field": "Rider.status", "value": "active"},
        input_field_paths=("Rider.status",),
        reason_code="SYNTHETIC_ACTIVE_AT_EVENT",
    )
    coverage = _coverage(1, "100")
    coverage = replace(
        coverage, current_confirmed_status="terminated", rules=(*coverage.rules, status_rule)
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(coverage)
    )
    assert result.candidates[0].condition_result == "MATCH"
    assert coverage.current_confirmed_status == "terminated"


def test_different_receipt_currency_preserves_candidate_but_does_not_relabel_money() -> None:
    calculation = _calculation(
        1,
        kind="INDEMNITY",
        document_kind="indemnity_eligibility",
        input_field_paths=("Receipt.covered_amount",),
        calculation={"op": "multiply", "args": [{"field": "Receipt.covered_amount"}, {"value": 1}]},
    )
    # Calculation document kind uses the existing indemnity amount DSL.
    calculation = replace(
        calculation,
        calculation_document={**calculation.calculation_document, "rule_kind": "rate_amount"},
    )
    coverage = _coverage(1, "100", benefit_type="INDEMNITY", calculation=calculation)
    context = replace(
        _context(coverage),
        supporting_facts={"Receipt.covered_amount": KnowledgeFact(100, "USER_CONFIRMED")},
        receipt_currency="USD",
    )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), context)
    assert result.candidates[0].estimate.kind == "FORMULA"
    assert result.candidates[0].estimate.amount is None
    assert "Receipt.covered_amount" in result.candidates[0].estimate.missing_inputs


def test_guidance_snapshot_round_trip_preserves_version_and_assumptions() -> None:
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), _event(), _context(_coverage(1, "100"))
    )
    restored = LocalGuidanceResponse.model_validate_json(result.model_dump_json())
    assert restored == result


def test_guidance_rejects_cross_household_or_member_context() -> None:
    context = _context(_coverage(1, "100"))
    with pytest.raises(ValueError, match="scope"):
        LocalGuidanceEngine().evaluate(HouseholdScope(_id(9999)), _event(), context)
    with pytest.raises(ValueError, match="scope"):
        LocalGuidanceEngine().evaluate(
            HouseholdScope(HOUSEHOLD_ID), _event(), replace(context, family_member_id=_id(9999))
        )
