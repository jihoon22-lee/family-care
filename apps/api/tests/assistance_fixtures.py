"""Explicit opt-in fixture for historical recommendation-worker integration."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import psycopg
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.assistance import AnalysisAssistance
from familycare_api.decisions.assistance_repository import AnalysisAssistanceRepository
from familycare_api.decisions.domain import MedicalEvent


class ExplicitReviewRepository(AnalysisAssistanceRepository):
    """Exercise the existing worker contract without restoring automatic query AI."""

    def create_search_projection(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        decision_run_id: UUID,
        *,
        reviewed_fact_tokens: tuple[str, ...] = (),
        enqueue_review: bool = False,
    ) -> AnalysisAssistance:
        return super().create_search_projection(
            connection,
            scope,
            event,
            decision_run_id,
            reviewed_fact_tokens=reviewed_fact_tokens,
            enqueue_review=True,
        )
