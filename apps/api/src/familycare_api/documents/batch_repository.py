"""Household-scoped persistence for private document-import batches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.documents.generated_batch_contracts import (
    StructurePreparationError,
    StructurePreparationState,
)
from familycare_api.documents.import_sources import ResolvedImportSource


class BatchRepositoryError(RuntimeError):
    """Fixed-message repository failure."""


class BatchRepositoryUnavailable(BatchRepositoryError):
    def __init__(self) -> None:
        super().__init__("BATCH_REPOSITORY_UNAVAILABLE")


@dataclass(frozen=True)
class BatchItemRecord:
    source_id: str
    display_label: str
    document_kind: str
    state: str
    error_code: str | None
    attempts: int
    ocr_state: str
    ocr_pages_processed: int
    ocr_warning_codes: tuple[str, ...]
    structure_state: StructurePreparationState | None = None
    structure_error_code: StructurePreparationError | None = None
    structure_planned_chunks: int | None = None
    structure_unprocessed_ranges: int | None = None


@dataclass(frozen=True)
class BatchRecord:
    batch_id: UUID
    family_member_id: UUID
    state: str
    items: tuple[BatchItemRecord, ...]


@dataclass(frozen=True)
class BatchSourceSelection:
    """One catalog source paired with its caller-selected private kind."""

    source: ResolvedImportSource
    document_kind: str


_PRIVATE_DOCUMENT_KINDS = frozenset(
    {"application", "policy", "product_explanation", "supporting", "terms"}
)


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError("database URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _batch(row: dict[str, Any], items: list[dict[str, Any]]) -> BatchRecord:
    states = [cast(str, item["state"]) for item in items]
    if states and all(state == "succeeded" for state in states):
        state = "succeeded"
    elif any(state in {"queued", "running", "retryable_failed"} for state in states):
        state = (
            "running"
            if row["state"] != "created" or any(state != "queued" for state in states)
            else "created"
        )
    elif states and all(state == "permanently_failed" for state in states):
        state = "failed"
    elif row["state"] == "cancelled":
        state = "cancelled"
    else:
        state = "partial"
    return BatchRecord(
        batch_id=cast(UUID, row["id"]),
        family_member_id=cast(UUID, row["family_member_id"]),
        state=state,
        items=tuple(
            BatchItemRecord(
                source_id=cast(str, item["source_id"]),
                display_label=cast(str, item["display_label"]),
                document_kind=cast(str, item["document_kind"]),
                state=cast(str, item["state"]),
                error_code=cast(str | None, item["error_code"]),
                attempts=cast(int, item["attempts"]),
                ocr_state=cast(str, item["ocr_state"]),
                ocr_pages_processed=cast(int, item["ocr_pages_processed"]),
                ocr_warning_codes=tuple(cast(list[str], item["ocr_warning_codes"])),
                structure_state=item.get("structure_state"),
                structure_error_code=item.get("structure_error_code"),
                structure_planned_chunks=item.get("structure_planned_chunks"),
                structure_unprocessed_ranges=item.get("structure_unprocessed_ranges"),
            )
            for item in items
        ),
    )


class BatchRepository:
    """Use short transactions and require household scope on every API lookup."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def create(
        self,
        *,
        household_space_id: UUID,
        created_by: UUID,
        family_member_id: UUID,
        sources: tuple[BatchSourceSelection, ...],
    ) -> BatchRecord | None:
        if any(not isinstance(selection, BatchSourceSelection) for selection in sources):
            raise TypeError("explicit batch source selection required")
        source_ids = [selection.source.source_id for selection in sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("duplicate source id")
        for selection in sources:
            if selection.document_kind not in _PRIVATE_DOCUMENT_KINDS:
                raise ValueError("unsupported document kind")
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                member = connection.execute(
                    """
                    SELECT id
                    FROM family_members
                    WHERE id = %s AND household_space_id = %s AND deleted_at IS NULL
                    FOR SHARE
                    """,
                    (family_member_id, household_space_id),
                ).fetchone()
                if member is None:
                    return None
                creator = connection.execute(
                    """
                    SELECT id
                    FROM app_users
                    WHERE id = %s AND household_space_id = %s AND is_active
                    FOR SHARE
                    """,
                    (created_by, household_space_id),
                ).fetchone()
                if creator is None:
                    return None
                row = connection.execute(
                    """
                    INSERT INTO document_batches (
                        household_space_id, family_member_id, created_by
                    )
                    VALUES (%s, %s, %s)
                    RETURNING id, family_member_id, state
                    """,
                    (household_space_id, family_member_id, created_by),
                ).fetchone()
                if row is None:
                    raise BatchRepositoryUnavailable
                items: list[dict[str, Any]] = []
                for selection in sources:
                    source = selection.source
                    item = connection.execute(
                        """
                        INSERT INTO document_batch_items (
                            batch_id, source_id, source_key, display_label, document_kind
                        )
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING source_id, display_label, document_kind, state,
                                  error_code, attempts,
                                  ocr_state, ocr_pages_processed, ocr_warning_codes
                        """,
                        (
                            row["id"],
                            source.source_id,
                            source.source_key,
                            source.display_label,
                            selection.document_kind,
                        ),
                    ).fetchone()
                    if item is None:
                        raise BatchRepositoryUnavailable
                    items.append(item)
                return _batch(row, items)
        except BatchRepositoryUnavailable:
            raise
        except psycopg.Error:
            raise BatchRepositoryUnavailable from None

    def get(self, *, household_space_id: UUID, batch_id: UUID) -> BatchRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT id, family_member_id, state
                    FROM document_batches
                    WHERE id = %s AND household_space_id = %s
                    """,
                    (batch_id, household_space_id),
                ).fetchone()
                if row is None:
                    return None
                items = connection.execute(
                    """
                    SELECT item.source_id, item.display_label, item.document_kind, item.state,
                           item.error_code, item.attempts, item.ocr_state,
                           item.ocr_pages_processed, item.ocr_warning_codes,
                           CASE WHEN item.state = 'succeeded'
                             THEN COALESCE(preparation.state, 'PENDING')
                             ELSE NULL END AS structure_state,
                           preparation.error_code AS structure_error_code,
                           jsonb_array_length(generation.plan_json->'chunks')
                             AS structure_planned_chunks,
                           jsonb_array_length(generation.plan_json->'unprocessed')
                             AS structure_unprocessed_ranges
                    FROM document_batch_items item
                    LEFT JOIN LATERAL (
                      SELECT version.id FROM document_versions version
                      WHERE version.document_id = item.document_id
                        AND (item.processed_document_version_id IS NULL
                             OR version.id = item.processed_document_version_id)
                      ORDER BY version.version_number DESC, version.id LIMIT 1
                    ) version ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT extraction.id FROM extractions extraction
                      WHERE extraction.document_version_id = version.id
                        AND extraction.status = 'succeeded'
                      ORDER BY extraction.succeeded_at DESC, extraction.id LIMIT 1
                    ) extraction ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT layer.id FROM ocr_layers layer
                      WHERE layer.extraction_id = extraction.id AND layer.status = 'succeeded'
                      ORDER BY layer.created_at DESC, layer.id DESC LIMIT 1
                    ) layer ON TRUE
                    LEFT JOIN LATERAL (
                      SELECT preparation.* FROM document_structure_preparations preparation
                      WHERE preparation.batch_item_id = item.id
                        AND preparation.extraction_id = extraction.id
                        AND preparation.ocr_layer_id IS NOT DISTINCT FROM layer.id
                      ORDER BY preparation.created_at DESC, preparation.id DESC LIMIT 1
                    ) preparation ON TRUE
                    LEFT JOIN document_structure_generations generation
                      ON generation.id = preparation.generation_id
                     AND generation.household_space_id = %s
                    WHERE item.batch_id = %s
                    ORDER BY item.created_at, item.id
                    """,
                    (household_space_id, batch_id),
                ).fetchall()
                return _batch(row, items)
        except psycopg.Error:
            raise BatchRepositoryUnavailable from None

    def requeue_password_required(
        self,
        *,
        household_space_id: UUID,
        batch_id: UUID,
    ) -> BatchRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                batch = connection.execute(
                    """
                    SELECT id
                    FROM document_batches
                    WHERE id = %s AND household_space_id = %s
                      AND state IN ('running', 'partial')
                    FOR UPDATE
                    """,
                    (batch_id, household_space_id),
                ).fetchone()
                if batch is None:
                    return None
                changed = connection.execute(
                    """
                    UPDATE document_batch_items
                    SET state = 'queued', error_code = NULL,
                        available_at = clock_timestamp(), updated_at = clock_timestamp()
                    WHERE batch_id = %s AND state = 'password_required'
                    RETURNING id
                    """,
                    (batch_id,),
                ).fetchall()
                if not changed:
                    return None
                connection.execute(
                    """
                    UPDATE document_batches
                    SET state = 'running', updated_at = clock_timestamp()
                    WHERE id = %s
                    """,
                    (batch_id,),
                )
            return self.get(household_space_id=household_space_id, batch_id=batch_id)
        except psycopg.Error:
            raise BatchRepositoryUnavailable from None

    def cancel(self, *, household_space_id: UUID, batch_id: UUID) -> BatchRecord | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                batch = connection.execute(
                    """
                    SELECT id, state
                    FROM document_batches
                    WHERE id = %s AND household_space_id = %s
                    FOR UPDATE
                    """,
                    (batch_id, household_space_id),
                ).fetchone()
                if batch is None:
                    return None
                if batch["state"] not in {"succeeded", "failed", "cancelled"}:
                    connection.execute(
                        """
                        UPDATE document_batch_items
                        SET state = 'cancelled', error_code = NULL,
                            lease_owner = NULL, lease_expires_at = NULL, heartbeat_at = NULL,
                            completed_at = clock_timestamp(), updated_at = clock_timestamp()
                        WHERE batch_id = %s
                          AND state NOT IN ('succeeded', 'permanently_failed', 'cancelled')
                        """,
                        (batch_id,),
                    )
                    connection.execute(
                        """
                        UPDATE document_batches
                        SET state = 'cancelled', completed_at = clock_timestamp(),
                            updated_at = clock_timestamp()
                        WHERE id = %s
                        """,
                        (batch_id,),
                    )
            return self.get(household_space_id=household_space_id, batch_id=batch_id)
        except psycopg.Error:
            raise BatchRepositoryUnavailable from None


__all__ = [
    "BatchItemRecord",
    "BatchRecord",
    "BatchRepository",
    "BatchRepositoryError",
    "BatchRepositoryUnavailable",
    "BatchSourceSelection",
]
