"""Explicit local reduction of an immutable earlier draft; never a provider cache hit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from familycare_worker.ai.policy_draft_normalization import (
    POLICY_DRAFT_NORMALIZATION_REVISION,
    PolicyDraftInvalid,
    normalize_policy_draft,
)
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.jobs import psycopg_database_url
from familycare_worker.policy_jobs import PolicyStructuringJobRecord
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeWork, _lock
from familycare_worker.policy_source_association import (
    load_local_members,
    member_identity_fingerprint,
)


@dataclass(frozen=True, repr=False)
class ReplayedPolicyDraft:
    batch: PolicyRangeBatch
    request_id: str


def _source(
    connection: psycopg.Connection[dict[str, Any]],
    job: PolicyStructuringJobRecord,
    work: PolicyRangeWork,
    source_request: UUID,
) -> dict[str, Any]:
    row = connection.execute(
        """
        SELECT old.id AS source_job_id, request.response_json, request.request_id,
          original_plan.associations_json AS old_associations,
          target_plan.associations_json AS new_associations,
          document_structure_projection(target.generation_id,%s,%s) AS structure_json
        FROM document_policy_ranges target
        JOIN policy_structuring_jobs current ON current.id=target.job_id
        JOIN document_policy_range_plans target_plan ON target_plan.job_id=current.id
        JOIN policy_provider_requests request ON request.id=%s AND request.state='SUCCEEDED'
        JOIN policy_structuring_jobs old ON old.id=request.job_id
        JOIN document_policy_ranges original ON original.job_id=old.id
          AND original.envelope_id=target.envelope_id
        JOIN document_policy_range_plans original_plan ON original_plan.job_id=old.id
        WHERE current.id=%s AND current.processing_mode='retained'
          AND current.pipeline_version='retained-policy-association-v4'
          AND old.id<>current.id AND old.processing_mode='retained'
          AND old.pipeline_version IN
            ('retained-policy-association-v2','retained-policy-association-v3')
          AND old.resubmission_of_job_id=current.resubmission_of_job_id
          AND old.household_space_id=current.household_space_id
          AND old.family_member_id=current.family_member_id
          AND old.document_version_id=current.document_version_id
          AND old.extraction_id=current.extraction_id AND old.batch_item_id=current.batch_item_id
          AND old.source_generation_id=current.source_generation_id
          AND original.generation_id=target.generation_id
          AND original.envelope_json=target.envelope_json
          AND original_plan.privacy_fingerprint=target_plan.privacy_fingerprint
          AND target.generation_id=%s AND target.envelope_id=%s
          AND target.envelope_json=%s AND target.state='PENDING'
          AND policy_structuring_source_current(old.id)
          AND policy_structuring_source_current(current.id)
        FOR SHARE OF old, original, original_plan, target_plan, request
        """,
        (
            job.household_space_id,
            sorted({item.page for item in work.envelope.evidence}),
            source_request,
            job.id,
            work.generation_id,
            work.envelope.envelope_id,
            Jsonb(work.envelope.to_provider_payload()),
        ),
    ).fetchone()
    if row is None or row["structure_json"] is None or not row["request_id"]:
        raise PolicyRangeConflict
    identity = member_identity_fingerprint(load_local_members(connection, job.household_space_id))
    if any(
        row[key].get("member_fingerprint") != identity
        for key in ("old_associations", "new_associations")
    ):
        raise PolicyRangeConflict
    try:
        response_json = json.dumps(
            row["response_json"], sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        batch = PolicyRangeBatch.model_validate_json(response_json, strict=True)
        normalized = normalize_policy_draft(
            batch,
            work.envelope,
            local_nodes={node["node_id"]: node for node in row["structure_json"]["nodes"]},
        )
    except ValueError, TypeError, KeyError, PolicyDraftInvalid, ValidationError:
        raise PolicyRangeConflict from None
    return {
        "source_job_id": row["source_job_id"],
        "source_response_hash": hashlib.sha256(response_json.encode()).hexdigest(),
        "normalization_revision": normalized.revision,
        "normalized_batch_json": normalized.batch.model_dump(mode="json"),
        "adjustments_json": json.loads(
            json.dumps([asdict(item) for item in normalized.adjustments], default=str)
        ),
        "partial": normalized.partial,
        "request_id": row["request_id"],
    }


def validate_replay_receipt(
    connection: psycopg.Connection[dict[str, Any]],
    job: PolicyStructuringJobRecord,
    work: PolicyRangeWork,
    *,
    expected_request: UUID | None = None,
) -> dict[str, Any] | None:
    receipt = connection.execute(
        "SELECT * FROM policy_range_replay_sources WHERE job_id=%s AND envelope_id=%s FOR SHARE",
        (job.id, work.envelope.envelope_id),
    ).fetchone()
    if receipt is None:
        if expected_request is not None:
            raise PolicyRangeConflict
        return None
    if (
        receipt["normalization_revision"] != POLICY_DRAFT_NORMALIZATION_REVISION
        or receipt["source_envelope_id"] != work.envelope.envelope_id
        or (
            expected_request is not None
            and receipt["source_provider_request_id"] != expected_request
        )
    ):
        raise PolicyRangeConflict
    expected = _source(connection, job, work, receipt["source_provider_request_id"])
    if any(receipt[key] != value for key, value in expected.items() if key != "request_id"):
        raise PolicyRangeConflict
    return {**receipt, "request_id": expected["request_id"]}


class PolicyDraftReplayRepository:
    """Caller explicitly selects one old provider request; other jobs cannot consume it."""

    def __init__(self, database_url: str, *, source_provider_request_id: UUID) -> None:
        if not isinstance(source_provider_request_id, UUID) or source_provider_request_id.int == 0:
            raise PolicyRangeConflict
        self.database_url = psycopg_database_url(database_url)
        self.source_request = source_provider_request_id

    def prepare(
        self, job: PolicyStructuringJobRecord, worker_id: str, work: PolicyRangeWork
    ) -> ReplayedPolicyDraft:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock(connection, job, worker_id)
            existing = connection.execute(
                "SELECT 1 FROM policy_range_replay_sources WHERE job_id=%s AND envelope_id=%s",
                (job.id, work.envelope.envelope_id),
            ).fetchone()
            if existing is None:
                source = _source(connection, job, work, self.source_request)
                connection.execute(
                    "INSERT INTO policy_range_replay_sources(job_id,envelope_id,source_job_id,"
                    "source_envelope_id,source_provider_request_id,source_response_hash,"
                    "normalization_revision,normalized_batch_json,adjustments_json,partial) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        job.id,
                        work.envelope.envelope_id,
                        source["source_job_id"],
                        work.envelope.envelope_id,
                        self.source_request,
                        source["source_response_hash"],
                        source["normalization_revision"],
                        Jsonb(source["normalized_batch_json"]),
                        Jsonb(source["adjustments_json"]),
                        source["partial"],
                    ),
                )
            receipt = validate_replay_receipt(
                connection, job, work, expected_request=self.source_request
            )
            assert receipt is not None
            return ReplayedPolicyDraft(
                PolicyRangeBatch.model_validate_json(
                    json.dumps(receipt["normalized_batch_json"]), strict=True
                ),
                receipt["request_id"],
            )

    def assert_current(
        self, job: PolicyStructuringJobRecord, worker_id: str, work: PolicyRangeWork
    ) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock(connection, job, worker_id)
            validate_replay_receipt(connection, job, work, expected_request=self.source_request)
