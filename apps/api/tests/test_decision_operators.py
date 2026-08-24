from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from familycare_api.clauses.dsl import CompiledExpression, validate_expression
from familycare_api.decisions.domain import FactContext, FactValue
from familycare_api.decisions.operators import (
    OperatorEvaluationError,
    compare_required,
    evaluate_expression,
)


def fact(value: object | None, confirmation: str = "user") -> FactValue:
    return FactValue(value, confirmation, ())  # type: ignore[arg-type]


def context(
    *,
    event: dict[str, FactValue] | None = None,
    policy: dict[str, FactValue] | None = None,
    rider: dict[str, FactValue] | None = None,
    history: dict[str, FactValue] | None = None,
    as_of: date | None = None,
) -> FactContext:
    return FactContext(
        medical_event=event or {},
        policy=policy or {},
        rider=rider or {},
        claim_history=history or {},
        as_of_date=as_of,
    )


def compile_expression(document: dict[str, object]) -> CompiledExpression:
    return validate_expression(document)


@pytest.mark.parametrize(
    ("value", "expected", "result", "reason"),
    [
        (fact("injury"), "injury", "MATCH", "FACT_EQUALS"),
        (fact("illness"), "injury", "NO_MATCH", "DETERMINISTIC_VALUE_MISMATCH"),
        (None, "injury", "UNKNOWN", "MISSING_OR_CONFLICTING_FACT"),
        (fact(None), "injury", "UNKNOWN", "MISSING_OR_CONFLICTING_FACT"),
        (fact("injury", "unconfirmed"), "injury", "UNKNOWN", "UNCONFIRMED_FACT"),
        (fact("injury", "conflicting"), "injury", "UNKNOWN", "MISSING_OR_CONFLICTING_FACT"),
    ],
)
def test_compare_required_is_fail_closed(
    value: FactValue | None,
    expected: object,
    result: str,
    reason: str,
) -> None:
    outcome = compare_required(value, expected)

    assert (outcome.result, outcome.reason_code) == (result, reason)


def test_equals_preserves_missing_field_for_follow_up_question() -> None:
    expression = compile_expression(
        {"op": "equals", "field": "MedicalEvent.classification", "value": "injury"}
    )

    outcome = evaluate_expression(expression, context())

    assert outcome.result == "UNKNOWN"
    assert outcome.missing_fields == ("MedicalEvent.classification",)


def test_present_equals_in_and_range_are_deterministic() -> None:
    ctx = context(
        event={"classification": fact("injury"), "admission_days": fact(3)},
    )

    assert (
        evaluate_expression(
            compile_expression({"op": "present", "field": "MedicalEvent.classification"}), ctx
        ).result
        == "MATCH"
    )
    assert (
        evaluate_expression(
            compile_expression(
                {"op": "equals", "field": "MedicalEvent.classification", "value": "injury"}
            ),
            ctx,
        ).result
        == "MATCH"
    )
    assert (
        evaluate_expression(
            compile_expression(
                {
                    "op": "in",
                    "field": "MedicalEvent.classification",
                    "value": ["illness", "injury"],
                }
            ),
            ctx,
        ).result
        == "MATCH"
    )
    assert (
        evaluate_expression(
            compile_expression(
                {
                    "op": "range",
                    "field": "MedicalEvent.admission_days",
                    "value": {"min": 2, "max": 3},
                    "unit": "days",
                }
            ),
            ctx,
        ).result
        == "MATCH"
    )


@pytest.mark.parametrize(
    ("operator", "expected"),
    [
        ("all", "UNKNOWN"),
        ("any", "MATCH"),
    ],
)
def test_boolean_operators_use_three_valued_logic(operator: str, expected: str) -> None:
    expression = compile_expression(
        {
            "op": operator,
            "args": [
                {"op": "equals", "field": "MedicalEvent.classification", "value": "injury"},
                {"op": "present", "field": "MedicalEvent.event_date"},
            ],
        }
    )

    assert (
        evaluate_expression(expression, context(event={"classification": fact("injury")})).result
        == expected
    )


def test_all_no_match_wins_over_unknown_and_not_preserves_unknown() -> None:
    all_expression = compile_expression(
        {
            "op": "all",
            "args": [
                {"op": "equals", "field": "MedicalEvent.classification", "value": "injury"},
                {"op": "equals", "field": "Rider.status", "value": "active"},
            ],
        }
    )
    not_expression = compile_expression(
        {
            "op": "not",
            "args": [
                {"op": "equals", "field": "Rider.status", "value": "active"},
            ],
        }
    )

    assert (
        evaluate_expression(
            all_expression, context(event={"classification": fact("illness")})
        ).result
        == "NO_MATCH"
    )
    assert evaluate_expression(not_expression, context()).result == "UNKNOWN"


def test_date_between_is_inclusive_and_days_since_uses_explicit_reference() -> None:
    ctx = context(
        event={"event_date": fact(date(2026, 9, 4))},
        policy={"contract_start": fact(date(2026, 8, 25))},
    )

    date_between = compile_expression(
        {
            "op": "date_between",
            "field": "MedicalEvent.event_date",
            "value": {"start": "2026-09-04", "end": "2026-09-04"},
            "unit": "date",
        }
    )
    days_since = compile_expression(
        {
            "op": "days_since",
            "field": "PolicyContract.contract_start",
            "value": 10,
            "unit": "days",
        }
    )

    assert evaluate_expression(date_between, ctx).result == "MATCH"
    assert evaluate_expression(days_since, ctx).result == "MATCH"


def test_count_before_counts_only_confirmed_history() -> None:
    expression = compile_expression(
        {
            "op": "count_before",
            "field": "ClaimHistory.counted_occurrence",
            "value": 2,
            "unit": "occurrences",
        }
    )

    assert (
        evaluate_expression(
            expression,
            context(history={"counted_occurrence": fact(2)}),
        ).result
        == "MATCH"
    )
    assert evaluate_expression(expression, context()).result == "UNKNOWN"
    assert (
        evaluate_expression(
            expression,
            context(history={"counted_occurrence": fact(1)}),
        ).result
        == "NO_MATCH"
    )


def test_decimal_comparison_is_exact_and_invalid_runtime_value_is_structural_error() -> None:
    expression = compile_expression(
        {
            "op": "range",
            "field": "Rider.insured_amount",
            "value": {"min": 1000, "max": 1000},
            "unit": "amount",
        }
    )
    assert (
        evaluate_expression(
            expression,
            context(rider={"insured_amount": fact(Decimal("1000.00"))}),
        ).result
        == "MATCH"
    )

    with pytest.raises(OperatorEvaluationError, match="INVALID_DECIMAL"):
        evaluate_expression(expression, context(rider={"insured_amount": fact("not-a-number")}))

    equals_expression = compile_expression(
        {
            "op": "equals",
            "field": "Rider.insured_amount",
            "value": 1000,
        }
    )
    with pytest.raises(OperatorEvaluationError, match="INVALID_DECIMAL"):
        evaluate_expression(
            equals_expression,
            context(rider={"insured_amount": fact("not-a-number")}),
        )
