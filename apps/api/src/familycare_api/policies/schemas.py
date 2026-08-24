"""Strict HTTP adapters for the policy ledger."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Self, cast
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from familycare_api.common.evidence import EvidenceRef, EvidenceReviewState
from familycare_api.policies.domain import (
    BenefitType,
    FamilyMember,
    PartyRole,
    PolicyContract,
    PolicyParty,
    PolicyStatus,
    Rider,
)
from familycare_api.policies.service import PolicyPartyInput

_STRICT = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class FamilyMemberCreateRequest(BaseModel):
    model_config = _STRICT

    display_name: str = Field(min_length=1, max_length=160)
    internal_alias: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class FamilyMemberUpdateRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    internal_alias: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    )

    @model_validator(mode="after")
    def require_change(self) -> Self:
        if self.display_name is None and self.internal_alias is None:
            raise ValueError("at least one editable field is required")
        return self


class ExpectedVersionRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)


class FamilyMemberResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    display_name: str
    internal_alias: str
    version: int = Field(ge=1)
    deleted: bool

    @classmethod
    def from_domain(cls, member: FamilyMember) -> FamilyMemberResponse:
        return cls(
            id=member.id,
            display_name=member.display_name,
            internal_alias=member.internal_alias,
            version=member.version,
            deleted=member.deleted_at is not None,
        )


class EvidenceResponse(BaseModel):
    model_config = _STRICT

    evidence_id: UUID
    document_version_id: UUID
    content_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    physical_page: int = Field(ge=1)
    bbox: tuple[float, float, float, float] | None
    review_state: EvidenceReviewState

    @classmethod
    def from_domain(cls, evidence: EvidenceRef) -> EvidenceResponse:
        bbox = (
            cast(tuple[float, float, float, float], tuple(float(value) for value in evidence.bbox))
            if evidence.bbox
            else None
        )
        return cls(
            evidence_id=evidence.evidence_id,
            document_version_id=evidence.document_version_id,
            content_sha256=evidence.content_sha256,
            physical_page=evidence.physical_page,
            bbox=bbox,
            review_state=evidence.review_state,
        )


class PolicyPartyCreateRequest(BaseModel):
    model_config = _STRICT

    family_member_id: UUID
    role: PartyRole
    evidence_id: UUID
    effective_from: date | None = None
    effective_to: date | None = None

    @model_validator(mode="after")
    def validate_dates(self) -> Self:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective period is invalid")
        return self

    def to_domain(self) -> PolicyPartyInput:
        return PolicyPartyInput(
            family_member_id=self.family_member_id,
            role=self.role,
            evidence_id=self.evidence_id,
            effective_from=self.effective_from,
            effective_to=self.effective_to,
        )


class PolicyPartyResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    family_member_id: UUID
    role: PartyRole
    effective_from: date | None
    effective_to: date | None
    evidence: EvidenceResponse
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, party: PolicyParty) -> PolicyPartyResponse:
        return cls(
            id=party.id,
            family_member_id=party.family_member_id,
            role=party.role,
            effective_from=party.effective_from,
            effective_to=party.effective_to,
            evidence=EvidenceResponse.from_domain(party.evidence),
            version=party.version,
        )


class PolicyCreateRequest(BaseModel):
    model_config = _STRICT

    source_document_version_id: UUID
    source_evidence_id: UUID
    insurer_display: str = Field(min_length=1, max_length=160)
    insurer_key: str = Field(min_length=1, max_length=160)
    product_display: str = Field(min_length=1, max_length=200)
    product_key: str = Field(min_length=1, max_length=200)
    contract_date: date | None = None
    coverage_start_date: date | None = None
    coverage_end_date: date | None = None
    status: PolicyStatus = "unknown"
    status_evidence_id: UUID | None = None
    parties: tuple[PolicyPartyCreateRequest, ...] = Field(min_length=1, max_length=16)

    @model_validator(mode="after")
    def validate_policy(self) -> Self:
        if (
            self.coverage_start_date is not None
            and self.coverage_end_date is not None
            and self.coverage_end_date < self.coverage_start_date
        ):
            raise ValueError("coverage period is invalid")
        return self


class PolicyUpdateRequest(BaseModel):
    model_config = _STRICT

    expected_version: int = Field(ge=1)
    status: PolicyStatus | None = None
    status_evidence_id: UUID | None = None
    coverage_end_date: date | None = None

    @model_validator(mode="after")
    def validate_update(self) -> Self:
        if not ({"status", "coverage_end_date"} & self.model_fields_set):
            raise ValueError("at least one editable field is required")
        if self.status is None and self.status_evidence_id is not None:
            raise ValueError("status is required with status evidence")
        return self


class PolicyResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    source_document_version_id: UUID
    source_evidence: EvidenceResponse
    insurer_display: str
    insurer_key: str
    product_display: str
    product_key: str
    contract_date: date | None
    coverage_start_date: date | None
    coverage_end_date: date | None
    status: PolicyStatus
    status_evidence: EvidenceResponse | None
    parties: tuple[PolicyPartyResponse, ...]
    version: int = Field(ge=1)
    deleted: bool

    @classmethod
    def from_domain(cls, policy: PolicyContract) -> PolicyResponse:
        return cls(
            id=policy.id,
            source_document_version_id=policy.source_document_version_id,
            source_evidence=EvidenceResponse.from_domain(policy.source_evidence),
            insurer_display=policy.insurer_display,
            insurer_key=policy.insurer_key,
            product_display=policy.product_display,
            product_key=policy.product_key,
            contract_date=policy.contract_date,
            coverage_start_date=policy.coverage_start_date,
            coverage_end_date=policy.coverage_end_date,
            status=policy.status,
            status_evidence=(
                EvidenceResponse.from_domain(policy.status_evidence)
                if policy.status_evidence is not None
                else None
            ),
            parties=tuple(PolicyPartyResponse.from_domain(party) for party in policy.parties),
            version=policy.version,
            deleted=policy.deleted_at is not None,
        )


class RiderResponse(BaseModel):
    model_config = _STRICT

    id: UUID
    policy_contract_id: UUID
    display_name: str
    normalized_key: str
    benefit_type: BenefitType
    insured_amount: Decimal | None
    currency: str | None
    coverage_start_date: date | None
    coverage_end_date: date | None
    renewable: bool | None
    status: PolicyStatus
    source_evidence: EvidenceResponse
    status_evidence: EvidenceResponse | None
    version: int = Field(ge=1)

    @classmethod
    def from_domain(cls, rider: Rider) -> RiderResponse:
        return cls(
            id=rider.id,
            policy_contract_id=rider.policy_contract_id,
            display_name=rider.display_name,
            normalized_key=rider.normalized_key,
            benefit_type=rider.benefit_type,
            insured_amount=rider.insured_amount,
            currency=rider.currency,
            coverage_start_date=rider.coverage_start_date,
            coverage_end_date=rider.coverage_end_date,
            renewable=rider.renewable,
            status=rider.status,
            source_evidence=EvidenceResponse.from_domain(rider.source_evidence),
            status_evidence=(
                EvidenceResponse.from_domain(rider.status_evidence)
                if rider.status_evidence is not None
                else None
            ),
            version=rider.version,
        )


__all__ = [
    "EvidenceResponse",
    "ExpectedVersionRequest",
    "FamilyMemberCreateRequest",
    "FamilyMemberResponse",
    "FamilyMemberUpdateRequest",
    "PolicyCreateRequest",
    "PolicyPartyCreateRequest",
    "PolicyPartyResponse",
    "PolicyResponse",
    "PolicyUpdateRequest",
    "RiderResponse",
]
