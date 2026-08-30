"""Synthetic multi-coverage tables for the private knowledge engine."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from uuid import UUID

from familycare_api.clauses.dsl import RuleKind
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import FactValue, MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeCalculationPublication,
    KnowledgeCitation,
    KnowledgeCoverageContext,
    KnowledgeDecisionContext,
    KnowledgeDecisionResult,
    KnowledgeRulePublication,
    KnowledgeStatusInterval,
)
from familycare_api.decisions.knowledge_engine import (
    DeterministicKnowledgeDecisionEngine,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000006101")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000006102")
KNOWLEDGE_RUN_ID = UUID("00000000-0000-4000-8000-000000006103")
RULE_RUN_ID = UUID("00000000-0000-4000-8000-000000006104")
DECISION_RUN_ID = UUID("00000000-0000-4000-8000-000000006105")


def _id(offset: int) -> UUID:
    return UUID(int=0x6100 + offset)


def _event(
    value: str = "sample_category",
    *,
    confirmation: str = "user",
    extra_facts: dict[str, FactValue] | None = None,
) -> MedicalEvent:
    facts = {
        "MedicalEvent.classification": FactValue(
            value=value,
            confirmation=confirmation,  # type: ignore[arg-type]
            evidence_ids=(),
        )
    }
    facts.update(extra_facts or {})
    return MedicalEvent(
        id=_id(1),
        household_space_id=HOUSEHOLD_ID,
        family_member_id=MEMBER_ID,
        mode="post_treatment",
        situation="synthetic event description",
        event_date=date(2026, 6, 1),
        visit_date=date(2026, 6, 1),
        facts=facts,
    )


def _citation(offset: int, *, valid: bool = True) -> KnowledgeCitation:
    return KnowledgeCitation(
        citation_key=f"synthetic-citation-{offset}",
        terms_section_id=_id(100 + offset),
        source_clause_id=_id(200 + offset),
        fact_id=_id(300 + offset),
        evidence_purpose="ELIGIBILITY",
        page_start=2,
        page_end=2,
        source_text_sha256="a" * 64,
        lineage_valid=valid,
    )


def _eligibility_rule(offset: int, *, exclusion: bool = False) -> KnowledgeRulePublication:
    citation = _citation(offset)
    kind: RuleKind = "exclusion" if exclusion else "eligibility"
    return KnowledgeRulePublication(
        publication_id=_id(400 + offset),
        rule_key=f"synthetic-rule-{offset}",
        rule_kind=kind,
        required=True,
        result_reason_code=("SYNTHETIC_EXCLUSION" if exclusion else "SYNTHETIC_MATCH"),
        rule_document={
            "schema_version": "coverage-rule-v1",
            "rule_kind": kind,
            "required": True,
            "input_field_paths": ["MedicalEvent.classification"],
            "expression": {
                "op": "equals",
                "field": "MedicalEvent.classification",
                "value": "sample_category",
            },
            "result_reason_code": ("SYNTHETIC_EXCLUSION" if exclusion else "SYNTHETIC_MATCH"),
            "evidence_ids": [citation.citation_key],
        },
        citations=(citation,),
    )


def _expression_rule(
    offset: int,
    *,
    kind: RuleKind,
    expression: dict[str, object],
    input_field_paths: tuple[str, ...],
    reason_code: str,
) -> KnowledgeRulePublication:
    citation = _citation(offset)
    return KnowledgeRulePublication(
        publication_id=_id(400 + offset),
        rule_key=f"synthetic-rule-{offset}",
        rule_kind=kind,
        required=True,
        result_reason_code=reason_code,
        rule_document={
            "schema_version": "coverage-rule-v1",
            "rule_kind": kind,
            "required": True,
            "input_field_paths": list(input_field_paths),
            "expression": expression,
            "result_reason_code": reason_code,
            "evidence_ids": [citation.citation_key],
        },
        citations=(citation,),
    )


def _fixed_calculation(offset: int) -> KnowledgeCalculationPublication:
    citation = _citation(500 + offset)
    return KnowledgeCalculationPublication(
        publication_id=_id(1000 + offset),
        calculation_key=f"synthetic-calculation-{offset}",
        calculation_kind="FIXED",
        result_reason_code="SYNTHETIC_FIXED_AMOUNT",
        calculation_document={
            "schema_version": "coverage-rule-v1",
            "rule_kind": "fixed_amount",
            "required": False,
            "input_field_paths": ["Rider.insured_amount"],
            "calculation": {
                "op": "add",
                "args": [
                    {"field": "Rider.insured_amount"},
                    {"value": 0},
                ],
            },
            "result_reason_code": "SYNTHETIC_FIXED_AMOUNT",
            "evidence_ids": [citation.citation_key],
        },
        citations=(citation,),
    )


def _calculation(
    offset: int,
    *,
    kind: str,
    document_kind: str,
    input_field_paths: tuple[str, ...],
    calculation: dict[str, object],
) -> KnowledgeCalculationPublication:
    citation = _citation(500 + offset)
    return KnowledgeCalculationPublication(
        publication_id=_id(1000 + offset),
        calculation_key=f"synthetic-calculation-{offset}",
        calculation_kind=kind,  # type: ignore[arg-type]
        result_reason_code="SYNTHETIC_CALCULATION",
        calculation_document={
            "schema_version": "coverage-rule-v1",
            "rule_kind": document_kind,
            "required": False,
            "input_field_paths": list(input_field_paths),
            "calculation": calculation,
            "result_reason_code": "SYNTHETIC_CALCULATION",
            "evidence_ids": [citation.citation_key],
        },
        citations=(citation,),
    )


def _coverage(
    offset: int,
    amount: str,
    *,
    benefit_type: str = "FIXED",
    currency: str = "KRW",
    rule: KnowledgeRulePublication | None = None,
    rules: tuple[KnowledgeRulePublication, ...] | None = None,
    calculation: KnowledgeCalculationPublication | None = None,
    status_intervals: tuple[KnowledgeStatusInterval, ...] | None = None,
) -> KnowledgeCoverageContext:
    return KnowledgeCoverageContext(
        knowledge_contract_id=_id(1100 + offset),
        knowledge_coverage_id=_id(1200 + offset),
        contract_label=f"Sample Contract {offset}",
        coverage_label=f"Sample Coverage {offset}",
        benefit_type=benefit_type,  # type: ignore[arg-type]
        insured_amount=Decimal(amount),
        currency=currency,
        contract_start=date(2026, 1, 1),
        contract_end=date(2026, 12, 31),
        disposition="PUBLISHED",
        subject_binding_decision="MATCH",
        enrollment_decision="MATCH",
        component_classification="BENEFIT_COVERAGE",
        mapping_applicability="APPLICABLE",
        mapping_enrollment_decision="MATCH",
        document_identity_decision="MATCH",
        edition_applicability_decision="MATCH",
        section_mapping_decision="MATCH",
        overall_mapping_decision="MATCH",
        current_confirmation_decision="MATCH",
        current_confirmed_status="active",
        status_intervals=(
            status_intervals
            if status_intervals is not None
            else (
                KnowledgeStatusInterval(
                    effective_from=date(2026, 1, 1),
                    effective_through=date(2026, 12, 31),
                    decision="MATCH",
                    confirmed_status="active",
                    authority="USER_CONFIRMED_EVENT_DATE",
                ),
            )
        ),
        rules=(rules if rules is not None else (rule or _eligibility_rule(offset),)),
        calculation=(
            calculation
            if calculation is not None
            else (_fixed_calculation(offset) if benefit_type == "FIXED" else None)
        ),
    )


def _context(*coverages: KnowledgeCoverageContext) -> KnowledgeDecisionContext:
    return KnowledgeDecisionContext(
        household_space_id=HOUSEHOLD_ID,
        family_member_id=MEMBER_ID,
        knowledge_import_run_id=KNOWLEDGE_RUN_ID,
        rule_import_run_id=RULE_RUN_ID,
        status_projection_digest_sha256="b" * 64,
        coverages=coverages,
        normalizers=(),
    )


def _evaluate(
    event: MedicalEvent,
    *coverages: KnowledgeCoverageContext,
) -> KnowledgeDecisionResult:
    return DeterministicKnowledgeDecisionEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        event,
        _context(*coverages),
        run_id=DECISION_RUN_ID,
    )


def test_two_fixed_coverages_match_and_sum_only_calculated_same_currency() -> None:
    result = _evaluate(_event(), _coverage(1, "100"), _coverage(2, "200"))

    assert [item.result for item in result.candidates] == ["MATCH", "MATCH"]
    assert [item.status for item in result.calculations] == ["CALCULATED", "CALCULATED"]
    assert len(result.fixed_subtotals) == 1
    assert result.fixed_subtotals[0].currency == "KRW"
    assert result.fixed_subtotals[0].amount == Decimal("300")
    assert all(item.steps for item in result.calculations)


def test_four_fixed_coverages_sum_while_indemnity_gap_stays_separate() -> None:
    receipt_rule = _expression_rule(
        6,
        kind="required_document",
        expression={"op": "present", "field": "Receipt.covered_amount"},
        input_field_paths=("Receipt.covered_amount",),
        reason_code="SYNTHETIC_RECEIPT_REQUIRED",
    )
    indemnity_calculation = _calculation(
        5,
        kind="INDEMNITY",
        document_kind="indemnity_eligibility",
        input_field_paths=("Receipt.covered_amount",),
        calculation={
            "op": "multiply",
            "args": [
                {"field": "Receipt.covered_amount"},
                {"value": Decimal("0.5")},
            ],
        },
    )
    indemnity = _coverage(
        5,
        "0",
        benefit_type="INDEMNITY",
        rules=(_eligibility_rule(5), receipt_rule),
        calculation=indemnity_calculation,
    )
    result = _evaluate(
        _event(),
        _coverage(1, "100"),
        _coverage(2, "200"),
        _coverage(3, "300"),
        _coverage(4, "400"),
        indemnity,
    )

    assert len([item for item in result.candidates if item.result == "MATCH"]) == 4
    assert result.fixed_subtotals[0].amount == Decimal("1000")
    assert result.indemnity_summary.status == "UNKNOWN"
    assert result.indemnity_summary.unresolved_candidate_count == 1
    assert result.source_failure_codes == ()

    with_receipt = _evaluate(
        _event(
            extra_facts={
                "Receipt.covered_amount": FactValue(Decimal("50"), "user", ()),
            }
        ),
        indemnity,
    )
    assert with_receipt.calculations[0].conditional_amount == Decimal("25.0")
    assert with_receipt.indemnity_summary.status == "CALCULATED"


def test_waiting_frequency_and_reduction_use_reviewed_runtime_facts() -> None:
    waiting = _expression_rule(
        20,
        kind="temporal",
        expression={
            "op": "days_since",
            "field": "PolicyContract.contract_start",
            "value": 30,
            "unit": "days",
        },
        input_field_paths=("PolicyContract.contract_start",),
        reason_code="SYNTHETIC_WAITING_COMPLETE",
    )
    frequency = _expression_rule(
        21,
        kind="exclusion",
        expression={
            "op": "count_before",
            "field": "ClaimHistory.counted_occurrence",
            "value": 1,
            "unit": "occurrences",
        },
        input_field_paths=("ClaimHistory.counted_occurrence",),
        reason_code="SYNTHETIC_FREQUENCY_EXCLUSION",
    )
    reduced = _calculation(
        20,
        kind="FIXED",
        document_kind="rate_amount",
        input_field_paths=("Rider.insured_amount",),
        calculation={
            "op": "multiply",
            "args": [
                {"field": "Rider.insured_amount"},
                {"value": Decimal("0.5")},
            ],
        },
    )
    coverage = _coverage(
        20,
        "100",
        rules=(_eligibility_rule(20), waiting, frequency),
        calculation=reduced,
    )
    no_history = _event(
        extra_facts={
            "ClaimHistory.counted_occurrence": FactValue(0, "user", ()),
        }
    )

    result = _evaluate(no_history, coverage)

    assert result.candidates[0].result == "MATCH"
    assert result.calculations[0].conditional_amount == Decimal("50.0")
    assert [item.result for item in result.evaluations] == ["MATCH", "MATCH", "MATCH"]

    prior_claim = _event(
        extra_facts={
            "ClaimHistory.counted_occurrence": FactValue(1, "user", ()),
        }
    )
    assert _evaluate(prior_claim, coverage).candidates[0].result == "NO_MATCH"


def test_every_coverage_authority_gate_fails_closed() -> None:
    base = _coverage(30, "100")
    cases = (
        (replace(base, disposition="BLOCKED"), "UNKNOWN"),
        (replace(base, subject_binding_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, enrollment_decision="NO_MATCH"), "NO_MATCH"),
        (replace(base, component_classification="UNKNOWN"), "UNKNOWN"),
        (replace(base, mapping_applicability="UNKNOWN"), "UNKNOWN"),
        (replace(base, mapping_enrollment_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, document_identity_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, edition_applicability_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, section_mapping_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, overall_mapping_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, current_confirmation_decision="UNKNOWN"), "UNKNOWN"),
        (replace(base, current_confirmed_status="inactive"), "NO_MATCH"),
    )

    assert [(_evaluate(_event(), coverage).candidates[0].result) for coverage, _ in cases] == [
        expected for _, expected in cases
    ]
    assert _evaluate(replace(_event(), event_date=None), base).candidates[0].result == "UNKNOWN"


def test_untrusted_fact_missing_event_status_and_invalid_citation_fail_closed() -> None:
    suggested = _evaluate(_event(confirmation="ai_structured"), _coverage(1, "100"))
    assert suggested.candidates[0].result == "UNKNOWN"
    assert "MedicalEvent.classification" in suggested.evaluations[0].missing_fields

    no_status = _evaluate(_event(), _coverage(2, "100", status_intervals=()))
    assert no_status.candidates[0].result == "UNKNOWN"
    assert "EVENT_DATE_STATUS_UNCONFIRMED" in no_status.candidates[0].hold_reason_codes

    inactive_status = KnowledgeStatusInterval(
        effective_from=date(2026, 1, 1),
        effective_through=date(2026, 12, 31),
        decision="MATCH",
        confirmed_status="inactive",
        authority="REVIEWED_STATUS_DOCUMENT",
    )
    inactive = _evaluate(
        _event(),
        _coverage(2, "100", status_intervals=(inactive_status,)),
    )
    assert inactive.candidates[0].result == "NO_MATCH"
    assert "EVENT_DATE_CONTRACT_INACTIVE" in inactive.candidates[0].hold_reason_codes

    bad_rule = _eligibility_rule(3)
    bad_rule = KnowledgeRulePublication(
        publication_id=bad_rule.publication_id,
        rule_key=bad_rule.rule_key,
        rule_kind=bad_rule.rule_kind,
        required=bad_rule.required,
        result_reason_code=bad_rule.result_reason_code,
        rule_document=bad_rule.rule_document,
        citations=(_citation(3, valid=False),),
    )
    invalid = _evaluate(_event(), _coverage(3, "100", rule=bad_rule))
    assert invalid.candidates[0].result == "UNKNOWN"
    assert invalid.evaluations[0].reason_code == "CITATION_INVALID"


def test_exclusion_is_decisive_and_one_bad_coverage_does_not_hide_other_results() -> None:
    exclusion = _coverage(1, "100", rule=_eligibility_rule(1, exclusion=True))
    malformed_rule = _eligibility_rule(2)
    malformed_rule = KnowledgeRulePublication(
        publication_id=malformed_rule.publication_id,
        rule_key=malformed_rule.rule_key,
        rule_kind=malformed_rule.rule_kind,
        required=True,
        result_reason_code=malformed_rule.result_reason_code,
        rule_document={"unsupported": True},
        citations=malformed_rule.citations,
    )
    healthy = _coverage(3, "100")

    result = _evaluate(
        _event(),
        exclusion,
        _coverage(2, "100", rule=malformed_rule),
        healthy,
    )

    assert [item.result for item in result.candidates] == ["NO_MATCH", "UNKNOWN", "MATCH"]
    assert result.completeness == "PARTIAL"
    assert "KNOWLEDGE_COVERAGE_EVALUATION_FAILED" in result.source_failure_codes


def test_mixed_currency_has_separate_subtotals_without_cross_currency_total() -> None:
    result = _evaluate(
        _event(),
        _coverage(1, "100", currency="KRW"),
        _coverage(2, "2", currency="USD"),
    )

    assert [(item.currency, item.amount) for item in result.fixed_subtotals] == [
        ("KRW", Decimal("100")),
        ("USD", Decimal("2")),
    ]
