"""Strict HTTP schemas for structured events and deterministic decisions."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.common.evidence import EvidenceRef
from familycare_api.decisions.domain import (
    ClaimCandidate,
    DecisionRunResult,
    FactConfirmation,
    FactValue,
    MedicalEvent,
    Question,
    RuleEvaluation,
    TriState,
)

FactScalar = str | int | Decimal | date | None
_EVENT_FIELDS = frozenset(
    {
        "MedicalEvent.classification",
        "MedicalEvent.admission_days",
    }
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FactInput(StrictModel):
    value: FactScalar
    confirmation: FactConfirmation


class MedicalEventCreateRequest(StrictModel):
    family_member_id: UUID
    mode: Literal["pre_visit", "post_treatment"]
    event_date: date | None = None
    visit_date: date | None = None
    facts: dict[str, FactInput] = Field(default_factory=dict, max_length=16)

    @model_validator(mode="after")
    def validate_fact_paths(self) -> Self:
        _require_event_fields(self.facts)
        return self


class MedicalEventUpdateRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1)]
    mode: Literal["pre_visit", "post_treatment"] | None = None
    event_date: date | None = None
    visit_date: date | None = None
    facts: dict[str, FactInput] | None = Field(default=None, max_length=16)

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if "mode" in self.model_fields_set and self.mode is None:
            raise ValueError("mode cannot be null")
        if "facts" in self.model_fields_set and self.facts is None:
            raise ValueError("facts cannot be null")
        if self.facts is not None:
            _require_event_fields(self.facts)
        if self.model_fields_set <= {"expected_version"}:
            raise ValueError("empty update")
        return self


class ExpectedVersionRequest(StrictModel):
    expected_version: Annotated[int, Field(ge=1)]


class FactResponse(StrictModel):
    value: FactScalar
    confirmation: FactConfirmation

    @classmethod
    def from_domain(cls, value: FactValue) -> Self:
        return cls(value=cast(FactScalar, value.value), confirmation=value.confirmation)


class MedicalEventResponse(StrictModel):
    id: UUID
    family_member_id: UUID
    mode: Literal["pre_visit", "post_treatment"]
    event_date: date | None
    visit_date: date | None
    facts: dict[str, FactResponse]
    version: int
    deleted: bool

    @classmethod
    def from_value(cls, value: MedicalEvent | dict[str, object]) -> Self:
        if isinstance(value, MedicalEvent):
            return cls(
                id=value.id,
                family_member_id=value.family_member_id,
                mode=value.mode,
                event_date=value.event_date,
                visit_date=value.visit_date,
                facts={key: FactResponse.from_domain(item) for key, item in value.facts.items()},
                version=value.version,
                deleted=value.deleted_at is not None,
            )
        return cls.model_validate(value)


class EvidenceResponse(StrictModel):
    evidence_id: UUID
    document_version_id: UUID
    extraction_id: UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_page: int = Field(ge=1, le=500)
    bbox: tuple[float, float, float, float] | None
    review_state: Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]

    @classmethod
    def from_domain(cls, value: EvidenceRef) -> Self:
        bbox = None if value.bbox is None else tuple(float(item) for item in value.bbox)
        return cls(
            evidence_id=value.evidence_id,
            document_version_id=value.document_version_id,
            extraction_id=value.extraction_id,
            content_sha256=value.content_sha256,
            physical_page=value.physical_page,
            bbox=cast(tuple[float, float, float, float] | None, bbox),
            review_state=value.review_state,
        )


class QuestionResponse(StrictModel):
    field_path: str
    reason_code: str

    @classmethod
    def from_domain(cls, value: Question) -> Self:
        return cls(field_path=value.field_path, reason_code=value.reason_code)


class RuleEvaluationResponse(StrictModel):
    evaluation_id: UUID
    rider_id: UUID
    rule_version_id: UUID
    result: TriState
    required: bool
    reason_code: str
    fact_paths: list[str]
    missing_fields: list[str]
    conflicting_fields: list[str]
    evidence: list[EvidenceResponse]
    engine_version: str

    @classmethod
    def from_domain(cls, value: RuleEvaluation) -> Self:
        if value.id is None:
            raise ValueError("evaluation id is required")
        return cls(
            evaluation_id=value.id,
            rider_id=value.rider_id,
            rule_version_id=value.rule_version_id,
            result=value.result,
            required=value.required,
            reason_code=value.reason_code,
            fact_paths=list(value.fact_paths),
            missing_fields=list(value.missing_fields),
            conflicting_fields=list(value.conflicting_fields),
            evidence=[EvidenceResponse.from_domain(item) for item in value.evidence],
            engine_version=value.evaluator_version,
        )


class ClaimCandidateResponse(StrictModel):
    candidate_id: UUID
    rider_id: UUID
    rider_type: Literal["fixed", "indemnity"]
    aggregate_result: TriState
    required_match_count: int = Field(ge=0)
    required_unknown_count: int = Field(ge=0)
    required_no_match_count: int = Field(ge=0)
    questions: list[QuestionResponse]
    hold_reason_codes: list[str]

    @classmethod
    def from_domain(cls, value: ClaimCandidate) -> Self:
        if value.id is None or value.rider_type not in {"fixed", "indemnity"}:
            raise ValueError("persisted candidate identity and type are required")
        return cls(
            candidate_id=value.id,
            rider_id=value.rider_id,
            rider_type=cast(Literal["fixed", "indemnity"], value.rider_type),
            aggregate_result=value.aggregate_result,
            required_match_count=value.required_match_count,
            required_unknown_count=value.required_unknown_count,
            required_no_match_count=value.required_no_match_count,
            questions=[QuestionResponse.from_domain(item) for item in value.questions],
            hold_reason_codes=list(value.hold_reason_codes),
        )


class CoverageDecisionResponse(StrictModel):
    schema_version: Literal["1"] = "1"
    run_id: UUID
    medical_event_id: UUID
    event_version: int = Field(ge=1)
    engine_version: str
    rule_set_version: str
    policy_snapshot_at: datetime
    stale: bool
    candidates: list[ClaimCandidateResponse]
    evaluations: list[RuleEvaluationResponse]

    @classmethod
    def from_value(cls, value: DecisionRunResult | dict[str, object]) -> Self:
        if isinstance(value, DecisionRunResult):
            return cls(
                run_id=value.run_id,
                medical_event_id=value.medical_event_id,
                event_version=value.event_version,
                engine_version=value.engine_version,
                rule_set_version=value.rule_set_version,
                policy_snapshot_at=value.policy_snapshot_at,
                stale=value.stale,
                candidates=[ClaimCandidateResponse.from_domain(item) for item in value.candidates],
                evaluations=[
                    RuleEvaluationResponse.from_domain(item) for item in value.evaluations
                ],
            )
        return cls.model_validate(value)


class DecisionErrorResponse(StrictModel):
    error_code: str
    message: str


def _require_event_fields(facts: dict[str, FactInput]) -> None:
    if any(field not in _EVENT_FIELDS for field in facts):
        raise ValueError("unsupported fact field")
    classification = facts.get("MedicalEvent.classification")
    if classification is not None and classification.value is not None:
        value = classification.value
        if not isinstance(value, str) or not value.strip() or len(value) > 160:
            raise ValueError("invalid event classification")
    admission_days = facts.get("MedicalEvent.admission_days")
    if admission_days is not None and admission_days.value is not None:
        value = admission_days.value
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 36_500:
            raise ValueError("invalid admission days")


__all__ = [
    "CoverageDecisionResponse",
    "DecisionErrorResponse",
    "ExpectedVersionRequest",
    "FactInput",
    "MedicalEventCreateRequest",
    "MedicalEventResponse",
    "MedicalEventUpdateRequest",
]
