"""Household-scoped use cases for insurance ledger reconciliation."""

from __future__ import annotations

import os
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.errors import ApiBoundaryError
from familycare_api.identity.context import AuthContext
from familycare_api.insurance_reconciliation.domain import (
    DocumentResolutionHistory,
    MemberInsuranceReconciliation,
    OperationalLinkHistory,
    TriState,
)
from familycare_api.insurance_reconciliation.repository import (
    InsuranceReconciliationRepository,
    ReconciliationRepositoryConflict,
    ReconciliationRepositoryNotFound,
    ReconciliationRepositoryTooLarge,
    ReconciliationRepositoryUnavailable,
)


class InsuranceReconciliationNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "INSURANCE_RECONCILIATION_NOT_FOUND"
    public_message = "insurance reconciliation not found"


class InsuranceReconciliationConflict(ApiBoundaryError):
    status_code = 409
    error_code = "INSURANCE_RECONCILIATION_CONFLICT"
    public_message = "insurance reconciliation state conflict"


class InsuranceReconciliationTooLarge(ApiBoundaryError):
    status_code = 409
    error_code = "INSURANCE_RECONCILIATION_TOO_LARGE"
    public_message = "insurance reconciliation exceeds the safe response bound"


class InsuranceReconciliationUnavailable(ApiBoundaryError):
    status_code = 503
    error_code = "INSURANCE_RECONCILIATION_UNAVAILABLE"
    public_message = "insurance reconciliation service unavailable"


class InsuranceReconciliationService:
    def __init__(
        self,
        context: AuthContext,
        repository: InsuranceReconciliationRepository,
    ) -> None:
        self.context = context
        self.scope = HouseholdScope(context.household_space_id)
        self.repository = repository

    @classmethod
    def from_environment(cls, context: AuthContext) -> InsuranceReconciliationService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise InsuranceReconciliationUnavailable
        return cls(context, InsuranceReconciliationRepository(database_url))

    def get_member(self, member_id: UUID) -> MemberInsuranceReconciliation:
        try:
            result = self.repository.get_member(self.scope, member_id)
        except ReconciliationRepositoryTooLarge:
            raise InsuranceReconciliationTooLarge from None
        except ReconciliationRepositoryUnavailable:
            raise InsuranceReconciliationUnavailable from None
        if result is None:
            raise InsuranceReconciliationNotFound
        return result

    def confirm_operational_link(
        self,
        knowledge_contract_id: UUID,
        *,
        decision: TriState,
        conflict: bool,
        policy_contract_id: UUID | None,
        reason_code: str,
        expected_current_link_id: UUID | None,
    ) -> OperationalLinkHistory:
        try:
            return self.repository.confirm_operational_link(
                self.scope,
                actor_id=self.context.user_id,
                knowledge_contract_id=knowledge_contract_id,
                decision=decision,
                conflict=conflict,
                policy_contract_id=policy_contract_id,
                reason_code=reason_code,
                expected_current_link_id=expected_current_link_id,
            )
        except ReconciliationRepositoryNotFound:
            raise InsuranceReconciliationNotFound from None
        except ReconciliationRepositoryConflict:
            raise InsuranceReconciliationConflict from None
        except ReconciliationRepositoryUnavailable:
            raise InsuranceReconciliationUnavailable from None

    def confirm_document_resolution(
        self,
        failed_item_id: UUID,
        *,
        resolution: str,
        replacement_item_id: UUID | None,
        reason_code: str,
        expected_current_resolution_id: UUID | None,
    ) -> DocumentResolutionHistory:
        try:
            return self.repository.confirm_document_resolution(
                self.scope,
                actor_id=self.context.user_id,
                failed_item_id=failed_item_id,
                resolution=resolution,
                replacement_item_id=replacement_item_id,
                reason_code=reason_code,
                expected_current_resolution_id=expected_current_resolution_id,
            )
        except ReconciliationRepositoryNotFound:
            raise InsuranceReconciliationNotFound from None
        except ReconciliationRepositoryConflict:
            raise InsuranceReconciliationConflict from None
        except ReconciliationRepositoryUnavailable:
            raise InsuranceReconciliationUnavailable from None


__all__ = ["InsuranceReconciliationService"]
