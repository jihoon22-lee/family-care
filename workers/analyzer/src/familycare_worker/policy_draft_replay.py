"""Explicit local reduction of an immutable earlier draft; never a provider cache hit."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from familycare_worker.ai.policy_draft_normalization import (
    CERTIFICATE_TITLE_NORMALIZATION_REVISION,
    POLICY_DRAFT_NORMALIZATION_REVISION,
    SOURCE_SCOPED_NORMALIZATION_REVISION,
    PolicyDraftAdjustment,
    PolicyDraftInvalid,
    PolicyDraftNormalization,
    normalize_policy_draft,
)
from familycare_worker.ai.range_structurer import PolicyRangeBatch, RangeDisposition
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.jobs import psycopg_database_url
from familycare_worker.policy_jobs import PolicyStructuringJobRecord
from familycare_worker.policy_range_repository import PolicyRangeConflict, PolicyRangeWork, _lock
from familycare_worker.policy_source_association import (
    load_local_members,
    member_identity_fingerprint,
)

SOURCE_SCOPED_POLICY_PIPELINES = frozenset({"retained-policy-association-v8"})
CERTIFICATE_TITLE_PIPELINES = SOURCE_SCOPED_POLICY_PIPELINES | frozenset(
    {"policy-range-normalized-v2", "retained-policy-association-v7"}
)
NORMALIZED_POLICY_PIPELINES = (
    frozenset({"policy-range-normalized-v1", "retained-policy-association-v6"})
    | CERTIFICATE_TITLE_PIPELINES
)


def normalization_revision(pipeline_version: str) -> str:
    return (
        SOURCE_SCOPED_NORMALIZATION_REVISION
        if pipeline_version in SOURCE_SCOPED_POLICY_PIPELINES
        else CERTIFICATE_TITLE_NORMALIZATION_REVISION
        if pipeline_version in CERTIFICATE_TITLE_PIPELINES
        else POLICY_DRAFT_NORMALIZATION_REVISION
    )


def _current_source(
    connection: psycopg.Connection[dict[str, Any]],
    job: PolicyStructuringJobRecord,
    work: PolicyRangeWork,
) -> None:
    """Bind initial automatic work to its live source, without a terminal-job guard."""
    connection.execute(
        "SELECT id FROM document_batch_items WHERE id=%s FOR UPDATE", (job.batch_item_id,)
    )
    row = connection.execute(
        """
        SELECT generation.id, plan.associations_json
        FROM policy_structuring_jobs current
        JOIN document_batch_items item ON item.id=current.batch_item_id
          AND item.state='succeeded' AND item.document_kind='policy'
        JOIN document_batches batch ON batch.id=item.batch_id
          AND batch.household_space_id=current.household_space_id
          AND batch.family_member_id=current.family_member_id AND batch.state<>'cancelled'
        JOIN document_versions version ON version.id=current.document_version_id
          AND version.document_id=item.document_id
        JOIN documents document ON document.id=version.document_id
          AND document.deleted_at IS NULL AND document.document_kind='policy'
        JOIN extractions extraction ON extraction.id=current.extraction_id
          AND extraction.document_version_id=version.id AND extraction.status='succeeded'
        JOIN family_members member ON member.id=current.family_member_id
          AND member.household_space_id=current.household_space_id AND member.deleted_at IS NULL
        JOIN document_structure_generations generation ON generation.id=%s
          AND generation.household_space_id=current.household_space_id
          AND generation.family_member_id=current.family_member_id
          AND generation.batch_item_id=item.id AND generation.document_version_id=version.id
          AND generation.extraction_id=extraction.id
          AND generation.is_current AND NOT generation.cancelled
        JOIN document_policy_range_plans plan ON plan.job_id=current.id
          AND plan.generation_id=generation.id AND plan.state='PROCESSING'
        JOIN document_policy_ranges target ON target.job_id=current.id
          AND target.generation_id=generation.id
          AND target.envelope_id=%s AND target.envelope_json=%s AND target.state='PENDING'
        WHERE current.id=%s AND current.household_space_id=%s
          AND ((current.pipeline_version IN
                ('policy-range-normalized-v1','policy-range-normalized-v2')
                AND current.processing_mode='automatic')
            OR (current.pipeline_version IN
                ('retained-policy-association-v6','retained-policy-association-v7',
                 'retained-policy-association-v8')
                AND current.processing_mode='retained'))
          AND policy_structuring_source_current(current.id)
          AND (item.processed_document_version_id IS NULL
            OR item.processed_document_version_id=version.id)
        FOR SHARE OF generation, plan, target, batch, version, extraction, document, member
        """,
        (
            work.generation_id,
            work.envelope.envelope_id,
            Jsonb(work.envelope.to_provider_payload()),
            job.id,
            job.household_space_id,
        ),
    ).fetchone()
    identity = member_identity_fingerprint(load_local_members(connection, job.household_space_id))
    if row is None or row["associations_json"].get("member_fingerprint") != identity:
        raise PolicyRangeConflict


@dataclass(frozen=True, repr=False)
class ReplayedPolicyDraft:
    batch: PolicyRangeBatch
    request_id: str


def _preserve_verified_riders(
    connection: psycopg.Connection[dict[str, Any]],
    job: PolicyStructuringJobRecord,
    work: PolicyRangeWork,
    normalized: PolicyDraftNormalization,
) -> PolicyDraftNormalization:
    """Exclude unchanged, still-current v7 Riders from a new v8 verifier request.

    This grants no new approval and creates no copy. The existing candidate remains
    the authority-bearing record; its exact fields, citations and source must match.
    The new range remains partial, accounting for every omitted candidate.
    """
    rows = connection.execute(
        "SELECT old.id AS job_id,r.result_json FROM policy_structuring_jobs old "
        "JOIN document_policy_range_plans p ON p.job_id=old.id "
        "JOIN document_policy_range_plans current ON current.job_id=%s "
        "JOIN document_policy_ranges r ON r.job_id=old.id AND r.generation_id=p.generation_id "
        "WHERE old.id<>%s AND old.pipeline_version='retained-policy-association-v7' "
        "AND old.processing_mode='retained' AND old.household_space_id=%s "
        "AND old.family_member_id=%s AND old.document_version_id=%s "
        "AND old.extraction_id=%s AND old.batch_item_id=%s "
        "AND old.resubmission_of_job_id IS NOT DISTINCT FROM %s "
        "AND old.source_generation_id=%s AND p.generation_id=%s "
        "AND p.privacy_fingerprint=current.privacy_fingerprint "
        "AND p.associations_json=current.associations_json "
        "AND r.envelope_id=%s AND r.envelope_json=%s "
        "AND r.state IN ('COMPLETE','REVIEW') AND policy_structuring_source_current(old.id) "
        "AND r.result_json->>'program_validation_version'='range-grounding-v3' "
        "AND r.result_json->'draft_normalization'->>'normalization_revision'="
        "'policy-draft-normalization-v2' FOR SHARE OF old,p,current,r",
        (
            job.id,
            job.id,
            job.household_space_id,
            job.family_member_id,
            job.document_version_id,
            job.extraction_id,
            job.batch_item_id,
            job.resubmission_of_job_id,
            work.generation_id,
            work.generation_id,
            work.envelope.envelope_id,
            Jsonb(work.envelope.to_provider_payload()),
        ),
    ).fetchall()
    preserved: set[UUID] = set()
    sources = {
        item.candidate_id: item
        for item in normalized.batch.candidates
        if item.candidate_kind == "rider"
    }
    for row in rows:
        previous = CandidatePipelineResult.model_validate(row["result_json"]["result"])
        for candidate in previous.candidates:
            draft = sources.get(candidate.candidate_id)
            if (
                draft is None
                or candidate.candidate_kind != "rider"
                or candidate.status != "AI_VERIFIED"
                or candidate.fields != draft.fields
            ):
                continue
            version = connection.execute(
                "SELECT id FROM analysis_candidate_versions WHERE structuring_job_id=%s "
                "AND source_candidate_id=%s AND household_space_id=%s "
                "AND candidate_kind='rider' AND is_current AND status='AI_VERIFIED' "
                "AND generator_version='policy-draft-normalization-v2' FOR SHARE",
                (
                    row["job_id"],
                    uuid5(row["job_id"], f"{work.envelope.envelope_id}:{candidate.candidate_id}"),
                    job.household_space_id,
                ),
            ).fetchone()
            if version is None:
                continue
            fields = connection.execute(
                "SELECT f.field_id,f.value,ARRAY(SELECT e.evidence_id "
                "FROM analysis_candidate_evidence e "
                "WHERE e.candidate_version_id=f.candidate_version_id AND e.field_id=f.field_id "
                "ORDER BY e.evidence_id) AS evidence_ids FROM analysis_candidate_fields f "
                "WHERE f.candidate_version_id=%s ORDER BY f.position FOR SHARE OF f",
                (version["id"],),
            ).fetchall()
            expected = [
                {"field_id": f.field_id, "value": f.value, "evidence_ids": sorted(f.evidence_ids)}
                for f in draft.fields
            ]
            if fields == expected:
                preserved.add(candidate.candidate_id)
    if not preserved:
        return normalized
    ranges = tuple(
        RangeDisposition(chunk_id=item.chunk_id, outcome="UNRESOLVED", candidate_ids=())
        if item.candidate_ids and set(item.candidate_ids) <= preserved
        else item.model_copy(
            update={
                "candidate_ids": tuple(key for key in item.candidate_ids if key not in preserved)
            }
        )
        for item in normalized.batch.ranges
    )
    return PolicyDraftNormalization(
        normalized.batch.model_copy(
            update={
                "candidates": tuple(
                    c for c in normalized.batch.candidates if c.candidate_id not in preserved
                ),
                "ranges": ranges,
            }
        ),
        (
            *normalized.adjustments,
            *(
                PolicyDraftAdjustment("PRIOR_VERIFIED_CANDIDATE_PRESERVED", key)
                for key in sorted(preserved)
            ),
        ),
        True,
        revision=normalized.revision,
    )


def _source(
    connection: psycopg.Connection[dict[str, Any]],
    job: PolicyStructuringJobRecord,
    work: PolicyRangeWork,
    source_request: UUID,
    *,
    expected_batch: PolicyRangeBatch | None = None,
) -> dict[str, Any]:
    normalized_pipeline = job.pipeline_version in NORMALIZED_POLICY_PIPELINES
    if normalized_pipeline:
        _current_source(connection, job, work)
    route = (
        """
        AND ((old.id=current.id AND current.pipeline_version IN
          ('policy-range-normalized-v1','retained-policy-association-v6',
           'policy-range-normalized-v2','retained-policy-association-v7','retained-policy-association-v8'))
          OR (current.pipeline_version='retained-policy-association-v6'
            AND current.processing_mode='retained' AND old.id<>current.id
            AND old.processing_mode='retained'
            AND old.pipeline_version='retained-policy-association-v5')
          OR (current.pipeline_version='retained-policy-association-v7'
            AND current.processing_mode='retained' AND old.id<>current.id
            AND old.processing_mode='retained'
            AND old.pipeline_version IN
              ('retained-policy-association-v5','retained-policy-association-v6'))
          OR (current.pipeline_version='retained-policy-association-v8'
            AND current.processing_mode='retained' AND old.id<>current.id
            AND old.processing_mode='retained'
            AND old.pipeline_version IN
              ('retained-policy-association-v5','retained-policy-association-v6',
               'retained-policy-association-v7')))
        AND (SELECT count(*) FROM policy_provider_requests matching
          WHERE matching.job_id=request.job_id AND matching.request_id=request.request_id
            AND matching.state='SUCCEEDED')=1
        AND request.document_id=(SELECT document_id FROM document_versions
          WHERE id=current.document_version_id)
        AND original_plan.generation_id=original.generation_id
        """
        if normalized_pipeline
        else """
        AND current.processing_mode='retained'
        AND current.pipeline_version='retained-policy-association-v4'
        AND old.id<>current.id AND old.processing_mode='retained'
        AND old.pipeline_version IN
          ('retained-policy-association-v2','retained-policy-association-v3')
        """
    )
    row = connection.execute(
        f"""
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
        WHERE current.id=%s {route}
          AND old.resubmission_of_job_id IS NOT DISTINCT FROM current.resubmission_of_job_id
          AND old.household_space_id=current.household_space_id
          AND old.family_member_id=current.family_member_id
          AND old.document_version_id=current.document_version_id
          AND old.extraction_id=current.extraction_id AND old.batch_item_id=current.batch_item_id
          AND old.source_generation_id IS NOT DISTINCT FROM current.source_generation_id
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
        if expected_batch is not None and batch != expected_batch:
            raise PolicyRangeConflict
        normalized = normalize_policy_draft(
            batch,
            work.envelope,
            local_nodes={node["node_id"]: node for node in row["structure_json"]["nodes"]},
            revision=normalization_revision(job.pipeline_version),
        )
        if job.pipeline_version in SOURCE_SCOPED_POLICY_PIPELINES:
            normalized = _preserve_verified_riders(connection, job, work, normalized)
    except ValueError, TypeError, KeyError, PolicyDraftInvalid, ValidationError:
        raise PolicyRangeConflict from None
    return {
        **(
            {"origin": "initial" if row["source_job_id"] == job.id else "replay"}
            if normalized_pipeline
            else {}
        ),
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
        receipt["normalization_revision"] != normalization_revision(job.pipeline_version)
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
                if source.get("origin") == "initial":
                    raise PolicyRangeConflict
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
            if receipt.get("origin") == "initial":
                raise PolicyRangeConflict
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
            receipt = validate_replay_receipt(
                connection, job, work, expected_request=self.source_request
            )
            if receipt is not None and receipt.get("origin") == "initial":
                raise PolicyRangeConflict


class PolicyDraftNormalizationRepository:
    """Store an immutable initial reduction and require a fresh verifier for that batch."""

    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def prepare(
        self, job: PolicyStructuringJobRecord, worker_id: str, work: PolicyRangeWork
    ) -> ReplayedPolicyDraft | None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock(connection, job, worker_id)
            _current_source(connection, job, work)
            receipt = validate_replay_receipt(connection, job, work)
            if receipt is None:
                return None
            return ReplayedPolicyDraft(
                PolicyRangeBatch.model_validate_json(
                    json.dumps(receipt["normalized_batch_json"]), strict=True
                ),
                receipt["request_id"],
            )

    def normalize(
        self,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        work: PolicyRangeWork,
        batch: PolicyRangeBatch,
        request_id: str,
    ) -> ReplayedPolicyDraft:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock(connection, job, worker_id)
            requests = connection.execute(
                "SELECT id FROM policy_provider_requests WHERE job_id=%s "
                "AND request_id=%s AND state='SUCCEEDED' FOR SHARE",
                (job.id, request_id),
            ).fetchall()
            if len(requests) != 1:
                raise PolicyRangeConflict
            request = requests[0]["id"]
            source = _source(connection, job, work, request, expected_batch=batch)
            if source["origin"] != "initial":
                raise PolicyRangeConflict
            connection.execute(
                "INSERT INTO policy_range_replay_sources(job_id,envelope_id,source_job_id,"
                "source_envelope_id,source_provider_request_id,source_response_hash,"
                "normalization_revision,normalized_batch_json,adjustments_json,partial,origin) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'initial') "
                "ON CONFLICT(job_id,envelope_id) DO NOTHING",
                (
                    job.id,
                    work.envelope.envelope_id,
                    job.id,
                    work.envelope.envelope_id,
                    request,
                    source["source_response_hash"],
                    source["normalization_revision"],
                    Jsonb(source["normalized_batch_json"]),
                    Jsonb(source["adjustments_json"]),
                    source["partial"],
                ),
            )
            receipt = validate_replay_receipt(connection, job, work, expected_request=request)
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
            _current_source(connection, job, work)
            validate_replay_receipt(connection, job, work)
