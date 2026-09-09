"""Bind a saved run to equivalent review input without reserving provider work."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import psycopg
from pydantic import ValidationError

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.errors import DecisionInvalid, MedicalEventNotFound
from familycare_api.decisions.repository import DecisionRepository, _medical_event
from familycare_api.guidance.models import LocalGuidanceResponse
from familycare_api.guidance_review.models import GuidanceReviewResult
from familycare_api.guidance_review.sources import ReviewSources, read_review_sources
from familycare_api.terms_knowledge.work_repository import _privacy_digest


@dataclass(frozen=True, repr=False)
class ReviewInput:
    event: MedicalEvent
    guidance: LocalGuidanceResponse
    sources: ReviewSources
    privacy_digest: str
    input_digest: str


@dataclass(frozen=True, repr=False)
class SavedGuidanceSource:
    guidance: LocalGuidanceResponse
    decision_run_id: UUID
    original_decision_run_id: UUID
    review_job_id: UUID | None = None
    review_source_digest: str | None = None
    review_result_digest: str | None = None


def _saved(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    run_id: UUID,
    expected_event_version: int,
) -> LocalGuidanceResponse:
    run = connection.execute(
        "SELECT local_guidance_json FROM decision_runs WHERE id=%s AND household_space_id=%s "
        "AND medical_event_id=%s AND event_version=%s AND status IN ('succeeded','partial')",
        (run_id, scope.household_space_id, event_id, expected_event_version),
    ).fetchone()
    if run is None:
        raise MedicalEventNotFound
    try:
        guidance = LocalGuidanceResponse.model_validate(run["local_guidance_json"])
    except ValidationError:
        raise DecisionInvalid from None
    if guidance.medical_event_id != event_id or guidance.event_version != expected_event_version:
        raise DecisionInvalid
    member = connection.execute(
        "SELECT 1 FROM family_members WHERE id=%s AND household_space_id=%s",
        (guidance.family_member_id, scope.household_space_id),
    ).fetchone()
    if member is None:
        raise MedicalEventNotFound
    return guidance


def read_review_input(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_row: dict[str, Any],
    run_id: UUID,
    *,
    model: str,
    prompt_revision: str,
    decisions: DecisionRepository,
) -> ReviewInput:
    event = _medical_event(event_row)
    guidance = _saved(connection, scope, event.id, run_id, event.version)
    if (
        guidance.family_member_id != event.family_member_id
        or decisions._local_guidance_is_stale(connection, scope, event, guidance) is not False
    ):
        raise DecisionInvalid
    sources = read_review_sources(connection, scope, event, decisions)
    privacy_digest = _privacy_digest(connection, scope)
    digest = hashlib.sha256(
        json.dumps(
            [
                event_row,
                guidance.model_dump(mode="json"),
                model,
                prompt_revision,
                sources.digest_sha256,
                privacy_digest,
            ],
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return ReviewInput(event, guidance, sources, privacy_digest, digest)


def require_review_match(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_row: dict[str, Any],
    run_id: UUID,
    job: dict[str, Any],
    *,
    decisions: DecisionRepository,
) -> ReviewInput:
    if (
        job["household_space_id"] != scope.household_space_id
        or job["medical_event_id"] != event_row["id"]
    ):
        raise MedicalEventNotFound
    value = read_review_input(
        connection,
        scope,
        event_row,
        run_id,
        model=job["model"],
        prompt_revision=job["prompt_revision"],
        decisions=decisions,
    )
    current = connection.execute(
        "SELECT scope_digest=guidance_review_scope_digest(review_job_id) AS current "
        "FROM guidance_review_contexts WHERE review_job_id=%s",
        (job["id"],),
    ).fetchone()
    if (
        value.input_digest != job["input_digest"]
        or current is None
        or current["current"] is not True
    ):
        raise DecisionInvalid
    return value


def resolve_guidance_source(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    *,
    decision_run_id: UUID,
    expected_event_version: int,
    review_job_id: UUID | None = None,
    require_current: bool = True,
) -> SavedGuidanceSource:
    # Repository construction does not open a connection. All reads use the caller's transaction.
    decisions = DecisionRepository(connection.info.dsn)
    event_row = decisions._event_row(connection, scope, event_id)
    if event_row is None:
        raise MedicalEventNotFound
    guidance = _saved(connection, scope, event_id, decision_run_id, expected_event_version)
    if require_current:
        event = _medical_event(event_row)
        if (
            event.version != expected_event_version
            or guidance.family_member_id != event.family_member_id
            or decisions._local_guidance_is_stale(connection, scope, event, guidance) is not False
        ):
            raise DecisionInvalid
    if review_job_id is None:
        return SavedGuidanceSource(guidance, decision_run_id, decision_run_id)
    job = connection.execute(
        "SELECT j.*,r.result_json,encode(sha256(convert_to(r.result_json::text,'UTF8')),'hex') "
        "AS result_digest FROM guidance_review_jobs j JOIN guidance_review_results r "
        "ON r.review_job_id=j.id WHERE j.id=%s AND j.household_space_id=%s "
        "AND j.medical_event_id=%s AND j.event_version=%s "
        "AND j.state IN ('partial','completed','disagreement')",
        (review_job_id, scope.household_space_id, event_id, expected_event_version),
    ).fetchone()
    if job is None:
        raise MedicalEventNotFound
    if require_current or decision_run_id != job["decision_run_id"]:
        require_review_match(
            connection, scope, event_row, decision_run_id, job, decisions=decisions
        )
    result = GuidanceReviewResult.model_validate(job["result_json"])
    reviewed = result.guidance
    if (
        result.source_digest != job["source_digest"]
        or reviewed.medical_event_id != event_id
        or reviewed.event_version != expected_event_version
        or reviewed.family_member_id != guidance.family_member_id
    ):
        raise DecisionInvalid
    return SavedGuidanceSource(
        reviewed,
        decision_run_id,
        job["decision_run_id"],
        review_job_id,
        job["source_digest"],
        job["result_digest"],
    )


def resolve_saved_guidance(
    connection: psycopg.Connection[dict[str, Any]],
    scope: HouseholdScope,
    event_id: UUID,
    *,
    decision_run_id: UUID,
    expected_event_version: int,
    review_job_id: UUID | None = None,
    require_current: bool = True,
) -> LocalGuidanceResponse:
    return resolve_guidance_source(
        connection,
        scope,
        event_id,
        decision_run_id=decision_run_id,
        expected_event_version=expected_event_version,
        review_job_id=review_job_id,
        require_current=require_current,
    ).guidance
