"""Immutable policy-ledger projections and value vocabulary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

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
    family_member_id: UUID
    role: PartyRole
    effective_from: date | None
    effective_to: date | None
    evidence_id: UUID
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
    source_evidence_id: UUID
    status_evidence_id: UUID | None
    version: int


@dataclass(frozen=True)
class PolicyContract:
    id: UUID
    household_space_id: UUID
    source_document_version_id: UUID
    insurer_display: str
    insurer_key: str
    product_display: str
    product_key: str
    contract_date: date | None
    coverage_start_date: date | None
    coverage_end_date: date | None
    status: PolicyStatus
    status_evidence_id: UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None


__all__ = [
    "BenefitType",
    "FamilyMember",
    "PartyRole",
    "PolicyContract",
    "PolicyParty",
    "PolicyStatus",
    "Rider",
]
