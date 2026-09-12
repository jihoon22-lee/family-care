"""Explicit, idempotent processing revisions over an unchanged retained policy source."""

from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_worker.jobs import psycopg_database_url
from familycare_worker.policy_jobs import (
    _SAFE_JOB_COLUMNS,
    _SAFE_RETURNING_COLUMNS,
    PolicyStructuringJobQueue,
    PolicyStructuringJobRecord,
    PolicyStructuringQueueUnavailable,
    _row_to_job,
    _validate_job_id,
    _validate_lease_seconds,
    _validate_worker_id,
)

# This identifies local association/grounding semantics independently of the immutable IR.
# Advancing it permits a new explicit run; it never schedules one automatically.
RETAINED_POLICY_PIPELINE_REVISION = "retained-policy-association-v7"
SOURCE_SCOPED_POLICY_PIPELINE_REVISION = "retained-policy-association-v8"


class RetainedPolicyConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("RETAINED_POLICY_SOURCE_CONFLICT")


def _identity(value: UUID) -> UUID:
    if not isinstance(value, UUID) or value.int == 0:
        raise RetainedPolicyConflict
    return value


def _revision(value: str) -> str:
    if value not in {RETAINED_POLICY_PIPELINE_REVISION, SOURCE_SCOPED_POLICY_PIPELINE_REVISION}:
        raise RetainedPolicyConflict
    return value


class RetainedPolicyRepository:
    """Append work only; callers separately authorize and target provider execution."""

    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def enqueue(
        self,
        *,
        household_space_id: UUID,
        source_job_id: UUID,
        expected_generation_id: UUID,
        pipeline_revision: str = RETAINED_POLICY_PIPELINE_REVISION,
    ) -> PolicyStructuringJobRecord:
        household, source, generation = map(
            _identity, (household_space_id, source_job_id, expected_generation_id)
        )
        revision = _revision(pipeline_revision)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                # Serialize requests for this source before the idempotency read. Pin the
                # generation through insertion; a changed source must not silently replace it.
                selected = connection.execute(
                    "SELECT original.id FROM policy_structuring_jobs original "
                    "JOIN document_structure_generations generation ON generation.id=%s "
                    "WHERE original.id=%s AND original.household_space_id=%s "
                    "AND generation.is_current AND NOT generation.cancelled "
                    "AND retained_policy_source_is_current(original.id,generation.id,%s) "
                    "FOR UPDATE OF original FOR SHARE OF generation",
                    (generation, source, household, household),
                ).fetchone()
                if selected is None:
                    raise RetainedPolicyConflict
                existing = connection.execute(
                    f"SELECT {_SAFE_JOB_COLUMNS} FROM policy_structuring_jobs "
                    "WHERE resubmission_of_job_id=%s AND source_generation_id=%s "
                    "AND pipeline_version=%s AND processing_mode='retained'",
                    (source, generation, revision),
                ).fetchone()
                if existing is not None:
                    return _row_to_job(existing)
                row = connection.execute(
                    "INSERT INTO policy_structuring_jobs (household_space_id,batch_item_id,"
                    "family_member_id,document_version_id,extraction_id,pipeline_version,"
                    "processing_mode,resubmission_of_job_id,source_generation_id) "
                    "SELECT household_space_id,batch_item_id,family_member_id,document_version_id,"
                    "extraction_id,%s,'retained',id,%s FROM policy_structuring_jobs WHERE id=%s "
                    f"RETURNING {_SAFE_JOB_COLUMNS}",
                    (revision, generation, source),
                ).fetchone()
                if row is None:
                    raise RetainedPolicyConflict
                return _row_to_job(row)
        except psycopg.IntegrityError:
            raise RetainedPolicyConflict from None
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None


