"""Policy-candidate review use cases."""

from __future__ import annotations

import os
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.policies.candidate_errors import (
    CandidateRepositoryUnavailable,
    InvalidCandidateCorrection,
    ReviewItemNotFound,
)
from familycare_api.policies.candidate_models import (
    CandidateConfirmationRequest,
    CandidateCorrectionRequest,
    CandidateRejectionRequest,
    PolicyReviewItem,
    validate_candidate_field_value,
)
from familycare_api.policies.candidate_repository import CandidateRepository


class CandidateReviewService:
    """Validate review commands before the scoped repository transaction."""

    def __init__(self, repository: CandidateRepository) -> None:
        self.repository = repository

    @classmethod
    def from_environment(cls) -> CandidateReviewService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise CandidateRepositoryUnavailable
        return cls(CandidateRepository(database_url))

    def list_review_items(
        self,
        *,
        scope: HouseholdScope,
        status: str = "NEEDS_REVIEW",
        domain: str = "policy",
        family_member_id: UUID | None = None,
    ) -> list[PolicyReviewItem]:
        if domain not in {"policy", "rider_clause", "coverage_rule"}:
            raise InvalidCandidateCorrection
        return self.repository.list_review_items(
            scope,
            status=status,
            domain=domain,
            family_member_id=family_member_id,
        )

    def get_review_item(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
    ) -> PolicyReviewItem:
        item = self.repository.get_review_item(scope, review_item_id)
        if item is None:
            raise ReviewItemNotFound
        return item

    def correct_field(
        self,
        *,
        scope: HouseholdScope,
        request: CandidateCorrectionRequest,
        actor_id: UUID,
        review_item_id: UUID | None = None,
        policy_id: UUID | None = None,
    ) -> PolicyReviewItem:
        if (review_item_id is None) == (policy_id is None):
            raise ReviewItemNotFound
        try:
            validate_candidate_field_value(request.field_id, request.value)
        except ValueError:
            raise InvalidCandidateCorrection from None
        return self.repository.correct_field(
            scope,
            request=request,
            actor_id=actor_id,
            review_item_id=review_item_id,
            policy_id=policy_id,
        )

    def confirm(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
        request: CandidateConfirmationRequest,
        actor_id: UUID,
    ) -> PolicyReviewItem:
        return self.repository.transition(
            scope,
            review_item_id,
            expected_version=request.expected_version,
            status="USER_CONFIRMED",
            actor_id=actor_id,
        )

    def reject(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
        request: CandidateRejectionRequest,
        actor_id: UUID,
    ) -> PolicyReviewItem:
        return self.repository.transition(
            scope,
            review_item_id,
            expected_version=request.expected_version,
            status="rejected",
            actor_id=actor_id,
            rejection_reason=request.reason_code,
        )


__all__ = ["CandidateReviewService"]
