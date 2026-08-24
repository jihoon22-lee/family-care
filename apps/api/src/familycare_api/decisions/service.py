"""Household-scoped MedicalEvent and deterministic analysis use cases."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date
from typing import Protocol
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import (
    DecisionRunResult,
    FactConfirmation,
    FactValue,
    MedicalEvent,
)
from familycare_api.decisions.errors import DecisionInvalid, DecisionRepositoryUnavailable
from familycare_api.decisions.facts import FactNormalizationError, normalize_facts
from familycare_api.decisions.schemas import (
    MedicalEventCreateRequest,
    MedicalEventUpdateRequest,
)


class DecisionStore(Protocol):
    def create_medical_event(
        self,
        scope: HouseholdScope,
        *,
        family_member_id: UUID,
        mode: str,
        event_date: date | None,
        visit_date: date | None,
        facts: Mapping[str, FactValue],
    ) -> MedicalEvent: ...
    def get_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> MedicalEvent: ...
    def update_medical_event(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        *,
        expected_version: int,
        **changes: object,
    ) -> MedicalEvent: ...
    def soft_delete_medical_event(
        self, scope: HouseholdScope, event_id: UUID, *, expected_version: int
    ) -> MedicalEvent: ...
    def list_deleted_medical_events(self, scope: HouseholdScope) -> list[MedicalEvent]: ...
    def restore_medical_event(
        self, scope: HouseholdScope, event_id: UUID, *, expected_version: int
    ) -> MedicalEvent: ...
    def analyze_medical_event(self, scope: HouseholdScope, event_id: UUID) -> DecisionRunResult: ...
    def get_decision_result(
        self, scope: HouseholdScope, event_id: UUID, version: int
    ) -> DecisionRunResult: ...


class DecisionService:
    def __init__(self, scope: HouseholdScope, repository: DecisionStore) -> None:
        self.scope = scope
        self.repository = repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> DecisionService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise DecisionRepositoryUnavailable
        from familycare_api.decisions.repository import DecisionRepository

        return cls(scope, DecisionRepository(database_url))

    def create_medical_event(
        self,
        request: MedicalEventCreateRequest | None = None,
        *,
        family_member_id: UUID | None = None,
        mode: str | None = None,
        event_date: date | None = None,
        visit_date: date | None = None,
        facts: Mapping[str, object] | None = None,
        confirmation: Mapping[str, FactConfirmation] | None = None,
    ) -> MedicalEvent:
        if request is not None:
            family_member_id = request.family_member_id
            mode = request.mode
            event_date = request.event_date
            visit_date = request.visit_date
            facts = {key: item.value for key, item in request.facts.items()}
            confirmation = {key: item.confirmation for key, item in request.facts.items()}
        if family_member_id is None or mode not in {"pre_visit", "post_treatment"}:
            raise DecisionInvalid
        normalized = self._facts(facts or {}, confirmation or {})
        return self.repository.create_medical_event(
            self.scope,
            family_member_id=family_member_id,
            mode=mode,
            event_date=event_date,
            visit_date=visit_date,
            facts=normalized,
        )

    def get_medical_event(
        self,
        event_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> MedicalEvent:
        return self.repository.get_medical_event(
            self.scope,
            event_id,
            deleted_only=deleted_only,
        )

    def update_medical_event(
        self,
        event_id: UUID,
        request: MedicalEventUpdateRequest | None = None,
        *,
        expected_version: int | None = None,
        facts: Mapping[str, object] | None = None,
        confirmation: Mapping[str, FactConfirmation] | None = None,
    ) -> MedicalEvent:
        changes: dict[str, object] = {}
        if request is not None:
            expected_version = request.expected_version
            if "mode" in request.model_fields_set:
                if request.mode is None:
                    raise DecisionInvalid
                changes["mode"] = request.mode
            if "event_date" in request.model_fields_set:
                changes["event_date"] = request.event_date
            if "visit_date" in request.model_fields_set:
                changes["visit_date"] = request.visit_date
            if request.facts is not None:
                facts = {key: item.value for key, item in request.facts.items()}
                confirmation = {key: item.confirmation for key, item in request.facts.items()}
        if expected_version is None:
            raise DecisionInvalid
        if facts is not None:
            changes["facts"] = self._facts(facts, confirmation or {})
        return self.repository.update_medical_event(
            self.scope,
            event_id,
            expected_version=expected_version,
            **changes,
        )

    def delete_medical_event(self, event_id: UUID, *, expected_version: int) -> MedicalEvent:
        return self.repository.soft_delete_medical_event(
            self.scope,
            event_id,
            expected_version=expected_version,
        )

    def list_deleted_medical_events(self) -> list[MedicalEvent]:
        return self.repository.list_deleted_medical_events(self.scope)

    def restore_medical_event(self, event_id: UUID, *, expected_version: int) -> MedicalEvent:
        return self.repository.restore_medical_event(
            self.scope,
            event_id,
            expected_version=expected_version,
        )

    def analyze_medical_event(self, event_id: UUID) -> DecisionRunResult:
        return self.repository.analyze_medical_event(self.scope, event_id)

    def get_decision_result(self, event_id: UUID, version: int) -> DecisionRunResult:
        return self.repository.get_decision_result(self.scope, event_id, version)

    @staticmethod
    def _facts(
        facts: Mapping[str, object],
        confirmation: Mapping[str, FactConfirmation],
    ) -> dict[str, FactValue]:
        try:
            return normalize_facts(facts, confirmations=confirmation)
        except FactNormalizationError:
            raise DecisionInvalid from None


__all__ = ["DecisionService"]