class RetainedPolicyJobQueue(PolicyStructuringJobQueue):
    """Run the existing bounded policy runner against one explicitly selected job."""

    def __init__(
        self,
        database_url: str,
        *,
        household_space_id: UUID,
        job_id: UUID,
        pipeline_revision: str = RETAINED_POLICY_PIPELINE_REVISION,
    ) -> None:
        super().__init__(database_url)
        self.household_space_id = _identity(household_space_id)
        self.job_id = _identity(job_id)
        self.pipeline_revision = _revision(pipeline_revision)

    def claim_next_job(
        self,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> PolicyStructuringJobRecord | None:
        owner = _validate_worker_id(worker_id)
        lease = _validate_lease_seconds(
            self.default_lease_seconds if lease_seconds is None else lease_seconds
        )
        scope = (self.job_id, self.household_space_id, self.pipeline_revision)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                current = connection.execute(
                    "SELECT job.id FROM policy_structuring_jobs job "
                    "JOIN document_structure_generations generation "
                    "ON generation.id=job.source_generation_id "
                    "WHERE job.id=%s AND job.household_space_id=%s AND job.pipeline_version=%s "
                    "AND job.processing_mode='retained' "
                    "AND generation.is_current AND NOT generation.cancelled "
                    "AND policy_structuring_source_current(job.id) "
                    "FOR UPDATE OF job SKIP LOCKED FOR SHARE OF generation",
                    scope,
                ).fetchone()
                if current is None:
                    return None
                connection.execute(
                    "UPDATE policy_structuring_jobs SET "
                    "state=CASE WHEN attempts>=max_attempts THEN 'permanently_failed' "
                    "ELSE 'retryable_failed' END,available_at=clock_timestamp(),lease_owner=NULL,"
                    "lease_expires_at=NULL,heartbeat_at=NULL,"
                    "error_code='POLICY_STRUCTURING_PROVIDER_TIMEOUT',"
                    "completed_at=CASE WHEN attempts>=max_attempts THEN clock_timestamp() "
                    "ELSE NULL END,updated_at=clock_timestamp() "
                    "WHERE id=%s AND household_space_id=%s AND pipeline_version=%s "
                    "AND processing_mode='retained' AND state='running' "
                    "AND lease_expires_at<=clock_timestamp()",
                    scope,
                )
                connection.execute(
                    "UPDATE policy_structuring_jobs SET state='permanently_failed',"
                    "lease_owner=NULL,lease_expires_at=NULL,heartbeat_at=NULL,"
                    "error_code=COALESCE(error_code,'POLICY_STRUCTURING_UNAVAILABLE'),"
                    "completed_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "WHERE id=%s AND household_space_id=%s AND pipeline_version=%s "
                    "AND processing_mode='retained' AND state IN ('queued','retryable_failed') "
                    "AND attempts>=max_attempts",
                    scope,
                )
                row = connection.execute(
                    "UPDATE policy_structuring_jobs job SET state='running',lease_owner=%s,"
                    "lease_expires_at=clock_timestamp()+(%s*interval '1 second'),"
                    "heartbeat_at=clock_timestamp(),attempts=job.attempts+1,error_code=NULL,"
                    "updated_at=clock_timestamp() "
                    "WHERE job.id=%s AND job.household_space_id=%s AND job.pipeline_version=%s "
                    "AND job.processing_mode='retained' "
                    "AND job.state IN ('queued','retryable_failed') "
                    "AND job.available_at<=clock_timestamp() AND job.attempts<job.max_attempts "
                    "AND policy_structuring_source_current(job.id) "
                    f"RETURNING {_SAFE_RETURNING_COLUMNS}",
                    (owner, lease, *scope),
                ).fetchone()
                return _row_to_job(row) if row is not None else None
        except psycopg.Error:
            raise PolicyStructuringQueueUnavailable from None

    def heartbeat(
        self,
        job_id: UUID,
        worker_id: str,
        *,
        lease_seconds: int | None = None,
    ) -> bool:
        if _validate_job_id(job_id) != self.job_id:
            return False
        return super().heartbeat(job_id, worker_id, lease_seconds=lease_seconds)
