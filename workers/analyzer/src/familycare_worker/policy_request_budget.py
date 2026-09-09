"""Durable request reservations and private response reuse; no source text in metadata."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.ai.provider import (
    AiProvider,
    ProviderBoundaryError,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderUnavailableError,
    ProviderValidationError,
)
from familycare_worker.jobs import psycopg_database_url
from familycare_worker.policy_jobs import PolicyStructuringJobRecord
from familycare_worker.provider_quota import request_counts


class PolicyBudgetExhausted(ProviderRateLimitError):
    def __init__(self) -> None:
        ProviderBoundaryError.__init__(self, "POLICY_PROVIDER_BUDGET_EXHAUSTED")


class PolicyRequestBudget:
    """Serialize short reservations globally; never hold a transaction over the network."""

    def __init__(self, database_url: str, *, per_document: int = 4, daily: int = 8) -> None:
        if any(type(value) is not int or not 1 <= value <= 256 for value in (per_document, daily)):
            raise ProviderConfigurationError
        self.database_url = psycopg_database_url(database_url)
        self.per_document = per_document
        self.daily = daily

    def reserve(
        self,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        fingerprint: str,
    ) -> UUID | ProviderResponse:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout = '5s'")
                connection.execute("SELECT pg_advisory_xact_lock(523019, 28)")
                scope = connection.execute(
                    """
                    SELECT v.document_id FROM policy_structuring_jobs j
                    JOIN document_versions v ON v.id = j.document_version_id
                    JOIN documents d ON d.id = v.document_id AND d.deleted_at IS NULL
                    WHERE j.id = %s AND j.household_space_id = %s AND j.family_member_id = %s
                      AND j.document_version_id = %s AND j.extraction_id = %s
                      AND j.pipeline_version = %s AND j.state = 'running'
                      AND j.lease_owner = %s AND j.attempts = %s
                      AND j.lease_expires_at > clock_timestamp()
                    FOR SHARE OF j, d
                    """,
                    (
                        job.id,
                        job.household_space_id,
                        job.family_member_id,
                        job.document_version_id,
                        job.extraction_id,
                        job.pipeline_version,
                        worker_id,
                        job.attempts,
                    ),
                ).fetchone()
                if scope is None:
                    raise ProviderUnavailableError
                cached = connection.execute(
                    "SELECT response_json, request_id FROM policy_provider_requests "
                    "WHERE job_id = %s AND fingerprint = %s AND state = 'SUCCEEDED'",
                    (job.id, fingerprint),
                ).fetchone()
                if cached is not None:
                    return ProviderResponse(
                        payload=cached["response_json"], request_id=cached["request_id"]
                    )
                connection.execute(
                    "UPDATE policy_provider_requests SET state = 'FAILED' "
                    "WHERE job_id = %s AND fingerprint = %s AND state = 'RESERVED' "
                    "AND expires_at <= clock_timestamp()",
                    (job.id, fingerprint),
                )
                busy = connection.execute(
                    "SELECT 1 FROM policy_provider_requests WHERE job_id = %s "
                    "AND fingerprint = %s AND state = 'RESERVED'",
                    (job.id, fingerprint),
                ).fetchone()
                if busy:
                    raise ProviderUnavailableError
                document_requests, daily_requests = request_counts(connection, scope["document_id"])
                if document_requests >= self.per_document or daily_requests >= self.daily:
                    raise PolicyBudgetExhausted
                row = connection.execute(
                    "INSERT INTO policy_provider_requests(job_id, document_id, fingerprint, state) "
                    "VALUES (%s, %s, %s, 'RESERVED') RETURNING id",
                    (job.id, scope["document_id"], fingerprint),
                ).fetchone()
                assert row is not None
                return UUID(str(row["id"]))
        except psycopg.Error:
            raise ProviderUnavailableError from None

    def finish(self, reservation: UUID, response: ProviderResponse | None) -> None:
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    """
                    UPDATE policy_provider_requests
                    SET state = %s, response_json = %s, request_id = %s
                    WHERE id = %s AND state = 'RESERVED' AND expires_at > clock_timestamp()
                    RETURNING id
                    """,
                    (
                        "FAILED" if response is None else "SUCCEEDED",
                        None if response is None else Jsonb(dict(response.payload)),
                        None if response is None else response.request_id,
                        reservation,
                    ),
                ).fetchone()
                if row is None:
                    raise ProviderUnavailableError
        except psycopg.Error:
            raise ProviderUnavailableError from None

    def pause(self, job: PolicyStructuringJobRecord, worker_id: str) -> None:
        """A budget wait does not consume a processing retry or lose retained stages."""
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    """
                    UPDATE policy_structuring_jobs
                    SET state = 'retryable_failed', attempts = attempts - 1,
                      available_at = (date_trunc('day', clock_timestamp() AT TIME ZONE 'UTC')
                          + interval '1 day') AT TIME ZONE 'UTC',
                      lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                      error_code = 'POLICY_STRUCTURING_RATE_LIMITED',
                      updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND family_member_id = %s
                      AND state = 'running' AND lease_owner = %s AND attempts = %s
                      AND lease_expires_at > clock_timestamp() AND attempts > 0
                    RETURNING id
                    """,
                    (job.id, job.household_space_id, job.family_member_id, worker_id, job.attempts),
                ).fetchone()
                if row is None:
                    raise ProviderUnavailableError
        except psycopg.Error:
            raise ProviderUnavailableError from None


class BudgetedPolicyProvider:
    """Cache each exact stage so a verifier retry reuses its earlier structurer response."""

    def __init__(
        self,
        *,
        provider: AiProvider,
        budget: PolicyRequestBudget,
        job: PolicyStructuringJobRecord,
        worker_id: str,
    ) -> None:
        self.provider = provider
        self.budget = budget
        self.job = job
        self.worker_id = worker_id
        self.budget_exhausted = False

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        try:
            encoded = json.dumps(
                [model, schema_name, system_instruction, dict(input_payload)],
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
        except TypeError, ValueError, OverflowError, RecursionError:
            raise ProviderValidationError from None
        if len(encoded) > 131072:
            raise ProviderValidationError
        fingerprint = hashlib.sha256(encoded).hexdigest()
        try:
            reserved = self.budget.reserve(self.job, self.worker_id, fingerprint)
        except PolicyBudgetExhausted:
            self.budget_exhausted = True
            raise
        if isinstance(reserved, ProviderResponse):
            return reserved
        try:
            response = self.provider.complete(
                model=model,
                schema_name=schema_name,
                system_instruction=system_instruction,
                input_payload=input_payload,
            )
        except Exception:
            self.budget.finish(reserved, None)
            raise
        self.budget.finish(reserved, response)
        return response
