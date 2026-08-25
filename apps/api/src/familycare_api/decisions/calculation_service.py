"""Household-scoped receipt and benefit-calculation use cases."""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Protocol
from uuid import UUID, uuid4

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.calculation_schemas import (
    ReceiptLineCreateRequest,
    ReceiptLineUpdateRequest,
)
from familycare_api.decisions.calculation_validation import validate_receipt_line
from familycare_api.decisions.calculations import ReceiptLine
from familycare_api.decisions.errors import DecisionRepositoryUnavailable


class CalculationStore(Protocol):
    def create_receipt_line(
        self, scope: HouseholdScope, event_id: UUID, line: ReceiptLine
    ) -> ReceiptLine: ...
    def update_receipt_line(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        line_id: UUID,
        *,
        expected_version: int,
        changes: Mapping[str, object],
    ) -> ReceiptLine: ...
    def soft_delete_receipt_line(
        self,
        scope: HouseholdScope,
        event_id: UUID,
        line_id: UUID,
        *,
        expected_version: int,
    ) -> None: ...
    def calculate_event(
        self, scope: HouseholdScope, event_id: UUID
    ) -> tuple[dict[str, object], ...]: ...


class CalculationService:
    def __init__(self, scope: HouseholdScope, repository: CalculationStore) -> None:
        self.scope = scope
        self.repository = repository

    @classmethod
    def from_environment(cls, scope: HouseholdScope) -> CalculationService:
        database_url = os.getenv("FAMILYCARE_DATABASE_URL")
        if not database_url:
            raise DecisionRepositoryUnavailable
        from familycare_api.decisions.calculation_repository import CalculationRepository

        return cls(scope, CalculationRepository(database_url))

    def create_receipt_line(
        self,
        event_id: UUID,
        request: ReceiptLineCreateRequest,
    ) -> ReceiptLine:
        line = request.to_domain(line_id=uuid4())
        validate_receipt_line(line)
        return self.repository.create_receipt_line(self.scope, event_id, line)

    def update_receipt_line(
        self,
        event_id: UUID,
        line_id: UUID,
        request: ReceiptLineUpdateRequest,
    ) -> ReceiptLine:
        return self.repository.update_receipt_line(
            self.scope,
            event_id,
            line_id,
            expected_version=request.expected_version,
            changes=request.editable_values(),
        )

    def delete_receipt_line(
        self,
        event_id: UUID,
        line_id: UUID,
        *,
        expected_version: int,
    ) -> None:
        self.repository.soft_delete_receipt_line(
            self.scope,
            event_id,
            line_id,
            expected_version=expected_version,
        )

    def get_calculations(self, event_id: UUID) -> tuple[dict[str, object], ...]:
        return self.repository.calculate_event(self.scope, event_id)


__all__ = ["CalculationService", "CalculationStore"]
