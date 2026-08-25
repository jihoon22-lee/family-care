"""Strict HTTP adapters for manual receipt lines and benefit calculations.

The calculation boundary deliberately keeps wire amounts as decimal strings.  A
JSON number is not accepted for an amount, and response adapters format domain
``Decimal`` values back to strings before Pydantic serializes them.  Household
scope and authoritative calculation inputs are service concerns, so they are
not fields on any request model here.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from familycare_api.decisions.calculations import (
    BenefitCalculationResult,
    CalculationKind,
    CalculationStatus,
    CalculationStep,
    CoverageCategory,
    Money,
    ReceiptCategory,
    ReceiptConfirmation,
    ReceiptLine,
)

_STRICT = ConfigDict(extra="forbid", frozen=True)
_DECIMAL_PATTERN = r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$"
_CURRENCY_PATTERN = r"^[A-Z]{3}$"
_REASON_CODE_PATTERN = r"^[A-Z][A-Z0-9_]{0,63}$"

DecimalString = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=19,
        pattern=_DECIMAL_PATTERN,
        description="A non-negative decimal string with at most 12 integer and 6 fraction digits.",
    ),
]
ReceiptDecimalString = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=15,
        pattern=r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,2})?$",
        description="A non-negative decimal string with at most two fraction digits.",
    ),
]
CurrencyCode = Annotated[StrictStr, Field(pattern=_CURRENCY_PATTERN)]
ReasonCode = Annotated[
    StrictStr,
    Field(min_length=1, max_length=64, pattern=_REASON_CODE_PATTERN),
]
PositiveVersion = Annotated[StrictInt, Field(ge=1)]
StepNumber = Annotated[StrictInt, Field(ge=1, le=64)]
BoundedText = Annotated[StrictStr, Field(min_length=1, max_length=64)]
Operation = Annotated[
    StrictStr,
    Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9_]*$"),
]
VersionString = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
RateString = Annotated[
    StrictStr,
    Field(
        min_length=1,
        max_length=8,
        pattern=r"^(?:0(?:\.[0-9]{1,6})?|1(?:\.0{1,6})?)$",
    ),
]
RoundingRule = Literal["half_up", "half_even", "up", "down"]


def _wire_decimal(value: Decimal | int) -> str:
    """Return a fixed-point string without accepting binary floating point."""

    if isinstance(value, bool) or not isinstance(value, Decimal | int):
        raise TypeError("amount must be Decimal or int")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("amount must be finite")
    return format(Decimal(value), "f")


def _money_payload(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if isinstance(value, Money):
        return {"amount": _wire_decimal(value.amount), "currency": value.currency}
    if not isinstance(value, Mapping):
        raise TypeError("money must be a Money value or mapping")
    payload = dict(value)
    raw_amount = payload.get("amount")
    if isinstance(raw_amount, Decimal | int) and not isinstance(raw_amount, bool):
        payload["amount"] = _wire_decimal(raw_amount)
    return payload


class _StrictModel(BaseModel):
    model_config = _STRICT


class MoneyResponse(_StrictModel):
    """One amount/currency pair in a calculation response."""

    amount: DecimalString
    currency: CurrencyCode

    @classmethod
    def from_domain(cls, value: Money) -> Self:
        return cls(amount=_wire_decimal(value.amount), currency=value.currency)

    @classmethod
    def from_value(cls, value: Money | Mapping[str, object]) -> Self:
        payload = _money_payload(value)
        if payload is None:  # pragma: no cover - type signature excludes None.
            raise ValueError("money is required")
        return cls.model_validate(payload)


class ReceiptLineCreateRequest(_StrictModel):
    """User-entered receipt metadata; files and calculation authority are absent."""

    category: ReceiptCategory
    coverage_category: CoverageCategory
    amount: ReceiptDecimalString
    currency: CurrencyCode
    confirmation_level: ReceiptConfirmation
    note_code: ReasonCode | None = None

    def to_domain(self, *, line_id: UUID) -> ReceiptLine:
        return ReceiptLine(
            line_id=line_id,
            category=self.category,
            coverage_category=self.coverage_category,
            amount=Money(Decimal(self.amount), self.currency),
            confirmation_level=self.confirmation_level,
            note_code=self.note_code,
        )


class ReceiptLineUpdateRequest(_StrictModel):
    """Versioned receipt metadata update; omitted fields remain unchanged."""

    expected_version: PositiveVersion
    category: ReceiptCategory | None = None
    coverage_category: CoverageCategory | None = None
    amount: ReceiptDecimalString | None = None
    currency: CurrencyCode | None = None
    confirmation_level: ReceiptConfirmation | None = None
    note_code: ReasonCode | None = None

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.model_fields_set <= {"expected_version"}:
            raise ValueError("at least one editable field is required")
        return self

    def editable_values(self) -> dict[str, object]:
        return self.model_dump(exclude={"expected_version"}, exclude_unset=True)


class ReceiptLineDeleteRequest(_StrictModel):
    """Versioned soft-delete request."""

    expected_version: PositiveVersion


class ReceiptLineResponse(_StrictModel):
    """Safe receipt projection with no household scope or source-document fields."""

    id: UUID
    category: ReceiptCategory
    coverage_category: CoverageCategory
    amount: ReceiptDecimalString
    currency: CurrencyCode
    confirmation_level: ReceiptConfirmation
    note_code: ReasonCode | None = None
    version: PositiveVersion
    deleted: bool = False

    @property
    def line_id(self) -> UUID:
        """Domain-compatible name without adding a second wire field."""

        return self.id

    @classmethod
    def from_domain(cls, value: ReceiptLine) -> Self:
        return cls(
            id=value.line_id,
            category=value.category,
            coverage_category=value.coverage_category,
            amount=_wire_decimal(value.amount.amount),
            currency=value.amount.currency,
            confirmation_level=value.confirmation_level,
            note_code=value.note_code,
            version=value.version,
        )

    @classmethod
    def from_value(cls, value: ReceiptLine | Mapping[str, object]) -> Self:
        if isinstance(value, ReceiptLine):
            return cls.from_domain(value)
        payload = dict(value)
        if "id" not in payload and "line_id" in payload:
            payload["id"] = payload.pop("line_id")
        raw_amount = payload.get("amount")
        if isinstance(raw_amount, Money):
            payload["amount"] = _wire_decimal(raw_amount.amount)
            payload.setdefault("currency", raw_amount.currency)
        elif isinstance(raw_amount, Decimal | int) and not isinstance(raw_amount, bool):
            payload["amount"] = _wire_decimal(raw_amount)
        return cls.model_validate(payload)


class CalculationStepResponse(_StrictModel):
    """Bounded arithmetic trace entry; only normalized values are exposed."""

    step_number: StepNumber
    operation: Operation
    input_amount: MoneyResponse | None
    output_amount: MoneyResponse | None
    rounding_rule: RoundingRule | None
    reason_code: ReasonCode

    @classmethod
    def from_domain(cls, value: CalculationStep) -> Self:
        return cls(
            step_number=value.step_number,
            operation=value.operation,
            input_amount=(
                MoneyResponse.from_domain(value.input_amount)
                if value.input_amount is not None
                else None
            ),
            output_amount=(
                MoneyResponse.from_domain(value.output_amount)
                if value.output_amount is not None
                else None
            ),
            rounding_rule=cast(RoundingRule | None, value.rounding_rule),
            reason_code=value.reason_code,
        )

    @classmethod
    def from_value(cls, value: CalculationStep | Mapping[str, object]) -> Self:
        if isinstance(value, CalculationStep):
            return cls.from_domain(value)
        payload = dict(value)
        payload["input_amount"] = _money_payload(payload.get("input_amount"))
        payload["output_amount"] = _money_payload(payload.get("output_amount"))
        return cls.model_validate(payload)


class BenefitCalculationResponse(_StrictModel):
    """Calculation result projection with conditional amounts and trace reasons."""

    schema_version: Literal["1"]
    kind: CalculationKind
    status: CalculationStatus
    calculation_id: UUID | None
    claim_candidate_id: UUID | None
    rule_version_id: UUID
    currency: CurrencyCode | None
    confirmed: MoneyResponse | None
    additional: MoneyResponse | None
    excluded: MoneyResponse | None
    deductible: MoneyResponse | None
    applied_rate: RateString | None
    applied_limit: MoneyResponse | None
    rounding_rule: RoundingRule | None
    engine_version: VersionString
    version: PositiveVersion | None
    created_at: datetime | None
    steps: tuple[CalculationStepResponse, ...] = Field(max_length=64)
    hold_reason_codes: tuple[ReasonCode, ...] = Field(max_length=16)
    excluded_reason_codes: tuple[ReasonCode, ...] = Field(max_length=16)
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=16)

    @classmethod
    def from_result(
        cls,
        value: BenefitCalculationResult,
        *,
        rule_version_id: UUID,
        engine_version: str,
        evidence_ids: tuple[UUID, ...],
        calculation_id: UUID | None = None,
        claim_candidate_id: UUID | None = None,
        version: int | None = None,
        created_at: datetime | None = None,
        rounding_rule: RoundingRule | None = None,
        excluded_reason_codes: tuple[str, ...] = (),
    ) -> Self:
        currency = _result_currency(value)
        if rounding_rule is None:
            rounding_rule = cast(
                RoundingRule | None,
                next(
                    (
                        item.rounding_rule
                        for item in reversed(value.steps)
                        if item.rounding_rule is not None
                    ),
                    None,
                ),
            )
        return cls(
            schema_version="1",
            kind=value.kind,
            status=value.status,
            calculation_id=calculation_id,
            claim_candidate_id=claim_candidate_id,
            rule_version_id=rule_version_id,
            currency=currency,
            confirmed=(
                MoneyResponse.from_domain(value.confirmed) if value.confirmed is not None else None
            ),
            additional=(
                MoneyResponse.from_domain(value.additional)
                if value.additional is not None
                else None
            ),
            excluded=(
                MoneyResponse.from_domain(value.excluded) if value.excluded is not None else None
            ),
            deductible=(
                MoneyResponse.from_domain(value.deductible)
                if value.deductible is not None
                else None
            ),
            applied_rate=(
                _wire_decimal(value.applied_rate) if value.applied_rate is not None else None
            ),
            applied_limit=(
                MoneyResponse.from_domain(value.applied_limit)
                if value.applied_limit is not None
                else None
            ),
            rounding_rule=rounding_rule,
            engine_version=engine_version,
            version=version,
            created_at=created_at,
            steps=tuple(CalculationStepResponse.from_domain(item) for item in value.steps),
            hold_reason_codes=value.hold_reason_codes,
            excluded_reason_codes=excluded_reason_codes,
            evidence_ids=evidence_ids,
        )

    @classmethod
    def from_domain(
        cls,
        value: BenefitCalculationResult,
        **metadata: object,
    ) -> Self:
        return cls.from_result(value, **metadata)  # type: ignore[arg-type]

    @classmethod
    def from_value(cls, value: Mapping[str, object]) -> Self:
        payload = dict(value)
        for field_name in (
            "confirmed",
            "additional",
            "excluded",
            "deductible",
            "applied_limit",
        ):
            payload[field_name] = _money_payload(payload.get(field_name))
        raw_rate = payload.get("applied_rate")
        if isinstance(raw_rate, Decimal | int) and not isinstance(raw_rate, bool):
            payload["applied_rate"] = _wire_decimal(raw_rate)
        raw_steps = payload.get("steps")
        if isinstance(raw_steps, (list, tuple)):
            payload["steps"] = tuple(CalculationStepResponse.from_value(item) for item in raw_steps)
        return cls.model_validate(payload)


class BenefitCalculationsResponse(_StrictModel):
    """Versioned collection returned by the calculation read endpoint."""

    schema_version: Literal["1"]
    calculations: tuple[BenefitCalculationResponse, ...] = Field(max_length=64)


def _result_currency(value: BenefitCalculationResult) -> str | None:
    for money in (
        value.confirmed,
        value.additional,
        value.excluded,
        value.deductible,
        value.applied_limit,
    ):
        if money is not None:
            return money.currency
    for step in value.steps:
        for money in (step.input_amount, step.output_amount):
            if money is not None:
                return money.currency
    return None


# Names used by the router/service layer are kept explicit while these aliases
# make the result/read terminology interchangeable at the HTTP boundary.
CalculationResponse = BenefitCalculationResponse
CalculationListResponse = BenefitCalculationsResponse


__all__ = [
    "BenefitCalculationResponse",
    "BenefitCalculationsResponse",
    "BoundedText",
    "CalculationListResponse",
    "CalculationResponse",
    "CalculationStepResponse",
    "CurrencyCode",
    "DecimalString",
    "MoneyResponse",
    "PositiveVersion",
    "ReceiptLineCreateRequest",
    "ReceiptLineDeleteRequest",
    "ReceiptLineResponse",
    "ReceiptLineUpdateRequest",
    "ReasonCode",
    "ReceiptDecimalString",
]
