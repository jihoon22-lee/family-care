"""Deterministic execution of the allowlisted clause expression DSL."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import cast
from uuid import UUID

from familycare_api.clauses.dsl import CompiledExpression
from familycare_api.decisions.domain import FactContext, FactValue, OperatorOutcome, TriState


class OperatorEvaluationError(ValueError):
    """Structural runtime error; never a tri-state mismatch."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def compare_required(
    value: FactValue | None,
    expected: object,
    *,
    field: str | None = None,
) -> OperatorOutcome:
    """Compare a confirmed fact and make missing trust explicit as UNKNOWN."""

    availability = _availability(value, field=field)
    if availability is not None:
        return availability
    assert value is not None
    if _same_value(value.value, expected):
        return OperatorOutcome("MATCH", "FACT_EQUALS", evidence_ids=value.evidence_ids)
    return OperatorOutcome(
        "NO_MATCH",
        "DETERMINISTIC_VALUE_MISMATCH",
        evidence_ids=value.evidence_ids,
    )


def evaluate_expression(compiled: CompiledExpression, context: FactContext) -> OperatorOutcome:
    """Evaluate a compiled, data-only expression against normalized facts."""

    operator = compiled.operator
    if operator in {"all", "any"}:
        children = tuple(
            evaluate_expression(cast(CompiledExpression, operand), context)
            for operand in compiled.operands
        )
        return _combine_boolean(operator, children)
    if operator == "not":
        child = evaluate_expression(cast(CompiledExpression, compiled.operands[0]), context)
        if child.result == "MATCH":
            return _copy_outcome(child, result="NO_MATCH", reason_code="NOT_MATCH")
        if child.result == "NO_MATCH":
            return _copy_outcome(child, result="MATCH", reason_code="NOT_NO_MATCH")
        return _copy_outcome(child, reason_code="NOT_UNKNOWN")

    field = cast(str, compiled.operands[0])
    value = context.get(field)
    if operator == "present":
        available = _availability(value, field=field)
        if available is not None:
            return available
        assert value is not None
        return OperatorOutcome("MATCH", "FACT_PRESENT", evidence_ids=value.evidence_ids)

    if operator == "equals":
        return compare_required(value, compiled.operands[1], field=field)
    if operator == "in":
        available = _availability(value, field=field)
        if available is not None:
            return available
        assert value is not None
        expected_values = cast(tuple[object, ...], compiled.operands[1])
        result = any(_same_value(value.value, expected) for expected in expected_values)
        return OperatorOutcome(
            "MATCH" if result else "NO_MATCH",
            "FACT_IN_SET" if result else "DETERMINISTIC_VALUE_MISMATCH",
            evidence_ids=value.evidence_ids,
        )
    if operator == "range":
        return _evaluate_range(field, value, compiled.operands[1:])
    if operator == "date_between":
        return _evaluate_date_between(field, value, compiled.operands[1:])
    if operator == "days_since":
        return _evaluate_days_since(field, value, compiled.operands[1:], context)
    if operator == "count_before":
        return _evaluate_count_before(field, value, compiled.operands[1:])
    raise OperatorEvaluationError("UNKNOWN_OPERATOR")


def _evaluate_range(
    field: str,
    value: FactValue | None,
    operands: Sequence[object],
) -> OperatorOutcome:
    available = _availability(value, field=field)
    if available is not None:
        return available
    assert value is not None
    if len(operands) != 3:
        raise OperatorEvaluationError("INVALID_ARGUMENTS")
    low, high, unit = operands
    if unit not in {"days", "amount", "currency", "occurrences"}:
        raise OperatorEvaluationError("INVALID_UNIT")
    actual = _decimal(value.value)
    lower = _decimal(low)
    upper = _decimal(high)
    if lower > upper:
        raise OperatorEvaluationError("INVALID_RANGE")
    result = lower <= actual <= upper
    return OperatorOutcome(
        "MATCH" if result else "NO_MATCH",
        "VALUE_IN_RANGE" if result else "DETERMINISTIC_VALUE_MISMATCH",
        evidence_ids=value.evidence_ids,
    )


