"""Actual local event interpretation and versioned code scopes reach candidate evaluation."""

from dataclasses import replace

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.semantic_binding import bind_semantic_root

from apps.api.tests.test_guidance_semantic_binding import source_and_root
from apps.api.tests.test_private_knowledge_engine import HOUSEHOLD_ID, _context, _coverage, _event


def context():
    source, clause, current = source_and_root()
    bound = bind_semantic_root(current, clause, source.source.model_dump())
    base = adapt_private_guidance(_context(_coverage(1, "100")))
    return replace(
        base,
        coverages=(replace(base.coverages[0], rules=bound.rules, calculation=bound.calculation),),
    )


def test_planned_guidance_retains_optional_relevance_separate_from_required_conditions():
    event = replace(_event(), facts={}, situation="5일 입원 예정입니다.")
    candidate = (
        LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context()).candidates[0]
    )
    assert candidate.condition_result == "UNKNOWN"
    assert any(
        not condition.required and condition.result == "UNKNOWN"
        for condition in candidate.conditions
    )
    assert any(condition.required for condition in candidate.conditions)


@pytest.mark.parametrize("scope_case", ["matching", "missing", "wrong_version", "wrong_code"])
def test_local_admission_computes_while_code_scope_remains_an_independent_condition(scope_case):
    event = replace(_event(), facts={}, situation="5일 입원했습니다.")
    if scope_case != "missing":
        event = replace(
            event,
            structured_facts=(
                {
                    "field_id": "condition_class",
                    "value": "class-b" if scope_case == "wrong_code" else "class-a",
                    "source": "user",
                    "state": "confirmed",
                    "confidence": "high",
                    "evidence_ids": (),
                    "code_system": "synthetic-classification",
                    "code_version": "edition-2" if scope_case == "wrong_version" else "edition-1",
                },
            ),
        )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    if scope_case == "wrong_code":
        assert not result.candidates
        return
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.estimate.amount == "300"
    assert candidate.condition_result == ("MATCH" if scope_case == "matching" else "UNKNOWN")
    assert candidate.group == ("PRIMARY" if scope_case == "matching" else "CONDITIONAL")


@pytest.mark.parametrize(
    "text", ["입원하지 않았습니다.", "어머니는 5일 입원했습니다. 저는 외래 진료를 받았습니다."]
)
def test_negated_or_other_person_admission_does_not_create_relevance(text):
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), replace(_event(), facts={}, situation=text), context()
    )
    assert not result.candidates


def test_semantic_currency_is_never_reinterpreted_as_the_rider_currency():
    original = context()
    mismatched = replace(original, coverages=(replace(original.coverages[0], currency="USD"),))
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        replace(_event(), facts={}, situation="5일 입원했습니다."),
        mismatched,
    )
    assert result.candidates[0].estimate.kind == "FORMULA"
    assert result.candidates[0].estimate.amount is None
    assert result.candidates[0].estimate.reason_code == "CALCULATION_CURRENCY_MISMATCH"


def test_partial_semantics_keeps_a_supported_formula_without_confirming_all_conditions():
    original = context()
    coverage = original.coverages[0]
    partial = replace(
        original,
        coverages=(
            replace(
                coverage,
                rules=tuple(rule for rule in coverage.rules if not rule.required),
                knowledge_incomplete=True,
            ),
        ),
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        replace(_event(), facts={}, situation="5일 입원했습니다."),
        partial,
    )
    candidate = result.candidates[0]
    assert candidate.condition_result == "UNKNOWN" and candidate.group == "CONDITIONAL"
    assert candidate.estimate.amount == "300"
    assert "SEMANTIC_KNOWLEDGE_PARTIAL" in candidate.reason_codes
    assert result.support.unsupported_coverages == 1


@pytest.mark.parametrize("trusted_relevance", [False, True])
def test_ai_suggested_days_are_neither_confirmed_relevance_nor_calculation_input(trusted_relevance):
    from familycare_api.decisions.domain import FactValue

    event = replace(
        _event(),
        situation="Synthetic event",
        facts={"MedicalEvent.admission_days": FactValue(5, "ai_structured", ())},
    )
    if trusted_relevance:
        event = replace(
            event,
            structured_facts=(
                {
                    "field_id": "condition_class",
                    "value": "class-a",
                    "source": "user",
                    "state": "confirmed",
                    "confidence": "high",
                    "evidence_ids": (),
                    "code_system": "synthetic-classification",
                    "code_version": "edition-1",
                },
            ),
        )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    if not trusted_relevance:
        assert not result.candidates
    else:
        assert result.candidates[0].estimate.kind == "FORMULA"
        assert result.candidates[0].estimate.amount is None
        assert "MedicalEvent.admission_days" in result.candidates[0].estimate.missing_inputs


def surgery_context():
    from apps.api.tests.test_private_knowledge_engine import _expression_rule

    rule = _expression_rule(
        111,
        kind="eligibility",
        expression={
            "op": "all",
            "args": [
                {"op": "equals", "field": "MedicalEvent.treatment_kind", "value": "surgery"},
                {"op": "equals", "field": "MedicalEvent.performed", "value": True},
            ],
        },
        input_field_paths=("MedicalEvent.treatment_kind", "MedicalEvent.performed"),
        reason_code="SYNTHETIC_SURGERY_ELIGIBILITY",
    )
    return adapt_private_guidance(_context(replace(_coverage(1, "100"), rules=(rule,))))


