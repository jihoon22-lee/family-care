"""Pure domain values for deterministic benefit calculations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP, Decimal
from typing import Literal, cast
from uuid import UUID

from familycare_api.clauses.dsl import (
    CompiledCalculation,
    EvidenceIndex,
    RuleValidationError,
    validate_rule_document,
)
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.decisions.domain import ClaimCandidate, FactContext, FactValue

CalculationKind = Literal["fixed", "indemnity"]
CalculationStatus = Literal["computed", "partial", "unknown"]
ReceiptCategory = Literal["outpatient", "inpatient", "pharmacy"]
CoverageCategory = Literal["covered", "possible_excluded", "excluded", "unknown"]
ReceiptConfirmation = Literal["user", "ai_structured", "unconfirmed"]

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_MAX_AMOUNT = Decimal("1000000000000")
_APPROVED_STATES = frozenset({"AI_VERIFIED", "USER_CONFIRMED"})
_FIXED_RULE_KINDS = frozenset({"fixed_amount", "rate_amount"})
_DECIMAL_ROUNDING = {
    "half_up": ROUND_HALF_UP,
    "half_even": ROUND_HALF_EVEN,
    "up": ROUND_UP,
    "down": ROUND_DOWN,
}


class CalculationValidationError(ValueError):
    """Stable, value-free validation failure at a calculation boundary."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


def _validate_decimal_amount(value: object) -> Decimal:
    if not isinstance(value, Decimal):
        raise CalculationValidationError("AMOUNT_NOT_DECIMAL")
    if not value.is_finite() or value < 0:
        raise CalculationValidationError("INVALID_AMOUNT")
    exponent = cast(int, value.as_tuple().exponent)
    if exponent < -6:
        raise CalculationValidationError("AMOUNT_SCALE_EXCEEDED")
    if value >= _MAX_AMOUNT:
        raise CalculationValidationError("AMOUNT_PRECISION_EXCEEDED")
    return value


def _validate_currency(value: object) -> str:
    if not isinstance(value, str) or _CURRENCY_PATTERN.fullmatch(value) is None:
        raise CalculationValidationError("INVALID_CURRENCY")
    return value


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        _validate_decimal_amount(self.amount)
        _validate_currency(self.currency)


@dataclass(frozen=True)
class ReceiptLine:
    line_id: UUID
    category: ReceiptCategory
    coverage_category: CoverageCategory
    amount: Money
    confirmation_level: ReceiptConfirmation
    note_code: str | None = None
    version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.line_id, UUID) or self.line_id.int == 0:
            raise CalculationValidationError("INVALID_RECEIPT_LINE_ID")
        if isinstance(self.version, bool) or self.version < 1:
            raise CalculationValidationError("INVALID_VERSION")
        if self.note_code is not None and (
            not isinstance(self.note_code, str)
            or _REASON_CODE_PATTERN.fullmatch(self.note_code) is None
        ):
            raise CalculationValidationError("INVALID_NOTE_CODE")


@dataclass(frozen=True)
class CalculationStep:
    step_number: int
    operation: str
    input_amount: Money | None
    output_amount: Money | None
    rounding_rule: str | None
    reason_code: str

    def __post_init__(self) -> None:
        if isinstance(self.step_number, bool) or self.step_number < 1:
            raise CalculationValidationError("INVALID_STEP_NUMBER")
        if not self.operation or len(self.operation) > 32:
            raise CalculationValidationError("INVALID_OPERATION")
        if _REASON_CODE_PATTERN.fullmatch(self.reason_code) is None:
            raise CalculationValidationError("INVALID_REASON_CODE")


@dataclass(frozen=True)
class BenefitCalculationResult:
    kind: CalculationKind
    status: CalculationStatus
    confirmed: Money | None = None
    additional: Money | None = None
    excluded: Money | None = None
    deductible: Money | None = None
    applied_rate: Decimal | None = None
    applied_limit: Money | None = None
    steps: tuple[CalculationStep, ...] = ()
    hold_reason_codes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {"fixed", "indemnity"}:
            raise CalculationValidationError("INVALID_CALCULATION_KIND")
        if self.status not in {"computed", "partial", "unknown"}:
            raise CalculationValidationError("INVALID_CALCULATION_STATUS")
        if self.applied_rate is not None and (
            not isinstance(self.applied_rate, Decimal)
            or not self.applied_rate.is_finite()
            or self.applied_rate < 0
        ):
            raise CalculationValidationError("INVALID_RATE")
        if any(_REASON_CODE_PATTERN.fullmatch(item) is None for item in self.hold_reason_codes):
            raise CalculationValidationError("INVALID_HOLD_REASON")

    @classmethod
    def unknown(cls, kind: CalculationKind, reason_code: str) -> BenefitCalculationResult:
        return cls(kind=kind, status="unknown", hold_reason_codes=(reason_code,))