def _evaluate_date_between(
    field: str,
    value: FactValue | None,
    operands: Sequence[object],
) -> OperatorOutcome:
    available = _availability(value, field=field)
    if available is not None:
        return available
    assert value is not None
    if len(operands) != 3 or operands[2] != "date":
        raise OperatorEvaluationError("INVALID_UNIT")
    actual = _date(value.value)
    lower = _date(operands[0])
    upper = _date(operands[1])
    if lower > upper:
        raise OperatorEvaluationError("INVALID_RANGE")
    result = lower <= actual <= upper
    return OperatorOutcome(
        "MATCH" if result else "NO_MATCH",
        "DATE_IN_RANGE" if result else "DETERMINISTIC_DATE_MISMATCH",
        evidence_ids=value.evidence_ids,
    )


def _evaluate_days_since(
    field: str,
    value: FactValue | None,
    operands: Sequence[object],
    context: FactContext,
) -> OperatorOutcome:
    available = _availability(value, field=field)
    if available is not None:
        return available
    assert value is not None
    if len(operands) != 2 or operands[1] != "days":
        raise OperatorEvaluationError("INVALID_UNIT")
    event_date = context.get("MedicalEvent.event_date")
    reference_value = _confirmed_value(event_date, field="MedicalEvent.event_date")
    if reference_value is None:
        return OperatorOutcome(
            "UNKNOWN",
            "MISSING_REFERENCE_DATE",
            missing_fields=("MedicalEvent.event_date",),
        )
    reference = _date(reference_value)
    actual = _date(value.value)
    threshold = operands[0]
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise OperatorEvaluationError("INVALID_DAYS")
    result = (reference - actual).days >= threshold
    return OperatorOutcome(
        "MATCH" if result else "NO_MATCH",
        "DAYS_THRESHOLD_MET" if result else "DAYS_THRESHOLD_NOT_MET",
        evidence_ids=value.evidence_ids,
    )


def _evaluate_count_before(
    field: str,
    value: FactValue | None,
    operands: Sequence[object],
) -> OperatorOutcome:
    available = _availability(value, field=field)
    if available is not None:
        return available
    assert value is not None
    if len(operands) != 2 or operands[1] != "occurrences":
        raise OperatorEvaluationError("INVALID_UNIT")
    threshold = operands[0]
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise OperatorEvaluationError("INVALID_OCCURRENCES")
    count = _history_count(value.value)
    result = count >= threshold
    return OperatorOutcome(
        "MATCH" if result else "NO_MATCH",
        "HISTORY_COUNT_MET" if result else "HISTORY_COUNT_NOT_MET",
        evidence_ids=value.evidence_ids,
    )


def _combine_boolean(
    operator: str,
    children: Sequence[OperatorOutcome],
) -> OperatorOutcome:
    if not children:
        raise OperatorEvaluationError("INVALID_ARGUMENTS")
    missing = _unique_fields(item.missing_fields for item in children)
    conflicting = _unique_fields(item.conflicting_fields for item in children)
    evidence_ids = _unique_ids(item.evidence_ids for item in children)
    if operator == "all":
        if any(item.result == "NO_MATCH" for item in children):
            result: str = "NO_MATCH"
            reason = "ALL_NO_MATCH"
        elif any(item.result == "UNKNOWN" for item in children):
            result = "UNKNOWN"
            reason = "ALL_UNKNOWN"
        else:
            result = "MATCH"
            reason = "ALL_MATCH"
    else:
        if any(item.result == "MATCH" for item in children):
            result = "MATCH"
            reason = "ANY_MATCH"
        elif any(item.result == "UNKNOWN" for item in children):
            result = "UNKNOWN"
            reason = "ANY_UNKNOWN"
        else:
            result = "NO_MATCH"
            reason = "ANY_NO_MATCH"
    return OperatorOutcome(
        cast(TriState, result),
        reason,
        missing_fields=missing,
        conflicting_fields=conflicting,
        evidence_ids=evidence_ids,
    )


