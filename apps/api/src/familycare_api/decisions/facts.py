"""Normalization helpers for structured decision facts.

Normalization is intentionally conservative: it converts representations (for
example an ISO date or decimal string) but never fills an omitted value,
upgrades confirmation, or derives a medical conclusion.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from familycare_api.decisions.domain import FactConfirmation, FactValue


class FactNormalizationError(ValueError):
    """Stable, value-free error for malformed structured input."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


_DATE_FIELDS = frozenset(
    {
        "MedicalEvent.event_date",
        "MedicalEvent.visit_date",
        "PolicyContract.contract_start",
        "PolicyContract.contract_end",
    }
)
_DECIMAL_FIELDS = frozenset({"Rider.insured_amount"})
_INTEGER_FIELDS = frozenset({"MedicalEvent.admission_days", "ClaimHistory.counted_occurrence"})
_KNOWN_FIELDS = frozenset(
    {
        "MedicalEvent.event_date",
        "MedicalEvent.visit_date",
        "MedicalEvent.classification",
        "MedicalEvent.admission_days",
        "PolicyContract.contract_start",
        "PolicyContract.contract_end",
        "Rider.status",
        "Rider.insured_amount",
        "ClaimHistory.counted_occurrence",
    }
)
_CONFIRMATIONS = frozenset({"user", "ai_structured", "unconfirmed", "conflicting"})


def normalize_fact(
    value: object | None,
    *,
    confirmation: FactConfirmation = "unconfirmed",
    evidence_ids: Iterable[UUID | str] = (),
    field: str | None = None,
    evidence_stale: bool = False,
) -> FactValue:
    """Normalize one fact while preserving its explicit trust metadata."""

    if confirmation not in _CONFIRMATIONS:
        raise FactNormalizationError("INVALID_CONFIRMATION")
    if not isinstance(evidence_stale, bool):
        raise FactNormalizationError("INVALID_EVIDENCE_STATE")

    normalized_ids: list[UUID] = []
    for raw_id in evidence_ids:
        if isinstance(raw_id, UUID):
            evidence_id = raw_id
        elif isinstance(raw_id, str):
            try:
                evidence_id = UUID(raw_id)
            except ValueError:
                raise FactNormalizationError("INVALID_EVIDENCE_ID") from None
        else:
            raise FactNormalizationError("INVALID_EVIDENCE_ID")
        if evidence_id.int == 0 or evidence_id in normalized_ids:
            raise FactNormalizationError("INVALID_EVIDENCE_ID")
        normalized_ids.append(evidence_id)

    normalized = _normalize_value(value, field)
    return FactValue(
        value=normalized,
        confirmation=confirmation,
        evidence_ids=tuple(normalized_ids),
        evidence_stale=evidence_stale,
    )


def normalize_facts(
    values: Mapping[str, object | None],
    *,
    confirmations: Mapping[str, FactConfirmation] | None = None,
    evidence_ids: Mapping[str, Iterable[UUID | str]] | None = None,
    stale_fields: Iterable[str] = (),
) -> dict[str, FactValue]:
    """Normalize a qualified fact mapping without adding missing fields."""

    confirmation_map = confirmations or {}
    evidence_map = evidence_ids or {}
    stale = frozenset(stale_fields)
    normalized: dict[str, FactValue] = {}
    for field, value in values.items():
        if not isinstance(field, str) or not field:
            raise FactNormalizationError("INVALID_FIELD_PATH")
        qualified_field = _qualify_short_field(field)
        if qualified_field not in _KNOWN_FIELDS:
            raise FactNormalizationError("INVALID_FIELD_PATH")
        normalized[field] = normalize_fact(
            value,
            confirmation=confirmation_map.get(field, "unconfirmed"),
            evidence_ids=evidence_map.get(field, ()),
            field=qualified_field,
            evidence_stale=field in stale,
        )
    return normalized


def normalize_fact_mapping(
    values: Mapping[str, object | None],
    *,
    confirmation: FactConfirmation = "unconfirmed",
) -> dict[str, FactValue]:
    """Normalize a namespace mapping using one default confirmation level."""

    return {
        key: normalize_fact(value, confirmation=confirmation, field=key)
        for key, value in values.items()
    }


def _normalize_value(value: object | None, field: str | None) -> object | None:
    if value is None or field is None:
        return value
    qualified_field = field if "." in field else _qualify_short_field(field)
    if qualified_field in _DATE_FIELDS:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise FactNormalizationError("INVALID_DATE")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise FactNormalizationError("INVALID_DATE") from None
    if qualified_field in _DECIMAL_FIELDS:
        if isinstance(value, bool):
            raise FactNormalizationError("INVALID_DECIMAL")
        if isinstance(value, Decimal):
            number = value
        elif isinstance(value, int | float | str):
            try:
                number = Decimal(str(value))
            except InvalidOperation, ValueError:
                raise FactNormalizationError("INVALID_DECIMAL") from None
        else:
            raise FactNormalizationError("INVALID_DECIMAL")
        if not number.is_finite() or number < 0:
            raise FactNormalizationError("INVALID_DECIMAL")
        return number
    if qualified_field in _INTEGER_FIELDS and (
        isinstance(value, bool) or not isinstance(value, int) or value < 0
    ):
        raise FactNormalizationError("INVALID_INTEGER")
    return value


def _qualify_short_field(field: str) -> str:
    for prefix in ("MedicalEvent", "PolicyContract", "Rider", "ClaimHistory"):
        candidate = f"{prefix}.{field}"
        if candidate in _KNOWN_FIELDS:
            return candidate
    return field


__all__ = [
    "FactNormalizationError",
    "normalize_fact",
    "normalize_fact_mapping",
    "normalize_facts",
]
