"""Decision tables for the data-only CoverageRule DSL.

All values in this module are synthetic.  These tests specify the transport
neutral validator boundary; they never evaluate a rule against a fact.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
from familycare_api.clauses.dsl import (
    CALCULATION_OPERATORS,
    EXPRESSION_OPERATORS,
    RULE_SCHEMA_VERSION,
    CompiledCalculation,
    CompiledExpression,
    RuleValidationError,
    validate_calculation,
    validate_expression,
    validate_field_path,
    validate_rule_document,
)

ALLOWED_FIELDS = (
    "MedicalEvent.event_date",
    "MedicalEvent.classification",
    "MedicalEvent.admission_days",
    "PolicyContract.contract_start",
    "PolicyContract.contract_end",
    "Rider.status",
    "Rider.insured_amount",
    "ClaimHistory.counted_occurrence",
)

EVIDENCE_INDEX = {
    "evidence-policy": {
        "document_version_id": "document-policy",
        "physical_page": 1,
    },
    "evidence-terms": {
        "document_version_id": "document-terms",
        "physical_page": 2,
    },
}


def _rule(
    expression: dict[str, object] | None = None,
    *,
    calculation: dict[str, object] | None = None,
    input_field_paths: tuple[str, ...] = (
        "MedicalEvent.event_date",
        "PolicyContract.contract_start",
    ),
    evidence_ids: tuple[str, ...] = ("evidence-policy", "evidence-terms"),
) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": "temporal",
        "required": True,
        "input_field_paths": list(input_field_paths),
        "result_reason_code": "SYNTHETIC_WAITING_PERIOD_SATISFIED",
        "evidence_ids": list(evidence_ids),
    }
    if expression is not None:
        document["expression"] = expression
    if calculation is not None:
        document["calculation"] = calculation
    return document


def _error_code(call: Any, *args: object, **kwargs: object) -> str:
    with pytest.raises(RuleValidationError) as raised:
        call(*args, **kwargs)
    return raised.value.reason_code


@pytest.mark.parametrize("field_path", ALLOWED_FIELDS)
def test_field_registry_accepts_only_each_explicit_supported_path(field_path: str) -> None:
    assert validate_field_path(field_path) is None


@pytest.mark.parametrize(
    "field_path",
    [
        "MedicalEvent",
        "MedicalEvent.unknown",
        "MedicalEvent.event_date.year",
        "MedicalEvent.items[0].event_date",
        "MedicalEvent['event_date']",
        "MedicalEvent.event_date[0]",
        "MedicalEvent.__class__",
        "ClaimHistory.counted_occurrence()",
        "../PolicyContract.contract_start",
        "/synthetic/private/document.pdf",
        "https://synthetic.invalid/field",
        "policy_contract.contract_start",
        "PolicyContract.contract-start",
        "",
    ],
)
def test_field_registry_rejects_unknown_and_dynamic_paths(field_path: str) -> None:
    assert _error_code(validate_field_path, field_path) == "UNKNOWN_FIELD_PATH"


@pytest.mark.parametrize(
    ("operator", "expression", "expected_fields"),
    [
        (
            "all",
            {
                "op": "all",
                "args": [
                    {"op": "present", "field": "MedicalEvent.event_date"},
                    {
                        "op": "equals",
                        "field": "Rider.status",
                        "value": "active",
                    },
                ],
            },
            ("MedicalEvent.event_date", "Rider.status"),
        ),
        (
            "any",
            {
                "op": "any",
                "args": [
                    {"op": "equals", "field": "MedicalEvent.classification", "value": "injury"},
                    {"op": "equals", "field": "MedicalEvent.classification", "value": "illness"},
                ],
            },
            ("MedicalEvent.classification",),
        ),
        (
            "not",
            {
                "op": "not",
                "args": [
                    {"op": "equals", "field": "Rider.status", "value": "cancelled"},
                ],
            },
            ("Rider.status",),
        ),
        (
            "present",
            {"op": "present", "field": "MedicalEvent.event_date"},
            ("MedicalEvent.event_date",),
        ),
        (
            "equals",
            {"op": "equals", "field": "MedicalEvent.classification", "value": "injury"},
            ("MedicalEvent.classification",),
        ),
        (
            "in",
            {
                "op": "in",
                "field": "Rider.status",
                "value": ["active", "unknown"],
            },
            ("Rider.status",),
        ),
        (
            "range",
            {
                "op": "range",
                "field": "MedicalEvent.admission_days",
                "value": {"min": 1, "max": 30},
                "unit": "days",
            },
            ("MedicalEvent.admission_days",),
        ),
        (
            "date_between",
            {
                "op": "date_between",
                "field": "MedicalEvent.event_date",
                "value": {"start": "2026-01-01", "end": "2026-12-31"},
                "unit": "date",
            },
            ("MedicalEvent.event_date",),
        ),
        (
            "days_since",
            {
                "op": "days_since",
                "field": "PolicyContract.contract_start",
                "value": 30,
                "unit": "days",
            },
            ("PolicyContract.contract_start",),
        ),
        (
            "count_before",
            {
                "op": "count_before",
                "field": "ClaimHistory.counted_occurrence",
                "value": 1,
                "unit": "occurrences",
            },
            ("ClaimHistory.counted_occurrence",),
        ),
    ],
)
def test_every_expression_operator_compiles_without_evaluating_facts(
    operator: str,
    expression: dict[str, object],
    expected_fields: tuple[str, ...],
) -> None:
    compiled = validate_expression(expression)

    assert isinstance(compiled, CompiledExpression)
    assert compiled.operator == operator
    assert compiled.referenced_fields == expected_fields
    assert compiled.operands


def test_expression_operator_allowlist_is_exact_and_versioned() -> None:
    assert (
        frozenset(
            {
                "all",
                "any",
                "not",
                "present",
                "equals",
                "in",
                "range",
                "date_between",
                "days_since",
                "count_before",
            }
        )
        == EXPRESSION_OPERATORS
    )


def test_expression_rejects_more_than_sixteen_children() -> None:
    expression = {
        "op": "any",
        "args": [{"op": "present", "field": "MedicalEvent.event_date"} for _ in range(17)],
    }

    assert _error_code(validate_expression, expression) == "INVALID_ARGUMENTS"


def test_expression_rejects_excessive_recursive_depth() -> None:
    expression: dict[str, object] = {
        "op": "present",
        "field": "MedicalEvent.event_date",
    }
    for _ in range(17):
        expression = {"op": "not", "args": [expression]}

    assert _error_code(validate_expression, expression) == "INVALID_ARGUMENTS"


@pytest.mark.parametrize(
    "operator",
    ["python", "eval", "exec", "lookup", "contains", "", "ALL"],
)
def test_unknown_expression_operator_has_stable_reason_code(operator: str) -> None:
    expression = {"op": operator, "field": "Rider.status", "value": "active"}

    assert _error_code(validate_expression, expression) == "UNKNOWN_OPERATOR"


@pytest.mark.parametrize(
    "expression",
    [
        {"field": "Rider.status", "value": "active"},
        {"op": "all", "args": [{"field": "Rider.status", "value": "active"}]},
        {
            "op": "any",
            "args": [{"op": "present", "field": "Rider.status"}, {"field": "Rider.status"}],
        },
    ],
)
def test_every_expression_node_requires_its_own_operator(expression: dict[str, object]) -> None:
    assert _error_code(validate_expression, expression) == "MISSING_REQUIRED_FIELD"


@pytest.mark.parametrize(
    "expression",
    [
        {"op": "present", "field": "Rider.status", "extra": []},
        {"op": "equals", "field": "Rider.status", "value": "active", "extra": 1},
        {
            "op": "range",
            "field": "MedicalEvent.admission_days",
            "value": {"min": 1, "max": 2},
            "unit": "days",
            "code": "x",
        },
        {"op": "all", "args": [], "call": "__import__('os')"},
    ],
)
def test_expression_unknown_keys_are_rejected_without_inspecting_payload_values(
    expression: dict[str, object],
) -> None:
    assert _error_code(validate_expression, expression) == "UNKNOWN_RULE_FIELD"


@pytest.mark.parametrize(
    "expression",
    [
        {"op": "present", "field": "MedicalEvent.event_date", "value": True},
        {"op": "equals", "field": "Rider.status", "value": "active", "unit": "days"},
        {"op": "in", "field": "Rider.status", "value": "active"},
        {"op": "range", "field": "MedicalEvent.admission_days", "value": {"min": 1}},
        {
            "op": "range",
            "field": "MedicalEvent.admission_days",
            "value": {"min": 1, "max": 2},
            "unit": "weeks",
        },
        {
            "op": "date_between",
            "field": "MedicalEvent.classification",
            "value": {"start": "2026-01-01", "end": "2026-01-02"},
            "unit": "date",
        },
        {"op": "days_since", "field": "PolicyContract.contract_start", "value": -1, "unit": "days"},
        {
            "op": "count_before",
            "field": "ClaimHistory.counted_occurrence",
            "value": 1.5,
            "unit": "occurrences",
        },
    ],
)
def test_expression_wrong_shapes_types_and_units_have_stable_reason_codes(
    expression: dict[str, object],
) -> None:
    assert _error_code(validate_expression, expression) in {
        "INVALID_ARGUMENTS",
        "MISSING_REQUIRED_FIELD",
        "INVALID_RULE_TYPE",
        "INVALID_UNIT",
        "UNKNOWN_UNIT",
        "INVALID_VALUE",
        "INVALID_FIELD_FOR_OPERATOR",
    }


@pytest.mark.parametrize(
    "expression",
    [
        {
            "op": "equals",
            "field": "MedicalEvent.event_date",
            "value": "PolicyContract.contract_start",
        },
        {
            "op": "in",
            "field": "MedicalEvent.classification",
            "value": ["MedicalEvent.event_date"],
        },
        {
            "op": "date_between",
            "field": "MedicalEvent.event_date",
            "value": {"start": {"field": "PolicyContract.contract_start"}, "end": "2026-12-31"},
            "unit": "date",
        },
    ],
)
def test_expression_cross_field_references_are_not_an_evaluation_escape_hatch(
    expression: dict[str, object],
) -> None:
    assert _error_code(validate_expression, expression) == "UNSUPPORTED_CROSS_REFERENCE"


@pytest.mark.parametrize(
    "payload",
    [
        "__import__('os').system('synthetic')",
        "eval('synthetic')",
        "lambda: synthetic",
        "javascript:synthetic()",
        {"__class__": "synthetic"},
    ],
)
def test_expression_arbitrary_executable_values_are_rejected(payload: object) -> None:
    expression = {"op": "equals", "field": "MedicalEvent.classification", "value": payload}

    assert _error_code(validate_expression, expression) == "ARBITRARY_EXECUTABLE"


@pytest.mark.parametrize(
    ("calculation", "operator"),
    [
        (
            {"op": "add", "args": [{"value": Decimal("10")}, {"value": Decimal("2")}]},
            "add",
        ),
        (
            {"op": "subtract", "args": [{"value": Decimal("10")}, {"value": Decimal("2")}]},
            "subtract",
        ),
        (
            {"op": "multiply", "args": [{"value": Decimal("10")}, {"value": Decimal("2")}]},
            "multiply",
        ),
        (
            {"op": "min", "args": [{"value": Decimal("10")}, {"field": "Rider.insured_amount"}]},
            "min",
        ),
        (
            {"op": "max", "args": [{"value": Decimal("10")}, {"field": "Rider.insured_amount"}]},
            "max",
        ),
        (
            {"op": "round", "args": [{"value": Decimal("10.25")}], "rounding": "half_up"},
            "round",
        ),
    ],
)
def test_every_calculation_operator_compiles_decimal_operands_without_evaluation(
    calculation: dict[str, object],
    operator: str,
) -> None:
    compiled = validate_calculation(calculation)

    assert isinstance(compiled, CompiledCalculation)
    assert compiled.operator == operator
    assert compiled.operands
    if operator == "round":
        assert compiled.rounding == "half_up"
    else:
        assert compiled.rounding is None


def test_calculation_operator_allowlist_is_exact_and_versioned() -> None:
    assert (
        frozenset({"add", "subtract", "multiply", "min", "max", "round"}) == CALCULATION_OPERATORS
    )


@pytest.mark.parametrize(
    "calculation",
    [
        {"op": "add", "args": [{"value": Decimal("1")}, {"value": Decimal("2")}], "extra": 1},
        {"op": "add", "args": [{"value": Decimal("1")}, {"value": Decimal("2")}], "unit": "days"},
        {"op": "add", "args": [{"value": Decimal("1")}], "rounding": "half_up"},
        {"op": "round", "args": [{"value": Decimal("1")}], "rounding": "python"},
        {"op": "add", "args": [{"value": "1"}, {"value": Decimal("2")}]},
        {"op": "add", "args": [{"field": "MedicalEvent.event_date"}, {"value": Decimal("2")}]},
    ],
)
def test_calculation_rejects_unknown_keys_wrong_arity_rounding_and_operand_types(
    calculation: dict[str, object],
) -> None:
    assert _error_code(validate_calculation, calculation) in {
        "UNKNOWN_RULE_FIELD",
        "INVALID_ARGUMENTS",
        "INVALID_ROUNDING",
        "INVALID_RULE_TYPE",
        "INVALID_FIELD_FOR_CALCULATION",
    }


@pytest.mark.parametrize("operator", ["python", "eval", "exec", "lookup", ""])
def test_unknown_calculation_operator_has_stable_reason_code(operator: str) -> None:
    calculation = {
        "op": operator,
        "args": [{"value": Decimal("1")}, {"value": Decimal("2")}],
    }

    assert _error_code(validate_calculation, calculation) == "UNKNOWN_OPERATOR"


def test_nested_calculations_are_recursive_and_collect_referenced_fields() -> None:
    calculation = {
        "op": "round",
        "args": [
            {
                "op": "multiply",
                "args": [
                    {"field": "Rider.insured_amount"},
                    {"value": Decimal("0.5")},
                ],
            }
        ],
        "rounding": "half_up",
    }

    compiled = validate_calculation(calculation)

    assert compiled.referenced_fields == ("Rider.insured_amount",)
    assert isinstance(compiled.operands[0], CompiledCalculation)


def test_conflicting_definition_in_nested_expressions_is_rejected() -> None:
    expression = {
        "op": "all",
        "args": [
            {"op": "equals", "field": "Rider.status", "value": "active"},
            {"op": "equals", "field": "Rider.status", "value": "cancelled"},
        ],
    }

    assert _error_code(validate_expression, expression) == "CONFLICTING_DEFINITION"


def test_alternative_any_branches_do_not_conflict_with_each_other() -> None:
    expression = {
        "op": "all",
        "args": [
            {
                "op": "any",
                "args": [
                    {"op": "equals", "field": "Rider.status", "value": "active"},
                    {"op": "equals", "field": "Rider.status", "value": "cancelled"},
                ],
            },
            {"op": "present", "field": "MedicalEvent.event_date"},
        ],
    }

    assert validate_expression(expression).referenced_fields == (
        "Rider.status",
        "MedicalEvent.event_date",
    )


@pytest.mark.parametrize(
    "expression",
    [
        {
            "op": "range",
            "field": "MedicalEvent.admission_days",
            "value": {"min": 10, "max": 2},
            "unit": "days",
        },
        {
            "op": "date_between",
            "field": "MedicalEvent.event_date",
            "value": {"start": "2026-12-31", "end": "2026-01-01"},
            "unit": "date",
        },
    ],
)
def test_reversed_bounds_are_stable_conflicting_definitions(expression: dict[str, object]) -> None:
    assert _error_code(validate_expression, expression) == "CONFLICTING_DEFINITION"


def test_valid_rule_document_compiles_expression_and_checks_all_evidence_ids() -> None:
    document = _rule(
        {
            "op": "all",
            "args": [
                {"op": "present", "field": "MedicalEvent.event_date"},
                {
                    "op": "date_between",
                    "field": "MedicalEvent.event_date",
                    "value": {"start": "2026-01-01", "end": "2026-12-31"},
                    "unit": "date",
                },
            ],
        }
    )

    validated = validate_rule_document(document, EVIDENCE_INDEX)

    assert validated.schema_version == RULE_SCHEMA_VERSION
    assert validated.rule_kind == "temporal"
    assert validated.required is True
    assert validated.input_field_paths == (
        "MedicalEvent.event_date",
        "PolicyContract.contract_start",
    )
    assert validated.evidence_ids == ("evidence-policy", "evidence-terms")
    assert isinstance(validated.expression, CompiledExpression)
    assert validated.calculation is None


def test_valid_rule_document_compiles_calculation_instead_of_expression() -> None:
    document = _rule(
        calculation={
            "op": "multiply",
            "args": [
                {"field": "Rider.insured_amount"},
                {"value": Decimal("0.5")},
            ],
        },
        input_field_paths=("Rider.insured_amount",),
    )
    document["rule_kind"] = "rate_amount"

    validated = validate_rule_document(document, EVIDENCE_INDEX)

    assert validated.expression is None
    assert isinstance(validated.calculation, CompiledCalculation)


@pytest.mark.parametrize(
    "mutation",
    [
        {"schema_version": "coverage-rule-v0"},
        {"rule_kind": "unsupported"},
        {"required": "yes"},
        {"input_field_paths": ["MedicalEvent.event_date", "MedicalEvent.event_date"]},
        {"input_field_paths": ["MedicalEvent.unknown"]},
        {"result_reason_code": "eval('synthetic')"},
        {"unknown": "synthetic"},
    ],
)
def test_rule_document_rejects_wrong_metadata_and_unknown_keys_with_stable_codes(
    mutation: dict[str, object],
) -> None:
    document = _rule({"op": "present", "field": "MedicalEvent.event_date"})
    document.update(mutation)

    assert _error_code(validate_rule_document, document, EVIDENCE_INDEX) in {
        "INVALID_SCHEMA_VERSION",
        "INVALID_RULE_KIND",
        "INVALID_RULE_TYPE",
        "DUPLICATE_FIELD_PATH",
        "UNKNOWN_FIELD_PATH",
        "ARBITRARY_EXECUTABLE",
        "UNKNOWN_RULE_FIELD",
    }


@pytest.mark.parametrize(
    ("document_mutation", "expected_reason"),
    [
        ({"expression": None}, "MISSING_REQUIRED_FIELD"),
        (
            {
                "expression": {"op": "present", "field": "MedicalEvent.event_date"},
                "calculation": {
                    "op": "add",
                    "args": [{"value": Decimal("1")}, {"value": Decimal("2")}],
                },
            },
            "CONFLICTING_DEFINITION",
        ),
        ({"evidence_ids": []}, "MISSING_EVIDENCE"),
        ({"evidence_ids": ["evidence-policy", "missing-evidence"]}, "EVIDENCE_NOT_FOUND"),
        ({"evidence_ids": ["evidence-policy", "evidence-policy"]}, "DUPLICATE_EVIDENCE"),
        ({"input_field_paths": ["MedicalEvent.classification"]}, "INPUT_FIELD_MISMATCH"),
    ],
)
def test_rule_document_rejects_missing_conflicting_or_unindexed_parts(
    document_mutation: dict[str, object],
    expected_reason: str,
) -> None:
    document = _rule({"op": "present", "field": "MedicalEvent.event_date"})
    document.update(document_mutation)

    assert _error_code(validate_rule_document, document, EVIDENCE_INDEX) == expected_reason


def test_rule_document_rejects_non_mapping_and_non_data_values() -> None:
    assert _error_code(validate_rule_document, ["synthetic"], EVIDENCE_INDEX) == "INVALID_RULE_TYPE"
    assert _error_code(validate_expression, "eval('synthetic')") == "ARBITRARY_EXECUTABLE"
    assert _error_code(validate_calculation, "__import__('os')") == "ARBITRARY_EXECUTABLE"
