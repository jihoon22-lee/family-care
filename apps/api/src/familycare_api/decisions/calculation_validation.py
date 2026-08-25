"""Validation and explicit rounding boundaries for benefit calculations."""

from __future__ import annotations

from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    InvalidOperation,
)
from typing import cast

from familycare_api.decisions.calculations import (
    CalculationValidationError,
    Money,
    ReceiptLine,
)

_ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "up": ROUND_UP,
    "down": ROUND_DOWN,
}
_RECEIPT_CATEGORIES = frozenset({"outpatient", "inpatient", "pharmacy"})
_COVERAGE_CATEGORIES = frozenset({"covered", "possible_excluded", "excluded", "unknown"})
_CONFIRMATION_LEVELS = frozenset({"user", "ai_structured", "unconfirmed"})


def decimal_from_wire(value: object) -> Decimal:
    """Parse one non-negative NUMERIC(18,6) value without accepting floats."""

    if not isinstance(value, str):
        raise CalculationValidationError("AMOUNT_NOT_DECIMAL_STRING")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise CalculationValidationError("INVALID_AMOUNT") from None
    if not parsed.is_finite() or parsed < 0:
        raise CalculationValidationError("INVALID_AMOUNT")
    exponent = cast(int, parsed.as_tuple().exponent)
    if exponent < -6:
        raise CalculationValidationError("AMOUNT_SCALE_EXCEEDED")
    if parsed >= Decimal("1000000000000"):
        raise CalculationValidationError("AMOUNT_PRECISION_EXCEEDED")
    return parsed


def validate_receipt_line(line: ReceiptLine) -> None:
    if line.category not in _RECEIPT_CATEGORIES:
        raise CalculationValidationError("INVALID_RECEIPT_CATEGORY")
    if line.coverage_category not in _COVERAGE_CATEGORIES:
        raise CalculationValidationError("INVALID_COVERAGE_CATEGORY")
    if line.confirmation_level not in _CONFIRMATION_LEVELS:
        raise CalculationValidationError("INVALID_CONFIRMATION_LEVEL")


def round_money(value: Money, rounding_rule: str, *, scale: int = 0) -> Money:
    """Round at an explicit rule boundary and preserve the original value."""

    rounding = _ROUNDING.get(rounding_rule)
    if rounding is None:
        raise CalculationValidationError("INVALID_ROUNDING")
    if isinstance(scale, bool) or scale < 0 or scale > 6:
        raise CalculationValidationError("INVALID_ROUNDING_SCALE")
    quantum = Decimal(1).scaleb(-scale)
    try:
        amount = value.amount.quantize(quantum, rounding=rounding)
    except InvalidOperation:
        raise CalculationValidationError("AMOUNT_PRECISION_EXCEEDED") from None
    return Money(amount, value.currency)


__all__ = [
    "CalculationValidationError",
    "decimal_from_wire",
    "round_money",
    "validate_receipt_line",
]