def _availability(value: FactValue | None, *, field: str | None = None) -> OperatorOutcome | None:
    field_name = (field,) if field is not None else ()
    if value is None or value.value is None:
        return OperatorOutcome("UNKNOWN", "MISSING_OR_CONFLICTING_FACT", missing_fields=field_name)
    if value.evidence_stale:
        return OperatorOutcome("UNKNOWN", "STALE_EVIDENCE", conflicting_fields=field_name)
    if value.confirmation == "conflicting":
        return OperatorOutcome(
            "UNKNOWN",
            "MISSING_OR_CONFLICTING_FACT",
            conflicting_fields=field_name,
            evidence_ids=value.evidence_ids,
        )
    if value.confirmation not in {"user", "ai_structured"}:
        return OperatorOutcome("UNKNOWN", "UNCONFIRMED_FACT", missing_fields=field_name)
    return None


def _confirmed_value(value: FactValue | None, *, field: str) -> object | None:
    unavailable = _availability(value, field=field)
    if unavailable is not None:
        return None
    assert value is not None
    return value.value


def _same_value(actual: object, expected: object) -> bool:
    if isinstance(actual, date | datetime) or isinstance(expected, date | datetime):
        return _date(actual) == _date(expected)
    if isinstance(actual, int | float | Decimal) and not isinstance(actual, bool):
        return _decimal(actual) == _decimal(expected)
    if isinstance(expected, int | float | Decimal) and not isinstance(expected, bool):
        return _decimal(actual) == _decimal(expected)
    return actual == expected


def _decimal(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, int | float | Decimal | str):
        raise OperatorEvaluationError("INVALID_DECIMAL")
    try:
        number = Decimal(str(value))
    except InvalidOperation, ValueError:
        raise OperatorEvaluationError("INVALID_DECIMAL") from None
    if not number.is_finite():
        raise OperatorEvaluationError("INVALID_DECIMAL")
    return number


def _date(value: object) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise OperatorEvaluationError("INVALID_DATE") from None
    raise OperatorEvaluationError("INVALID_DATE")


def _history_count(value: object) -> int:
    if isinstance(value, bool):
        raise OperatorEvaluationError("INVALID_OCCURRENCES")
    if isinstance(value, int):
        if value < 0:
            raise OperatorEvaluationError("INVALID_OCCURRENCES")
        return value
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        count = 0
        for item in value:
            counted = getattr(item, "counted_occurrence", None)
            if not isinstance(counted, bool):
                raise OperatorEvaluationError("INVALID_HISTORY")
            count += int(counted)
        return count
    raise OperatorEvaluationError("INVALID_OCCURRENCES")


def _unique_fields(groups: Iterable[Sequence[str]]) -> tuple[str, ...]:
    values: list[str] = []
    for group in groups:
        for value in group:
            if value not in values:
                values.append(value)
    return tuple(values)


def _unique_ids(groups: Iterable[Sequence[UUID]]) -> tuple[UUID, ...]:
    values: list[UUID] = []
    for group in groups:
        for value in group:
            if value not in values:
                values.append(value)
    return tuple(values)


def _copy_outcome(
    outcome: OperatorOutcome,
    *,
    result: str | None = None,
    reason_code: str | None = None,
) -> OperatorOutcome:
    return OperatorOutcome(
        cast(TriState, result or outcome.result),
        reason_code or outcome.reason_code,
        outcome.missing_fields,
        outcome.conflicting_fields,
        outcome.evidence_ids,
    )


__all__ = [
    "OperatorEvaluationError",
    "compare_required",
    "evaluate_expression",
]