def calculate_fixed_benefit(
    candidate: ClaimCandidate,
    rule: CoverageRuleVersion,
    facts: FactContext,
) -> BenefitCalculationResult:
    """Evaluate one approved fixed formula and retain every arithmetic step."""

    if candidate.aggregate_result != "MATCH":
        return BenefitCalculationResult.unknown("fixed", "CANDIDATE_NOT_MATCHED")
    if (
        not rule.executable
        or rule.review_state not in _APPROVED_STATES
        or rule.published_at is None
    ):
        return BenefitCalculationResult.unknown("fixed", "RULE_NOT_EXECUTABLE")
    if rule.rule_kind not in _FIXED_RULE_KINDS:
        return BenefitCalculationResult.unknown("fixed", "UNSUPPORTED_CALCULATION_RULE")
    if any(item.review_state not in _APPROVED_STATES for item in rule.evidence):
        return BenefitCalculationResult.unknown("fixed", "RULE_EVIDENCE_UNAVAILABLE")

    try:
        calculation = _compile_calculation(rule)
        currency = _confirmed_currency(facts)
        state = _CalculationState(currency=currency, facts=facts, reason_prefix="FIXED")
        amount = state.evaluate(calculation)
        confirmed = Money(amount, currency)
    except CalculationValidationError as error:
        reason = (
            "MISSING_FIXED_INPUT"
            if error.reason_code in {"MISSING_CALCULATION_FACT", "UNCONFIRMED_CALCULATION_FACT"}
            else error.reason_code
        )
        return BenefitCalculationResult.unknown("fixed", reason)
    except RuleValidationError:
        return BenefitCalculationResult.unknown("fixed", "UNSUPPORTED_CALCULATION_RULE")

    return BenefitCalculationResult(
        kind="fixed",
        status="computed",
        confirmed=confirmed,
        steps=tuple(state.steps),
    )


def _compile_calculation(rule: CoverageRuleVersion) -> CompiledCalculation:
    evidence_ids = tuple(item.evidence_id for item in rule.evidence)
    validated = validate_rule_document(rule.rule_document, EvidenceIndex(evidence_ids))
    if (
        validated.calculation is None
        or validated.schema_version != rule.schema_version
        or validated.rule_kind != rule.rule_kind
        or validated.required is not rule.required
        or validated.input_field_paths != rule.input_field_paths
        or validated.result_reason_code != rule.result_reason_code
    ):
        raise CalculationValidationError("UNSUPPORTED_CALCULATION_RULE")
    return validated.calculation


def _confirmed_currency(facts: FactContext) -> str:
    fact = facts.get("Rider.currency")
    if fact is None or fact.value is None:
        raise CalculationValidationError("MISSING_CALCULATION_FACT")
    _require_confirmed_fact(fact)
    return _validate_currency(fact.value)


def _require_confirmed_fact(fact: FactValue) -> None:
    if fact.evidence_stale or fact.confirmation not in {"user", "ai_structured"}:
        raise CalculationValidationError("UNCONFIRMED_CALCULATION_FACT")


@dataclass
class _CalculationState:
    currency: str
    facts: FactContext
    reason_prefix: str
    steps: list[CalculationStep] = field(default_factory=list)

    def evaluate(self, node: CompiledCalculation) -> Decimal:
        values = tuple(self._operand(value) for value in node.operands)
        if node.operator == "add":
            output = sum(values, Decimal("0"))
        elif node.operator == "subtract":
            output = values[0]
            for value in values[1:]:
                output -= value
        elif node.operator == "multiply":
            output = Decimal("1")
            for value in values:
                output *= value
        elif node.operator == "min":
            output = min(values)
        elif node.operator == "max":
            output = max(values)
        elif node.operator == "round":
            rounding = _DECIMAL_ROUNDING.get(cast(str, node.rounding))
            if rounding is None:
                raise CalculationValidationError("INVALID_ROUNDING")
            output = values[0].quantize(Decimal("1"), rounding=rounding)
        else:  # pragma: no cover - the DSL compiler owns this closed set.
            raise CalculationValidationError("UNSUPPORTED_CALCULATION_RULE")
        result = _validate_decimal_amount(output)
        input_money = Money(values[0], self.currency) if values else None
        output_money = Money(result, self.currency)
        self.steps.append(
            CalculationStep(
                step_number=len(self.steps) + 1,
                operation=node.operator,
                input_amount=input_money,
                output_amount=output_money,
                rounding_rule=node.rounding,
                reason_code=f"{self.reason_prefix}_{node.operator.upper()}",
            )
        )
        return result

    def _operand(self, value: object) -> Decimal:
        if isinstance(value, CompiledCalculation):
            return self.evaluate(value)
        if isinstance(value, str):
            fact = self.facts.get(value)
            if fact is None or fact.value is None:
                raise CalculationValidationError("MISSING_CALCULATION_FACT")
            _require_confirmed_fact(fact)
            return _decimal_fact(fact.value)
        if isinstance(value, Decimal):
            return _validate_decimal_amount(value)
        raise CalculationValidationError("UNSUPPORTED_CALCULATION_RULE")


def _decimal_fact(value: object) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise CalculationValidationError("INVALID_AMOUNT")
    return _validate_decimal_amount(Decimal(value))


__all__ = [
    "BenefitCalculationResult",
    "CalculationKind",
    "CalculationStatus",
    "CalculationStep",
    "CalculationValidationError",
    "CoverageCategory",
    "Money",
    "ReceiptCategory",
    "ReceiptConfirmation",
    "ReceiptLine",
    "calculate_fixed_benefit",
]
