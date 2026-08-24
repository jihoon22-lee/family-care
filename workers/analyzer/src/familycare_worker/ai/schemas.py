"""Strict policy-candidate payload and projection models."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

type CandidateKind = Literal["policy_contract", "policy_party", "rider"]
type CandidateStatus = Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED", "rejected"]
type PipelineClassification = Literal[
    "SUCCESS",
    "NEEDS_REVIEW",
    "RETRYABLE_PROVIDER_ERROR",
    "CONFIGURATION_ERROR",
    "VALIDATION_ERROR",
]
type PolicyCandidateFieldId = Literal[
    "insurer",
    "product_name",
    "contract_start",
    "contract_end",
    "policy_status",
    "rider_name",
    "rider_key",
    "benefit_type",
    "sum_assured",
    "currency",
    "coverage_start",
    "coverage_end",
    "renewable",
    "rider_status",
]
type JsonScalar = str | int | float | bool | None
type IssueCode = Literal[
    "MISSING_EVIDENCE",
    "CONFLICTING_EVIDENCE",
    "TERMS_ONLY_RIDER",
    "UNSUPPORTED_STRUCTURE",
    "LOW_CONFIDENCE",
    "INVALID_UNIT",
    "INVALID_DATE",
    "INVENTED_EVIDENCE",
    "INVENTED_FIELD",
]

_STRICT = ConfigDict(extra="forbid", frozen=True, strict=True)


class CandidateField(BaseModel):
    model_config = _STRICT

    field_id: PolicyCandidateFieldId
    value: JsonScalar
    evidence_ids: tuple[UUID, ...] = Field(max_length=16)


class StructurerCandidate(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    candidate_id: UUID
    candidate_kind: CandidateKind
    fields: tuple[CandidateField, ...] = Field(min_length=1, max_length=32)


class VerifierDecision(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    candidate_id: UUID
    decision: Literal["approved", "needs_review", "rejected"]
    evidence_ids: tuple[UUID, ...] = Field(max_length=64)
    issue_codes: tuple[IssueCode, ...] = Field(max_length=16)


class PolicyCandidate(BaseModel):
    """Sanitized candidate retained after the provider boundary."""

    model_config = _STRICT

    schema_version: Literal["1"] = "1"
    candidate_id: UUID
    candidate_kind: CandidateKind
    status: CandidateStatus
    fields: tuple[CandidateField, ...]
    issue_codes: tuple[IssueCode, ...]
    provider_request_ids: tuple[str, ...] = Field(max_length=2)


class CandidatePipelineResult(BaseModel):
    """One bounded batch outcome with no raw provider or Evidence content."""

    model_config = _STRICT

    schema_version: Literal["1"] = "1"
    classification: PipelineClassification
    candidates: tuple[PolicyCandidate, ...]


def openai_schema_registry() -> dict[str, dict[str, object]]:
    """Return strict JSON Schemas used by the OpenAI adapter."""

    return {
        "policy_candidate_structurer_v1": StructurerCandidate.model_json_schema(),
        "policy_candidate_verifier_v1": VerifierDecision.model_json_schema(),
    }


__all__ = [
    "CandidateField",
    "CandidateKind",
    "CandidatePipelineResult",
    "CandidateStatus",
    "IssueCode",
    "JsonScalar",
    "PipelineClassification",
    "PolicyCandidate",
    "PolicyCandidateFieldId",
    "StructurerCandidate",
    "VerifierDecision",
    "openai_schema_registry",
]