@pytest.mark.parametrize(
    ("text", "expected"),
    [("수술을 받았습니다.", True), ("수술을 받지 않았습니다.", False)],
)
def test_explicit_surgery_is_bound_to_the_source_treatment_kind(text, expected):
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), replace(_event(), facts={}, situation=text), surgery_context()
    )
    assert bool(result.candidates) is expected
    if expected:
        assert result.candidates[0].condition_result == "MATCH"
        assert result.candidates[0].estimate.amount == "100"


def test_surgery_observation_cannot_confirm_an_unscoped_performed_predicate():
    original = surgery_context()
    coverage = original.coverages[0]
    rule = coverage.rules[0]
    document = {
        **rule.rule_document,
        "input_field_paths": ["MedicalEvent.performed"],
        "expression": {"op": "equals", "field": "MedicalEvent.performed", "value": True},
    }
    unscoped = replace(
        original,
        coverages=(replace(coverage, rules=(replace(rule, rule_document=document),)),),
    )
    result = LocalGuidanceEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        replace(_event(), facts={}, situation="수술을 받았습니다."),
        unscoped,
    )
    assert not result.candidates


def test_user_korean_activity_name_matches_the_same_broad_source_activity():
    event = replace(
        _event(),
        situation="수술을 받았습니다.",
        facts={},
        structured_facts=(
            {
                "field_id": "treatment_kind",
                "value": "수술",
                "source": "user",
                "state": "confirmed",
                "confidence": "high",
                "evidence_ids": (),
            },
        ),
    )
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, surgery_context())
    assert result.candidates[0].condition_result == "MATCH"
    assert event.structured_facts[0]["value"] == "수술"


def test_more_specific_unrecognized_treatment_name_is_not_a_decisive_activity_mismatch():
    from familycare_api.guidance.event_facts import build_event_facts

    event = replace(
        _event(),
        situation="수술을 받았습니다.",
        facts={},
        structured_facts=(
            {
                "field_id": "treatment_kind",
                "value": "synthetic-specific-procedure",
                "source": "user",
                "state": "confirmed",
                "confidence": "high",
                "evidence_ids": (),
            },
        ),
    )
    read = build_event_facts(event, activity="surgery")
    assert not read.context.get("MedicalEvent.treatment_kind").is_trusted
    assert read.context.get("MedicalEvent.treatment_kind").value is None
    assert event.structured_facts[0]["value"] == "synthetic-specific-procedure"


def test_local_topic_can_support_a_conditional_candidate_without_confirming_classification():
    from familycare_api.decisions.knowledge_domain import KnowledgeFactNormalizer

    original = adapt_private_guidance(_context(_coverage(1, "100")))
    source = replace(
        original,
        normalizers=(
            KnowledgeFactNormalizer(
                "synthetic-reviewed-topic",
                "MedicalEvent.classification",
                ("sample", "category", "phrase"),
                "sample_category",
                100,
            ),
        ),
    )
    event = replace(_event(), facts={}, situation="sample category phrase")
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, source)
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL" and candidate.condition_result == "UNKNOWN"
    assert candidate.estimate.amount == "100"
    assert candidate.relevance[0].kind == "LOCAL_TOPIC"
    assert candidate.conditions[0].result == "UNKNOWN"
    assert event.facts == {}


def test_planned_admission_keeps_candidate_and_formula_without_confirmed_days():
    event = replace(_event(), facts={}, situation="5일 입원 예정입니다.")
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, context())
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL" and candidate.condition_result == "UNKNOWN"
    assert candidate.estimate.kind == "FORMULA" and candidate.estimate.amount is None
    assert any(item.kind == "PLANNED_EVENT" for item in candidate.relevance)
    assert event.facts == {}


@pytest.mark.parametrize("input_case", ["amount", "claim_history"])
def test_unverified_source_amount_or_ai_history_cannot_decisively_exclude_a_related_candidate(
    input_case,
):
    from familycare_api.decisions.knowledge_domain import KnowledgeFact

    from apps.api.tests.test_private_knowledge_engine import _expression_rule

    coverage = _coverage(1, "100")
    if input_case == "amount":
        document = {
            "op": "range",
            "field": "Rider.insured_amount",
            "value": {"min": 200, "max": 300},
            "unit": "amount",
        }
        coverage = replace(
            coverage,
            certificate_amount_decision="UNKNOWN",
            certificate_amount_evidence_state="UNAVAILABLE",
        )
    else:
        document = {
            "op": "count_before",
            "field": "ClaimHistory.counted_occurrence",
            "value": 2,
            "unit": "occurrences",
        }
        coverage = replace(
            coverage, claim_history_counted_occurrence=KnowledgeFact(99, "AI_SUGGESTED")
        )
    rule = _expression_rule(
        112,
        kind="eligibility",
        expression=document,
        input_field_paths=(document["field"],),
        reason_code="SYNTHETIC_SOURCE_CONDITION",
    )
    source = adapt_private_guidance(_context(replace(coverage, rules=(*coverage.rules, rule))))
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), _event(), source)
    assert result.candidates[0].condition_result == "UNKNOWN"
    assert result.candidates[0].group == "CONDITIONAL"
