"""A read-only use case that never queues analysis or changes candidate authority."""

import os
from typing import Protocol
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.guidance_evidence.models import GuidanceEvidenceDetail, GuidanceEvidenceRequest


class GuidanceEvidenceStore(Protocol):
    def get_detail(
        self, scope: HouseholdScope, event_id: UUID, request: GuidanceEvidenceRequest
    ) -> GuidanceEvidenceDetail: ...


class GuidanceEvidenceService:
    def __init__(self, scope: HouseholdScope, repository: GuidanceEvidenceStore) -> None:
        self.scope, self.repository = scope, repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> GuidanceEvidenceService:
        from familycare_api.guidance_evidence.repository import GuidanceEvidenceRepository

        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise DecisionRepositoryUnavailable
        return cls(scope, GuidanceEvidenceRepository(database_url))

    def get_detail(
        self, event_id: UUID, request: GuidanceEvidenceRequest
    ) -> GuidanceEvidenceDetail:
        return self.repository.get_detail(self.scope, event_id, request)
