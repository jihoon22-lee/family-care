"""Durable review reservations with no automatic resend after an ambiguous outcome."""

from __future__ import annotations

import re
from dataclasses import asdict
from typing import Literal
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.ai.provider import ProviderCallMetadata
from familycare_worker.guidance_review_jobs import ReviewLease
from familycare_worker.policy_request_budget import PolicyRequestBudget
from familycare_worker.provider_quota import request_counts


class ReviewBudgetRejected(RuntimeError):
    def __init__(self) -> None:
        super().__init__("REVIEW_BUDGET_REJECTED")


class GuidanceReviewBudget:
    def __init__(self, database_url: str, *, per_document: int = 4, daily: int = 8) -> None:
        shared = PolicyRequestBudget(database_url, per_document=per_document, daily=daily)
        self.database_url = shared.database_url
        self.per_document, self.daily = per_document, daily

    def reserve(
        self,
        job: ReviewLease,
        *,
        phase: Literal["discover", "compare"],
        document_ids: tuple[UUID, ...],
        fingerprint: str,
        input_tokens: int,
        output_tokens: int,
    ) -> UUID:
        if (
            phase not in ("discover", "compare")
            or not isinstance(document_ids, tuple)
            or not 1 <= len(document_ids) <= 128
            or any(not isinstance(value, UUID) for value in document_ids)
            or len(set(document_ids)) != len(document_ids)
            or not isinstance(fingerprint, str)
            or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
            or type(input_tokens) is not int
            or not 1 <= input_tokens <= 32768
            or type(output_tokens) is not int
            or not 1 <= output_tokens <= 4000
        ):
            raise ReviewBudgetRejected
        documents = sorted(document_ids)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                connection.execute("SELECT pg_advisory_xact_lock(523019,28)")
                owned = connection.execute(
                    "SELECT j.id FROM guidance_review_jobs j JOIN medical_events e "
                    "ON e.id=j.medical_event_id AND e.household_space_id=j.household_space_id "
                    "WHERE j.id=%s AND j.household_space_id=%s AND j.family_member_id=%s "
                    "AND j.lease_token=%s AND j.input_digest=%s AND j.source_digest=%s "
                    "AND j.state='running' AND j.http_attempts<2 "
                    "AND j.deadline_at>clock_timestamp() AND j.lease_expires_at>clock_timestamp() "
                    "AND e.family_member_id=j.family_member_id AND e.version=j.event_version "
                    "AND e.deleted_at IS NULL FOR UPDATE OF j FOR SHARE OF e",
                    (
                        job.id,
                        job.household_space_id,
                        job.family_member_id,
                        job.lease_token,
                        job.input_digest,
                        job.source_digest,
                    ),
                ).fetchone()
                if owned is None:
                    raise ReviewBudgetRejected
                used = connection.execute(
                    "SELECT coalesce(sum(input_token_bound),0) AS input, "
                    "coalesce(sum(output_token_bound),0) AS output, "
                    "count(*) FILTER(WHERE phase=%s) AS same_phase "
                    "FROM guidance_review_requests WHERE review_job_id=%s",
                    (phase, job.id),
                ).fetchone()
                assert used is not None
                if (
                    used["same_phase"]
                    or used["input"] + input_tokens > 65536
                    or used["output"] + output_tokens > 8000
                ):
                    raise ReviewBudgetRejected
                for document_id in documents:
                    document_count, daily_count = request_counts(connection, document_id)
                    if document_count >= self.per_document or daily_count >= self.daily:
                        raise ReviewBudgetRejected
                row = connection.execute(
                    "INSERT INTO guidance_review_requests(review_job_id,phase,document_ids,"
                    "fingerprint,input_token_bound,output_token_bound) "
                    "VALUES(%s,%s,%s,%s,%s,%s) RETURNING id",
                    (job.id, phase, documents, fingerprint, input_tokens, output_tokens),
                ).fetchone()
                assert row is not None
                connection.execute(
                    "UPDATE guidance_review_jobs SET http_attempts=http_attempts+1 WHERE id=%s",
                    (job.id,),
                )
                return UUID(str(row["id"]))
        except psycopg.Error:
            raise ReviewBudgetRejected from None

    def finish(
        self, reservation: UUID, *, succeeded: bool, metadata: ProviderCallMetadata | None
    ) -> None:
        """Late accounting can settle independently of cancellation or an expired lease."""
        if type(succeeded) is not bool or (
            metadata is not None and not isinstance(metadata, ProviderCallMetadata)
        ):
            raise ReviewBudgetRejected
        usage = asdict(metadata.usage) if metadata and metadata.usage else None
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    "UPDATE guidance_review_requests SET state=%s,settled_at=clock_timestamp(),"
                    "usage_json=%s,model=%s,service_tier=%s WHERE id=%s AND state='RESERVED' "
                    "RETURNING id",
                    (
                        "SUCCEEDED" if succeeded else "FAILED",
                        Jsonb(usage) if usage else None,
                        metadata.model if metadata else None,
                        metadata.service_tier if metadata else None,
                        reservation,
                    ),
                ).fetchone()
                if row is None:
                    raise ReviewBudgetRejected
        except psycopg.Error:
            raise ReviewBudgetRejected from None
