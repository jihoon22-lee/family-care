"""Immutable policy-ledger projections and value vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from familycare_api.common.evidence import EvidenceRef

PolicyStatus = Literal["active", "inactive", "expired", "cancelled", "unknown"]
BenefitType = Literal["fixed", "indemnity"]
PartyRole = Literal[
    "policyholder",
    "primary_insured",
    "additional_insured",
    "beneficiary",
]


@dataclass(frozen=True)
class FamilyMember:
    id: UUID
    household_space_id: UUID
    display_name: str
    internal_alias: str
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class PolicyParty:
    id: UUID
    policy_contract_id: UUID
    family_member_id: UUID
    role: PartyRole
    effective_from: date | None
    effective_to: date | None
    evidence: EvidenceRef
    version: int


@dataclass(frozen=True)
class Rider:
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
    source_evidence: EvidenceRef
    status_evidence: EvidenceRef | None
    version: int


@dataclass(frozen=True)
class PolicyContract:
    id: UUID
    household_space_id: UUID
    source_document_version_id: UUID
    source_evidence: EvidenceRef
    insurer_display: str
    insurer_key: str
    product_display: str
    product_key: str
    contract_date: date | None
    coverage_start_date: date | None
    coverage_end_date: date | None
    status: PolicyStatus
    status_evidence: EvidenceRef | None
    parties: tuple[PolicyParty, ...]
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


@dataclass(frozen=True)
class CreatePolicyParty:
    family_member_id: UUID
    role: PartyRole
    effective_from: date | None
    effective_to: date | None
    evidence: EvidenceRef


__all__ = [
    "BenefitType",
    "CreatePolicyParty",
    "FamilyMember",
    "PartyRole",
    "PolicyContract",
    "PolicyParty",
    "PolicyStatus",
    "Rider",
]
