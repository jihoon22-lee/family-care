"""Bind the reference to a saved candidate before any source-specific disclosure."""

from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import DecisionRepositoryUnavailable, EvidenceNotFound
from familycare_api.guidance.models import (
    GuidanceCandidate,
    GuidanceEvidenceRef,
    GuidanceSemanticEvidence,
)
from familycare_api.guidance_evidence.models import GuidanceEvidenceDetail, GuidanceEvidenceRequest
from familycare_api.guidance_evidence.operational import read_operational_evidence
from familycare_api.guidance_evidence.private import read_private_evidence
from familycare_api.guidance_evidence.semantic import read_semantic_evidence


def contains_reference(candidate: GuidanceCandidate, reference: GuidanceEvidenceRef) -> bool:
    requested = reference.model_dump(mode="json")
    pending: list[Any] = [candidate.model_dump(mode="json")]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value == requested:
                return True
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return False


class GuidanceEvidenceRepository:
    def __init__(self, database_url: str) -> None:
        if not isinstance(database_url, str) or not database_url:
            raise DecisionRepositoryUnavailable
        self.database_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    def get_detail(
        self, scope: HouseholdScope, event_id: UUID, request: GuidanceEvidenceRequest
    ) -> GuidanceEvidenceDetail:
        # Shared B06 binding owns historical reads and verified cross-run review reuse.
        from familycare_api.guidance_review.binding import resolve_saved_guidance

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL statement_timeout='10s'")
                guidance = resolve_saved_guidance(
                    connection,
                    scope,
                    event_id,
                    decision_run_id=request.decision_run_id,
                    expected_event_version=request.expected_event_version,
                    review_job_id=request.review_job_id,
                    require_current=False,
                )
                candidates = tuple(c for c in guidance.candidates if c.ref == request.coverage)
                if len(candidates) != 1 or not contains_reference(candidates[0], request.evidence):
                    raise EvidenceNotFound
                reference = request.evidence
                if isinstance(reference, GuidanceSemanticEvidence):
                    return read_semantic_evidence(connection, scope, reference)
                if reference.kind == "TERMS_SECTION":
                    return read_private_evidence(connection, scope, reference)
                return read_operational_evidence(connection, scope, reference)
        except psycopg.Error, ValueError:
            raise DecisionRepositoryUnavailable from None
