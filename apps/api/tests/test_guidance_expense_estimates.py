"""Registered cost subsets reach the real guidance calculator without inventing totals."""

from dataclasses import replace
from decimal import Decimal

import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.private_adapter import adapt_private_guidance

from apps.api.tests.test_guidance_expenses import ExpenseDatabase, line, uid
from apps.api.tests.test_private_knowledge_engine import (
    HOUSEHOLD_ID,
    _calculation,
    _context,
    _coverage,
    _event,
    _expression_rule,
)


def evaluate(rows, *, stale=False, cost_condition=False):
    publication = _calculation(
        1,
        kind="INDEMNITY",
        document_kind="rate_amount",
        input_field_paths=("Receipt.covered_amount",),
        calculation={
            "op": "multiply",
            "args": [
                {"field": "Receipt.covered_amount"},
                {"value": Decimal("0.8")},
            ],
        },
    )
    ctx = adapt_private_guidance(
        _context(
            _coverage(
                1,
                "100",
                benefit_type="INDEMNITY",
                calculation=publication,
            )
        )
    )
    if cost_condition:
        condition = _expression_rule(
            2,
            kind="eligibility",
            expression={
                "op": "range",
                "field": "Receipt.covered_amount",
                "value": {"min": 60000, "max": 100000},
                "unit": "amount",
            },
            input_field_paths=("Receipt.covered_amount",),
            reason_code="SYNTHETIC_COST_REQUIRED",
        )
        rule = (
            adapt_private_guidance(_context(_coverage(2, "100", rule=condition)))
            .coverages[0]
            .rules[0]
        )
        coverage = ctx.coverages[0]
        ctx = replace(ctx, coverages=(replace(coverage, rules=(*coverage.rules, rule)),))
    expenses = ExpenseDatabase(rows).read()
    ctx = replace(ctx, expenses=expenses)
    event = replace(_event(), id=uid(2), version=4 if stale else 3)
    return LocalGuidanceEngine().evaluate(HouseholdScope(HOUSEHOLD_ID), event, ctx)


def test_review_and_excluded_costs_keep_only_confirmed_covered_partial_estimate():
    result = evaluate(
        [
            line(10, "50000"),
            line(11, "20000", coverage="unknown"),
            line(12, "5000", coverage="excluded"),
            line(13, "10000", confirmation="ai_structured"),
        ]
    )
    estimate = result.candidates[0].estimate
    assert estimate.kind == "FORMULA" and estimate.amount is None
    assert estimate.partial_amount == "40000" and estimate.basis == "CONFIRMED_COST_SUBSET"
    assert "Receipt.unresolved_costs" in estimate.missing_inputs
    operand = estimate.trace.steps[0].operands[0]
    assert operand.value == "50000" and operand.provenance == "USER_CONFIRMED"
    assert [ref.source_id for ref in operand.source_refs] == [str(uid(10))]
    costs = result.expenses.currencies[0]
    assert costs.covered.known_cost == "50000" and costs.excluded.known_cost == "5000"
    assert costs.coverage_review.known_cost == "20000" and costs.unconfirmed.known_cost == "10000"


@pytest.mark.parametrize(
    "rows,amount",
    [
        ([line(10, "50000"), line(11, "5000", coverage="excluded")], "40000"),
        ([line(10, "0")], "0"),
        ([line(10, "5000", coverage="excluded")], "0"),
    ],
)
def test_fully_classified_registered_costs_have_a_scoped_point(rows, amount):
    estimate = evaluate(rows).candidates[0].estimate
    assert estimate.kind == "POINT" and estimate.amount == amount
    assert estimate.basis == "REGISTERED_COSTS" and estimate.partial_amount is None


@pytest.mark.parametrize(
    "rows",
    [
        [],
        [line(10, None)],
        [line(10, "20000", coverage="unknown")],
        [line(10, "50000", currency="USD")],
        [line(10, "50000", confirmation="ai_structured")],
    ],
)
def test_no_confirmed_covered_input_never_becomes_zero(rows):
    estimate = evaluate(rows).candidates[0].estimate
    assert estimate.kind == "FORMULA" and estimate.amount is estimate.partial_amount is None
    assert "Receipt.covered_amount" in estimate.missing_inputs


@pytest.mark.parametrize("extra", [line(11, None), line(11, "50000", currency="USD")])
def test_unknown_or_other_currency_costs_never_enter_known_subset(extra):
    result = evaluate([line(10, "50000"), extra])
    assert result.candidates[0].estimate.partial_amount == "40000"
    assert result.candidates[0].estimate.amount is None


def test_old_expense_event_version_cannot_enter_new_calculation():
    result = evaluate([line(10)], stale=True)
    assert result.candidates[0].estimate.amount is None
    assert result.candidates[0].estimate.partial_amount is None
    assert "EXPENSE_EVENT_VERSION_MISMATCH" in result.support.failure_codes


def test_partial_cost_is_not_a_decisive_failure_of_a_full_cost_condition():
    result = evaluate([line(10, "50000"), line(11, None)], cost_condition=True)
    assert len(result.candidates) == 1
    assert result.candidates[0].condition_result == "UNKNOWN"
    assert all(c.reason_code != "UNSUPPORTED_DSL" for c in result.candidates[0].conditions)
    assert result.candidates[0].estimate.partial_amount == "40000"
