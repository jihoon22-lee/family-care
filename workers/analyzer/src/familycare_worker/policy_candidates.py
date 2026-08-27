"""Atomic persistence boundary for private policy candidate batches."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from typing import cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.ai.schemas import CandidatePipelineResult, PolicyCandidate
from familycare_worker.jobs import psycopg_database_url
from familycare_worker.policy_jobs import PolicyStructuringJobRecord

_WORKER_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_CONTRACT_FIELDS = frozenset(
    {"insurer", "product_name", "contract_start", "contract_end", "policy_status"}
)
_RIDER_FIELDS = frozenset(
    {
        "rider_name",
        "rider_key",
        "benefit_type",
        "sum_assured",
        "currency",
        "coverage_start",
        "coverage_end",
        "renewable",
        "rider_status",
    }
)
_REQUIRED_CONTRACT_FIELDS = frozenset({"insurer", "product_name"})
_REQUIRED_RIDER_FIELDS = frozenset({"rider_name", "rider_key", "benefit_type"})
_ISSUE_CODES = frozenset(
    {
        "MISSING_EVIDENCE",
        "CONFLICTING_EVIDENCE",
        "TERMS_ONLY_RIDER",
        "UNSUPPORTED_STRUCTURE",
        "LOW_CONFIDENCE",
        "INVALID_UNIT",
        "INVALID_DATE",
        "INVENTED_EVIDENCE",
        "INVENTED_FIELD",
    }
)
_GENERATOR_VERSION = "policy-structurer-v2"
_VERIFIER_VERSION = "policy-verifier-v1"


class PolicyCandidatePersistenceError(RuntimeError):
    """Base persistence error whose message includes no candidate or Evidence data."""


class InvalidPolicyCandidateBatch(PolicyCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("INVALID_POLICY_CANDIDATE_BATCH")


class PolicyCandidateJobConflict(PolicyCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("POLICY_CANDIDATE_JOB_CONFLICT")


class PolicyCandidateRepositoryUnavailable(PolicyCandidatePersistenceError):
    def __init__(self) -> None:
        super().__init__("POLICY_CANDIDATE_REPOSITORY_UNAVAILABLE")


def _validate_worker_id(worker_id: str) -> str:
    if not isinstance(worker_id, str) or _WORKER_ID_PATTERN.fullmatch(worker_id) is None:
        raise ValueError("invalid worker identity")
    return worker_id


def _validate_scalar(value: object) -> None:
    if isinstance(value, str):
        if len(value) > 240:
            raise InvalidPolicyCandidateBatch
        return
    if value is None or isinstance(value, bool | int):
        return
    if isinstance(value, float) and math.isfinite(value):
        return
    raise InvalidPolicyCandidateBatch


def _validated_candidates(
    result: CandidatePipelineResult,
    evidence_by_id: Mapping[UUID, EvidenceSlice],
) -> tuple[PolicyCandidate, ...]:
    if not isinstance(result, CandidatePipelineResult):
        raise InvalidPolicyCandidateBatch
    candidates = result.candidates
    if (
        result.classification == "VALIDATION_ERROR"
        or not 1 <= len(candidates) <= 32
        or len({candidate.candidate_id for candidate in candidates}) != len(candidates)
        or sum(candidate.candidate_kind == "policy_contract" for candidate in candidates) != 1
        or any(
            candidate.candidate_kind not in {"policy_contract", "rider"} for candidate in candidates
        )
    ):
        raise InvalidPolicyCandidateBatch
    for candidate in candidates:
        if candidate.status not in {"AI_VERIFIED", "NEEDS_REVIEW", "rejected"}:
            raise InvalidPolicyCandidateBatch
        fields = candidate.fields
        field_ids = tuple(field.field_id for field in fields)
        allowed = (
            _CONTRACT_FIELDS if candidate.candidate_kind == "policy_contract" else _RIDER_FIELDS
        )
        required = (
            _REQUIRED_CONTRACT_FIELDS
            if candidate.candidate_kind == "policy_contract"
            else _REQUIRED_RIDER_FIELDS
        )
        if (
            not 1 <= len(fields) <= 15
            or len(field_ids) != len(set(field_ids))
            or not set(field_ids) <= allowed
            or not required <= set(field_ids)
        ):
            raise InvalidPolicyCandidateBatch
        for field in fields:
            _validate_scalar(field.value)
            if (
                not 1 <= len(field.evidence_ids) <= 16
                or len(field.evidence_ids) != len(set(field.evidence_ids))
                or not set(field.evidence_ids) <= evidence_by_id.keys()
            ):
                raise InvalidPolicyCandidateBatch
        if (
            len(candidate.issue_codes) > 16
            or any(code not in _ISSUE_CODES for code in candidate.issue_codes)
            or not 1 <= len(candidate.provider_request_ids) <= 2
            or any(
                not isinstance(request_id, str) or _REQUEST_ID_PATTERN.fullmatch(request_id) is None
                for request_id in candidate.provider_request_ids
            )
        ):
            raise InvalidPolicyCandidateBatch
    return candidates


def _validated_evidence(
    evidence: Sequence[EvidenceSlice],
    *,
    document_version_id: UUID,
) -> dict[UUID, EvidenceSlice]:
    bounded = tuple(evidence)
    if (
        not 1 <= len(bounded) <= 64
        or any(not isinstance(item, EvidenceSlice) for item in bounded)
        or len({item.evidence_id for item in bounded}) != len(bounded)
        or any(
            item.document_version_id != document_version_id
            or item.document_kind != "policy"
            or item.bbox is not None
            for item in bounded
        )
    ):
        raise InvalidPolicyCandidateBatch
    return {item.evidence_id: item for item in bounded}


def _locked_job_matches(
    row: Mapping[str, object] | None,
    job: PolicyStructuringJobRecord,
    worker_id: str,
) -> bool:
    if row is None:
        return False
    expected = {
        "id": job.id,
        "household_space_id": job.household_space_id,
        "batch_item_id": job.batch_item_id,
        "family_member_id": job.family_member_id,
        "document_version_id": job.document_version_id,
        "extraction_id": job.extraction_id,
        "policy_aggregate_id": job.policy_aggregate_id,
        "pipeline_version": job.pipeline_version,
    }
    return (
        all(row.get(name) == value for name, value in expected.items())
        and row.get("state") == "running"
        and row.get("lease_owner") == worker_id
        and row.get("lease_valid") is True
        and row.get("batch_household_space_id") == job.household_space_id
        and row.get("batch_family_member_id") == job.family_member_id
        and row.get("member_household_space_id") == job.household_space_id
        and row.get("member_deleted_at") is None
        and row.get("item_state") == "succeeded"
        and row.get("item_document_kind") == "policy"
        and row.get("item_document_id") == row.get("version_document_id")
        and row.get("document_kind") == "policy"
        and row.get("document_deleted_at") is None
        and row.get("extraction_status") == "succeeded"
    )


def _evidence_rows_match(
    rows: Sequence[Mapping[str, object]],
    evidence_by_id: Mapping[UUID, EvidenceSlice],
    job: PolicyStructuringJobRecord,
) -> bool:
    if len(rows) != len(evidence_by_id):
        return False
    rows_by_id = {row.get("id"): row for row in rows}
    if set(rows_by_id) != set(evidence_by_id):
        return False
    for evidence_id, supplied in evidence_by_id.items():
        row = rows_by_id[evidence_id]
        stored_bbox = tuple(row.get(name) for name in ("x0", "y0", "x1", "y1"))
        supplied_bbox: tuple[object, ...] = (
            tuple(supplied.bbox) if supplied.bbox is not None else (None, None, None, None)
        )
        if stored_bbox != supplied_bbox:
            return False
        if (
            row.get("household_space_id") != job.household_space_id
            or row.get("document_version_id") != job.document_version_id
            or row.get("extraction_id") != job.extraction_id
            or row.get("physical_page") != supplied.page
            or row.get("content_sha256") != row.get("version_content_sha256")
            or row.get("extraction_status") != "succeeded"
            or row.get("document_kind") != "policy"
            or row.get("document_deleted_at") is not None
            or row.get("review_state") not in {"AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED"}
        ):
            return False
    return True


def _candidate_status(
    candidate: PolicyCandidate,
    evidence_review_states: Mapping[UUID, str],
) -> str:
    referenced = {evidence_id for field in candidate.fields for evidence_id in field.evidence_ids}
    if candidate.status == "AI_VERIFIED" and any(
        evidence_review_states[evidence_id] == "NEEDS_REVIEW" for evidence_id in referenced
    ):
        return "NEEDS_REVIEW"
    return candidate.status


class PolicyCandidatePublisher:
    """Persist one validated candidate batch and complete its leased job atomically."""

    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def publish(
        self,
        *,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        result: CandidatePipelineResult,
        evidence: Sequence[EvidenceSlice],
    ) -> tuple[UUID, ...]:
        if not isinstance(job, PolicyStructuringJobRecord):
            raise InvalidPolicyCandidateBatch
        owner = _validate_worker_id(worker_id)
        evidence_by_id = _validated_evidence(
            evidence,
            document_version_id=job.document_version_id,
        )
        candidates = _validated_candidates(result, evidence_by_id)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                locked_job = connection.execute(
                    """
                    SELECT job.*,
                           job.lease_expires_at > clock_timestamp() AS lease_valid,
                           batch.household_space_id AS batch_household_space_id,
                           batch.family_member_id AS batch_family_member_id,
                           member.household_space_id AS member_household_space_id,
                           member.deleted_at AS member_deleted_at,
                           item.state AS item_state,
                           item.document_kind AS item_document_kind,
                           item.document_id AS item_document_id,
                           version.document_id AS version_document_id,
                           document.document_kind,
                           document.deleted_at AS document_deleted_at,
                           extraction.status AS extraction_status
                    FROM policy_structuring_jobs AS job
                    JOIN document_batch_items AS item ON item.id = job.batch_item_id
                    JOIN document_batches AS batch ON batch.id = item.batch_id
                    JOIN family_members AS member ON member.id = job.family_member_id
                    JOIN document_versions AS version ON version.id = job.document_version_id
                    JOIN documents AS document ON document.id = version.document_id
                    JOIN extractions AS extraction ON extraction.id = job.extraction_id
                    WHERE job.id = %s
                    FOR UPDATE OF job
                    """,
                    (job.id,),
                ).fetchone()
                if not _locked_job_matches(locked_job, job, owner):
                    raise PolicyCandidateJobConflict
                rows = connection.execute(
                    """
                    SELECT evidence.id, evidence.household_space_id,
                           evidence.document_version_id, evidence.extraction_id,
                           evidence.content_sha256, evidence.physical_page,
                           evidence.x0, evidence.y0, evidence.x1, evidence.y1,
                           evidence.review_state,
                           version.content_sha256 AS version_content_sha256,
                           extraction.status AS extraction_status,
                           document.document_kind,
                           document.deleted_at AS document_deleted_at
                    FROM evidence
                    JOIN document_versions AS version
                      ON version.id = evidence.document_version_id
                    JOIN documents AS document ON document.id = version.document_id
                    JOIN extractions AS extraction ON extraction.id = evidence.extraction_id
                    WHERE evidence.id = ANY(%s)
                    FOR SHARE OF evidence
                    """,
                    (list(evidence_by_id),),
                ).fetchall()
                if not _evidence_rows_match(rows, evidence_by_id, job):
                    raise InvalidPolicyCandidateBatch
                evidence_review_states = {
                    cast(UUID, row["id"]): cast(str, row["review_state"]) for row in rows
                }
                version_ids: list[UUID] = []
                for candidate in candidates:
                    version_id = uuid4()
                    version_ids.append(version_id)
                    issues = tuple(dict.fromkeys(candidate.issue_codes))[:8]
                    connection.execute(
                        """
                        INSERT INTO analysis_candidate_versions (
                            id, review_item_id, household_space_id, candidate_kind,
                            aggregate_id, version, is_current, status, schema_version,
                            generator_version, verifier_version, provider_request_id,
                            issues, structuring_job_id, source_candidate_id
                        ) VALUES (%s, %s, %s, %s, %s, 1, true, %s, %s, %s,
                                  %s, %s, %s, %s, %s)
                        """,
                        (
                            version_id,
                            uuid4(),
                            job.household_space_id,
                            candidate.candidate_kind,
                            job.policy_aggregate_id,
                            _candidate_status(candidate, evidence_review_states),
                            candidate.schema_version,
                            _GENERATOR_VERSION,
                            _VERIFIER_VERSION,
                            candidate.provider_request_ids[-1],
                            Jsonb([{"code": code, "field_id": None} for code in issues]),
                            job.id,
                            candidate.candidate_id,
                        ),
                    )
                    for position, field in enumerate(candidate.fields):
                        connection.execute(
                            """
                            INSERT INTO analysis_candidate_fields (
                                candidate_version_id, field_id, position, value
                            ) VALUES (%s, %s, %s, %s)
                            """,
                            (version_id, field.field_id, position, Jsonb(field.value)),
                        )
                        for evidence_id in field.evidence_ids:
                            supplied = evidence_by_id[evidence_id]
                            coordinates = (
                                tuple(supplied.bbox)
                                if supplied.bbox is not None
                                else (None, None, None, None)
                            )
                            connection.execute(
                                """
                                INSERT INTO analysis_candidate_evidence (
                                    candidate_version_id, field_id,
                                    document_version_id, evidence_id, physical_page,
                                    bounded_excerpt, x0, y0, x1, y1
                                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                                """,
                                (
                                    version_id,
                                    field.field_id,
                                    supplied.document_version_id,
                                    evidence_id,
                                    supplied.page,
                                    supplied.text,
                                    *coordinates,
                                ),
                            )
                completed = connection.execute(
                    """
                    UPDATE policy_structuring_jobs
                    SET state = 'succeeded', lease_owner = NULL,
                        lease_expires_at = NULL, heartbeat_at = NULL,
                        error_code = NULL, completed_at = clock_timestamp(),
                        updated_at = clock_timestamp()
                    WHERE id = %s AND state = 'running' AND lease_owner = %s
                      AND lease_expires_at > clock_timestamp()
                    RETURNING id
                    """,
                    (job.id, owner),
                ).fetchone()
                if completed is None:
                    raise PolicyCandidateJobConflict
                return tuple(version_ids)
        except psycopg.Error:
            raise PolicyCandidateRepositoryUnavailable from None


__all__ = [
    "InvalidPolicyCandidateBatch",
    "PolicyCandidateJobConflict",
    "PolicyCandidatePersistenceError",
    "PolicyCandidatePublisher",
    "PolicyCandidateRepositoryUnavailable",
]
