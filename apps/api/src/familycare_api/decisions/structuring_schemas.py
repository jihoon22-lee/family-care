"""Strict HTTP projections for optional MedicalEvent structuring jobs."""

from __future__ import annotations

import re
from datetime import date
from typing import Annotated, Any, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

FactFieldId = Literal[
    "event_date",
    "visit_date",
    "condition_class",
    "diagnosis_label",
    "treatment_kind",
    "admission",
    "outpatient",
    "pharmacy",
    "diagnosis_code",
    "procedure_code",
    "anatomical_site_code",
    "pathology_code",
    "treatment_setting",
    "treatment_context",
    "separately_billed_treatment",
]
_BOOLEAN_FACT_FIELDS = frozenset(
    {"admission", "outpatient", "pharmacy", "separately_billed_treatment"}
)
_DATE_FACT_FIELDS = frozenset({"event_date", "visit_date"})
_NORMALIZED_CODE_FACT_FIELDS = frozenset(
    {
        "diagnosis_code",
        "procedure_code",
        "anatomical_site_code",
        "pathology_code",
        "treatment_setting",
        "treatment_context",
    }
)
_NORMALIZED_CODE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
FactSource = Literal["user", "ai", "system"]
FactState = Literal["confirmed", "ambiguous", "missing", "conflict"]
FactConfidence = Literal["high", "medium", "low"]
FactIssueCode = Literal[
    "INVENTED_FIELD",
    "INVALID_VALUE",
    "INVALID_STATE",
    "DUPLICATE_FIELD",
    "INVENTED_QUESTION",
    "INVENTED_EVIDENCE",
    "UNSUPPORTED_SOURCE",
    "INVALID_CONFIDENCE",
]
StructuringJobState = Literal[
    "queued",
    "running",
    "succeeded",
    "retryable_failed",
    "permanently_failed",
    "cancelled",
]
StructuringErrorCode = Literal[
    "STRUCTURING_AUTHENTICATION_FAILED",
    "STRUCTURING_INVALID_RESPONSE",
    "STRUCTURING_PROVIDER_TIMEOUT",
    "STRUCTURING_RATE_LIMITED",
    "STRUCTURING_UNAVAILABLE",
]


def is_valid_structured_fact_value(field_id: str, value: object | None) -> bool:
    """Validate a bounded fact representation without coercion."""

    if value is None:
        return True
    if field_id in _BOOLEAN_FACT_FIELDS:
        return isinstance(value, bool)
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        return False
    if field_id in _NORMALIZED_CODE_FACT_FIELDS:
        return _NORMALIZED_CODE_PATTERN.fullmatch(value) is not None
    if field_id in _DATE_FACT_FIELDS:
        try:
            date.fromisoformat(value)
        except ValueError:
            return False
    return True


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StructureAcceptedResponse(StrictModel):
    schema_version: Literal["1"] = "1"
    job_id: UUID
    state: StructuringJobState
    status_url: str = Field(pattern=r"^/api/v1/medical-event-structuring-jobs/[0-9a-f-]{36}$")

    @classmethod
    def from_value(cls, value: object) -> Self:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls.model_validate(value)
        job = cast(Any, value)
        job_id = job.id
        return cls(
            job_id=job_id,
            state=job.state,
            status_url=f"/api/v1/medical-event-structuring-jobs/{job_id}",
        )


class StructuredFactResponse(StrictModel):
    fact_id: UUID
    field_id: FactFieldId
    value: Annotated[str, Field(min_length=1, max_length=160)] | bool | None
    source: FactSource
    state: FactState
    confidence: FactConfidence
    evidence_ids: Annotated[list[UUID], Field(max_length=8)]


class OptionalQuestionResponse(StrictModel):
    question_code: FactFieldId
    field_id: FactFieldId


class FactIssueResponse(StrictModel):
    code: FactIssueCode


class StructuringJobResponse(StrictModel):
    schema_version: Literal["1"] = "1"
    job_id: UUID
    state: StructuringJobState
    attempts: Annotated[int, Field(ge=0, le=10)]
    facts: Annotated[list[StructuredFactResponse], Field(max_length=32)]
    questions: Annotated[list[OptionalQuestionResponse], Field(max_length=16)]
    issues: Annotated[list[FactIssueResponse], Field(max_length=16)]
    error_code: StructuringErrorCode | None

    @classmethod
    def from_value(cls, value: object) -> Self:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls.model_validate(value)
        job = cast(Any, value)
        return cls(
            job_id=job.id,
            state=job.state,
            attempts=job.attempts,
            facts=list(job.facts),
            questions=list(job.questions),
            issues=list(job.issues),
            error_code=job.error_code,
        )


__all__ = [
    "FactConfidence",
    "FactFieldId",
    "FactIssueCode",
    "FactIssueResponse",
    "FactSource",
    "FactState",
    "OptionalQuestionResponse",
    "StructureAcceptedResponse",
    "StructuredFactResponse",
    "StructuringErrorCode",
    "StructuringJobResponse",
    "StructuringJobState",
    "is_valid_structured_fact_value",
]
