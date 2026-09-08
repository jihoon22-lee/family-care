"""A bounded explicit review lease never changes the saved local answer."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.jobs import psycopg_database_url

_ERROR_CODES = frozenset(
    {
        "REVIEW_PROVIDER_UNAVAILABLE",
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


def _lease(row: dict[str, Any]) -> ReviewLease:
    return ReviewLease(**{key: row[key] for key in ReviewLease.__dataclass_fields__})
