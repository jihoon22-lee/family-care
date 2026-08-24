"""Strict HTTP models for policy-candidate review."""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from familycare_api.clauses.dsl import (
    CALCULATION_OPERATORS,
    EXPRESSION_OPERATORS,
    FIELD_PATHS,
    RULE_KINDS,
    UNIT_REGISTRY,
)
from familycare_api.contracts.generated_business import (
    CandidateErrorCode,
    CandidateIssueCode,
    CandidateKind,
    CandidateRejectionReason,
    CandidateStatus,
    PolicyCandidateFieldId,
)

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)
type CandidateScalar = str | float | bool | None


class CandidateErrorResponse(BaseModel):
    model_config = _STRICT

    error_code: CandidateErrorCode
    message: str = Field(min_length=1, max_length=160)


class CandidateEvidenceRef(BaseModel):
    model_config = _STRICT

    evidence_id: UUID
    document_version_id: UUID
    document_label: str = Field(min_length=1, max_length=160)
    page: int = Field(ge=1, le=500)
    bbox: tuple[float, float, float, float] | None
    bounded_excerpt: str = Field(min_length=1, max_length=240)

    @field_validator("bbox")
    @classmethod
    def validate_bbox(
        cls,
        value: tuple[float, float, float, float] | None,
    ) -> tuple[float, float, float, float] | None:
        if value is None:
            return None
        x0, y0, x1, y1 = value
        if (
            any(isinstance(item, bool) or not math.isfinite(item) for item in value)
            or x0 < 0
            or y0 < 0
            or x1 <= x0
            or y1 <= y0
            or any(item > 1_000_000 for item in value)
        ):
            raise ValueError("invalid Evidence coordinates")
        return value


class CandidateField(BaseModel):
    model_config = _STRICT

    field_id: PolicyCandidateFieldId
    value: CandidateScalar
    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=16)

    @field_validator("value")
    @classmethod
    def bound_value(cls, value: CandidateScalar) -> CandidateScalar:
        if isinstance(value, str) and len(value) > 240:
            raise ValueError("candidate value is too long")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("candidate number must be finite")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: tuple[UUID, ...]) -> tuple[UUID, ...]:
        if len(value) != len(set(value)):
            raise ValueError("Evidence IDs must be unique")
        return value


class ReviewIssue(BaseModel):
    model_config = _STRICT

    code: CandidateIssueCode
    field_id: PolicyCandidateFieldId | None


class PolicyReviewItem(BaseModel):
    model_config = _STRICT

    review_item_id: UUID
    candidate_version_id: UUID
    aggregate_id: UUID | None
    candidate_kind: CandidateKind
    status: CandidateStatus
    fields: tuple[CandidateField, ...] = Field(min_length=1, max_length=15)
    evidence: tuple[CandidateEvidenceRef, ...] = Field(min_length=1, max_length=64)
    issues: tuple[ReviewIssue, ...] = Field(max_length=8)
    expected_version: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_evidence_references(self) -> Self:
        evidence_ids = [item.evidence_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("Evidence references must be unique")
        known = set(evidence_ids)
        field_evidence_ids = (
            set(field.evidence_ids)
            if isinstance(field, CandidateField)
            else {UUID(str(item)) for item in field.get("evidence_ids", ())}
            for field in self.fields
        )
        if any(not evidence_ids <= known for evidence_ids in field_evidence_ids):
            raise ValueError("candidate field has unknown Evidence")
        return self


class CandidateCorrectionRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)
    field_id: PolicyCandidateFieldId
    value: CandidateScalar
    evidence_id: UUID

    @field_validator("value")
    @classmethod
    def bound_value(cls, value: CandidateScalar) -> CandidateScalar:
        return CandidateField.bound_value(value)

    @model_validator(mode="after")
    def validate_typed_value(self) -> Self:
        try:
            validate_candidate_field_value(self.field_id, self.value)
        except ValueError:
            raise ValueError("invalid candidate value") from None
        return self


class CandidateConfirmationRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)


class CandidateRejectionRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)
    reason_code: CandidateRejectionReason


def validate_candidate_field_value(field_id: PolicyCandidateFieldId, value: Any) -> None:
    """Apply field-specific checks without echoing the rejected value."""

    string_fields = {
        "insurer",
        "product_name",
        "contract_start",
        "contract_end",
        "policy_status",
        "rider_name",
        "rider_key",
        "benefit_type",
        "currency",
        "coverage_start",
        "coverage_end",
        "rider_status",
        "rider_id",
        "terms_edition_id",
        "clause_id",
        "link_review_state",
        "rule_kind",
        "rule_operator",
        "fact_field",
        "unit",
        "date_boundary",
    }
    if field_id in string_fields and (not isinstance(value, str) or not value):
        raise ValueError("invalid candidate value")
    if field_id in {"contract_start", "contract_end", "coverage_start", "coverage_end"}:
        if not isinstance(value, str):
            raise ValueError("invalid candidate value")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("invalid candidate value") from None
    if field_id == "renewable" and not isinstance(value, bool):
        raise ValueError("invalid candidate value")
    if field_id == "sum_assured" and (
        isinstance(value, bool) or not isinstance(value, int | float) or value < 0
    ):
        raise ValueError("invalid candidate value")
    if field_id in {"policy_status", "rider_status"} and value not in {
        "active",
        "inactive",
        "expired",
        "cancelled",
        "unknown",
    }:
        raise ValueError("invalid candidate value")
    if field_id == "benefit_type" and value not in {"fixed", "indemnity"}:
        raise ValueError("invalid candidate value")
    if field_id in {"rider_id", "terms_edition_id", "clause_id"}:
        if not isinstance(value, str):
            raise ValueError("invalid candidate value")
        try:
            parsed = UUID(value)
        except ValueError:
            raise ValueError("invalid candidate value") from None
        if parsed.int == 0:
            raise ValueError("invalid candidate value")
    if field_id == "link_review_state" and value not in {
        "AI_VERIFIED",
        "NEEDS_REVIEW",
        "USER_CONFIRMED",
    }:
        raise ValueError("invalid candidate value")
    if field_id == "rule_kind" and value not in RULE_KINDS:
        raise ValueError("invalid candidate value")
    if field_id == "rule_operator" and value not in {
        *EXPRESSION_OPERATORS,
        *CALCULATION_OPERATORS,
    }:
        raise ValueError("invalid candidate value")
    if field_id == "fact_field" and value not in FIELD_PATHS:
        raise ValueError("invalid candidate value")
    if field_id == "unit" and value not in UNIT_REGISTRY:
        raise ValueError("invalid candidate value")
    if field_id == "decimal_boundary" and (
        isinstance(value, bool) or not isinstance(value, int | float) or value < 0
    ):
        raise ValueError("invalid candidate value")
    if field_id == "date_boundary":
        if not isinstance(value, str):
            raise ValueError("invalid candidate value")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("invalid candidate value") from None
    if field_id == "required" and not isinstance(value, bool):
        raise ValueError("invalid candidate value")
    if field_id == "currency" and (
        not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isupper()
    ):
        raise ValueError("invalid candidate value")


__all__ = [
    "CandidateConfirmationRequest",
    "CandidateCorrectionRequest",
    "CandidateErrorResponse",
    "CandidateEvidenceRef",
    "CandidateField",
    "CandidateRejectionRequest",
    "PolicyReviewItem",
    "ReviewIssue",
    "validate_candidate_field_value",
]
