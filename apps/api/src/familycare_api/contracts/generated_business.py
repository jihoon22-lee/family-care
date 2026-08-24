"""Generated from packages/contracts/schemas; do not edit manually."""

from __future__ import annotations

from typing import Literal, TypedDict

__all__ = [
    "BenefitType",
    "DocumentVersionId",
    "Evidence",
    "EvidenceId",
    "EvidenceReviewState",
    "FamilyMemberId",
    "FamilyMemberRecord",
    "PartyRole",
    "PolicyApiErrorCode",
    "PolicyErrorCode",
    "PolicyId",
    "PolicyLedger",
    "PolicyPartyId",
    "PolicyPartyRecord",
    "PolicyRecord",
    "PolicyStatus",
    "PositiveVersion",
    "RiderId",
    "RiderRecord",
]


BenefitType = Literal[
    "fixed",
    "indemnity",
]


DocumentVersionId = str


EvidenceId = str


EvidenceReviewState = Literal[
    "AI_VERIFIED",
    "NEEDS_REVIEW",
    "USER_CONFIRMED",
]


FamilyMemberId = str


PartyRole = Literal[
    "additional_insured",
    "beneficiary",
    "policyholder",
    "primary_insured",
]


PolicyApiErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "EVIDENCE_INVALID",
    "FAMILY_MEMBER_NOT_FOUND",
    "INVALID_REQUEST",
    "POLICY_NOT_FOUND",
    "POLICY_STATE_CONFLICT",
    "RESOURCE_LIMIT_EXCEEDED",
    "VERSION_CONFLICT",
]


PolicyErrorCode = Literal[
    "AUTHENTICATION_REQUIRED",
    "EVIDENCE_INVALID",
    "FAMILY_MEMBER_NOT_FOUND",
    "POLICY_NOT_FOUND",
    "POLICY_STATE_CONFLICT",
    "VERSION_CONFLICT",
]


PolicyId = str


PolicyPartyId = str


PolicyStatus = Literal[
    "active",
    "cancelled",
    "expired",
    "inactive",
    "unknown",
]


PositiveVersion = int


RiderId = str


class Evidence(TypedDict):
    bbox: list[float] | None
    content_sha256: str
    document_version_id: DocumentVersionId
    evidence_id: EvidenceId
    physical_page: int
    review_state: EvidenceReviewState


class FamilyMemberRecord(TypedDict):
    deleted: bool
    display_name: str
    id: FamilyMemberId
    internal_alias: str
    version: PositiveVersion


class PolicyLedger(TypedDict):
    evidence: Evidence
    family_member_id: FamilyMemberId
    policy_id: PolicyId
    rider_id: RiderId
    schema_version: Literal["1"]
    status: PolicyStatus
    version: PositiveVersion


class PolicyPartyRecord(TypedDict):
    effective_from: str | None
    effective_to: str | None
    evidence: Evidence
    family_member_id: FamilyMemberId
    id: PolicyPartyId
    role: PartyRole
    version: PositiveVersion


class PolicyRecord(TypedDict):
    contract_date: str | None
    coverage_end_date: str | None
    coverage_start_date: str | None
    deleted: bool
    id: PolicyId
    insurer_display: str
    insurer_key: str
    parties: list[PolicyPartyRecord]
    product_display: str
    product_key: str
    source_document_version_id: DocumentVersionId
    source_evidence: Evidence
    status: PolicyStatus
    status_evidence: Evidence | None
    version: PositiveVersion


class RiderRecord(TypedDict):
    benefit_type: BenefitType
    coverage_end_date: str | None
    coverage_start_date: str | None
    currency: str | None
    display_name: str
    id: RiderId
    insured_amount: str | None
    normalized_key: str
    policy_contract_id: PolicyId
    renewable: bool | None
    source_evidence: Evidence
    status: PolicyStatus
    status_evidence: Evidence | None
    version: PositiveVersion
