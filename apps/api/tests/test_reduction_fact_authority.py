"""Reduction branches require an explicit user-confirmed plain event fact."""

from dataclasses import replace
from decimal import Decimal

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import FactValue
from familycare_api.decisions.knowledge_domain import KnowledgeFactNormalizer
from familycare_api.decisions.knowledge_engine import DeterministicKnowledgeDecisionEngine
from familycare_api.guidance.event_facts import build_event_facts

from apps.api.tests.test_private_knowledge_engine import (
    DECISION_RUN_ID,
    HOUSEHOLD_ID,
    _calculation,
    _context,
    _coverage,
    _event,
)

REDUCTION_FIELD = "MedicalEvent.reduction_applies"


@pytest.mark.parametrize("reduction", [True, False])
@pytest.mark.parametrize("confirmation", [None, "user", "ai_structured"])
def test_legacy_reduction_normalizer_cannot_replace_explicit_user_fact(reduction, confirmation):
    publication = _calculation(
        71,
        kind="FIXED",
        document_kind="rate_amount",
        input_field_paths=(REDUCTION_FIELD, "Rider.insured_amount"),
        calculation={
            "op": "if",
            "args": [
                {"field": REDUCTION_FIELD},
                {
                    "op": "multiply",
                    "args": [{"field": "Rider.insured_amount"}, {"value": 0.5}],
                },
                {"field": "Rider.insured_amount"},
            ],
        },
    )
    context = replace(
        _context(_coverage(71, "100", calculation=publication)),
        normalizers=(
            KnowledgeFactNormalizer(
                normalizer_key="synthetic-reduction-normalizer",
                field_path=REDUCTION_FIELD,
                normalized_tokens=("synthetic", "event"),
                normalized_value=reduction,
                priority=100,
            ),
        ),
    )
    event = _event(
        extra_facts=(
            {REDUCTION_FIELD: FactValue(reduction, confirmation, ())}
            if confirmation is not None
            else {}
        )
    )
    result = DeterministicKnowledgeDecisionEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID), event, context, run_id=DECISION_RUN_ID
    )

    assert result.candidates[0].result == "MATCH"
    assert len(result.calculations) == 1
    calculation = result.calculations[0]
    if confirmation == "user":
        expected = Decimal("50" if reduction else "100")
        assert calculation.status == "CALCULATED"
        assert calculation.confirmed_amount == expected
        assert calculation.conditional_amount == expected
    else:
        assert calculation.status == "UNKNOWN"
        assert calculation.confirmed_amount is None
        assert calculation.conditional_amount is None


@pytest.mark.parametrize("reduction", [True, False])
@pytest.mark.parametrize("source", ["system", "user", "ai"])
def test_structured_reduction_alias_cannot_supply_plain_fact_authority(reduction, source):
    event = replace(
        _event(),
        structured_facts=(
            {
                "field_id": "reduction_applies",
                "value": reduction,
                "source": source,
                "state": "confirmed",
            },
        ),
    )
    read = build_event_facts(event)
    assert read.context.get(REDUCTION_FIELD) is None
    assert "LOCAL_EXPLICIT_FIELD_UNSUPPORTED" in read.reason_codes

    explicit = replace(
        event,
        facts={**event.facts, REDUCTION_FIELD: FactValue(not reduction, "user", ())},
    )
    explicit_read = build_event_facts(explicit)
    fact = explicit_read.context.get(REDUCTION_FIELD)
    assert fact is not None
    assert fact.value is (not reduction)
    assert fact.provenance == "USER_CONFIRMED"
    assert REDUCTION_FIELD not in explicit_read.context.audit_conflicts
