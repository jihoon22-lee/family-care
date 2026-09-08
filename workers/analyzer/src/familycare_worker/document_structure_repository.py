"""Local structure persistence and bounded PostgreSQL range processing.

Preparing or indexing a range is not AI review, enrollment publication, or
knowledge completion. Protected source/result payloads never appear in errors.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from typing import Any, cast
from uuid import UUID, uuid4

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json, Jsonb

from familycare_worker.document_structure import (
    ChunkPlan,
    DocumentStructure,
    build_document_structure,
    plan_structure_chunks,
)
from familycare_worker.jobs import psycopg_database_url
from familycare_worker.structure_storage import (
    StructureStorageError,
    iter_structure_pages,
    restore_structure_payload,
    structure_header,
    structure_identity,
)

_WORKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_INLINE_STRUCTURE_BYTES = 8 * 1024 * 1024


class StructureScopeError(ValueError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_STRUCTURE_SCOPE_INVALID")


class StructureRepositoryUnavailable(RuntimeError):
    def __init__(self) -> None:
        super().__init__("DOCUMENT_STRUCTURE_UNAVAILABLE")


@dataclass(frozen=True, repr=False)
class StructureChunkLease:
    chunk_id: UUID
    generation_id: UUID
    household_space_id: UUID
    family_member_id: UUID
    worker_id: str
    lease_token: UUID
    chunk: dict[str, object]


@dataclass(frozen=True, repr=False)
class StructureProgress:
    generation_id: UUID
    range_plan_complete: bool
    unprocessed_ranges: int
    total_chunks: int
    completed_chunks: int
    failed_chunks: int
    cancelled_chunks: int

    @property
    def processing_complete(self) -> bool:
        return self.range_plan_complete and self.total_chunks == self.completed_chunks


def _bounded_json(value: object, *, maximum_bytes: int) -> bytes:
    try:
        encoded = json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    except ValueError, TypeError, OverflowError, RecursionError:
        raise StructureScopeError from None
    if len(encoded) > maximum_bytes:
        raise StructureScopeError
    return encoded


def _page_json(value: object) -> str:
    return _bounded_json(value, maximum_bytes=64 * 1024 * 1024).decode("utf-8")


class DocumentStructureRepository:
    def __init__(self, database_url: str) -> None:
        self.database_url = psycopg_database_url(database_url)

    def prepare(
        self,
        *,
        household_space_id: UUID,
        family_member_id: UUID,
        batch_item_id: UUID,
        structure: DocumentStructure,
        plan: ChunkPlan,
    ) -> UUID:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                return self.prepare_in_transaction(
                    connection,
                    household_space_id=household_space_id,
                    family_member_id=family_member_id,
                    batch_item_id=batch_item_id,
                    structure=structure,
                    plan=plan,
                )
        except psycopg.IntegrityError:
            raise StructureScopeError from None
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None

    def prepare_stored(
        self,
        *,
        household_space_id: UUID,
        family_member_id: UUID,
        batch_item_id: UUID,
        maximum_chunks: int = 16384,
    ) -> UUID:
        from familycare_worker.document_structure import DocumentStructureError
        from familycare_worker.document_structure_source import load_stored_structure

        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                structure = load_stored_structure(
                    connection,
                    household_space_id=household_space_id,
                    family_member_id=family_member_id,
                    batch_item_id=batch_item_id,
                )
                plan = plan_structure_chunks(
                    structure,
                    max_content_chars=240,
                    max_context_chars=1440,
                    max_chunks=maximum_chunks,
                )
                return self.prepare_in_transaction(
                    connection,
                    household_space_id=household_space_id,
                    family_member_id=family_member_id,
                    batch_item_id=batch_item_id,
                    structure=structure,
                    plan=plan,
                )
        except DocumentStructureError, psycopg.IntegrityError:
            raise StructureScopeError from None
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None

    @staticmethod
    def prepare_in_transaction(
        connection: psycopg.Connection[dict[str, Any]],
        *,
        household_space_id: UUID,
        family_member_id: UUID,
        batch_item_id: UUID,
        structure: DocumentStructure,
        plan: ChunkPlan,
    ) -> UUID:
        if structure.lineage != plan.lineage:
            raise StructureScopeError
        canonical_structure = build_document_structure(
            structure.source_extraction,
            extraction_id=structure.lineage.extraction_id,
            extraction_revision=structure.lineage.extraction_revision,
            document_version_id=structure.lineage.document_version_id,
            ocr_pages=structure.source_ocr_pages,
            ocr_revision=structure.lineage.ocr_revision,
        )
        if canonical_structure != structure:
            raise StructureScopeError
        del canonical_structure
        if not 0 <= plan.max_chunks <= 65536:
            raise StructureScopeError
        canonical_plan = plan_structure_chunks(
            structure,
            max_content_chars=plan.max_content_chars,
            max_context_chars=plan.max_context_chars,
            max_chunks=plan.max_chunks,
        )
        if canonical_plan != plan:
            raise StructureScopeError
        del canonical_plan
        try:
            identity, structure_digest, structure_bytes, _ = structure_identity(structure, plan)
        except StructureStorageError:
            raise StructureScopeError from None
        paged = structure_bytes > _INLINE_STRUCTURE_BYTES
        structure_json = (
            structure_header(structure, structure_digest) if paged else structure.to_dict()
        )
        _bounded_json(structure_json, maximum_bytes=64 * 1024 * 1024)
        plan_json = plan.to_dict()
        _bounded_json(plan_json, maximum_bytes=64 * 1024 * 1024)
        scoped = connection.execute(
            """
            SELECT item.id FROM document_batch_items item
            JOIN document_batches batch ON batch.id = item.batch_id
            JOIN family_members member ON member.id = batch.family_member_id
            JOIN document_versions version ON version.document_id = item.document_id
            JOIN extractions extraction ON extraction.document_version_id = version.id
            WHERE item.id = %s AND batch.household_space_id = %s
              AND batch.family_member_id = %s AND member.household_space_id = %s
              AND member.deleted_at IS NULL AND version.id = %s AND extraction.id = %s
              AND extraction.status = 'succeeded' AND version.content_sha256 = %s
            FOR UPDATE OF item
            """,
            (
                batch_item_id,
                household_space_id,
                family_member_id,
                household_space_id,
                structure.lineage.document_version_id,
                structure.lineage.extraction_id,
                structure.lineage.content_sha256,
            ),
        ).fetchone()
        if scoped is None:
            raise StructureScopeError
        existing = connection.execute(
            """
            SELECT id FROM document_structure_generations
            WHERE household_space_id = %s AND family_member_id = %s
              AND batch_item_id = %s AND identity_sha256 = %s
            """,
            (household_space_id, family_member_id, batch_item_id, identity),
        ).fetchone()
        if existing is not None:
            return cast(UUID, existing["id"])
        if plan.complete:
            connection.execute(
                """
                UPDATE document_structure_generations SET is_current = false,
                  updated_at = clock_timestamp()
                WHERE household_space_id = %s AND family_member_id = %s
                  AND batch_item_id = %s AND is_current
                """,
                (household_space_id, family_member_id, batch_item_id),
            )
        generation = connection.execute(
            """
            INSERT INTO document_structure_generations (
              household_space_id, family_member_id, batch_item_id,
              document_version_id, extraction_id, identity_sha256, structure_version,
              structure_json, plan_json, range_plan_complete, is_current
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
            """,
            (
                household_space_id,
                family_member_id,
                batch_item_id,
                structure.lineage.document_version_id,
                structure.lineage.extraction_id,
                identity,
                structure.lineage.structure_version,
                Jsonb(structure_json),
                Jsonb(plan_json),
                plan.complete,
                plan.complete,
            ),
        ).fetchone()
        if generation is None:
            raise StructureScopeError
        generation_id = cast(UUID, generation["id"])
        if paged:
            try:
                connection.execute(
                    "INSERT INTO document_structure_page_payloads "
                    "(generation_id,part_number,payload_json) VALUES (%s,0,%s)",
                    (generation_id, Json(structure_json, dumps=_page_json)),
                )
                for number, payload in iter_structure_pages(structure):
                    connection.execute(
                        "INSERT INTO document_structure_page_payloads "
                        "(generation_id,part_number,payload_json,node_ids) VALUES (%s,%s,%s,%s)",
                        (
                            generation_id,
                            number,
                            Json(payload, dumps=_page_json),
                            [node["node_id"] for node in payload["nodes"]],
                        ),
                    )
                # Validate inside the caller's savepoint so a failure can be
                # recorded durably; keep the deferred DB guard for other writers.
                connection.execute("SELECT validate_structure_page_manifest(%s)", (generation_id,))
            except StructureStorageError:
                raise StructureScopeError from None
        for position, chunk in enumerate(plan.chunks):
            chunk_json = asdict(chunk)
            _bounded_json(chunk_json, maximum_bytes=32768)
            connection.execute(
                """
                INSERT INTO document_structure_chunks (
                  generation_id, chunk_key, position, chunk_json)
                VALUES (%s,%s,%s,%s)
                """,
                (generation_id, chunk.chunk_id, position, Jsonb(chunk_json)),
            )
        return generation_id

    def read_structure_payload(
        self, household_id: UUID, member_id: UUID, generation_id: UUID
    ) -> dict[str, Any]:
        """Restore a full protected snapshot for local audits, never HTTP or logging."""
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION READ ONLY")
                row = connection.execute(
                    "SELECT structure_json FROM document_structure_generations "
                    "WHERE id=%s AND household_space_id=%s AND family_member_id=%s",
                    (generation_id, household_id, member_id),
                ).fetchone()
                if row is None:
                    raise StructureScopeError
                if row["structure_json"].get("storage_layout") != "page-v1":
                    return cast(dict[str, Any], row["structure_json"])
                parts = connection.execute(
                    "SELECT part_number,payload_json FROM document_structure_page_payloads "
                    "WHERE generation_id=%s ORDER BY part_number LIMIT 502",
                    (generation_id,),
                ).fetchall()
                if not parts or parts[0]["part_number"] != 0 or len(parts) > 501:
                    raise StructureScopeError
                return restore_structure_payload(
                    parts[0]["payload_json"], [part["payload_json"] for part in parts[1:]]
                )
        except StructureStorageError:
            raise StructureScopeError from None
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None

    def progress(
        self, household_id: UUID, member_id: UUID, generation_id: UUID
    ) -> StructureProgress:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                generation = self._scoped_generation(
                    connection, household_id, member_id, generation_id
                )
                counts = connection.execute(
                    """
                    SELECT count(*) AS total,
                      count(*) FILTER (WHERE state = 'SUCCEEDED') AS completed,
                      count(*) FILTER (WHERE state = 'FAILED') AS failed,
                      count(*) FILTER (WHERE state = 'CANCELLED') AS cancelled
                    FROM document_structure_chunks WHERE generation_id = %s
                    """,
                    (generation_id,),
                ).fetchone()
                if counts is None:
                    raise StructureScopeError
                return StructureProgress(
                    generation_id,
                    bool(generation["range_plan_complete"]),
                    int(generation["unprocessed_ranges"]),
                    int(counts["total"]),
                    int(counts["completed"]),
                    int(counts["failed"]),
                    int(counts["cancelled"]),
                )
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None

    @staticmethod
    def _scoped_generation(
        connection: psycopg.Connection[dict[str, Any]],
        household_id: UUID,
        member_id: UUID,
        generation_id: UUID,
    ) -> dict[str, Any]:
        row = connection.execute(
            """
            SELECT range_plan_complete,
              jsonb_array_length(plan_json->'unprocessed') AS unprocessed_ranges
            FROM document_structure_generations
            WHERE id = %s AND household_space_id = %s AND family_member_id = %s
            FOR UPDATE
            """,
            (generation_id, household_id, member_id),
        ).fetchone()
        if row is None:
            raise StructureScopeError
        return row

    def claim_next(
        self, worker_id: str, *, generation_id: UUID | None = None, lease_seconds: int = 60
    ) -> StructureChunkLease | None:
        if not _WORKER.fullmatch(worker_id) or not 1 <= lease_seconds <= 300:
            raise StructureScopeError
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                # Exhausted expired leases are terminal, not silently unclaimable RUNNING rows.
                connection.execute(
                    """
                    UPDATE document_structure_chunks SET state = 'FAILED',
                      lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                      error_code = 'STRUCTURE_RANGE_ATTEMPTS_EXHAUSTED',
                      completed_at = clock_timestamp(), updated_at = clock_timestamp()
                    WHERE state = 'RUNNING' AND lease_expires_at <= clock_timestamp()
                      AND attempts >= max_attempts
                    """
                )
                row = connection.execute(
                    """
                    SELECT chunk.id, generation.id AS generation_id,
                           generation.household_space_id, generation.family_member_id,
                           chunk.chunk_json
                    FROM document_structure_chunks chunk
                    JOIN document_structure_generations generation
                      ON generation.id = chunk.generation_id
                    WHERE NOT generation.cancelled AND (%s::uuid IS NULL OR generation.id = %s)
                      AND chunk.attempts < chunk.max_attempts
                      AND ((chunk.state IN ('PENDING','RETRYABLE_FAILED')
                            AND chunk.available_at <= clock_timestamp())
                           OR (chunk.state = 'RUNNING'
                               AND chunk.lease_expires_at <= clock_timestamp()))
                    ORDER BY generation.created_at, chunk.position
                    FOR UPDATE OF chunk SKIP LOCKED LIMIT 1
                    """,
                    (generation_id, generation_id),
                ).fetchone()
                if row is None:
                    return None
                token = uuid4()
                connection.execute(
                    """
                    UPDATE document_structure_chunks SET state = 'RUNNING', attempts = attempts + 1,
                      lease_owner = %s, lease_token = %s,
                      lease_expires_at = clock_timestamp() + %s * interval '1 second',
                      error_code = NULL, completed_at = NULL, updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (worker_id, token, lease_seconds, row["id"]),
                )
                return StructureChunkLease(
                    row["id"],
                    row["generation_id"],
                    row["household_space_id"],
                    row["family_member_id"],
                    worker_id,
                    token,
                    row["chunk_json"],
                )
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None

    def complete(self, lease: StructureChunkLease, *, result: dict[str, object]) -> bool:
        _bounded_json(result, maximum_bytes=65536)
        return self._finish(lease, result=result, reason_code=None, retryable=False)

    def fail(self, lease: StructureChunkLease, *, reason_code: str, retryable: bool) -> bool:
        if not _CODE.fullmatch(reason_code):
            raise StructureScopeError
        return self._finish(lease, result=None, reason_code=reason_code, retryable=retryable)

    def _finish(
        self,
        lease: StructureChunkLease,
        *,
        result: dict[str, object] | None,
        reason_code: str | None,
        retryable: bool,
    ) -> bool:
        try:
            with psycopg.connect(self.database_url) as connection:
                row = connection.execute(
                    """
                    UPDATE document_structure_chunks chunk SET
                      state = CASE WHEN %s THEN 'SUCCEEDED'
                                   WHEN %s AND attempts < max_attempts THEN 'RETRYABLE_FAILED'
                                   ELSE 'FAILED' END,
                      result_json = %s, error_code = %s,
                      available_at = clock_timestamp() + interval '5 seconds',
                      lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                      completed_at = clock_timestamp(), updated_at = clock_timestamp()
                    FROM document_structure_generations generation
                    WHERE chunk.id = %s AND chunk.generation_id = generation.id
                      AND generation.id = %s AND generation.household_space_id = %s
                      AND generation.family_member_id = %s AND NOT generation.cancelled
                      AND chunk.state = 'RUNNING' AND chunk.lease_owner = %s
                      AND chunk.lease_token = %s AND chunk.lease_expires_at > clock_timestamp()
                    RETURNING chunk.id
                    """,
                    (
                        result is not None,
                        retryable,
                        Jsonb(result) if result is not None else None,
                        reason_code,
                        lease.chunk_id,
                        lease.generation_id,
                        lease.household_space_id,
                        lease.family_member_id,
                        lease.worker_id,
                        lease.lease_token,
                    ),
                ).fetchone()
                return row is not None
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None

    def cancel(self, household_id: UUID, member_id: UUID, generation_id: UUID) -> None:
        self._set_cancelled(household_id, member_id, generation_id, cancelled=True)

    def resume(self, household_id: UUID, member_id: UUID, generation_id: UUID) -> None:
        self._set_cancelled(household_id, member_id, generation_id, cancelled=False)

    def _set_cancelled(
        self, household_id: UUID, member_id: UUID, generation_id: UUID, *, cancelled: bool
    ) -> None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                self._scoped_generation(connection, household_id, member_id, generation_id)
                connection.execute(
                    """
                    UPDATE document_structure_generations SET cancelled = %s,
                      updated_at = clock_timestamp() WHERE id = %s
                    """,
                    (cancelled, generation_id),
                )
                if cancelled:
                    connection.execute(
                        """
                        UPDATE document_structure_chunks SET state = 'CANCELLED',
                          lease_owner = NULL, lease_token = NULL, lease_expires_at = NULL,
                          error_code = 'STRUCTURE_RANGE_CANCELLED',
                          completed_at = clock_timestamp(), updated_at = clock_timestamp()
                        WHERE generation_id = %s
                          AND state IN ('PENDING','RUNNING','RETRYABLE_FAILED')
                        """,
                        (generation_id,),
                    )
                else:
                    connection.execute(
                        """
                        UPDATE document_structure_chunks SET
                          state = CASE WHEN attempts < max_attempts
                                       THEN 'PENDING' ELSE 'FAILED' END,
                          available_at = clock_timestamp(), completed_at = NULL,
                          error_code = CASE WHEN attempts < max_attempts THEN NULL
                            ELSE 'STRUCTURE_RANGE_ATTEMPTS_EXHAUSTED' END,
                          updated_at = clock_timestamp()
                        WHERE generation_id = %s AND state = 'CANCELLED'
                        """,
                        (generation_id,),
                    )
        except psycopg.Error:
            raise StructureRepositoryUnavailable from None
