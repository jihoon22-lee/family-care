"""Use cases for reviewed insurance-document inventory."""

from __future__ import annotations

import os
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.identity.context import AuthContext
from familycare_api.insurance_documents.domain import (
    DocumentRole,
    InsuranceDocumentComponentRecord,
    InsuranceDocumentSetItemRecord,
    InsuranceDocumentSetRecord,
    MemberInsuranceDocumentInventory,
    ReviewState,
)
from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
from familycare_api.policies.errors import (
    FamilyMemberNotFound,
    PolicyRepositoryUnavailable,
    PolicyStateConflict,
)


class InsuranceDocumentService:
    """Enforce member scope before deriving the insurance document projection."""

    def __init__(
        self,
        context: AuthContext,
        repository: InsuranceDocumentRepository,
    ) -> None:
        self.context = context
        self.scope = HouseholdScope(context.household_space_id)
        self.repository = repository

    @classmethod
    def from_environment(cls, context: AuthContext) -> InsuranceDocumentService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise PolicyRepositoryUnavailable
        return cls(context, InsuranceDocumentRepository(database_url))

    def get_inventory(self, member_id: UUID) -> MemberInsuranceDocumentInventory:
        inventory = self.repository.get_inventory(self.scope, member_id)
        if inventory is None:
            raise FamilyMemberNotFound
        return inventory

    def create_component(
        self,
        member_id: UUID,
        *,
        document_batch_item_id: UUID,
        role: DocumentRole,
        page_start: int,
        page_end: int,
        evidence_id: UUID | None,
        review_state: ReviewState,
    ) -> InsuranceDocumentComponentRecord:
        component = self.repository.create_component(
            self.scope,
            actor_id=self.context.user_id,
            member_id=member_id,
            document_batch_item_id=document_batch_item_id,
            role=role,
            page_start=page_start,
            page_end=page_end,
            evidence_id=evidence_id,
            review_state=review_state,
        )
        if component is None:
            raise PolicyStateConflict
        return component

    def create_document_set(
        self,
        member_id: UUID,
        *,
        policy_contract_id: UUID | None,
        insurer_display: str | None,
        product_display: str | None,
        display_label: str,
    ) -> InsuranceDocumentSetRecord:
        document_set = self.repository.create_document_set(
            self.scope,
            actor_id=self.context.user_id,
            member_id=member_id,
            policy_contract_id=policy_contract_id,
            insurer_display=insurer_display,
            product_display=product_display,
            display_label=display_label,
        )
        if document_set is None:
            raise FamilyMemberNotFound
        return document_set

    def attach_set_item(
        self,
        document_set_id: UUID,
        *,
        insurance_document_component_id: UUID,
        match_state: ReviewState,
        evidence_id: UUID | None,
        expected_set_version: int,
    ) -> InsuranceDocumentSetItemRecord:
        item = self.repository.attach_set_item(
            self.scope,
            actor_id=self.context.user_id,
            document_set_id=document_set_id,
            insurance_document_component_id=insurance_document_component_id,
            match_state=match_state,
            evidence_id=evidence_id,
            expected_set_version=expected_set_version,
        )
        if item is None:
            raise PolicyStateConflict
        return item

    def detach_set_item(self, item_id: UUID, *, expected_version: int) -> None:
        if not self.repository.detach_set_item(
            self.scope,
            item_id=item_id,
            expected_version=expected_version,
        ):
            raise PolicyStateConflict

    def delete_document_set(
        self,
        document_set_id: UUID,
        *,
        expected_version: int,
    ) -> None:
        if not self.repository.soft_delete_document_set(
            self.scope,
            document_set_id=document_set_id,
            expected_version=expected_version,
        ):
            raise PolicyStateConflict


__all__ = ["InsuranceDocumentService"]
