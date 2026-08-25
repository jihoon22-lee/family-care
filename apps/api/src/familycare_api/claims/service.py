"""Household-scoped use cases for manual insurer claim tracking."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID

from familycare_api.claims.domain import ClaimStatus
from familycare_api.claims.errors import ClaimRepositoryUnavailable
from familycare_api.claims.schemas import (
    ChecklistUpdateRequest,
    ClaimCreateRequest,
    ClaimTransitionRequest,
    ClaimUpdateRequest,
)
from familycare_api.common.scope import HouseholdScope


class ClaimStore(Protocol):
    def create_claim_case(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        rider_id: UUID,
    ) -> object: ...

    def list_claim_cases(
        self,
        scope: HouseholdScope,
        *,
        event_id: UUID | None = None,
        status: ClaimStatus | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
        deleted_only: bool = False,
    ) -> object: ...

    def get_claim_case(
        self, scope: HouseholdScope, claim_id: UUID, *, deleted_only: bool = False
    ) -> object: ...

    def update_claim_case(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> object: ...

    def transition_claim(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        *,
        target_status: ClaimStatus,
        expected_version: int,
        occurred_at: object,
        metadata: Mapping[str, object],
    ) -> object: ...

    def update_checklist_item(
        self,
        scope: HouseholdScope,
        claim_id: UUID,
        item_id: UUID,
        *,
        expected_version: int,
        prepared: bool,
        note_code: str | None,
    ) -> object: ...

    def soft_delete_claim_case(
        self, scope: HouseholdScope, claim_id: UUID, *, expected_version: int
    ) -> None: ...

    def restore_claim_case(
        self, scope: HouseholdScope, claim_id: UUID, *, expected_version: int
    ) -> object: ...


class ClaimService:
    def __init__(self, scope: HouseholdScope, repository: ClaimStore) -> None:
        self.scope = scope
        self.repository = repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> ClaimService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise ClaimRepositoryUnavailable
        from familycare_api.claims.repository import ClaimRepository

        return cls(scope, ClaimRepository(database_url))

    def create_claim_case(self, event_id: UUID, request: ClaimCreateRequest) -> object:
        return self.repository.create_claim_case(
            self.scope,
            event_id,
            rider_id=request.rider_id,
        )

    def list_claim_cases(
        self,
        *,
        event_id: UUID | None = None,
        status: ClaimStatus | None = None,
        cursor: UUID | None = None,
        limit: int = 50,
        deleted_only: bool = False,
    ) -> object:
        return self.repository.list_claim_cases(
            self.scope,
            event_id=event_id,
            status=status,
            cursor=cursor,
            limit=limit,
            deleted_only=deleted_only,
        )

    def get_claim_case(self, claim_id: UUID, *, deleted_only: bool = False) -> object:
        return self.repository.get_claim_case(self.scope, claim_id, deleted_only=deleted_only)

    def update_claim_case(self, claim_id: UUID, request: ClaimUpdateRequest) -> object:
        return self.repository.update_claim_case(
            self.scope,
            claim_id,
            expected_version=request.expected_version,
            changes=request.editable_values(),
        )

    def transition_claim(self, claim_id: UUID, request: ClaimTransitionRequest) -> object:
        return self.repository.transition_claim(
            self.scope,
            claim_id,
            target_status=request.target_status,
            expected_version=request.expected_version,
            occurred_at=request.occurred_at,
            metadata=request.normalized_metadata(),
        )

    def update_checklist_item(
        self, claim_id: UUID, item_id: UUID, request: ChecklistUpdateRequest
    ) -> object:
        return self.repository.update_checklist_item(
            self.scope,
            claim_id,
            item_id,
            expected_version=request.expected_version,
            prepared=request.prepared,
            note_code=request.note_code,
        )

    def delete_claim_case(self, claim_id: UUID, *, expected_version: int) -> None:
        self.repository.soft_delete_claim_case(
            self.scope, claim_id, expected_version=expected_version
        )

    def restore_claim_case(self, claim_id: UUID, *, expected_version: int) -> object:
        return self.repository.restore_claim_case(
            self.scope, claim_id, expected_version=expected_version
        )


__all__ = ["ClaimService", "ClaimStore"]
