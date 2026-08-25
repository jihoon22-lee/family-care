"""Immutable, sanitized domain values for insurer claim tracking."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from typing import Literal, cast
from uuid import UUID

from familycare_api.claims.snapshot import ClaimCaseSnapshot

ClaimStatus = Literal[
    "preparing",
    "submitted",
    "supplementation_requested",
    "paid",
    "partially_paid",
    "denied",
    "closed",
]
ClaimOutcome = Literal["paid", "partially_paid", "denied"]

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_RECEIPT_NUMBER_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,159}$")
_MAX_NUMERIC_AMOUNT = Decimal("10000000000000000")


def _require_uuid(value: object, label: str) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{label} must be a non-zero UUID")
    return value


def _require_bounded_text(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or not value.strip():
        raise ValueError(f"{label} must be a non-empty bounded string")
    return value


def _optional_bounded_text(value: object | None, label: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _require_bounded_text(value, label, maximum)


def _validate_receipt_number(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _RECEIPT_NUMBER_PATTERN.fullmatch(value) is None:
        raise ValueError("receipt number must be a bounded identifier")
    return value


def _require_aware_datetime(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _validate_amount(value: object | None, label: str) -> Decimal | None:
    if value is None:
        return None
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < 0
        or value >= _MAX_NUMERIC_AMOUNT
        or -cast(int, value.as_tuple().exponent) > 2
    ):
        raise ValueError(f"{label} must be a non-negative decimal with at most two places")
    return value


def _validate_currency(value: object | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _CURRENCY_PATTERN.fullmatch(value) is None:
        raise ValueError("currency must be a three-letter uppercase code")
    return value


def _validate_reason_code(value: object | None, label: str = "reason code") -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _REASON_CODE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be an uppercase reason code")
    return value


def _validate_version(value: object, label: str = "version") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{label} must be a positive integer")
    return value


def _freeze_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be a string-keyed mapping")
    return MappingProxyType(dict(value))


def _validate_status(value: object) -> ClaimStatus:
    from familycare_api.claims.state_machine import CLAIM_STATUSES

    if value not in CLAIM_STATUSES:
        raise ValueError("unsupported claim status")
    return value


def _validate_outcome(value: object) -> ClaimOutcome:
    if value not in {"paid", "partially_paid", "denied"}:
        raise ValueError("unsupported claim outcome")
    return value


@dataclass(frozen=True)
class ClaimCase:
    """A household-scoped claim preparation record, never an insurer submission."""

    id: UUID
    household_space_id: UUID
    medical_event_id: UUID
    family_member_id: UUID
    policy_contract_id: UUID
    rider_id: UUID
    insurer_key: str
    status: ClaimStatus = "preparing"
    receipt_number: str | None = None
    submitted_at: datetime | None = None
    claimed_amount: Decimal | None = None
    paid_amount: Decimal | None = None
    currency: str | None = None
    outcome_reason_code: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted_at: datetime | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.id, "claim id"),
            (self.household_space_id, "household scope"),
            (self.medical_event_id, "medical event"),
            (self.family_member_id, "family member"),
            (self.policy_contract_id, "policy contract"),
            (self.rider_id, "rider"),
        ):
            _require_uuid(value, label)
        _require_bounded_text(self.insurer_key, "insurer key", 160)
        _validate_status(self.status)
        _validate_receipt_number(self.receipt_number)
        _validate_amount(self.claimed_amount, "claimed amount")
        _validate_amount(self.paid_amount, "paid amount")
        _validate_currency(self.currency)
        _validate_reason_code(self.outcome_reason_code, "outcome reason code")
        _validate_version(self.version)


@dataclass(frozen=True)
class ClaimChecklistItem:
    """Metadata for a required document; it intentionally has no file field."""

    id: UUID
    claim_case_id: UUID
    document_kind: str
    requirement_code: str
    required: bool
    conditional: bool
    prepared: bool = False
    note_code: str | None = None
    source_rule_version_id: UUID | None = None
    source_evidence_id: UUID | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, "checklist item")
        _require_uuid(self.claim_case_id, "claim case")
        _require_bounded_text(self.document_kind, "document kind", 64)
        _require_bounded_text(self.requirement_code, "requirement code", 64)
        if not isinstance(self.required, bool) or not isinstance(self.conditional, bool):
            raise ValueError("checklist flags must be boolean")
        if not isinstance(self.prepared, bool):
            raise ValueError("prepared must be boolean")
        _validate_reason_code(self.note_code, "note code")
        for value, label in (
            (self.source_rule_version_id, "source rule version"),
            (self.source_evidence_id, "source Evidence"),
        ):
            if value is not None:
                _require_uuid(value, label)
        _validate_version(self.version)


@dataclass(frozen=True)
class ClaimStatusEvent:
    """Append-only status transition metadata."""

    id: UUID
    claim_case_id: UUID
    from_status: ClaimStatus | None
    to_status: ClaimStatus
    occurred_at: datetime
    reason_code: str | None = None
    metadata: Mapping[str, object] = MappingProxyType({})
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.id, "status event")
        _require_uuid(self.claim_case_id, "claim case")
        if self.from_status is not None:
            _validate_status(self.from_status)
        _validate_status(self.to_status)
        _require_aware_datetime(self.occurred_at, "occurred at")
        _validate_reason_code(self.reason_code)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata, "status metadata"))


@dataclass(frozen=True)
class ClaimHistoryRecord:
    """A manually recorded claim outcome used by future decision projections."""

    id: UUID
    household_space_id: UUID
    medical_event_id: UUID
    family_member_id: UUID
    policy_contract_id: UUID
    rider_id: UUID
    outcome: ClaimOutcome
    payment_date: date | None
    counted_occurrence: bool
    amount: Decimal | None = None
    currency: str | None = None
    reason_code: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        for value, label in (
            (self.id, "history id"),
            (self.household_space_id, "household scope"),
            (self.medical_event_id, "medical event"),
            (self.family_member_id, "family member"),
            (self.policy_contract_id, "policy contract"),
        ):
            _require_uuid(value, label)
        _require_uuid(self.rider_id, "rider")
        _validate_outcome(self.outcome)
        if not isinstance(self.counted_occurrence, bool):
            raise ValueError("counted occurrence must be boolean")
        if self.counted_occurrence is not (self.outcome in {"paid", "partially_paid"}):
            raise ValueError("counted occurrence must agree with outcome")
        _validate_amount(self.amount, "history amount")
        _validate_currency(self.currency)
        _validate_reason_code(self.reason_code)
        has_complete_payment = (
            self.payment_date is not None and self.amount is not None and self.currency is not None
        )
        has_any_payment = any(
            value is not None for value in (self.payment_date, self.amount, self.currency)
        )
        if (self.outcome in {"paid", "partially_paid"} and not has_complete_payment) or (
            self.outcome == "denied" and has_any_payment
        ):
            raise ValueError("payment details must agree with outcome")


ClaimHistory = ClaimHistoryRecord


__all__ = [
    "ClaimCase",
    "ClaimCaseSnapshot",
    "ClaimChecklistItem",
    "ClaimHistory",
    "ClaimHistoryRecord",
    "ClaimOutcome",
    "ClaimStatus",
    "ClaimStatusEvent",
]
