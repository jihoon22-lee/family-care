"""Strict, data-only validation for versioned CoverageRule documents.

This module deliberately compiles structure into immutable projections only.
It never reads a fact, invokes a callable, resolves a path, or evaluates an
expression/calculation.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal, cast
from uuid import UUID

RULE_SCHEMA_VERSION = "coverage-rule-v1"

RuleKind = Literal[
    "eligibility",
    "classification",
    "temporal",
    "exclusion",
    "frequency",
    "fixed_amount",
    "rate_amount",
    "indemnity_eligibility",
    "deductible",
    "limit",
    "required_document",
]

ExpressionOperator = Literal[
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
]

CalculationOperator = Literal["add", "subtract", "multiply", "min", "max", "round"]

RULE_KINDS = frozenset(
    {
        "eligibility",
        "classification",
        "temporal",
        "exclusion",
        "frequency",
        "fixed_amount",
        "rate_amount",
        "indemnity_eligibility",
        "deductible",
        "limit",
        "required_document",
    }
)
EXPRESSION_OPERATORS = frozenset(
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
CALCULATION_OPERATORS = frozenset({"add", "subtract", "multiply", "min", "max", "round"})
UNIT_REGISTRY = frozenset({"date", "days", "occurrences", "amount", "currency"})
ROUNDING_MODES = frozenset({"half_up", "half_even", "up", "down"})
MAX_RULE_DEPTH = 16
MAX_RULE_NODES = 256
MAX_RULE_ITEMS = 16
FIELD_PATHS = frozenset(
    {
        "MedicalEvent.event_date",
        "MedicalEvent.classification",
        "MedicalEvent.admission_days",
        "PolicyContract.contract_start",
        "PolicyContract.contract_end",
        "Rider.status",
        "Rider.insured_amount",
        "ClaimHistory.counted_occurrence",
        "Receipt.confirmed_amount",
    }
)

RuleValidationReasonCode = Literal[
    "UNKNOWN_RULE_FIELD",
    "UNKNOWN_OPERATOR",
    "UNKNOWN_FIELD_PATH",
    "UNKNOWN_UNIT",
    "INVALID_UNIT",
    "INVALID_RULE_TYPE",
    "INVALID_VALUE",
    "INVALID_ARGUMENTS",
    "INVALID_FIELD_FOR_OPERATOR",
    "INVALID_FIELD_FOR_CALCULATION",
    "INVALID_ROUNDING",
    "MISSING_REQUIRED_FIELD",
    "UNSUPPORTED_CROSS_REFERENCE",
    "ARBITRARY_EXECUTABLE",
    "CONFLICTING_DEFINITION",
    "INVALID_SCHEMA_VERSION",
    "INVALID_RULE_KIND",
    "DUPLICATE_FIELD_PATH",
    "INPUT_FIELD_MISMATCH",
    "MISSING_EVIDENCE",
    "EVIDENCE_NOT_FOUND",
    "DUPLICATE_EVIDENCE",
]


class RuleValidationError(ValueError):
    """Value-free, stable failure for any unsupported rule document shape."""

    reason_code: RuleValidationReasonCode | str

    def __init__(self, reason_code: RuleValidationReasonCode | str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)

    @property
    def code(self) -> RuleValidationReasonCode | str:
        """Alias used by callers that name validation outcomes ``code``."""

        return self.reason_code


@dataclass(frozen=True)
class CompiledExpression:
    operator: ExpressionOperator
    operands: tuple[object, ...]
    referenced_fields: tuple[str, ...]


@dataclass(frozen=True)
class CompiledCalculation:
    operator: CalculationOperator
    operands: tuple[object, ...]
    rounding: str | None
    referenced_fields: tuple[str, ...] = ()


EvidenceId = str | UUID


@dataclass(frozen=True)
class ValidatedRule:
    schema_version: str
    rule_kind: RuleKind
    required: bool
    input_field_paths: tuple[str, ...]
    expression: CompiledExpression | None
    calculation: CompiledCalculation | None
    result_reason_code: str
    evidence_ids: tuple[EvidenceId, ...]

    @property
    def referenced_fields(self) -> tuple[str, ...]:
        if self.expression is not None:
            return self.expression.referenced_fields
        if self.calculation is not None:
            return self.calculation.referenced_fields
        return ()


class EvidenceIndex:
    """Small immutable-ish adapter for the IDs available to a rule candidate.

    The validator also accepts a plain mapping or iterable for callers that
    already have an index.  This adapter exists so application code can pass a
    named contract without introducing a database dependency into this module.
    """

    _ids: frozenset[str]

    def __init__(self, values: Mapping[object, object] | Iterable[object]) -> None:
        raw_ids = values.keys() if isinstance(values, Mapping) else values
        normalized: set[str] = set()
        for value in raw_ids:
            if isinstance(value, str | UUID):
                normalized.add(str(value))
            else:
                raise RuleValidationError("INVALID_RULE_TYPE")
        self._ids = frozenset(normalized)

    @property
    def ids(self) -> frozenset[str]:
        return self._ids

    def __contains__(self, value: object) -> bool:
        return isinstance(value, str | UUID) and str(value) in self._ids


@dataclass(frozen=True)
class _FieldSpec:
    kind: Literal["date", "string", "integer", "decimal"]
    units: frozenset[str]


_FIELD_REGISTRY: dict[str, _FieldSpec] = {
    "MedicalEvent.event_date": _FieldSpec("date", frozenset({"date"})),
    "MedicalEvent.classification": _FieldSpec("string", frozenset()),
    "MedicalEvent.admission_days": _FieldSpec("integer", frozenset({"days"})),
    "PolicyContract.contract_start": _FieldSpec("date", frozenset({"date"})),
    "PolicyContract.contract_end": _FieldSpec("date", frozenset({"date"})),
    "Rider.status": _FieldSpec("string", frozenset()),
    "Rider.insured_amount": _FieldSpec("decimal", frozenset({"amount", "currency"})),
    "ClaimHistory.counted_occurrence": _FieldSpec("integer", frozenset({"occurrences"})),
    "Receipt.confirmed_amount": _FieldSpec("decimal", frozenset({"amount", "currency"})),
}

_RIDER_STATUSES = frozenset({"active", "inactive", "expired", "cancelled", "unknown"})
_EXPRESSION_NODE_KEYS = frozenset({"op", "args", "field", "value", "unit"})
_EXECUTABLE_PATTERN = re.compile(
    r"(?:__|\b(?:eval|exec|import|lambda|compile|open|globals|locals|builtins|subprocess)\b"
    r"|javascript\s*:|\bos\s*\.\s*system\b|=>|[;{}])",
    re.IGNORECASE,
)
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


def _reject_executable(value: object) -> None:
    """Reject executable-looking data recursively without executing it."""

    if callable(value):
        raise RuleValidationError("ARBITRARY_EXECUTABLE")
    if isinstance(value, str):
        if _EXECUTABLE_PATTERN.search(value):
            raise RuleValidationError("ARBITRARY_EXECUTABLE")
        return
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise RuleValidationError("INVALID_RULE_TYPE")
            if key.startswith("__"):
                raise RuleValidationError("ARBITRARY_EXECUTABLE")
            _reject_executable(nested)
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for nested in value:
            _reject_executable(nested)


def _mapping(value: object, *, executable_string: bool = False) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        if executable_string:
            _reject_executable(value)
        raise RuleValidationError("INVALID_RULE_TYPE")
    if any(not isinstance(key, str) for key in value):
        raise RuleValidationError("INVALID_RULE_TYPE")
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> tuple[object, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        raise RuleValidationError("INVALID_ARGUMENTS")
    return tuple(value)


def _require_keys(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
) -> None:
    unknown = set(value) - allowed
    if unknown:
        raise RuleValidationError("UNKNOWN_RULE_FIELD")
    if not required <= set(value):
        raise RuleValidationError("MISSING_REQUIRED_FIELD")


def _field_spec(path: object) -> tuple[str, _FieldSpec]:
    if not isinstance(path, str):
        raise RuleValidationError("INVALID_RULE_TYPE")
    spec = _FIELD_REGISTRY.get(path)
    if spec is None:
        raise RuleValidationError("UNKNOWN_FIELD_PATH")
    return path, spec


def validate_field_path(path: str) -> None:
    """Validate one explicit, non-dynamic field path."""

    _field_spec(path)


def _is_field_reference(value: object) -> bool:
    return isinstance(value, str) and value in _FIELD_REGISTRY


def _numeric(value: object) -> Decimal:
    _reject_executable(value)
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal):
        raise RuleValidationError("INVALID_RULE_TYPE")
    if isinstance(value, float) and not math.isfinite(value):
        raise RuleValidationError("INVALID_VALUE")
    try:
        number = Decimal(str(value))
    except InvalidOperation, ValueError:
        raise RuleValidationError("INVALID_VALUE") from None
    if not number.is_finite():
        raise RuleValidationError("INVALID_VALUE")
    return number


def _date_value(value: object) -> str:
    _reject_executable(value)
    if _is_field_reference(value):
        raise RuleValidationError("UNSUPPORTED_CROSS_REFERENCE")
    if isinstance(value, Mapping) and "field" in value:
        raise RuleValidationError("UNSUPPORTED_CROSS_REFERENCE")
    if not isinstance(value, str):
        raise RuleValidationError("INVALID_RULE_TYPE")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise RuleValidationError("INVALID_VALUE") from None
    return value


def _literal_for_field(field: str, spec: _FieldSpec, value: object) -> object:
    _reject_executable(value)
    if _is_field_reference(value):
        raise RuleValidationError("UNSUPPORTED_CROSS_REFERENCE")
    if isinstance(value, Mapping | Sequence) and not isinstance(value, str | bytes | bytearray):
        raise RuleValidationError("INVALID_RULE_TYPE")
    if spec.kind == "date":
        return _date_value(value)
    if spec.kind == "string":
        if not isinstance(value, str) or not value:
            raise RuleValidationError("INVALID_RULE_TYPE")
        if field == "Rider.status" and value not in _RIDER_STATUSES:
            raise RuleValidationError("INVALID_VALUE")
        return value
    if spec.kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise RuleValidationError("INVALID_VALUE")
        return value
    number = _numeric(value)
    if number < 0:
        raise RuleValidationError("INVALID_VALUE")
    return number


def _unit(value: object, expected: frozenset[str]) -> str:
    if not isinstance(value, str):
        raise RuleValidationError("INVALID_RULE_TYPE")
    if value not in UNIT_REGISTRY:
        raise RuleValidationError("UNKNOWN_UNIT")
    if value not in expected:
        raise RuleValidationError("INVALID_UNIT")
    return value


def _range_bounds(
    field: str,
    spec: _FieldSpec,
    value: object,
    *,
    date_between: bool = False,
) -> tuple[object, object]:
    bounds = _mapping(value)
    expected_keys = frozenset({"start", "end"}) if date_between else frozenset({"min", "max"})
    _require_keys(bounds, required=expected_keys, allowed=expected_keys)
    first_key, second_key = ("start", "end") if date_between else ("min", "max")
    first = (
        _date_value(bounds[first_key])
        if date_between
        else _literal_for_field(field, spec, bounds[first_key])
    )
    second = (
        _date_value(bounds[second_key])
        if date_between
        else _literal_for_field(field, spec, bounds[second_key])
    )
    if isinstance(first, str) and isinstance(second, str):
        reversed_bounds = first > second
    elif (
        isinstance(first, int | Decimal)
        and not isinstance(first, bool)
        and isinstance(second, int | Decimal)
        and not isinstance(second, bool)
    ):
        reversed_bounds = Decimal(first) > Decimal(second)
    else:
        raise RuleValidationError("INVALID_VALUE")
    if reversed_bounds:
        raise RuleValidationError("CONFLICTING_DEFINITION")
    return first, second


def _expected_unit(spec: _FieldSpec) -> frozenset[str]:
    return spec.units


def _collect_fields(expressions: Sequence[CompiledExpression]) -> tuple[str, ...]:
    result: list[str] = []
    for expression in expressions:
        for field in expression.referenced_fields:
            if field not in result:
                result.append(field)
    return tuple(result)


def _expression_definitions(expression: CompiledExpression) -> tuple[tuple[str, object], ...]:
    if expression.operator == "equals":
        field, value = expression.operands
        return ((cast(str, field), value),)
    if expression.operator == "all":
        definitions: list[tuple[str, object]] = []
        for operand in expression.operands:
            if isinstance(operand, CompiledExpression):
                definitions.extend(_expression_definitions(operand))
        return tuple(definitions)
    return ()


def _check_conflicting_definitions(expressions: Sequence[CompiledExpression]) -> None:
    definitions: dict[str, object] = {}
    for expression in expressions:
        for field, value in _expression_definitions(expression):
            if field in definitions and definitions[field] != value:
                raise RuleValidationError("CONFLICTING_DEFINITION")
            definitions[field] = value


def _expression_keys(
    value: Mapping[str, object],
    *,
    required: frozenset[str],
    allowed: frozenset[str],
) -> None:
    """Check global node keys first, then reject known-but-inapplicable keys."""

    _require_keys(value, required=required, allowed=_EXPRESSION_NODE_KEYS)
    if set(value) - allowed:
        raise RuleValidationError("INVALID_ARGUMENTS")


def validate_expression(value: Mapping[str, object]) -> CompiledExpression:
    """Validate and compile one bounded, recursive data-only expression node."""

    return _validate_expression(value, depth=0, remaining_nodes=[MAX_RULE_NODES])


def _validate_expression(
    value: Mapping[str, object],
    *,
    depth: int,
    remaining_nodes: list[int],
) -> CompiledExpression:
    if depth > MAX_RULE_DEPTH or remaining_nodes[0] <= 0:
        raise RuleValidationError("INVALID_ARGUMENTS")
    remaining_nodes[0] -= 1

    node = _mapping(value, executable_string=True)
    _require_keys(node, required=frozenset({"op"}), allowed=_EXPRESSION_NODE_KEYS)
    operator_value = node["op"]
    if not isinstance(operator_value, str):
        raise RuleValidationError("INVALID_RULE_TYPE")
    if operator_value not in EXPRESSION_OPERATORS:
        raise RuleValidationError("UNKNOWN_OPERATOR")
    operator = cast(ExpressionOperator, operator_value)

    if operator in {"all", "any"}:
        _expression_keys(
            node,
            required=frozenset({"op", "args"}),
            allowed=frozenset({"op", "args"}),
        )
        raw_args = _sequence(node["args"])
        if not raw_args or len(raw_args) > MAX_RULE_ITEMS:
            raise RuleValidationError("INVALID_ARGUMENTS")
        children = tuple(
            _validate_expression(
                cast(Mapping[str, object], argument),
                depth=depth + 1,
                remaining_nodes=remaining_nodes,
            )
            for argument in raw_args
        )
        if operator == "all":
            _check_conflicting_definitions(children)
        return CompiledExpression(operator, children, _collect_fields(children))

    if operator == "not":
        _expression_keys(
            node,
            required=frozenset({"op", "args"}),
            allowed=frozenset({"op", "args"}),
        )
        raw_args = _sequence(node["args"])
        if len(raw_args) != 1:
            raise RuleValidationError("INVALID_ARGUMENTS")
        child = _validate_expression(
            cast(Mapping[str, object], raw_args[0]),
            depth=depth + 1,
            remaining_nodes=remaining_nodes,
        )
        return CompiledExpression(operator, (child,), child.referenced_fields)

    if operator == "present":
        _expression_keys(
            node,
            required=frozenset({"op", "field"}),
            allowed=frozenset({"op", "field"}),
        )
        field, _ = _field_spec(node["field"])
        return CompiledExpression(operator, (field,), (field,))

    _expression_keys(
        node,
        required=frozenset({"op", "field", "value"}),
        allowed=frozenset({"op", "field", "value", "unit"}),
    )
    field, spec = _field_spec(node["field"])

    if operator in {"equals", "in"}:
        if "unit" in node:
            raise RuleValidationError("INVALID_ARGUMENTS")
        if operator == "equals":
            literal = _literal_for_field(field, spec, node["value"])
            return CompiledExpression(operator, (field, literal), (field,))
        values = _sequence(node["value"])
        if not values or len(values) > MAX_RULE_ITEMS:
            raise RuleValidationError("INVALID_ARGUMENTS")
        literals = tuple(_literal_for_field(field, spec, item) for item in values)
        return CompiledExpression(operator, (field, literals), (field,))

    if operator == "range":
        if "unit" not in node:
            raise RuleValidationError("MISSING_REQUIRED_FIELD")
        unit = _unit(node["unit"], _expected_unit(spec))
        first, second = _range_bounds(field, spec, node["value"])
        return CompiledExpression(operator, (field, first, second, unit), (field,))

    if operator == "date_between":
        if spec.kind != "date":
            raise RuleValidationError("INVALID_FIELD_FOR_OPERATOR")
        if "unit" not in node:
            raise RuleValidationError("MISSING_REQUIRED_FIELD")
        unit = _unit(node["unit"], frozenset({"date"}))
        first, second = _range_bounds(field, spec, node["value"], date_between=True)
        return CompiledExpression(operator, (field, first, second, unit), (field,))

    if operator == "days_since":
        if spec.kind != "date":
            raise RuleValidationError("INVALID_FIELD_FOR_OPERATOR")
        if "unit" not in node:
            raise RuleValidationError("MISSING_REQUIRED_FIELD")
        unit = _unit(node["unit"], frozenset({"days"}))
        threshold = node["value"]
        if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
            raise RuleValidationError("INVALID_VALUE")
        return CompiledExpression(operator, (field, threshold, unit), (field,))

    if spec.kind != "integer" or field != "ClaimHistory.counted_occurrence":
        raise RuleValidationError("INVALID_FIELD_FOR_OPERATOR")
    if "unit" not in node:
        raise RuleValidationError("MISSING_REQUIRED_FIELD")
    unit = _unit(node["unit"], frozenset({"occurrences"}))
    threshold = node["value"]
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise RuleValidationError("INVALID_VALUE")
    return CompiledExpression(operator, (field, threshold, unit), (field,))


def _calculation_operand(
    value: object,
    *,
    depth: int,
    remaining_nodes: list[int],
) -> tuple[object, tuple[str, ...]]:
    operand = _mapping(value, executable_string=True)
    if "op" in operand:
        compiled = _validate_calculation(
            operand,
            depth=depth,
            remaining_nodes=remaining_nodes,
        )
        return compiled, compiled.referenced_fields
    if "field" in operand:
        _require_keys(operand, required=frozenset({"field"}), allowed=frozenset({"field"}))
        field, spec = _field_spec(operand["field"])
        if spec.kind not in {"integer", "decimal"}:
            raise RuleValidationError("INVALID_FIELD_FOR_CALCULATION")
        return field, (field,)
    _require_keys(operand, required=frozenset({"value"}), allowed=frozenset({"value"}))
    return _numeric(operand["value"]), ()


def validate_calculation(value: Mapping[str, object]) -> CompiledCalculation:
    """Validate and compile one bounded, recursive decimal calculation node."""

    return _validate_calculation(value, depth=0, remaining_nodes=[MAX_RULE_NODES])


def _validate_calculation(
    value: Mapping[str, object],
    *,
    depth: int,
    remaining_nodes: list[int],
) -> CompiledCalculation:
    if depth > MAX_RULE_DEPTH or remaining_nodes[0] <= 0:
        raise RuleValidationError("INVALID_ARGUMENTS")
    remaining_nodes[0] -= 1

    node = _mapping(value, executable_string=True)
    _require_keys(
        node,
        required=frozenset({"op", "args"}),
        allowed=frozenset({"op", "args", "rounding"}),
    )
    operator_value = node["op"]
    if not isinstance(operator_value, str):
        raise RuleValidationError("INVALID_RULE_TYPE")
    if operator_value not in CALCULATION_OPERATORS:
        raise RuleValidationError("UNKNOWN_OPERATOR")
    operator = cast(CalculationOperator, operator_value)
    raw_args = _sequence(node["args"])
    if len(raw_args) > MAX_RULE_ITEMS:
        raise RuleValidationError("INVALID_ARGUMENTS")
    if operator == "round":
        if len(raw_args) != 1:
            raise RuleValidationError("INVALID_ARGUMENTS")
        if "rounding" not in node:
            raise RuleValidationError("MISSING_REQUIRED_FIELD")
        rounding_value = node["rounding"]
        if not isinstance(rounding_value, str) or rounding_value not in ROUNDING_MODES:
            raise RuleValidationError("INVALID_ROUNDING")
        rounding: str | None = rounding_value
    else:
        if len(raw_args) < 2 or "rounding" in node:
            raise RuleValidationError("INVALID_ARGUMENTS")
        rounding = None
    compiled_operands: list[object] = []
    referenced_fields: list[str] = []
    for raw_operand in raw_args:
        compiled, fields = _calculation_operand(
            raw_operand,
            depth=depth + 1,
            remaining_nodes=remaining_nodes,
        )
        compiled_operands.append(compiled)
        for field in fields:
            if field not in referenced_fields:
                referenced_fields.append(field)
    return CompiledCalculation(
        operator=operator,
        operands=tuple(compiled_operands),
        rounding=rounding,
        referenced_fields=tuple(referenced_fields),
    )


def _evidence_keys(
    index: EvidenceIndex | Mapping[object, object] | Iterable[object],
) -> frozenset[str]:
    if isinstance(index, EvidenceIndex):
        return index.ids
    try:
        return EvidenceIndex(index).ids
    except TypeError:
        raise RuleValidationError("INVALID_RULE_TYPE") from None


def _evidence_id(value: object) -> EvidenceId:
    if isinstance(value, str | UUID) and str(value):
        return value
    raise RuleValidationError("INVALID_RULE_TYPE")


def validate_rule_document(
    value: Mapping[str, object],
    evidence_index: EvidenceIndex | Mapping[object, object] | Iterable[object],
) -> ValidatedRule:
    """Validate a complete rule document and its referenced Evidence IDs."""

    document = _mapping(value)
    allowed = frozenset(
        {
            "schema_version",
            "rule_kind",
            "required",
            "input_field_paths",
            "expression",
            "calculation",
            "result_reason_code",
            "evidence_ids",
        }
    )
    required = frozenset(
        {
            "schema_version",
            "rule_kind",
            "required",
            "input_field_paths",
            "result_reason_code",
            "evidence_ids",
        }
    )
    _require_keys(document, required=required, allowed=allowed)

    schema_version = document["schema_version"]
    if schema_version != RULE_SCHEMA_VERSION:
        raise RuleValidationError("INVALID_SCHEMA_VERSION")
    rule_kind = document["rule_kind"]
    if not isinstance(rule_kind, str) or rule_kind not in RULE_KINDS:
        raise RuleValidationError("INVALID_RULE_KIND")
    required_value = document["required"]
    if not isinstance(required_value, bool):
        raise RuleValidationError("INVALID_RULE_TYPE")

    raw_input_paths = _sequence(document["input_field_paths"])
    if not raw_input_paths or len(raw_input_paths) > MAX_RULE_ITEMS:
        raise RuleValidationError("MISSING_REQUIRED_FIELD")
    input_paths: list[str] = []
    for raw_path in raw_input_paths:
        path, _ = _field_spec(raw_path)
        if path in input_paths:
            raise RuleValidationError("DUPLICATE_FIELD_PATH")
        input_paths.append(path)

    reason_code = document["result_reason_code"]
    _reject_executable(reason_code)
    if not isinstance(reason_code, str) or _REASON_CODE_PATTERN.fullmatch(reason_code) is None:
        raise RuleValidationError("INVALID_VALUE")

    raw_evidence_ids = _sequence(document["evidence_ids"])
    if not raw_evidence_ids or len(raw_evidence_ids) > MAX_RULE_ITEMS:
        raise RuleValidationError("MISSING_EVIDENCE")
    evidence_ids: list[EvidenceId] = []
    evidence_keys: set[str] = set()
    for raw_evidence_id in raw_evidence_ids:
        evidence_id = _evidence_id(raw_evidence_id)
        evidence_key = str(evidence_id)
        if evidence_key in evidence_keys:
            raise RuleValidationError("DUPLICATE_EVIDENCE")
        evidence_keys.add(evidence_key)
        evidence_ids.append(evidence_id)
    available_evidence = _evidence_keys(evidence_index)
    if any(evidence_id not in available_evidence for evidence_id in evidence_keys):
        raise RuleValidationError("EVIDENCE_NOT_FOUND")

    has_expression = "expression" in document and document["expression"] is not None
    has_calculation = "calculation" in document and document["calculation"] is not None
    if has_expression and has_calculation:
        raise RuleValidationError("CONFLICTING_DEFINITION")
    if not has_expression and not has_calculation:
        raise RuleValidationError("MISSING_REQUIRED_FIELD")

    expression = (
        validate_expression(cast(Mapping[str, object], document["expression"]))
        if has_expression
        else None
    )
    calculation = (
        validate_calculation(cast(Mapping[str, object], document["calculation"]))
        if has_calculation
        else None
    )
    if expression is not None:
        referenced_fields = expression.referenced_fields
    elif calculation is not None:
        referenced_fields = calculation.referenced_fields
    else:
        raise RuleValidationError("MISSING_REQUIRED_FIELD")
    if any(field not in input_paths for field in referenced_fields):
        raise RuleValidationError("INPUT_FIELD_MISMATCH")

    return ValidatedRule(
        schema_version=schema_version,
        rule_kind=cast(RuleKind, rule_kind),
        required=required_value,
        input_field_paths=tuple(input_paths),
        expression=expression,
        calculation=calculation,
        result_reason_code=reason_code,
        evidence_ids=tuple(evidence_ids),
    )


__all__ = [
    "CALCULATION_OPERATORS",
    "CalculationOperator",
    "CompiledCalculation",
    "CompiledExpression",
    "EXPRESSION_OPERATORS",
    "EvidenceIndex",
    "FIELD_PATHS",
    "ROUNDING_MODES",
    "RULE_KINDS",
    "RULE_SCHEMA_VERSION",
    "RuleKind",
    "RuleValidationError",
    "RuleValidationReasonCode",
    "UNIT_REGISTRY",
    "ValidatedRule",
    "validate_calculation",
    "validate_expression",
    "validate_field_path",
    "validate_rule_document",
]
