"""Persist private range results; raw source never enters job metadata or logs."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.ai.minimizer import MINIMIZATION_REVISION
from familycare_worker.ai.policy_ranges import (
    PolicyRangeEnvelope,
    RangeEvidenceSlice,
    build_policy_envelopes,
)
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.range_structurer import PolicyRangeBatch
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.document_structure import plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from familycare_worker.document_structure_source import load_stored_structure
from familycare_worker.jobs import psycopg_database_url
from familycare_worker.policy_jobs import PolicyStructuringJobRecord
from familycare_worker.policy_range_publication import publish_range_candidates
from familycare_worker.policy_source_association import (
    associate_policy_sources,
    load_local_members,
    member_identity_fingerprint,
)


class PolicyRangeConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("POLICY_RANGE_STATE_CONFLICT")


@dataclass(frozen=True, repr=False)
class PolicyRangeWork:
    generation_id: UUID
    envelope: PolicyRangeEnvelope


def _lock(
    connection: psycopg.Connection[dict[str, Any]], job: PolicyStructuringJobRecord, worker_id: str
) -> None:
    row = connection.execute(
        """
        SELECT j.id FROM policy_structuring_jobs j
        JOIN document_versions v ON v.id = j.document_version_id
        JOIN documents d ON d.id = v.document_id
        JOIN family_members m ON m.id = j.family_member_id
        WHERE j.id = %s AND j.household_space_id = %s AND j.family_member_id = %s
          AND j.batch_item_id = %s AND j.document_version_id = %s AND j.extraction_id = %s
          AND j.pipeline_version = %s AND j.attempts = %s AND j.lease_owner = %s
          AND j.processing_mode = %s
          AND j.source_generation_id IS NOT DISTINCT FROM %s
          AND j.resubmission_of_job_id IS NOT DISTINCT FROM %s
          AND j.state = 'running' AND j.lease_expires_at > clock_timestamp()
          AND d.deleted_at IS NULL AND m.deleted_at IS NULL
          AND m.household_space_id = j.household_space_id
        FOR UPDATE OF j FOR SHARE OF d, m
    """,
        (
            job.id,
            job.household_space_id,
            job.family_member_id,
            job.batch_item_id,
            job.document_version_id,
            job.extraction_id,
            job.pipeline_version,
            job.attempts,
            worker_id,
            job.processing_mode,
            job.source_generation_id,
            job.resubmission_of_job_id,
        ),
    ).fetchone()
    if row is None:
        raise PolicyRangeConflict
    if job.processing_mode == "retained":
        # Match preparation's item-before-generation lock order. The pin remains
        # current throughout this short transaction, including publication.
        connection.execute(
            "SELECT id FROM document_batch_items WHERE id=%s FOR UPDATE", (job.batch_item_id,)
        )
        pinned = connection.execute(
            "SELECT id FROM document_structure_generations WHERE id=%s "
            "AND is_current AND NOT cancelled "
            "AND policy_structuring_source_current(%s) FOR SHARE",
            (job.source_generation_id, job.id),
        ).fetchone()
        if pinned is None:
            raise PolicyRangeConflict


def _work(row: dict[str, Any]) -> PolicyRangeWork:
    value = row["envelope_json"]
    evidence = tuple(
        RangeEvidenceSlice(
            evidence_id=UUID(item["evidence_id"]),
            document_version_id=UUID(item["document_version_id"]),
            page=item["page"],
            text=item["text"],
            bbox=None if item["bbox"] is None else tuple(item["bbox"]),
            document_kind="policy" if item["source_role"] == "policy" else "terms",
            node_id=item["node_id"],
            start=item["start"],
            end=item["end"],
            primary=item["primary"],
            source_role=item["source_role"],
        )
        for item in value["evidence"]
    )
    return PolicyRangeWork(
        row["generation_id"],
        PolicyRangeEnvelope(
            value["envelope_id"],
            tuple(item["chunk_id"] for item in value["primary_ranges"]),
            tuple(UUID(item["evidence_id"]) for item in value["primary_ranges"]),
            evidence,
        ),
    )


class PolicyRangeRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def next(
        self, job: PolicyStructuringJobRecord, worker_id: str, *, sensitive_terms: Sequence[str]
    ) -> PolicyRangeWork | None:
        privacy_fingerprint = hashlib.sha256(
            json.dumps(
                [MINIMIZATION_REVISION, sorted(set(sensitive_terms))],
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock(connection, job, worker_id)
            plan = connection.execute(
                "SELECT state, privacy_fingerprint FROM document_policy_range_plans "
                "WHERE job_id = %s",
                (job.id,),
            ).fetchone()
            if plan is None:
                structure = load_stored_structure(
                    connection,
                    household_space_id=job.household_space_id,
                    family_member_id=job.family_member_id,
                    batch_item_id=job.batch_item_id,
                    expected_extraction_id=job.extraction_id,
                )
                if structure.lineage.document_version_id != job.document_version_id:
                    raise PolicyRangeConflict
                chunks = plan_structure_chunks(
                    structure, max_content_chars=4096, max_context_chars=4096, max_chunks=16384
                )
                generation_id = DocumentStructureRepository.prepare_in_transaction(
                    connection,
                    household_space_id=job.household_space_id,
                    family_member_id=job.family_member_id,
                    batch_item_id=job.batch_item_id,
                    structure=structure,
                    plan=chunks,
                )
                if job.processing_mode == "retained" and generation_id != job.source_generation_id:
                    raise PolicyRangeConflict
                envelopes = build_policy_envelopes(
                    structure, chunks, sensitive_terms=sensitive_terms
                )
                members = load_local_members(connection, job.household_space_id)
                associations = {
                    "member_fingerprint": member_identity_fingerprint(members),
                    "nodes": {
                        key: value.to_dict()
                        for key, value in associate_policy_sources(
                            structure,
                            members=members,
                            expected_member_id=job.family_member_id,
                        ).items()
                    },
                }
                connection.execute(
                    "INSERT INTO document_policy_range_plans "
                    "(job_id, generation_id, privacy_fingerprint, state, unprocessed_json, "
                    "associations_json) "
                    "VALUES (%s, %s, %s, 'PROCESSING', %s, %s)",
                    (
                        job.id,
                        generation_id,
                        privacy_fingerprint,
                        Jsonb([asdict(item) for item in envelopes.unprocessed]),
                        Jsonb(associations),
                    ),
                )
                for position, envelope in enumerate(envelopes.envelopes):
                    connection.execute(
                        "INSERT INTO document_policy_ranges(job_id, generation_id, envelope_id, "
                        "position, envelope_json, state) VALUES (%s, %s, %s, %s, %s, 'PENDING')",
                        (
                            job.id,
                            generation_id,
                            envelope.envelope_id,
                            position,
                            Jsonb(envelope.to_provider_payload()),
                        ),
                    )
            elif (
                plan["state"] != "PROCESSING" or plan["privacy_fingerprint"] != privacy_fingerprint
            ):
                raise PolicyRangeConflict
            row = connection.execute(
                "SELECT generation_id, envelope_json FROM document_policy_ranges "
                "WHERE job_id = %s AND state = 'PENDING' ORDER BY position LIMIT 1",
                (job.id,),
            ).fetchone()
            if row is None:
                self._advance(connection, job)
                return None
            return _work(row)

    def save(
        self,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        work: PolicyRangeWork,
        batch: PolicyRangeBatch,
        result: CandidatePipelineResult,
    ) -> None:
        if (
            result.classification not in {"SUCCESS", "NEEDS_REVIEW"}
            or {item.chunk_id for item in batch.ranges} != set(work.envelope.primary_chunk_ids)
            or {item.candidate_id for item in batch.candidates}
            != {item.candidate_id for item in result.candidates}
        ):
            raise PolicyRangeConflict
        sources = {item.candidate_id: item for item in batch.candidates}
        evidence_ids = {item.evidence_id for item in work.envelope.evidence}
        primary = dict(
            zip(work.envelope.primary_chunk_ids, work.envelope.primary_evidence_ids, strict=True)
        )
        for candidate in result.candidates:
            source = sources[candidate.candidate_id]
            if (
                candidate.fields != source.fields
                or candidate.candidate_kind != source.candidate_kind
            ):
                raise PolicyRangeConflict
            cited = {key for field in candidate.fields for key in field.evidence_ids}
            if not cited or not cited <= evidence_ids:
                raise PolicyRangeConflict
        for disposition in batch.ranges:
            for candidate_id in disposition.candidate_ids:
                if primary[disposition.chunk_id] not in {
                    key for field in sources[candidate_id].fields for key in field.evidence_ids
                }:
                    raise PolicyRangeConflict
        roles = {item.evidence_id: item.source_role for item in work.envelope.evidence}
        unsupported = {
            candidate_id
            for disposition in batch.ranges
            if roles[primary[disposition.chunk_id]] != "policy"
            for candidate_id in disposition.candidate_ids
        }
        if unsupported:
            result = result.model_copy(
                update={
                    "classification": "NEEDS_REVIEW",
                    "candidates": tuple(
                        candidate.model_copy(
                            update={
                                "status": "NEEDS_REVIEW",
                                "issue_codes": tuple(
                                    dict.fromkeys((*candidate.issue_codes, "UNSUPPORTED_STRUCTURE"))
                                ),
                            }
                        )
                        if candidate.candidate_id in unsupported and candidate.status != "rejected"
                        else candidate
                        for candidate in result.candidates
                    ),
                }
            )
        review = result.classification != "SUCCESS" or any(
            item.outcome == "UNRESOLVED" for item in batch.ranges
        )
        payload = {
            "schema_version": "1",
            "batch": batch.model_dump(mode="json"),
            "result": result.model_dump(mode="json"),
        }
        self._store(job, worker_id, work, payload, review=review)

    def reject(
        self, job: PolicyStructuringJobRecord, worker_id: str, work: PolicyRangeWork
    ) -> None:
        self._store(
            job,
            worker_id,
            work,
            {
                "schema_version": "1",
                "error_code": "POLICY_RANGE_INVALID_RESPONSE",
            },
            review=True,
        )

    def _store(
        self,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        work: PolicyRangeWork,
        payload: dict[str, Any],
        *,
        review: bool,
    ) -> None:
        from familycare_worker.policy_draft_replay import validate_replay_receipt

        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            _lock(connection, job, worker_id)
            receipt = (
                validate_replay_receipt(connection, job, work)
                if job.pipeline_version == "retained-policy-association-v4"
                else None
            )
            if receipt is not None:
                if "result" in payload and payload.get("batch") != receipt["normalized_batch_json"]:
                    raise PolicyRangeConflict
                review = review or receipt["partial"]
                payload = {
                    **payload,
                    "draft_replay": {
                        "source_provider_request_id": str(receipt["source_provider_request_id"]),
                        "source_response_hash": receipt["source_response_hash"],
                        "normalization_revision": receipt["normalization_revision"],
                        "partial": receipt["partial"],
                    },
                }
            source = connection.execute(
                "SELECT document_structure_projection(g.id,g.household_space_id,%s) "
                "AS structure_json "
                "FROM document_policy_ranges r "
                "JOIN document_structure_generations g ON g.id=r.generation_id "
                "WHERE r.job_id=%s AND r.generation_id=%s AND r.envelope_id=%s "
                "AND r.state='PENDING' AND r.envelope_json=%s AND g.household_space_id=%s "
                "FOR UPDATE OF r",
                (
                    sorted({item.page for item in work.envelope.evidence}),
                    job.id,
                    work.generation_id,
                    work.envelope.envelope_id,
                    Jsonb(work.envelope.to_provider_payload()),
                    job.household_space_id,
                ),
            ).fetchone()
            if source is None or source["structure_json"] is None:
                raise PolicyRangeConflict
            if "result" in payload:
                result = CandidatePipelineResult.model_validate_json(json.dumps(payload["result"]))
                nodes = {node["node_id"]: node for node in source["structure_json"]["nodes"]}
                grounded = tuple(
                    ground_range_candidate(candidate, work.envelope.evidence, local_nodes=nodes)
                    for candidate in result.candidates
                )
                result = result.model_copy(
                    update={
                        "candidates": grounded,
                        "classification": "NEEDS_REVIEW"
                        if any(item.status != "AI_VERIFIED" for item in grounded)
                        else result.classification,
                    }
                )
                payload = {
                    **payload,
                    "result": result.model_dump(mode="json"),
                    "program_validation_version": "range-grounding-v2",
                }
                review = review or result.classification != "SUCCESS"
            row = connection.execute(
                "UPDATE document_policy_ranges SET state = %s, result_json = %s "
                "WHERE job_id = %s AND generation_id = %s AND envelope_id = %s "
                "AND state = 'PENDING' AND envelope_json = %s RETURNING envelope_id",
                (
                    "REVIEW" if review else "COMPLETE",
                    Jsonb(payload),
                    job.id,
                    work.generation_id,
                    work.envelope.envelope_id,
                    Jsonb(work.envelope.to_provider_payload()),
                ),
            ).fetchone()
            if row is None:
                raise PolicyRangeConflict
            if "result" in payload:
                publish_range_candidates(
                    connection,
                    job,
                    work.envelope,
                    CandidatePipelineResult.model_validate_json(json.dumps(payload["result"])),
                )
            self._advance(connection, job)

    @staticmethod
    def _advance(
        connection: psycopg.Connection[dict[str, Any]], job: PolicyStructuringJobRecord
    ) -> None:
        remaining = connection.execute(
            "SELECT count(*) FILTER (WHERE state = 'PENDING') AS pending, "
            "count(*) FILTER (WHERE state = 'REVIEW') AS review, count(*) AS total "
            "FROM document_policy_ranges WHERE job_id = %s",
            (job.id,),
        ).fetchone()
        assert remaining is not None
        if remaining["pending"]:
            connection.execute(
                "UPDATE policy_structuring_jobs SET state = 'queued', attempts = 0, "
                "available_at = clock_timestamp(), lease_owner = NULL, lease_expires_at = NULL, "
                "heartbeat_at = NULL, error_code = NULL, updated_at = clock_timestamp() "
                "WHERE id = %s",
                (job.id,),
            )
            return
        plan = connection.execute(
            "SELECT unprocessed_json FROM document_policy_range_plans WHERE job_id = %s", (job.id,)
        ).fetchone()
        assert plan is not None
        partial = bool(plan["unprocessed_json"] or remaining["review"] or not remaining["total"])
        connection.execute(
            "UPDATE document_policy_range_plans SET state = %s WHERE job_id = %s",
            ("PARTIAL" if partial else "COMPLETE", job.id),
        )
        connection.execute(
            "UPDATE policy_structuring_jobs SET state = %s, completed_at = clock_timestamp(), "
            "lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL, error_code = %s, "
            "updated_at = clock_timestamp() WHERE id = %s",
            (
                "permanently_failed" if partial else "succeeded",
                "POLICY_STRUCTURING_INVALID_RESPONSE" if partial else None,
                job.id,
            ),
        )
