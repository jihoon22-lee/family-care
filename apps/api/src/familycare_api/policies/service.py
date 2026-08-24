"""Household-scoped policy-ledger use cases."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from familycare_api.common.evidence import EvidenceRef, EvidenceRepository
from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.domain import (
    CreatePolicyParty,
    FamilyMember,
    PartyRole,
    PolicyContract,
    PolicyStatus,
    Rider,
)
from familycare_api.policies.errors import (
    EvidenceInvalid,
    FamilyMemberNotFound,
    PolicyNotFound,
    PolicyRepositoryUnavailable,
)
from familycare_api.policies.repository import PolicyLedgerRepository


@dataclass(frozen=True)
class PolicyPartyInput:
    family_member_id: UUID
    role: PartyRole
    evidence_id: UUID
    effective_from: date | None = None
    effective_to: date | None = None


class PolicyLedgerService:
    """Enforce policy invariants before the repository transaction."""

    def __init__(
        self,
        scope: HouseholdScope,
        repository: PolicyLedgerRepository,
        evidence_repository: EvidenceRepository,
    ) -> None:
        self.scope = scope
        self.repository = repository
        self.evidence_repository = evidence_repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> PolicyLedgerService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise PolicyRepositoryUnavailable
        return cls(
            scope,
            PolicyLedgerRepository(database_url),
            EvidenceRepository(database_url),
        )

    def list_family_members(self, *, deleted_only: bool = False) -> list[FamilyMember]:
        return self.repository.list_family_members(self.scope, deleted_only=deleted_only)

    def create_family_member(self, display_name: str, internal_alias: str) -> FamilyMember:
        return self.repository.create_family_member(
            self.scope,
            display_name=display_name,
            internal_alias=internal_alias,
        )

    def get_family_member(
        self,
        member_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> FamilyMember:
        member = self.repository.get_family_member(
            self.scope,
            member_id,
            deleted_only=deleted_only,
        )
        if member is None:
            raise FamilyMemberNotFound
        return member

    def update_family_member(
        self,
        member_id: UUID,
        *,
        expected_version: int,
        display_name: str | None,
        internal_alias: str | None,
    ) -> FamilyMember:
        current = self.get_family_member(member_id)
        return self.repository.update_family_member(
            self.scope,
            member_id,
            expected_version=expected_version,
            display_name=display_name if display_name is not None else current.display_name,
            internal_alias=internal_alias if internal_alias is not None else current.internal_alias,
        )

    def delete_family_member(self, member_id: UUID, *, expected_version: int) -> None:
        self.get_family_member(member_id)
        self.repository.soft_delete_family_member(
            self.scope,
            member_id,
            expected_version=expected_version,
        )

    def restore_family_member(
        self,
        member_id: UUID,
        *,
        expected_version: int,
    ) -> FamilyMember:
        self.get_family_member(member_id, deleted_only=True)
        return self.repository.restore_family_member(
            self.scope,
            member_id,
            expected_version=expected_version,
        )

    def list_policies(self, *, deleted_only: bool = False) -> list[PolicyContract]:
        return self.repository.list_policies(self.scope, deleted_only=deleted_only)

    def create_policy(
        self,
        *,
        source_document_version_id: UUID,
        source_evidence_id: UUID,
        insurer_display: str,
        insurer_key: str,
        product_display: str,
        product_key: str,
        contract_date: date | None,
        coverage_start_date: date | None,
        coverage_end_date: date | None,
        status: PolicyStatus,
        status_evidence_id: UUID | None,
        parties: tuple[PolicyPartyInput, ...],
    ) -> PolicyContract:
        if not parties:
            raise EvidenceInvalid
        source_evidence = self.evidence_repository.validate_for_document(
            self.scope,
            source_evidence_id,
            source_document_version_id,
        )
        status_evidence = self._status_evidence(
            status,
            status_evidence_id,
            source_document_version_id,
        )
        confirmed_parties: list[CreatePolicyParty] = []
        for party in parties:
            self.get_family_member(party.family_member_id)
            evidence = self.evidence_repository.validate_for_document(
                self.scope,
                party.evidence_id,
                source_document_version_id,
            )
            confirmed_parties.append(
                CreatePolicyParty(
                    family_member_id=party.family_member_id,
                    role=party.role,
                    effective_from=party.effective_from,
                    effective_to=party.effective_to,
                    evidence=evidence,
                )
            )
        return self.repository.create_policy(
            self.scope,
            source_document_version_id=source_document_version_id,
            source_evidence=source_evidence,
            insurer_display=insurer_display,
            insurer_key=insurer_key,
            product_display=product_display,
            product_key=product_key,
            contract_date=contract_date,
            coverage_start_date=coverage_start_date,
            coverage_end_date=coverage_end_date,
            status=status,
            status_evidence=status_evidence,
            parties=tuple(confirmed_parties),
        )

    def get_policy(
        self,
        policy_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> PolicyContract:
        policy = self.repository.get_policy(
            self.scope,
            policy_id,
            deleted_only=deleted_only,
        )
        if policy is None:
            raise PolicyNotFound
        return policy

    def update_policy(
        self,
        policy_id: UUID,
        *,
        expected_version: int,
        status: PolicyStatus | None,
        status_evidence_id: UUID | None,
        coverage_end_date: date | None,
        change_coverage_end_date: bool,
    ) -> PolicyContract:
        current = self.get_policy(policy_id)
        status_evidence: EvidenceRef | None = None
        if status is not None:
            status_evidence = self._status_evidence(
                status,
                status_evidence_id,
                current.source_document_version_id,
            )
        elif status_evidence_id is not None:
            raise EvidenceInvalid
        return self.repository.update_policy(
            self.scope,
            policy_id,
            expected_version=expected_version,
            status=status,
            status_evidence=status_evidence,
            coverage_end_date=coverage_end_date,
            change_coverage_end_date=change_coverage_end_date,
        )

    def delete_policy(self, policy_id: UUID, *, expected_version: int) -> None:
        self.get_policy(policy_id)
        self.repository.soft_delete_policy(
            self.scope,
            policy_id,
            expected_version=expected_version,
        )

    def restore_policy(self, policy_id: UUID, *, expected_version: int) -> PolicyContract:
        self.get_policy(policy_id, deleted_only=True)
        return self.repository.restore_policy(
            self.scope,
            policy_id,
            expected_version=expected_version,
        )

    def list_policy_riders(self, policy_id: UUID) -> list[Rider]:
        self.get_policy(policy_id)
        return self.repository.list_policy_riders(self.scope, policy_id)

    def _status_evidence(
        self,
        status: PolicyStatus,
        evidence_id: UUID | None,
        document_version_id: UUID,
    ) -> EvidenceRef | None:
        if status == "unknown" and evidence_id is None:
            return None
        if evidence_id is None:
            raise EvidenceInvalid
        return self.evidence_repository.validate_for_document(
            self.scope,
            evidence_id,
            document_version_id,
        )


__all__ = ["PolicyLedgerService", "PolicyPartyInput"]
