"""Explicit Boolean reduction conditions select only their source-backed branch."""

from dataclasses import replace
from decimal import Decimal

import pytest
from familycare_api.clauses.dsl import CompiledCalculation
from familycare_api.guidance.calculation_runtime import CalculationInput, evaluate_calculation
from familycare_api.guidance.calculation_source import CalculationSourceError
from familycare_api.guidance.trace_projection import calculation_trace

from apps.api.tests.test_guidance_calculation_runtime import REF, SOURCE, field, hint
from apps.api.tests.test_guidance_calculation_source import (
    INSURED,
    evaluate,
    input_value,
    literal,
    op,
    publication,
)

CONDITION = "MedicalEvent.reduction_applies"


def condition(value, **changes):
    return replace(CalculationInput(value, "BOOLEAN", None, "USER_CONFIRMED", (REF,)), **changes)


def branch():
    return CompiledCalculation(
        "if",
        (
            CONDITION,
            CompiledCalculation("multiply", ("Rider.insured_amount", Decimal("0.5")), None),
            "Receipt.covered_amount",
        ),
        None,
        (CONDITION, "Rider.insured_amount", "Receipt.covered_amount"),
    )


def branch_hints():
    return {
        "/calculation": hint("MONEY", "KRW"),
        "/calculation/args/0": hint("BOOLEAN"),
        "/calculation/args/1/args/1": hint("RATIO"),
    }


@pytest.mark.parametrize("applies,expected,selected", [(True, 30, 1), (False, 60, 2)])
def test_condition_selects_only_one_branch_with_original_paths_and_boolean_trace(
    applies, expected, selected
):
    selected_field = "Rider.insured_amount" if applies else "Receipt.covered_amount"
    result = evaluate_calculation(
        branch(),
        {CONDITION: condition(applies), selected_field: field("60")},
        source=SOURCE,
        unit_hints=branch_hints(),
    )
    assert result.amount == expected and result.status == "COMPLETE"
    assert result.missing_paths == ()
    step = result.steps[-1]
    assert step.operation == "if" and step.value == expected
    assert [operand.path for operand in step.operands] == [
        "/calculation/args/0",
        f"/calculation/args/{selected}",
    ]
    assert step.operands[0].value is applies
    assert step.operands[0].source_refs == (REF,)
    trace = calculation_trace(result, "b" * 64)
    assert trace.steps[-1].operands[0].value is applies
    assert trace.steps[-1].operands[0].supplied_value is applies


@pytest.mark.parametrize("fault", ["missing", "ai", "stale", "conflicting", "no_source"])
def test_unresolved_condition_never_chooses_false_or_evaluates_either_amount(fault):
    item = condition(True)
    if fault == "missing":
        item = replace(item, value=None)
    elif fault == "ai":
        item = replace(item, provenance="AI_SUGGESTED")
    elif fault == "stale":
        item = replace(item, stale=True)
    elif fault == "conflicting":
        item = replace(item, provenance="CONFLICTING")
    else:
        item = replace(item, source_refs=())
    result = evaluate_calculation(
        branch(), {CONDITION: item}, source=SOURCE, unit_hints=branch_hints()
    )
    assert result.amount is None and result.status == "UNAVAILABLE"
    assert result.missing_paths == (CONDITION,)
    assert len(result.steps) == 1
    assert [operand.path for operand in result.steps[0].operands] == ["/calculation/args/0"]


@pytest.mark.parametrize(
    "value,unit", [(True, "MONEY"), (False, "NUMBER"), (Decimal(1), "BOOLEAN")]
)
def test_boolean_inputs_cannot_impersonate_numeric_values(value, unit):
    with pytest.raises(ValueError, match="CALCULATION_INPUT_INVALID"):
        CalculationInput(value, unit, "KRW" if unit == "MONEY" else None, "USER_CONFIRMED", (REF,))


def test_bound_source_applies_reduction_before_cap_and_rounding():
    expression = op(
        "round",
        op(
            "min",
            op(
                "if",
                {"field": CONDITION},
                op("multiply", INSURED, literal(Decimal("0.5"))),
                INSURED,
            ),
            literal(40),
        ),
        rounding="half_up",
    )
    source = publication(expression, document_kind="rate_amount")
    for applies, amount in [(True, 30), (False, 40)]:
        binding, result = evaluate(
            source, {CONDITION: condition(applies), "Rider.insured_amount": input_value(60)}
        )
        assert result.amount == amount
        assert binding.unit_hints["/calculation/args/0/args/0/args/0"].unit == "BOOLEAN"
        assert result.steps[-1].operation == "round"


def test_source_binding_rejects_incompatible_conditional_branch_dimensions():
    expression = op("if", {"field": CONDITION}, INSURED, {"field": "MedicalEvent.admission_days"})
    with pytest.raises(CalculationSourceError, match="CALCULATION_UNIT_MISMATCH"):
        evaluate(publication(expression, document_kind="rate_amount"))


def test_runtime_rejects_boolean_as_an_arithmetic_operand_even_without_dsl():
    node = CompiledCalculation("add", (CONDITION, Decimal(1)), None, (CONDITION,))
    result = evaluate_calculation(node, {CONDITION: condition(True)}, source=SOURCE)
    assert result.amount is None and result.status == "FAILED"
    assert "CALCULATION_UNIT_MISMATCH" in result.reason_codes


@pytest.mark.parametrize("applies,expected", [(True, 30), (False, 60)])
def test_dimensionless_conditional_factor_is_multiplied_after_typed_selection(applies, expected):
    expression = op(
        "multiply",
        INSURED,
        op("if", {"field": CONDITION}, literal(Decimal("0.5")), literal(1)),
    )
    _, result = evaluate(
        publication(expression, document_kind="rate_amount"),
        {CONDITION: condition(applies), "Rider.insured_amount": input_value(60)},
    )
    assert result.amount == expected
    assert result.steps[0].unit == "NUMBER"
    assert result.steps[0].operands[0].value is applies
