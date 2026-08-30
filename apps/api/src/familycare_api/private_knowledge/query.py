"""Household-scoped use cases for current private knowledge."""

from __future__ import annotations

import os
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.errors import ApiBoundaryError
from familycare_api.private_knowledge.query_repository import (
    PostgresPrivateKnowledgeQueryRepository,
    PrivateKnowledgeQueryRepositoryError,
    PrivateKnowledgeQueryTooLargeError,
)
from familycare_api.private_knowledge.schemas import (
    CurrentKnowledgeResponse,
    KnowledgeContractDetailResponse,
    KnowledgeContractPageResponse,
)


class PrivateKnowledgeNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "PRIVATE_KNOWLEDGE_NOT_FOUND"
    public_message = "private knowledge not found"


class PrivateKnowledgeTooLarge(ApiBoundaryError):
    status_code = 409
    error_code = "PRIVATE_KNOWLEDGE_TOO_LARGE"
    public_message = "private knowledge detail exceeds the safe response bound"


class PrivateKnowledgeUnavailable(ApiBoundaryError):
    status_code = 503
    error_code = "PRIVATE_KNOWLEDGE_UNAVAILABLE"
    public_message = "private knowledge service unavailable"


class PrivateKnowledgeQueryService:
    def __init__(
        self,
        scope: HouseholdScope,
        repository: PostgresPrivateKnowledgeQueryRepository,
    ) -> None:
        self.scope = scope
        self.repository = repository

    @classmethod
    def from_environment(
        cls,
        scope: HouseholdScope,
    ) -> PrivateKnowledgeQueryService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise PrivateKnowledgeUnavailable
        return cls(scope, PostgresPrivateKnowledgeQueryRepository(database_url))

    def current(self) -> CurrentKnowledgeResponse:
        try:
            result = self.repository.current(self.scope)
        except PrivateKnowledgeQueryRepositoryError:
            raise PrivateKnowledgeUnavailable from None
        if result is None:
            raise PrivateKnowledgeNotFound
        return result

    def list_contracts(
        self,
        *,
        limit: int,
        after: UUID | None,
        family_member_id: UUID | None = None,
    ) -> KnowledgeContractPageResponse:
        try:
            result = self.repository.list_contracts(
                self.scope,
                limit=limit,
                after=after,
                family_member_id=family_member_id,
            )
        except PrivateKnowledgeQueryRepositoryError:
            raise PrivateKnowledgeUnavailable from None
        if result is None:
            raise PrivateKnowledgeNotFound
        return result

    def get_contract(
        self,
        contract_id: UUID,
        *,
        section_limit: int,
        section_after: UUID | None,
    ) -> KnowledgeContractDetailResponse:
        try:
            result = self.repository.get_contract(
                self.scope,
                contract_id,
                section_limit=section_limit,
                section_after=section_after,
            )
        except PrivateKnowledgeQueryTooLargeError:
            raise PrivateKnowledgeTooLarge from None
        except PrivateKnowledgeQueryRepositoryError:
            raise PrivateKnowledgeUnavailable from None
        if result is None:
            raise PrivateKnowledgeNotFound
        return result
