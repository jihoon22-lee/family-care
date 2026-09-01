"""Strict public schemas for insurance ledger reconciliation."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.insurance_reconciliation.domain import (
    ContractReconciliation,
    DocumentResolutionHistory,
    MemberInsuranceReconciliation,
    OperationalLinkHistory,
    OperationalLinkProjection,
    OrphanOperationalPolicy,
    UnresolvedDocumentSource,
)

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

TriState = Literal["MATCH", "NO_MATCH", "UNKNOWN"]
LinkReason = Literal[
    "USER_CONFIRMED_SAME_CONTRACT",
    "USER_CONFIRMED_DISTINCT_CONTRACT",
    "USER_REOPENED_OPERATIONAL_REVIEW",
    "USER_REPORTED_OPERATIONAL_CONFLICT",
]
Resolution = Literal["REPLACED", "DISMISSED", "REOPENED"]
ResolutionReason = Literal[
    "USER_CONFIRMED_REPLACEMENT",
    "USER_DISMISSED_STALE_FAILURE",
    "USER_REOPENED_DOCUMENT_REVIEW",
]


class InsuranceReconciliationErrorResponse(BaseModel):
    model_config = _STRICT

    error_code: str
    message: str
    fields: list[str] | None = None


class OperationalLinkRequest(BaseModel):
    model_config = _STRICT

    decision: TriState
    conflict: bool
    policy_contract_id: UUID | None
    reason_code: LinkReason
    expected_current_link_id: UUID | None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        expected_reason: LinkReason
        if self.conflict:
            if self.decision != "UNKNOWN" or self.policy_contract_id is not None:
                raise ValueError("operational link conflict shape is invalid")
            expected_reason = "USER_REPORTED_OPERATIONAL_CONFLICT"
        elif self.decision == "MATCH":
            if self.policy_contract_id is None:
                raise ValueError("matched operational link requires a policy")
            expected_reason = "USER_CONFIRMED_SAME_CONTRACT"
        elif self.decision == "NO_MATCH":
            if self.policy_contract_id is not None:
                raise ValueError("unmatched operational link cannot include a policy")
            expected_reason = "USER_CONFIRMED_DISTINCT_CONTRACT"
        else:
            if self.policy_contract_id is not None:
                raise ValueError("unknown operational link cannot include a policy")
            expected_reason = "USER_REOPENED_OPERATIONAL_REVIEW"
        if self.reason_code != expected_reason:
            raise ValueError("operational link reason is invalid")
        return self


class DocumentResolutionRequest(BaseModel):
    model_config = _STRICT

    resolution: Resolution
    replacement_item_id: UUID | None
    reason_code: ResolutionReason
    expected_current_resolution_id: UUID | None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        expected_reason: ResolutionReason
        if self.resolution == "REPLACED":
            if self.replacement_item_id is None:
                raise ValueError("replacement resolution requires a replacement item")
            expected_reason = "USER_CONFIRMED_REPLACEMENT"
        elif self.resolution == "DISMISSED":
            if self.replacement_item_id is not None:
                raise ValueError("dismissed resolution cannot include a replacement item")
            expected_reason = "USER_DISMISSED_STALE_FAILURE"
        else:
            if self.replacement_item_id is not None:
                raise ValueError("reopened resolution cannot include a replacement item")
            expected_reason = "USER_REOPENED_DOCUMENT_REVIEW"
        if self.reason_code != expected_reason:
            raise ValueError("document resolution reason is invalid")
        return self


class OperationalLinkResponse(BaseModel):
    model_config = _STRICT

    id: UUID | None
    policy_contract_id: UUID | None
    decision: TriState
    conflict: bool
    authority: (
        Literal[
            "SNAPSHOT_EXACT_EVIDENCE",
            "USER_CONFIRMED_OPERATIONAL_IDENTITY",
        ]
        | None
    )
    reason_code: str = Field(min_length=1, max_length=64)
    confirmed_at: datetime | None

    @classmethod
    def from_projection(cls, value: OperationalLinkProjection) -> Self:
        return cls(**value.__dict__)


class OperationalLinkMutationResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    id: UUID
    knowledge_contract_id: UUID
    policy_contract_id: UUID | None
    decision: TriState
    conflict: bool
    authority: Literal["USER_CONFIRMED_OPERATIONAL_IDENTITY"]
    reason_code: str = Field(min_length=1, max_length=64)
    confirmed_at: datetime

    @classmethod
    def from_domain(cls, value: OperationalLinkHistory) -> Self:
        return cls(schema_version="1", **value.__dict__)


class DocumentResolutionResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    id: UUID
    failed_item_id: UUID
    replacement_item_id: UUID | None
    resolution: Resolution
    authority: Literal["USER_CONFIRMED_DOCUMENT_RESOLUTION"]
    reason_code: str = Field(min_length=1, max_length=64)
    confirmed_at: datetime

    @classmethod
    def from_domain(cls, value: DocumentResolutionHistory) -> Self:
        return cls(schema_version="1", **value.__dict__)


class DocumentReadinessResponse(BaseModel):
    model_config = _STRICT

    policy_contract_id: UUID
    completeness: Literal["CERTIFICATE_AND_TERMS", "CERTIFICATE_ONLY"]
    has_product_explanation: bool
    has_application: bool


class ContractReconciliationResponse(BaseModel):
    model_config = _STRICT

    knowledge_contract_id: UUID
    insurer_display: str = Field(min_length=1, max_length=240)
    product_display: str = Field(min_length=1, max_length=800)
    certificate_decision: TriState
    current_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    reconciliation_state: Literal[
        "EVIDENCE_READY",
        "DOCUMENTS_PENDING",
        "LINK_REVIEW_REQUIRED",
        "CONFLICT",
    ]
    operational_link: OperationalLinkResponse
    document_readiness: DocumentReadinessResponse | None

    @classmethod
    def from_domain(cls, value: ContractReconciliation) -> Self:
        return cls(
            knowledge_contract_id=value.knowledge_contract_id,
            insurer_display=value.insurer_display,
            product_display=value.product_display,
            certificate_decision=value.certificate_decision,
            current_status=value.current_status,
            reconciliation_state=value.reconciliation_state,
            operational_link=OperationalLinkResponse.from_projection(value.operational_link),
            document_readiness=(
                DocumentReadinessResponse(**value.document_readiness.__dict__)
                if value.document_readiness is not None
                else None
            ),
        )


class OrphanOperationalPolicyResponse(BaseModel):
    model_config = _STRICT

    policy_contract_id: UUID
    insurer_display: str = Field(min_length=1, max_length=240)
    product_display: str = Field(min_length=1, max_length=800)
    status: Literal["active", "inactive", "expired", "cancelled", "unknown"]
    completeness: Literal["CERTIFICATE_AND_TERMS", "CERTIFICATE_ONLY"]

    @classmethod
    def from_domain(cls, value: OrphanOperationalPolicy) -> Self:
        return cls(**value.__dict__)


class ReconciliationSummaryResponse(BaseModel):
    model_config = _STRICT

    total_contracts: int = Field(ge=0, le=256)
    evidence_ready_contracts: int = Field(ge=0, le=256)
    documents_pending_contracts: int = Field(ge=0, le=256)
    link_review_required_contracts: int = Field(ge=0, le=256)
    conflict_contracts: int = Field(ge=0, le=256)
    orphan_operational_contracts: int = Field(ge=0, le=256)
    unresolved_unreadable_sources: int = Field(ge=0, le=1000)


class ReconciliationUnreadableSourceResponse(BaseModel):
    model_config = _STRICT

    document_batch_item_id: UUID
    source_kind: Literal[
        "policy",
        "terms",
        "product_explanation",
        "application",
        "supporting",
    ]
    display_label: str = Field(min_length=1, max_length=80)
    processing_state: Literal["PASSWORD_REQUIRED", "OCR_REQUIRED", "FAILED"]
    current_resolution_id: UUID | None

    @classmethod
    def from_domain(cls, value: UnresolvedDocumentSource) -> Self:
        return cls(**value.__dict__)


class MemberInsuranceReconciliationResponse(BaseModel):
    model_config = _STRICT

    schema_version: Literal["1"]
    member_id: UUID
    knowledge_run_id: UUID
    generated_at: datetime
    summary: ReconciliationSummaryResponse
    contracts: tuple[ContractReconciliationResponse, ...] = Field(max_length=256)
    orphan_operational_contracts: tuple[OrphanOperationalPolicyResponse, ...] = Field(
        max_length=256
    )
    unresolved_sources: tuple[ReconciliationUnreadableSourceResponse, ...] = Field(max_length=1000)

    @classmethod
    def from_domain(cls, value: MemberInsuranceReconciliation) -> Self:
        return cls(
            schema_version="1",
            member_id=value.member_id,
            knowledge_run_id=value.knowledge_run_id,
            generated_at=value.generated_at,
            summary=ReconciliationSummaryResponse(**value.summary.__dict__),
            contracts=tuple(
                ContractReconciliationResponse.from_domain(item) for item in value.contracts
            ),
            orphan_operational_contracts=tuple(
                OrphanOperationalPolicyResponse.from_domain(item)
                for item in value.orphan_operational_contracts
            ),
            unresolved_sources=tuple(
                ReconciliationUnreadableSourceResponse.from_domain(item)
                for item in value.unresolved_sources
            ),
        )


__all__ = [
    "DocumentResolutionRequest",
    "DocumentResolutionResponse",
    "InsuranceReconciliationErrorResponse",
    "MemberInsuranceReconciliationResponse",
    "OperationalLinkMutationResponse",
    "OperationalLinkRequest",
]
