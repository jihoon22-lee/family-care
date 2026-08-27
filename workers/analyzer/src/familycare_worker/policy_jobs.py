"""Transactional PostgreSQL queue for private policy structuring."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.ai.provider import (
    ProviderBoundaryError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
    RetryableProviderError,
)
from familycare_worker.jobs import psycopg_database_url

DEFAULT_POLICY_STRUCTURING_LEASE_SECONDS = 180
MAX_POLICY_STRUCTURING_LEASE_SECONDS = 3_600
MAX_POLICY_STRUCTURING_BACKOFF_SECONDS = 300
MAX_POLICY_STRUCTURING_ATTEMPTS = 5

# Short aliases keep this queue's configuration readable beside the generic
# AnalysisJob queue while retaining an explicit policy namespace.
DEFAULT_LEASE_SECONDS = DEFAULT_POLICY_STRUCTURING_LEASE_SECONDS
MAX_LEASE_SECONDS = MAX_POLICY_STRUCTURING_LEASE_SECONDS
MAX_BACKOFF_SECONDS = MAX_POLICY_STRUCTURING_BACKOFF_SECONDS

_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_POLICY_STRUCTURING_STATES = frozenset(
    {
        "queued",
        "running",
        "succeeded",
        "retryable_failed",
        "permanently_failed",
        "cancelled",
    }
)
_POLICY_STRUCTURING_ERROR_CODES = frozenset(
    {
        "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
        "POLICY_STRUCTURING_INVALID_RESPONSE",
        "POLICY_STRUCTURING_NO_EVIDENCE",
        "POLICY_STRUCTURING_PROVIDER_TIMEOUT",
        "POLICY_STRUCTURING_RATE_LIMITED",
        "POLICY_STRUCTURING_UNAVAILABLE",
    }
)
POLICY_STRUCTURING_ERROR_CODES = _POLICY_STRUCTURING_ERROR_CODES
_PERMANENT_ERROR_CODES = frozenset(
    {
        "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
        "POLICY_STRUCTURING_INVALID_RESPONSE",
        "POLICY_STRUCTURING_NO_EVIDENCE",
    }
)
_RETRYABLE_ERROR_CODES = frozenset(
    {
        "POLICY_STRUCTURING_PROVIDER_TIMEOUT",
        "POLICY_STRUCTURING_RATE_LIMITED",
        "POLICY_STRUCTURING_UNAVAILABLE",
    }
)
_SAFE_JOB_COLUMNS = (
    "id, household_space_id, batch_item_id, family_member_id, document_version_id, "
    "extraction_id, policy_aggregate_id, state, pipeline_version, available_at, "
    "lease_owner, lease_expires_at, heartbeat_at, attempts, max_attempts, error_code, "
    "completed_at"
)
_SAFE_RETURNING_COLUMNS = ", ".join(
    f"job.{column.strip()}" for column in _SAFE_JOB_COLUMNS.split(",")
)

type PolicyStructuringErrorCode = Literal[
    "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
    "POLICY_STRUCTURING_INVALID_RESPONSE",
    "POLICY_STRUCTURING_NO_EVIDENCE",
    "POLICY_STRUCTURING_PROVIDER_TIMEOUT",
    "POLICY_STRUCTURING_RATE_LIMITED",
    "POLICY_STRUCTURING_UNAVAILABLE",
]
type PolicyStructuringJobState = Literal[
    "queued",
    "running",
    "succeeded",
    "retryable_failed",
    "permanently_failed",
    "cancelled",
]


class PolicyStructuringJobQueueError(RuntimeError):
    """Base queue error whose message contains no input or database data."""


class PolicyStructuringJobNotFound(PolicyStructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("POLICY_STRUCTURING_JOB_NOT_FOUND")


class PolicyStructuringJobStateConflict(PolicyStructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("POLICY_STRUCTURING_JOB_STATE_CONFLICT")


class InvalidPolicyStructuringJob(PolicyStructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("INVALID_POLICY_STRUCTURING_JOB")


class PolicyStructuringQueueUnavailable(PolicyStructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("POLICY_STRUCTURING_QUEUE_UNAVAILABLE")


class PolicyStructuringNoEvidenceError(PolicyStructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("POLICY_STRUCTURING_NO_EVIDENCE")


class PolicyStructuringRateLimitError(PolicyStructuringJobQueueError):
    def __init__(self) -> None:
        super().__init__("POLICY_STRUCTURING_RATE_LIMITED")


@dataclass(frozen=True, slots=True)
class PolicyStructuringJobRecord:
    """Validated policy-job metadata; document content never crosses this boundary."""

    id: UUID
    household_space_id: UUID
    batch_item_id: UUID
    family_member_id: UUID
    document_version_id: UUID
    extraction_id: UUID
    policy_aggregate_id: UUID
    state: PolicyStructuringJobState
    pipeline_version: str
    available_at: datetime
    lease_owner: str | None
    lease_expires_at: datetime | None
    heartbeat_at: datetime | None
    attempts: int
    max_attempts: int
    error_code: PolicyStructuringErrorCode | None


class PolicyStructuringQueue(Protocol):
    """Minimal queue boundary used by a future policy structuring runner."""

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> PolicyStructuringJobRecord | None: ...

    def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> bool: ...

    def complete_job(
        self,
        job_id: UUID,
        worker_id: str,
    ) -> bool: ...

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: PolicyStructuringErrorCode,
    ) -> PolicyStructuringJobState: ...

    def get_job(self, job_id: UUID) -> PolicyStructuringJobRecord | None: ...


def map_policy_structuring_error(error: BaseException) -> PolicyStructuringErrorCode:
    """Map provider failures to the closed policy-structuring error vocabulary."""

    if isinstance(error, ProviderConfigurationError):
        return "POLICY_STRUCTURING_AUTHENTICATION_FAILED"
    if isinstance(error, ProviderValidationError):
        return "POLICY_STRUCTURING_INVALID_RESPONSE"
    if isinstance(error, PolicyStructuringNoEvidenceError):
        return "POLICY_STRUCTURING_NO_EVIDENCE"
    if isinstance(error, PolicyStructuringRateLimitError):
        return "POLICY_STRUCTURING_RATE_LIMITED"
    if isinstance(error, ProviderRateLimitError):
        return "POLICY_STRUCTURING_RATE_LIMITED"
    if isinstance(error, ProviderUnavailableError):
        return "POLICY_STRUCTURING_UNAVAILABLE"
    if isinstance(error, ProviderTimeoutError | RetryableProviderError | TimeoutError):
        return "POLICY_STRUCTURING_PROVIDER_TIMEOUT"
    if isinstance(error, ProviderBoundaryError):
        return "POLICY_STRUCTURING_UNAVAILABLE"
    return "POLICY_STRUCTURING_UNAVAILABLE"


# Keep the provider-mapping spelling parallel with the existing event queue.
map_structuring_error = map_policy_structuring_error


def _validate_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or _WORKER_ID_PATTERN.fullmatch(worker_id) is None:
        raise ValueError("invalid worker identity")
    return worker_id


def _validate_lease_seconds(value: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 1 <= value <= MAX_POLICY_STRUCTURING_LEASE_SECONDS
    ):
        raise ValueError("invalid lease duration")
    return value


def _validate_job_id(job_id: UUID) -> UUID:
    if not isinstance(job_id, UUID):
        raise ValueError("invalid job id")
    return job_id


def _validate_error_code(value: object) -> PolicyStructuringErrorCode:
    if not isinstance(value, str) or value not in _POLICY_STRUCTURING_ERROR_CODES:
        raise ValueError("invalid policy structuring error code")
    return cast(PolicyStructuringErrorCode, value)


def _row_error_code(value: object) -> PolicyStructuringErrorCode | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in _POLICY_STRUCTURING_ERROR_CODES:
        raise InvalidPolicyStructuringJob
    return cast(PolicyStructuringErrorCode, value)


def _row_to_job(row: Mapping[str, object]) -> PolicyStructuringJobRecord:
    """Validate the queue projection and copy no unapproved joined columns."""

    try:
        values = {
            name: row[name]
            for name in (
                "id",
                "household_space_id",
                "batch_item_id",
                "family_member_id",
                "document_version_id",
                "extraction_id",
                "policy_aggregate_id",
            )
        }
        if not all(isinstance(value, UUID) for value in values.values()):
            raise InvalidPolicyStructuringJob
        state = row["state"]
        pipeline_version = row["pipeline_version"]
        available_at = row["available_at"]
        attempts = row["attempts"]
        max_attempts = row["max_attempts"]
        if state not in _POLICY_STRUCTURING_STATES:
            raise InvalidPolicyStructuringJob
        if (
            not isinstance(pipeline_version, str)
            or not pipeline_version.strip()
            or len(pipeline_version) > 64
        ):
            raise InvalidPolicyStructuringJob
        if not isinstance(available_at, datetime):
            raise InvalidPolicyStructuringJob
        if (
            isinstance(attempts, bool)
            or not isinstance(attempts, int)
            or isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or not 0 <= attempts <= max_attempts <= MAX_POLICY_STRUCTURING_ATTEMPTS
        ):
            raise InvalidPolicyStructuringJob
        lease_owner = row.get("lease_owner")
        lease_expires_at = row.get("lease_expires_at")
        heartbeat_at = row.get("heartbeat_at")
        if lease_owner is not None and (
            not isinstance(lease_owner, str) or _WORKER_ID_PATTERN.fullmatch(lease_owner) is None
        ):
            raise InvalidPolicyStructuringJob
        lease_values_present = all(
            value is not None for value in (lease_owner, lease_expires_at, heartbeat_at)
        )
        if state == "running" and not lease_values_present:
            raise InvalidPolicyStructuringJob
        if state != "running" and any(
            value is not None for value in (lease_owner, lease_expires_at, heartbeat_at)
        ):
            raise InvalidPolicyStructuringJob
        if any(
            value is not None and not isinstance(value, datetime)
            for value in (lease_expires_at, heartbeat_at)
        ):
            raise InvalidPolicyStructuringJob
        completed_at = row.get("completed_at")
        if state in {"succeeded", "permanently_failed", "cancelled"}:
            if completed_at is None:
                raise InvalidPolicyStructuringJob
        elif completed_at is not None:
            raise InvalidPolicyStructuringJob
        if completed_at is not None and not isinstance(completed_at, datetime):
            raise InvalidPolicyStructuringJob
        return PolicyStructuringJobRecord(
            id=cast(UUID, values["id"]),
            household_space_id=cast(UUID, values["household_space_id"]),
            batch_item_id=cast(UUID, values["batch_item_id"]),
            family_member_id=cast(UUID, values["family_member_id"]),
            document_version_id=cast(UUID, values["document_version_id"]),
            extraction_id=cast(UUID, values["extraction_id"]),
            policy_aggregate_id=cast(UUID, values["policy_aggregate_id"]),
            state=cast(PolicyStructuringJobState, state),
            pipeline_version=pipeline_version,
            available_at=available_at,
            lease_owner=lease_owner,
            lease_expires_at=cast(datetime | None, lease_expires_at),
            heartbeat_at=cast(datetime | None, heartbeat_at),
            attempts=attempts,
            max_attempts=max_attempts,
            error_code=_row_error_code(row.get("error_code")),
        )
    except KeyError, TypeError, ValueError:
        raise InvalidPolicyStructuringJob from None


def _next_failure_state(
    error_code: PolicyStructuringErrorCode | str,
    attempts: int,
    max_attempts: int,
) -> tuple[PolicyStructuringJobState, bool]:
    """Return the sanitized state transition and whether another try is allowed."""

    code = _validate_error_code(error_code)
    if (
        isinstance(attempts, bool)
        or not isinstance(attempts, int)
        or isinstance(max_attempts, bool)
        or not isinstance(max_attempts, int)
        or not 0 <= attempts <= max_attempts <= MAX_POLICY_STRUCTURING_ATTEMPTS
    ):
        raise ValueError("invalid policy structuring attempts")
    will_retry = code in _RETRYABLE_ERROR_CODES and attempts < max_attempts
    return ("retryable_failed" if will_retry else "permanently_failed", will_retry)


def _backoff_seconds(attempts: int) -> int:
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
        raise ValueError("invalid policy structuring attempts")
    return int(min(2**attempts, MAX_POLICY_STRUCTURING_BACKOFF_SECONDS))


class PolicyStructuringJobQueue:
    """Open short-lived connections for lease-safe policy queue operations."""

    def __init__(
        self,
        database_url: str,
        *,
        default_lease_seconds: int = DEFAULT_POLICY_STRUCTURING_LEASE_SECONDS,
    ) -> None:
        self.database_url = psycopg_database_url(database_url)
        self.default_lease_seconds = _validate_lease_seconds(default_lease_seconds)

    @staticmethod
    def _select_sql() -> str:
        return f"""
            SELECT {_SAFE_JOB_COLUMNS}
            FROM policy_structuring_jobs
            WHERE id = %s
        """

    @staticmethod
    def _claim_sql() -> str:
        return f"""
            WITH candidate AS (
                SELECT job.id
                FROM policy_structuring_jobs AS job
                WHERE job.state IN ('queued', 'retryable_failed')
                  AND job.available_at <= clock_timestamp()
                  AND job.attempts < job.max_attempts
                ORDER BY job.available_at, job.created_at, job.id
                FOR UPDATE OF job SKIP LOCKED
                LIMIT 1
            )
            UPDATE policy_structuring_jobs AS job
            SET state = 'running',
                lease_owner = %s,
                lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                heartbeat_at = clock_timestamp(),
                attempts = job.attempts + 1,
                error_code = NULL,
                updated_at = clock_timestamp()
            FROM candidate
            WHERE job.id = candidate.id
            RETURNING {_SAFE_RETURNING_COLUMNS}
        """

    @staticmethod
    def _recover_expired_sql() -> str:
        return """
            UPDATE policy_structuring_jobs
            SET state = CASE
                    WHEN attempts >= max_attempts THEN 'permanently_failed'
                    ELSE 'retryable_failed'
                END,
                available_at = clock_timestamp(),
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                error_code = 'POLICY_STRUCTURING_PROVIDER_TIMEOUT',
                completed_at = CASE
                    WHEN attempts >= max_attempts THEN clock_timestamp()
                    ELSE NULL
                END,
                updated_at = clock_timestamp()
            WHERE state = 'running'
              AND lease_expires_at <= clock_timestamp()
        """

    @staticmethod
    def _finalize_exhausted_sql() -> str:
        return """
            UPDATE policy_structuring_jobs
            SET state = 'permanently_failed',
                lease_owner = NULL,
                lease_expires_at = NULL,
                heartbeat_at = NULL,
                error_code = COALESCE(error_code, 'POLICY_STRUCTURING_UNAVAILABLE'),
                completed_at = clock_timestamp(),
                updated_at = clock_timestamp()
            WHERE state IN ('queued', 'retryable_failed')
              AND attempts >= max_attempts
        """

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> PolicyStructuringJobRecord | None:
        """Recover expired work, then claim one due job with a row lock."""

        owner = _validate_worker_id(worker_id)
        lease = _validate_lease_seconds(
            self.default_lease_seconds if lease_seconds is None else lease_seconds
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute(self._recover_expired_sql())
                connection.execute(self._finalize_exhausted_sql())
                row = connection.execute(self._claim_sql(), (owner, lease)).fetchone()
                if row is None:
                    return None
                try:
                    return _row_to_job(row)
                except InvalidPolicyStructuringJob:
                    connection.execute(
                        """
                        UPDATE policy_structuring_jobs
                        SET state = 'permanently_failed',
                            lease_owner = NULL,
                            lease_expires_at = NULL,
                            heartbeat_at = NULL,
                            error_code = 'POLICY_STRUCTURING_INVALID_RESPONSE',
                            completed_at = clock_timestamp(),
                            updated_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (row["id"],),
                    )
                    return None
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None

    def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> bool:
        """Extend a live lease only for its current owner."""

        target = _validate_job_id(job_id)
        owner = _validate_worker_id(worker_id)
        lease = _validate_lease_seconds(
            self.default_lease_seconds if lease_seconds is None else lease_seconds
        )
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    """
                    UPDATE policy_structuring_jobs
                    SET heartbeat_at = clock_timestamp(),
                        lease_expires_at = clock_timestamp() + (%s * interval '1 second'),
                        updated_at = clock_timestamp()
                    WHERE id = %s
                      AND state = 'running'
                      AND lease_owner = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING id
                    """,
                    (lease, target, owner),
                ).fetchone()
                return row is not None
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None

    def get_job(self, job_id: UUID) -> PolicyStructuringJobRecord | None:
        """Read only the policy-job projection, never source or provider content."""

        target = _validate_job_id(job_id)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(self._select_sql(), (target,)).fetchone()
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None
        return _row_to_job(row) if row is not None else None

    def complete_job(
        self,
        job_id: UUID | PolicyStructuringJobRecord,
        worker_id: str,
    ) -> bool:
        """Complete a job only while its lease is live and owned by the caller."""

        target = (
            job_id.id
            if isinstance(job_id, PolicyStructuringJobRecord)
            else _validate_job_id(job_id)
        )
        owner = _validate_worker_id(worker_id)
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    """
                    UPDATE policy_structuring_jobs
                    SET state = 'succeeded',
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        error_code = NULL,
                        completed_at = clock_timestamp(),
                        updated_at = clock_timestamp()
                    WHERE id = %s
                      AND state = 'running'
                      AND lease_owner = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING id
                    """,
                    (target, owner),
                ).fetchone()
                if row is not None:
                    return True
                exists = connection.execute(
                    "SELECT id FROM policy_structuring_jobs WHERE id = %s",
                    (target,),
                ).fetchone()
                if exists is None:
                    raise PolicyStructuringJobNotFound
                raise PolicyStructuringJobStateConflict
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None

    def fail_job(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: PolicyStructuringErrorCode,
    ) -> PolicyStructuringJobState:
        """Record a sanitized retryable or permanent failure for a live claim."""

        target = _validate_job_id(job_id)
        owner = _validate_worker_id(worker_id)
        code = _validate_error_code(error_code)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT state, lease_owner, lease_expires_at,
                           lease_expires_at > clock_timestamp() AS lease_valid,
                           attempts, max_attempts
                    FROM policy_structuring_jobs
                    WHERE id = %s
                    FOR UPDATE
                    """,
                    (target,),
                ).fetchone()
                if row is None:
                    raise PolicyStructuringJobNotFound
                if (
                    row["state"] != "running"
                    or row["lease_owner"] != owner
                    or row["lease_valid"] is not True
                ):
                    raise PolicyStructuringJobStateConflict
                next_state, will_retry = _next_failure_state(
                    code,
                    row["attempts"],
                    row["max_attempts"],
                )
                backoff_seconds = _backoff_seconds(row["attempts"])
                connection.execute(
                    """
                    UPDATE policy_structuring_jobs
                    SET state = %s,
                        available_at = CASE
                            WHEN %s THEN clock_timestamp() + (%s * interval '1 second')
                            ELSE available_at
                        END,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        heartbeat_at = NULL,
                        error_code = %s,
                        completed_at = CASE
                            WHEN %s THEN NULL
                            ELSE clock_timestamp()
                        END,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (next_state, will_retry, backoff_seconds, code, will_retry, target),
                )
                return next_state
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None


__all__ = [
    "DEFAULT_LEASE_SECONDS",
    "DEFAULT_POLICY_STRUCTURING_LEASE_SECONDS",
    "InvalidPolicyStructuringJob",
    "MAX_BACKOFF_SECONDS",
    "MAX_LEASE_SECONDS",
    "MAX_POLICY_STRUCTURING_ATTEMPTS",
    "MAX_POLICY_STRUCTURING_BACKOFF_SECONDS",
    "MAX_POLICY_STRUCTURING_LEASE_SECONDS",
    "POLICY_STRUCTURING_ERROR_CODES",
    "PolicyStructuringErrorCode",
    "PolicyStructuringJobNotFound",
    "PolicyStructuringJobQueue",
    "PolicyStructuringJobQueueError",
    "PolicyStructuringJobRecord",
    "PolicyStructuringJobState",
    "PolicyStructuringJobStateConflict",
    "PolicyStructuringNoEvidenceError",
    "PolicyStructuringQueue",
    "PolicyStructuringQueueUnavailable",
    "PolicyStructuringRateLimitError",
    "_backoff_seconds",
    "_next_failure_state",
    "_row_to_job",
    "map_structuring_error",
    "map_policy_structuring_error",
]
