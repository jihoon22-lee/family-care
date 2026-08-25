"""Household-scoped bounded Evidence disclosure use case."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import DecisionRepositoryUnavailable


@dataclass(frozen=True)
class EvidenceDetail:
    evidence_id: UUID
    document_version_id: UUID
    document_label: str
    physical_page: int
    clause_label: str | None
    bounded_excerpt: str
    bbox: tuple[float, float, float, float] | None
    review_state: Literal["AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"]


class EvidenceStore(Protocol):
    def get_evidence(self, scope: HouseholdScope, evidence_id: UUID) -> EvidenceDetail: ...


class EvidenceService:
    def __init__(self, scope: HouseholdScope, repository: EvidenceStore) -> None:
        self.scope = scope
        self.repository = repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> EvidenceService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise DecisionRepositoryUnavailable
        from familycare_api.decisions.evidence_repository import EvidenceRepository

        return cls(scope, EvidenceRepository(database_url))

    def get_evidence(self, evidence_id: UUID) -> EvidenceDetail:
        return self.repository.get_evidence(self.scope, evidence_id)


__all__ = ["EvidenceDetail", "EvidenceService", "EvidenceStore"]
