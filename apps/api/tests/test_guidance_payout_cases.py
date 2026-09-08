"""One canonical coverage retains independent original payout cases."""

from dataclasses import replace

from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.domain import GuidancePayoutCaseInput
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.private_adapter import adapt_private_guidance

from apps.api.tests.test_private_knowledge_engine import (
    HOUSEHOLD_ID,
    _calculation,
    _context,
    _coverage,
    _eligibility_rule,
    _event,
    _expression_rule,
)


def case(number, classification, amount, *, incomplete=False):
    classification_rule = _eligibility_rule(number)
    classification_rule = replace(
        classification_rule,
        rule_document={
            **classification_rule.rule_document,
            "expression": {
                "op": "equals",
                "field": "MedicalEvent.classification",
                "value": classification,
            },
        },
    )
    relevance = _expression_rule(
        100 + number,
        kind="eligibility",
        expression={
            "op": "range",
            "field": "MedicalEvent.admission_days",
            "value": {"min": 1, "max": 36500},
            "unit": "days",
        },
        input_field_paths=("MedicalEvent.admission_days",),
        reason_code="SYNTHETIC_ADMISSION",
    )
    relevance = replace(
        relevance, required=False, rule_document={**relevance.rule_document, "required": False}
    )
    publication = _calculation(
        number,
        kind="FIXED",
        document_kind="fixed_amount",
        input_field_paths=(),
        calculation={"op": "multiply", "args": [{"value": amount}, {"value": 1}]},
    )
    source = adapt_private_guidance(
        _context(
            _coverage(
                number,
                "100",
                rules=(classification_rule, relevance),
                calculation=publication,
            )
        )
    ).coverages[0]
    return GuidancePayoutCaseInput(
        case_key=str(source.calculation.publication_id),
        rules=source.rules,
        calculation=source.calculation,
        benefit_type="FIXED",
        knowledge_incomplete=incomplete,
    )


def evaluate(cases, *, facts=None):
    ctx = adapt_private_guidance(_context(_coverage(1, "100")))
    ctx = replace(
        ctx, coverages=(replace(ctx.coverages[0], rules=(), calculation=None, cases=cases),)
    )
    event = _event()
    if facts is not None:
        event = replace(event, facts=facts, situation="5일 입원했습니다.")
    return LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, ctx)


def test_one_case_mismatch_does_not_remove_the_matching_case_or_canonical_candidate():
    result = evaluate((case(1, "sample_category", 100), case(2, "other_category", 200)))
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.estimate.amount == "100" and len(candidate.cases) == 1
    assert candidate.cases[0].condition_result == "MATCH"


def test_unknown_classification_preserves_both_source_formulas_and_conditional_range():
    result = evaluate((case(1, "sample_category", 100), case(2, "other_category", 200)), facts={})
    candidate = result.candidates[0]
    assert len(candidate.cases) == 2 and candidate.case_relation == "MUTUALLY_EXCLUSIVE"
    assert {c.estimate.amount for c in candidate.cases} == {"100", "200"}
    assert candidate.estimate.kind == "RANGE" and candidate.estimate.amount is None
    assert (candidate.estimate.lower, candidate.estimate.upper) == ("100", "200")
    assert candidate.estimate.basis == "SOURCE_ALTERNATIVES"
    assert "SOURCE_CASE_APPLIES" in candidate.estimate.assumptions
    assert candidate.condition_result == "UNKNOWN"


def test_potentially_coexisting_cases_are_never_automatically_summed_or_ranged():
    result = evaluate((case(1, "sample_category", 100), case(2, "sample_category", 200)))
    candidate = result.candidates[0]
    assert len(candidate.cases) == 2 and candidate.case_relation == "UNRESOLVED"
    assert candidate.estimate.amount is candidate.estimate.lower is candidate.estimate.upper is None
    assert {c.estimate.amount for c in candidate.cases} == {"100", "200"}


def test_partial_case_does_not_change_complete_case_conditions_or_trace():
    result = evaluate(
        (case(1, "sample_category", 100), case(2, "sample_category", 200, incomplete=True))
    )
    first, second = result.candidates[0].cases
    assert first.condition_result == "MATCH" and first.estimate.amount == "100"
    assert second.condition_result == "UNKNOWN" and second.estimate.amount == "200"
    assert first.estimate.trace.publication_id != second.estimate.trace.publication_id
    assert result.support.unsupported_coverages == 1


def test_admission_and_surgery_bind_their_own_source_activity_without_summing():
    cases = []
    for number, activity in ((1, "admission"), (2, "surgery")):
        rule = _expression_rule(
            number,
            kind="eligibility",
            expression={
                "op": "equals",
                "field": "MedicalEvent.treatment_kind",
                "value": activity,
            },
            input_field_paths=("MedicalEvent.treatment_kind",),
            reason_code="SYNTHETIC_ACTIVITY",
        )
        source = adapt_private_guidance(_context(_coverage(number, "100", rule=rule))).coverages[0]
        cases.append(replace(case(number, "sample_category", number * 100), rules=source.rules))
    ctx = adapt_private_guidance(_context(_coverage(1, "100")))
    ctx = replace(
        ctx, coverages=(replace(ctx.coverages[0], rules=(), calculation=None, cases=tuple(cases)),)
    )
    event = replace(_event(), facts={}, situation="5일 입원했고 수술을 받았습니다.")
    result = LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, ctx)
    first, second = result.candidates[0].cases
    assert first.condition_result == second.condition_result == "MATCH"
    assert first.estimate.amount == "100" and second.estimate.amount == "200"
    assert result.candidates[0].estimate.amount is None
    assert result.candidates[0].case_relation == "UNRESOLVED"
