"""Household-scoped use cases for optional MedicalEvent structuring."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.decisions.structuring_schemas import (
    FactConfidence,
    FactFieldId,
    FactIssueCode,
    FactSource,
    FactState,
    StructuringErrorCode,
    StructuringJobState,
)


@dataclass(frozen=True)
class StructuredFact:
    fact_id: UUID
    field_id: FactFieldId
    value: str | bool | None
    source: FactSource
    state: FactState
    confidence: FactConfidence
    evidence_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True)
class OptionalQuestion:
    question_code: FactFieldId
    field_id: FactFieldId


@dataclass(frozen=True)
class FactIssue:
    code: FactIssueCode


@dataclass(frozen=True)
class StructuringJob:
    id: UUID
    medical_event_id: UUID
    event_version: int
    state: StructuringJobState
    attempts: int
    facts: tuple[StructuredFact, ...] = ()
    questions: tuple[OptionalQuestion, ...] = ()
    issues: tuple[FactIssue, ...] = ()
    error_code: StructuringErrorCode | None = None


class EventStructuringStore(Protocol):
    def enqueue(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        expected_version: int,
    ) -> StructuringJob: ...

    def get_job(self, scope: HouseholdScope, job_id: UUID) -> StructuringJob: ...

    def apply_user_override(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
        facts: Mapping[FactFieldId, str | bool | None],
    ) -> MedicalEvent: ...


class EventStructuringService:
    def __init__(self, scope: HouseholdScope, repository: EventStructuringStore) -> None:
        self.scope = scope
        self.repository = repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> EventStructuringService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise DecisionRepositoryUnavailable
        from familycare_api.decisions.structuring_repository import EventStructuringRepository

        return cls(scope, EventStructuringRepository(database_url))

    def enqueue(self, event_id: UUID, *, expected_version: int) -> StructuringJob:
        return self.repository.enqueue(self.scope, event_id, expected_version)

    def get_job(self, job_id: UUID) -> StructuringJob:
        return self.repository.get_job(self.scope, job_id)

    def apply_user_override(
        self,
        event_id: UUID,
        *,
        expected_version: int,
        facts: Mapping[FactFieldId, str | bool | None],
    ) -> MedicalEvent:
        return self.repository.apply_user_override(
            self.scope,
            event_id,
            expected_version=expected_version,
            facts=facts,
        )


__all__ = [
    "EventStructuringService",
    "EventStructuringStore",
    "FactIssue",
    "OptionalQuestion",
    "StructuredFact",
    "StructuringJob",
]
