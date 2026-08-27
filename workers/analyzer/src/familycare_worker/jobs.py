"""Transactional PostgreSQL AnalysisJob queue with bounded leases."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.generated_contracts import AnalysisSettings, JobState
from familycare_worker.pdf.errors import IntakeErrorCode

DEFAULT_LEASE_SECONDS = 180
MAX_LEASE_SECONDS = 3_600
MAX_BACKOFF_SECONDS = 300
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SOURCE_KEY_PATTERN = re.compile(
    r"^(?!/)(?![A-Za-z]:)(?!.*\\)(?!.*[\r\n])(?!.*(?:^|/)\.\.(?:/|$))[^\x00]+$"
)
_DOCUMENT_KINDS = frozenset(
    {"amendment", "application", "claim", "policy", "product_explanation", "supporting", "terms"}
)
_TABLE_STRATEGIES = frozenset({"auto", "lines", "text"})
_RETRYABLE_CODES = frozenset(
    {
        IntakeErrorCode.EXTRACTION_TIMEOUT,
        IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED,
    }
)


class JobQueueError(RuntimeError):
    """Base class whose message never includes job data."""


class JobNotFound(JobQueueError):
    def __init__(self) -> None:
        super().__init__("ANALYSIS_JOB_NOT_FOUND")


class JobStateConflict(JobQueueError):
    def __init__(self) -> None:
        super().__init__("JOB_STATE_CONFLICT")


class InvalidJobPayload(JobQueueError):
    def __init__(self) -> None:
        super().__init__("INVALID_JOB_PAYLOAD")


@dataclass(frozen=True)
class AnalysisJobRecord:
    """Validated internal row used by one Worker process."""

    id: UUID
    document_id: UUID
    source_key: str
    settings: AnalysisSettings
    extractor_config_hash: str
    state: JobState
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    attempts: int
    max_attempts: int
    error_code: IntakeErrorCode | None


def psycopg_database_url(database_url: str) -> str:
    """Convert the shared SQLAlchemy-form URL without exposing it in errors."""

    if not isinstance(database_url, str) or not database_url:
        raise ValueError("database URL is required")
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _validate_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or _WORKER_ID_PATTERN.fullmatch(worker_id) is None:
        raise ValueError("invalid worker identity")
    return worker_id


def _validate_lease_seconds(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_LEASE_SECONDS:
        raise ValueError("invalid lease duration")
    return value


def _validate_settings(value: object) -> AnalysisSettings:
    if not isinstance(value, dict) or set(value) != {"document_kind", "extractor_config"}:
        raise InvalidJobPayload
    document_kind = value.get("document_kind")
    extractor_config = value.get("extractor_config")
    if document_kind not in _DOCUMENT_KINDS or not isinstance(extractor_config, dict):
        raise InvalidJobPayload
    if set(extractor_config) != {"profile", "quality_rule_version", "table_strategy"}:
        raise InvalidJobPayload
    if (
        extractor_config.get("profile") != "quality-v1"
        or extractor_config.get("quality_rule_version") != "quality-v1"
        or extractor_config.get("table_strategy") not in _TABLE_STRATEGIES
    ):
        raise InvalidJobPayload
    return cast(AnalysisSettings, value)


def _row_to_job(row: dict[str, Any]) -> AnalysisJobRecord:
    try:
        job_id = row["id"]
        document_id = row["document_id"]
        source_key = row["source_key"]
        config_hash = row["extractor_config_hash"]
        state = row["state"]
        attempts = row["attempts"]
        max_attempts = row["max_attempts"]
        if not isinstance(job_id, UUID) or not isinstance(document_id, UUID):
            raise InvalidJobPayload
        if (
            not isinstance(source_key, str)
            or len(source_key) > 512
            or _SOURCE_KEY_PATTERN.fullmatch(source_key) is None
        ):
            raise InvalidJobPayload
        if not isinstance(config_hash, str) or _SHA256_PATTERN.fullmatch(config_hash) is None:
            raise InvalidJobPayload
        settings = _validate_settings(row["settings_json"])
        canonical_config = json.dumps(
            settings["extractor_config"],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if hashlib.sha256(canonical_config).hexdigest() != config_hash:
            raise InvalidJobPayload
        if state not in {
            "cancelled",
            "permanently_failed",
            "queued",
            "retryable_failed",
            "running",
            "succeeded",
        }:
            raise InvalidJobPayload
        if (
            isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or attempts < 0
            or max_attempts < 1
            or attempts > max_attempts
        ):
            raise InvalidJobPayload
        raw_error = row.get("error_code")
        error_code = IntakeErrorCode(raw_error) if raw_error is not None else None
        return AnalysisJobRecord(
            id=job_id,
            document_id=document_id,
            source_key=source_key,
            settings=settings,
            extractor_config_hash=config_hash,
            state=cast(JobState, state),
            available_at=row["available_at"],
            lease_owner=row.get("lease_owner"),
            lease_expires_at=row.get("lease_expires_at"),
            heartbeat_at=row.get("heartbeat_at"),
            attempts=attempts,
            max_attempts=max_attempts,
            error_code=error_code,
        )
    except KeyError, TypeError, ValueError:
        raise InvalidJobPayload from None


class JobQueue:
    """Open short-lived connections for lease-safe queue operations."""

    def __init__(self, database_url: str, *, default_lease_seconds: int = DEFAULT_LEASE_SECONDS):
        self.database_url = psycopg_database_url(database_url)
        self.default_lease_seconds = _validate_lease_seconds(default_lease_seconds)

    def claim_next_job(
        self,
        worker_id: str,
        lease_seconds: int | None = None,
    ) -> AnalysisJobRecord | None:
        """Atomically recover expired work and claim one due job."""

        owner = _validate_worker_id(worker_id)
        lease = _validate_lease_seconds(
            self.default_lease_seconds if lease_seconds is None else lease_seconds
        )
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            connection.execute(
                """
                UPDATE analysis_jobs
                SET state = CASE
                        WHEN attempts >= max_attempts THEN 'permanently_failed'
                        ELSE 'retryable_failed'
                    END,
                    available_at = clock_timestamp(),
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = 'EXTRACTION_TIMEOUT',
                    updated_at = clock_timestamp()
                WHERE state = 'running'
                  AND lease_expires_at <= clock_timestamp()
                """
            )
            connection.execute(
                """
                UPDATE analysis_jobs
                SET state = 'permanently_failed',
                    error_code = COALESCE(error_code, 'EXTRACTION_TIMEOUT'),
                    updated_at = clock_timestamp()
                WHERE state IN ('queued', 'retryable_failed')
                  AND attempts >= max_attempts
                """
            )
            row = connection.execute(
                """
                WITH candidate AS (
                    SELECT id
                    FROM analysis_jobs
                    WHERE state IN ('queued', 'retryable_failed')
                      AND available_at <= clock_timestamp()
                      AND attempts < max_attempts
                    ORDER BY available_at, created_at, id
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE analysis_jobs AS job
                SET state = 'running',
                    lease_owner = %s,
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    heartbeat_at = clock_timestamp(),
                    attempts = job.attempts + 1,
                    error_code = NULL,
                    updated_at = clock_timestamp()
                FROM candidate
                WHERE job.id = candidate.id
                RETURNING job.*
                """,
                (owner, lease),
            ).fetchone()
            if row is None:
                return None
            try:
                return _row_to_job(row)
            except InvalidJobPayload:
                connection.execute(
                    """
                    UPDATE analysis_jobs
                    SET state = 'permanently_failed',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        error_code = 'INVALID_REQUEST',
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (row["id"],),
                )
                return None

    def heartbeat(self, job_id: UUID, worker_id: str) -> bool:
        """Extend a live lease only for its current owner."""

        owner = _validate_worker_id(worker_id)
        with psycopg.connect(self.database_url) as connection:
            row = connection.execute(
                """
                UPDATE analysis_jobs
                SET heartbeat_at = clock_timestamp(),
                    lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                    updated_at = clock_timestamp()
                WHERE id = %s
                  AND state = 'running'
                  AND lease_owner = %s
                  AND lease_expires_at > clock_timestamp()
                RETURNING id
                """,
                (self.default_lease_seconds, job_id, owner),
            ).fetchone()
            return row is not None

    def get_job(self, job_id: UUID) -> AnalysisJobRecord | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT * FROM analysis_jobs WHERE id = %s",
                (job_id,),
            ).fetchone()
        return _row_to_job(row) if row is not None else None

    def cancel_job(self, job_id: UUID) -> JobState:
        """Cancel a non-succeeded job; repeated cancellation is idempotent."""

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT state FROM analysis_jobs WHERE id = %s FOR UPDATE",
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFound
            state = row["state"]
            if state == "succeeded":
                raise JobStateConflict
            if state != "cancelled":
                connection.execute(
                    """
                    UPDATE analysis_jobs
                    SET state = 'cancelled',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        error_code = NULL,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (job_id,),
                )
            return "cancelled"

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        code: IntakeErrorCode,
    ) -> JobState:
        """Record a retryable or permanent sanitized failure for a leased job."""

        owner = _validate_worker_id(worker_id)
        if not isinstance(code, IntakeErrorCode):
            raise ValueError("invalid error code")
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            row = connection.execute(
                """
                SELECT
                    state,
                    lease_owner,
                    lease_expires_at,
                    lease_expires_at > clock_timestamp() AS lease_valid,
                    attempts,
                    max_attempts
                FROM analysis_jobs
                WHERE id = %s
                FOR UPDATE
                """,
                (job_id,),
            ).fetchone()
            if row is None:
                raise JobNotFound
            if (
                row["state"] != "running"
                or row["lease_owner"] != owner
                or row["lease_expires_at"] is None
                or not row["lease_valid"]
            ):
                raise JobStateConflict
            retryable = code in _RETRYABLE_CODES and row["attempts"] < row["max_attempts"]
            next_state: JobState = "retryable_failed" if retryable else "permanently_failed"
            backoff_seconds = min(2 ** int(row["attempts"]), MAX_BACKOFF_SECONDS)
            connection.execute(
                """
                UPDATE analysis_jobs
                SET state = %s,
                    available_at = CASE
                        WHEN %s THEN clock_timestamp() + (%s * interval '1 second')
                        ELSE available_at
                    END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    error_code = %s,
                    updated_at = clock_timestamp()
                WHERE id = %s
                """,
                (next_state, retryable, backoff_seconds, code.value, job_id),
            )
            return next_state


__all__ = [
    "AnalysisJobRecord",
    "DEFAULT_LEASE_SECONDS",
    "InvalidJobPayload",
    "JobNotFound",
    "JobQueue",
    "JobQueueError",
    "JobStateConflict",
    "psycopg_database_url",
]
