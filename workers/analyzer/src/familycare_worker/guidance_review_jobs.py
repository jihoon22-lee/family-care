"""A bounded explicit review lease never changes the saved local answer."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.ai.evidence_loader import EvidenceLoadError, _household_member_terms
from familycare_worker.jobs import psycopg_database_url

_ERROR_CODES = frozenset(
    {
        "REVIEW_PROVIDER_UNAVAILABLE",
        "REVIEW_PROVIDER_TIMEOUT",
        "REVIEW_PROVIDER_CONFIGURATION",
        "REVIEW_RATE_LIMITED",
        "REVIEW_INVALID_RESPONSE",
        "REVIEW_BUDGET_EXCEEDED",
        "REVIEW_SOURCE_UNAVAILABLE",
        "REVIEW_REFUSED",
        "REVIEW_INCOMPLETE",
        "REVIEW_INPUT_CHANGED",
        "REVIEW_LEASE_EXPIRED",
        "REVIEW_DEADLINE_EXCEEDED",
    }
)


class ReviewQueueUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("REVIEW_QUEUE_UNAVAILABLE")


@dataclass(frozen=True, repr=False)
class ReviewLease:
    id: UUID
    household_space_id: UUID
    family_member_id: UUID
    medical_event_id: UUID
    decision_run_id: UUID
    event_version: int
    source_digest: str
    input_digest: str
    model: str
    prompt_revision: str
    lease_token: UUID
    deadline_at: datetime


@dataclass(frozen=True, repr=False)
class ReviewWorkInput:
    sources: dict[str, Any]
    event: dict[str, Any]
    local_guidance: dict[str, Any]
    sensitive_terms: tuple[str, ...]
    document_versions: dict[UUID, UUID]
    evidence_document_versions: dict[UUID, UUID] = field(default_factory=dict)


def _current_inputs(
    connection: psycopg.Connection[dict[str, Any]], job: ReviewLease
) -> dict[str, Any] | None:
    return connection.execute(
        "SELECT i.sources_json,i.event_json,r.local_guidance_json FROM guidance_review_jobs j "
        "JOIN guidance_review_inputs i ON i.review_job_id=j.id "
        "JOIN guidance_review_contexts c ON c.review_job_id=j.id "
        "JOIN decision_runs r ON r.id=j.decision_run_id "
        "JOIN medical_events e ON e.id=j.medical_event_id "
        "AND e.household_space_id=j.household_space_id "
        "JOIN family_members m ON m.id=j.family_member_id "
        "AND m.household_space_id=j.household_space_id AND m.deleted_at IS NULL "
        "JOIN household_spaces h ON h.id=j.household_space_id AND h.deleted_at IS NULL "
        "WHERE j.id=%s AND j.household_space_id=%s AND j.family_member_id=%s "
        "AND j.lease_token=%s AND j.input_digest=%s AND j.source_digest=%s "
        "AND j.state='running' AND j.deadline_at>clock_timestamp() "
        "AND j.lease_expires_at>clock_timestamp() AND e.deleted_at IS NULL "
        "AND e.family_member_id=j.family_member_id AND e.version=j.event_version "
        "AND c.scope_digest=guidance_review_scope_digest(j.id) "
        "AND i.privacy_digest=terms_semantic_privacy_digest(j.household_space_id)",
        (
            job.id,
            job.household_space_id,
            job.family_member_id,
            job.lease_token,
            job.input_digest,
            job.source_digest,
        ),
    ).fetchone()


def _input_documents(
    connection: psycopg.Connection[dict[str, Any]], job: ReviewLease, row: dict[str, Any]
) -> dict[UUID, UUID]:
    """Resolve only retained originals and citations; no caller-selected document scope."""
    sources = row["sources_json"]
    versions = {
        UUID(packet["envelope"]["source"]["document_version_id"]) for packet in sources["packets"]
    }
    versions.update(
        UUID(value)
        for entry in sources["index"]
        for value in entry.get("source_document_version_ids", [])
    )
    evidence_ids: set[UUID] = set()
    pending = [row["local_guidance_json"]]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("kind") == "OPERATIONAL_EVIDENCE":
                evidence_ids.add(UUID(value["evidence_id"]))
            elif value.get("kind") == "SEMANTIC_CITATION":
                versions.add(UUID(value["document_version_id"]))
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    if evidence_ids:
        versions.update(
            value["document_version_id"]
            for value in connection.execute(
                "SELECT document_version_id FROM evidence WHERE household_space_id=%s "
                "AND id=ANY(%s)",
                (job.household_space_id, list(evidence_ids)),
            )
        )
    return {
        value["id"]: value["document_id"]
        for value in connection.execute(
            "SELECT v.id,v.document_id FROM document_versions v JOIN documents d ON "
            "d.id=v.document_id "
            "AND d.deleted_at IS NULL WHERE v.id=ANY(%s)",
            (list(versions),),
        )
    }


class GuidanceReviewQueue:
    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def claim(self) -> ReviewLease | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                # Expiration is terminal even when transmission outcome is unknown.
                # Durable accounting is retained independently from this lease.
                connection.execute("""
                    UPDATE guidance_review_jobs j SET state='failed',
                      error_code=CASE
                        WHEN NOT EXISTS(SELECT 1 FROM medical_events e
                          WHERE e.id=j.medical_event_id
                            AND e.household_space_id=j.household_space_id
                            AND e.family_member_id=j.family_member_id AND e.version=j.event_version
                            AND e.deleted_at IS NULL) THEN 'REVIEW_INPUT_CHANGED'
                        WHEN j.deadline_at<=clock_timestamp() THEN 'REVIEW_DEADLINE_EXCEEDED'
                        ELSE 'REVIEW_LEASE_EXPIRED' END,
                      completed_at=clock_timestamp(),lease_token=NULL,lease_expires_at=NULL
                    WHERE j.state IN ('queued','running') AND (
                      j.lease_expires_at<=clock_timestamp() OR j.deadline_at<=clock_timestamp()
                      OR NOT EXISTS(SELECT 1 FROM medical_events e
                        WHERE e.id=j.medical_event_id AND e.household_space_id=j.household_space_id
                          AND e.family_member_id=j.family_member_id AND e.version=j.event_version
                          AND e.deleted_at IS NULL))
                """)
                row = connection.execute("""
                    WITH next AS (
                      SELECT id FROM guidance_review_jobs WHERE state='queued'
                      ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT 1
                    )
                    UPDATE guidance_review_jobs j SET state='running',lease_token=gen_random_uuid(),
                      deadline_at=clock_timestamp()+interval '100 seconds',
                      lease_expires_at=clock_timestamp()+interval '110 seconds'
                    FROM next WHERE j.id=next.id RETURNING j.*
                """).fetchone()
                return _lease(row) if row is not None else None
        except psycopg.Error:
            raise ReviewQueueUnavailable from None

    def fail(self, job: ReviewLease, code: str) -> bool:
        if code not in _ERROR_CODES:
            raise ReviewQueueUnavailable
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    "UPDATE guidance_review_jobs SET state='failed',error_code=%s,"
                    "completed_at=clock_timestamp(),lease_token=NULL,lease_expires_at=NULL "
                    "WHERE id=%s AND household_space_id=%s AND state='running' "
                    "AND lease_token=%s AND lease_expires_at>clock_timestamp() "
                    "AND deadline_at>clock_timestamp() RETURNING id",
                    (code, job.id, job.household_space_id, job.lease_token),
                ).fetchone()
                return row is not None
        except psycopg.Error:
            raise ReviewQueueUnavailable from None

    def record_proposal(self, job: ReviewLease, proposal: Mapping[str, object]) -> bool:
        """Store structured data for the API; the Worker cannot publish a local answer."""
        try:
            encoded = json.dumps(dict(proposal), allow_nan=False, separators=(",", ":"))
            if len(encoded.encode()) > 600000 or proposal.get("schema_revision") != (
                "guidance-review-proposals-v1"
            ):
                raise ReviewQueueUnavailable
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    "INSERT INTO "
                    "guidance_review_proposals(review_job_id,lease_token,proposal_json) "
                    "SELECT j.id,j.lease_token,%s FROM guidance_review_jobs j "
                    "JOIN medical_events e ON e.id=j.medical_event_id "
                    "AND e.household_space_id=j.household_space_id "
                    "WHERE j.id=%s AND j.household_space_id=%s AND j.lease_token=%s "
                    "AND j.input_digest=%s AND j.source_digest=%s AND j.state='running' "
                    "AND j.lease_expires_at>clock_timestamp() AND j.deadline_at>clock_timestamp() "
                    "AND e.family_member_id=j.family_member_id AND e.version=j.event_version "
                    "AND e.deleted_at IS NULL ON CONFLICT DO NOTHING RETURNING review_job_id",
                    (
                        Jsonb(json.loads(encoded)),
                        job.id,
                        job.household_space_id,
                        job.lease_token,
                        job.input_digest,
                        job.source_digest,
                    ),
                ).fetchone()
                return row is not None
        except psycopg.Error, TypeError, ValueError:
            raise ReviewQueueUnavailable from None

    def load_inputs(self, job: ReviewLease) -> ReviewWorkInput:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                row = _current_inputs(connection, job)
                if row is None:
                    raise ReviewQueueUnavailable
                members = connection.execute(
                    "SELECT display_name,internal_alias FROM family_members "
                    "WHERE household_space_id=%s AND deleted_at IS NULL ORDER BY id",
                    (job.household_space_id,),
                ).fetchall()
                versions = _input_documents(connection, job, row)
                evidence = connection.execute(
                    "SELECT id,document_version_id FROM evidence WHERE household_space_id=%s "
                    "AND document_version_id=ANY(%s)",
                    (job.household_space_id, list(versions)),
                ).fetchall()
                return ReviewWorkInput(
                    sources=row["sources_json"],
                    event=row["event_json"],
                    local_guidance=row["local_guidance_json"],
                    sensitive_terms=_household_member_terms(members),
                    document_versions=versions,
                    evidence_document_versions={
                        value["id"]: value["document_version_id"] for value in evidence
                    },
                )
        except psycopg.Error, TypeError, ValueError, EvidenceLoadError:
            raise ReviewQueueUnavailable from None


def _lease(row: dict[str, Any]) -> ReviewLease:
    return ReviewLease(**{key: row[key] for key in ReviewLease.__dataclass_fields__})
