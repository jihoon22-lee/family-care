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
