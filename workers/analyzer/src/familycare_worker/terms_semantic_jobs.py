"""Lease-safe terms proposals; only the API can verify and publish knowledge."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from familycare_worker.ai.evidence_loader import EvidenceLoadError, _household_member_terms
from familycare_worker.generated_terms_semantic import SemanticWorkEnvelope, TermsSemanticKnowledge
from familycare_worker.jobs import psycopg_database_url

_OWNER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_HEX = re.compile(r"[0-9a-f]{64}")
_STATES = {"queued", "running", "retryable_failed", "paused", "succeeded", "failed", "cancelled"}
_ERROR_CODES = {
    "TERMS_STRUCTURING_DISABLED",
    "TERMS_PROVIDER_UNCONFIGURED",
    "TERMS_PROVIDER_DOCUMENT_BUDGET",
    "TERMS_PROVIDER_DAILY_BUDGET",
    "TERMS_SOURCE_CHANGED",
    "TERMS_PRIVACY_CHANGED",
    "TERMS_PRIVACY_UNAVAILABLE",
    "TERMS_STRUCTURING_INVALID",
    "TERMS_PROVIDER_RETRYABLE",
    "TERMS_PROVIDER_FAILED",
    "TERMS_LEASE_EXHAUSTED",
}
_CLEAR_LEASE = "lease_owner=NULL,lease_token=NULL,lease_expires_at=NULL,heartbeat_at=NULL"
_DUE = """attempts<3 AND available_at<=clock_timestamp() AND (
    state IN ('queued','retryable_failed') OR
    (state='running' AND lease_expires_at<=clock_timestamp()) OR
    (state='paused' AND (error_code='TERMS_PROVIDER_DAILY_BUDGET' OR
      (%s AND error_code IN ('TERMS_STRUCTURING_DISABLED','TERMS_PROVIDER_UNCONFIGURED')))))"""


class TermsSemanticWorkConflict(RuntimeError):
    def __init__(self) -> None:
        super().__init__("TERMS_SEMANTIC_WORK_CONFLICT")


class TermsSemanticWorkUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("TERMS_SEMANTIC_WORK_UNAVAILABLE")


@dataclass(frozen=True, slots=True, repr=False)
class TermsSemanticJobRecord:
    id: UUID
    household_space_id: UUID
    terms_edition_id: UUID
    document_version_id: UUID
    input_context: dict[str, Any]
    input_digest: str
    privacy_digest: str
    work_key: str
    pipeline_revision: str
    envelope: SemanticWorkEnvelope
    state: str
    attempts: int
    lease_token: UUID | None
    lease_owner: str | None
    lease_expires_at: datetime | None
    available_at: datetime
    candidate_id: UUID | None
    error_code: str | None


def _worker(worker_id: str) -> None:
    if not isinstance(worker_id, str) or _OWNER.fullmatch(worker_id) is None:
        raise TermsSemanticWorkConflict


def _uuid(value: object) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise TermsSemanticWorkConflict


def _row(row: dict[str, Any]) -> TermsSemanticJobRecord:
    try:
        envelope = SemanticWorkEnvelope.model_validate(row["envelope_json"])
        for key in ("id", "household_space_id", "terms_edition_id", "document_version_id"):
            _uuid(row[key])
        if (
            row["state"] not in _STATES
            or type(row["attempts"]) is not int
            or not 0 <= row["attempts"] <= 3
            or any(
                not isinstance(row[key], str) or _HEX.fullmatch(row[key]) is None
                for key in ("input_digest", "privacy_digest", "work_key")
            )
            or row["pipeline_revision"] != "terms-semantic-work-v1"
            or row["error_code"] is not None
            and row["error_code"] not in _ERROR_CODES
            or not isinstance(row["input_context"], dict)
            or envelope.input_digest != row["input_digest"]
            or envelope.source.document_version_id != str(row["document_version_id"])
            or envelope.source.terms_edition_id != str(row["terms_edition_id"])
        ):
            raise TermsSemanticWorkConflict
        return TermsSemanticJobRecord(
            **{
                key: row[key]
                for key in TermsSemanticJobRecord.__dataclass_fields__
                if key != "envelope"
            },
            envelope=envelope,
        )
    except KeyError, TypeError, ValueError, ValidationError:
        raise TermsSemanticWorkConflict from None


def _lock_source(
    connection: psycopg.Connection[dict[str, Any]], row: dict[str, Any] | TermsSemanticJobRecord
) -> bool:
    edition, household = (
        (row.terms_edition_id, row.household_space_id)
        if isinstance(row, TermsSemanticJobRecord)
        else (row["terms_edition_id"], row["household_space_id"])
    )
    result = connection.execute(
        "SELECT lock_terms_semantic_work_source(%s,%s) AS valid", (edition, household)
    ).fetchone()
    return bool(result and result["valid"])


def _context_error(
    connection: psycopg.Connection[dict[str, Any]], row: dict[str, Any], valid: bool
) -> str | None:
    current = connection.execute(
        "SELECT terms_semantic_input_context(%s,%s) AS context,terms_semantic_privacy_digest(%s) "
        "AS privacy",
        (row["terms_edition_id"], row["household_space_id"], row["household_space_id"]),
    ).fetchone()
    if not valid or current is None or row["input_context"] != current["context"]:
        return "TERMS_SOURCE_CHANGED"
    if row["privacy_digest"] != current["privacy"]:
        return "TERMS_PRIVACY_CHANGED"
    return None


def _member_terms(
    connection: psycopg.Connection[dict[str, Any]], household: UUID
) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT display_name,internal_alias FROM family_members WHERE household_space_id=%s "
        "AND deleted_at IS NULL ORDER BY id FOR SHARE",
        (household,),
    ).fetchall()
    return _household_member_terms(rows)


def _owned(
    connection: psycopg.Connection[dict[str, Any]], job: TermsSemanticJobRecord, worker_id: str
) -> tuple[dict[str, Any], tuple[str, ...]]:
    """Household/source locks always precede the job lock, including budget access."""
    _worker(worker_id)
    if not isinstance(job, TermsSemanticJobRecord):
        raise TermsSemanticWorkConflict
    for value in (
        job.id,
        job.household_space_id,
        job.terms_edition_id,
        job.document_version_id,
        job.lease_token,
    ):
        _uuid(value)
    valid = _lock_source(connection, job)
    row = connection.execute(
        "SELECT * FROM terms_semantic_jobs WHERE id=%s AND household_space_id=%s "
        "AND terms_edition_id=%s AND document_version_id=%s AND state='running' "
        "AND lease_owner=%s AND lease_token=%s AND attempts=%s "
        "AND lease_expires_at>clock_timestamp() FOR UPDATE",
        (
            job.id,
            job.household_space_id,
            job.terms_edition_id,
            job.document_version_id,
            worker_id,
            job.lease_token,
            job.attempts,
        ),
    ).fetchone()
    if row is None or _context_error(connection, row, valid) is not None:
        raise TermsSemanticWorkConflict
    stored = _row(row)
    if any(
        getattr(stored, key) != getattr(job, key)
        for key in (
            "input_context",
            "input_digest",
            "privacy_digest",
            "work_key",
            "pipeline_revision",
            "envelope",
        )
    ):
        raise TermsSemanticWorkConflict
    try:
        return row, _member_terms(connection, job.household_space_id)
    except EvidenceLoadError:
        raise TermsSemanticWorkConflict from None


def _validated_graph(
    graph: TermsSemanticKnowledge, envelope: SemanticWorkEnvelope
) -> dict[str, Any]:
    try:
        if not isinstance(graph, TermsSemanticKnowledge):
            raise TermsSemanticWorkConflict
        graph = TermsSemanticKnowledge.model_validate(graph.model_dump(warnings="none"))
        supplied = {r.region_id: {c.citation_id: c for c in r.citations} for r in envelope.regions}
        citations = {key: c for refs in supplied.values() for key, c in refs.items()}
        node_ids = [n.node_id for n in graph.nodes]
        citation_ids = [c.citation_id for c in graph.citations]
        processing = graph.processing
        all_lists = [
            node_ids,
            citation_ids,
            graph.roots,
            processing.expected_region_ids,
            processing.consumed_region_ids,
            processing.unresolved_region_ids,
        ]
        edges = [(e.from_node_id, e.to_node_id, e.relation) for e in graph.edges]
        represented = {r for n in graph.nodes for r in n.region_ids}
        if (
            graph.sources != [envelope.source]
            or any(len(keys) != len(set(keys)) for keys in all_lists)
            or processing.expected_region_ids != envelope.expected_region_ids
            or not set(processing.consumed_region_ids) <= set(supplied)
            or set(processing.consumed_region_ids) & set(processing.unresolved_region_ids)
            or set(processing.consumed_region_ids) | set(processing.unresolved_region_ids)
            != set(envelope.expected_region_ids)
            or not set(envelope.primary_region_ids)
            <= represented | set(processing.unresolved_region_ids)
            or not set(graph.roots) <= set(node_ids)
            or len(edges) != len(set(edges))
            or any(left not in node_ids for left, _, _ in edges)
            or any(
                c.citation_id not in citations or c != citations[c.citation_id]
                for c in graph.citations
            )
        ):
            raise TermsSemanticWorkConflict
        for node in graph.nodes:
            if (
                node.source_id != envelope.source.source_id
                or len(node.region_ids) != 1
                or node.region_ids[0] not in supplied
                or len(node.citation_ids) != len(set(node.citation_ids))
                or not set(node.citation_ids)
                <= set(citation_ids) & set(supplied[node.region_ids[0]])
            ):
                raise TermsSemanticWorkConflict
        value = graph.model_dump(mode="json")
        if len(json.dumps(value, allow_nan=False).encode()) > 16777216:
            raise TermsSemanticWorkConflict
        return value
    except TypeError, ValueError, ValidationError:
        raise TermsSemanticWorkConflict from None


class TermsSemanticJobQueue:
    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def _maintenance(self) -> None:
        with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
            rows = connection.execute(
                "SELECT id,household_space_id,terms_edition_id FROM terms_semantic_jobs "
                "WHERE state IN ('queued','running','retryable_failed','paused') AND ("
                "input_context IS DISTINCT FROM terms_semantic_input_context(terms_edition_id,"
                "household_space_id) "
                "OR privacy_digest IS DISTINCT FROM "
                "terms_semantic_privacy_digest(household_space_id) "
                "OR (state='running' AND attempts=3 AND lease_expires_at<=clock_timestamp())) "
                "ORDER BY household_space_id,id"
            ).fetchall()
        for metadata in rows:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                valid = _lock_source(connection, metadata)
                row = connection.execute(
                    "SELECT *,lease_expires_at<=clock_timestamp() AS expired FROM "
                    "terms_semantic_jobs WHERE id=%s FOR UPDATE SKIP LOCKED",
                    (metadata["id"],),
                ).fetchone()
                if row is None or row["state"] in ("succeeded", "failed", "cancelled"):
                    continue
                error = _context_error(connection, row, valid)
                if error:
                    connection.execute(
                        f"UPDATE terms_semantic_jobs SET state='cancelled',error_code=%s,"
                        f"{_CLEAR_LEASE},updated_at=clock_timestamp() WHERE id=%s",
                        (error, row["id"]),
                    )
                elif row["state"] == "running" and row["attempts"] == 3 and row["expired"]:
                    connection.execute(
                        f"UPDATE terms_semantic_jobs SET state='failed',"
                        f"error_code='TERMS_LEASE_EXHAUSTED',{_CLEAR_LEASE},"
                        f"updated_at=clock_timestamp() WHERE id=%s",
                        (row["id"],),
                    )

    def claim(
        self, worker_id: str, *, include_paused_configuration: bool = False
    ) -> TermsSemanticJobRecord | None:
        _worker(worker_id)
        if type(include_paused_configuration) is not bool:
            raise TermsSemanticWorkConflict
        try:
            self._maintenance()
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    "SELECT id,household_space_id,terms_edition_id FROM terms_semantic_jobs WHERE "
                    + _DUE
                    + " ORDER BY available_at,created_at,id LIMIT 32",
                    (include_paused_configuration,),
                ).fetchall()
            for metadata in rows:
                with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                    connection.execute("SET LOCAL lock_timeout='5s'")
                    valid = _lock_source(connection, metadata)
                    row = connection.execute(
                        "SELECT * FROM terms_semantic_jobs WHERE id=%s AND "
                        + _DUE
                        + " FOR UPDATE SKIP LOCKED",
                        (metadata["id"], include_paused_configuration),
                    ).fetchone()
                    if row is None:
                        continue
                    error = _context_error(connection, row, valid)
                    if error:
                        connection.execute(
                            f"UPDATE terms_semantic_jobs SET state='cancelled',error_code=%s,"
                            f"{_CLEAR_LEASE},updated_at=clock_timestamp() WHERE id=%s",
                            (error, row["id"]),
                        )
                        continue
                    try:
                        _row(row)
                        _member_terms(connection, row["household_space_id"])
                    except TermsSemanticWorkConflict, EvidenceLoadError:
                        connection.execute(
                            f"UPDATE terms_semantic_jobs SET state='failed',"
                            f"error_code='TERMS_PRIVACY_UNAVAILABLE',{_CLEAR_LEASE},"
                            f"updated_at=clock_timestamp() WHERE id=%s",
                            (row["id"],),
                        )
                        continue
                    claimed = connection.execute(
                        "UPDATE terms_semantic_jobs SET state='running',attempts=attempts+1,"
                        "lease_owner=%s,"
                        "lease_token=gen_random_uuid(),lease_expires_at=clock_timestamp()+interval "
                        "'180 seconds',"
                        "heartbeat_at=clock_timestamp(),error_code=NULL,"
                        "updated_at=clock_timestamp() WHERE id=%s RETURNING *",
                        (worker_id, row["id"]),
                    ).fetchone()
                    assert claimed is not None
                    return _row(claimed)
            return None
        except psycopg.Error:
            raise TermsSemanticWorkUnavailable from None

    def get_job(self, job_id: UUID) -> TermsSemanticJobRecord | None:
        _uuid(job_id)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    "SELECT * FROM terms_semantic_jobs WHERE id=%s", (job_id,)
                ).fetchone()
                return _row(row) if row else None
        except psycopg.Error:
            raise TermsSemanticWorkUnavailable from None

    def heartbeat(self, job: TermsSemanticJobRecord, worker_id: str) -> bool:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                _owned(connection, job, worker_id)
                row = connection.execute(
                    "UPDATE terms_semantic_jobs SET lease_expires_at=clock_timestamp()+interval "
                    "'180 seconds',heartbeat_at=clock_timestamp(),updated_at=clock_timestamp() "
                    "WHERE id=%s AND lease_expires_at>clock_timestamp() RETURNING id",
                    (job.id,),
                ).fetchone()
                return row is not None
        except TermsSemanticWorkConflict:
            return False
        except psycopg.Error:
            raise TermsSemanticWorkUnavailable from None

    def load_sensitive_terms(self, job: TermsSemanticJobRecord, worker_id: str) -> tuple[str, ...]:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                return _owned(connection, job, worker_id)[1]
        except psycopg.Error:
            raise TermsSemanticWorkUnavailable from None

    def complete(
        self, job: TermsSemanticJobRecord, worker_id: str, graph: TermsSemanticKnowledge
    ) -> UUID:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                row, _ = _owned(connection, job, worker_id)
                canonical = _validated_graph(graph, _row(row).envelope)
                candidate = connection.execute(
                    "INSERT INTO terms_semantic_candidates(household_space_id,terms_edition_id,"
                    "input_context,input_digest,graph_json,graph_sha256) "
                    "VALUES(%s,%s,%s,%s,%s,encode(sha256(convert_to(%s::jsonb::text,'UTF8')),"
                    "'hex')) "
                    "ON CONFLICT(terms_edition_id,input_digest,graph_sha256) DO NOTHING RETURNING "
                    "id",
                    (
                        job.household_space_id,
                        job.terms_edition_id,
                        Jsonb(row["input_context"]),
                        row["input_digest"],
                        Jsonb(canonical),
                        Jsonb(canonical),
                    ),
                ).fetchone()
                if candidate is None:
                    candidate = connection.execute(
                        "SELECT id FROM terms_semantic_candidates WHERE household_space_id=%s AND "
                        "terms_edition_id=%s AND input_digest=%s AND "
                        "graph_sha256=encode(sha256(convert_to(%s::jsonb::text,'UTF8')),'hex')",
                        (
                            job.household_space_id,
                            job.terms_edition_id,
                            job.input_digest,
                            Jsonb(canonical),
                        ),
                    ).fetchone()
                assert candidate is not None
                updated = connection.execute(
                    f"UPDATE terms_semantic_jobs SET state='succeeded',candidate_id=%s,"
                    f"error_code=NULL,{_CLEAR_LEASE},updated_at=clock_timestamp() WHERE id=%s AND "
                    f"lease_expires_at>clock_timestamp() RETURNING id",
                    (candidate["id"], job.id),
                ).fetchone()
                if updated is None:
                    raise TermsSemanticWorkConflict
                return UUID(str(candidate["id"]))
        except psycopg.Error:
            raise TermsSemanticWorkUnavailable from None

    def fail(
        self,
        job: TermsSemanticJobRecord,
        worker_id: str,
        error_code: str,
        *,
        retryable: bool = False,
    ) -> None:
        if error_code not in _ERROR_CODES or type(retryable) is not bool:
            raise TermsSemanticWorkConflict
        self._finish_state(
            job, worker_id, error_code, pause=False, daily=False, retryable=retryable
        )

    def pause(
        self, job: TermsSemanticJobRecord, worker_id: str, error_code: str, *, daily: bool = False
    ) -> None:
        if (
            error_code not in _ERROR_CODES
            or type(daily) is not bool
            or daily != (error_code == "TERMS_PROVIDER_DAILY_BUDGET")
        ):
            raise TermsSemanticWorkConflict
        self._finish_state(job, worker_id, error_code, pause=True, daily=daily, retryable=False)

    def _finish_state(
        self,
        job: TermsSemanticJobRecord,
        worker_id: str,
        error_code: str,
        *,
        pause: bool,
        daily: bool,
        retryable: bool,
    ) -> None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET LOCAL lock_timeout='5s'")
                _owned(connection, job, worker_id)
                state = (
                    "paused"
                    if pause
                    else "retryable_failed"
                    if retryable and job.attempts < 3
                    else "failed"
                )
                row = connection.execute(
                    f"UPDATE terms_semantic_jobs SET state=%s,error_code=%s,attempts=attempts-%s,"
                    f"{_CLEAR_LEASE},"
                    "available_at=CASE WHEN %s THEN (date_trunc('day',clock_timestamp() AT TIME "
                    "ZONE 'UTC')+interval '1 day') AT TIME ZONE 'UTC' "
                    "ELSE clock_timestamp()+(%s*interval '1 second') END,"
                    "updated_at=clock_timestamp() "
                    "WHERE id=%s AND lease_expires_at>clock_timestamp() RETURNING id",
                    (
                        state,
                        error_code,
                        int(pause),
                        daily,
                        0 if pause else min(60, 5 * 2 ** (job.attempts - 1)),
                        job.id,
                    ),
                ).fetchone()
                if row is None:
                    raise TermsSemanticWorkConflict
        except psycopg.Error:
            raise TermsSemanticWorkUnavailable from None
