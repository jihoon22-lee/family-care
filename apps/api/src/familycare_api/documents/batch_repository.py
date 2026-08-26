"""Household-scoped persistence for private document-import batches."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

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
    state: str
    error_code: str | None
    attempts: int
    ocr_state: str
    ocr_pages_processed: int
    ocr_warning_codes: tuple[str, ...]


@dataclass(frozen=True)
class BatchRecord:
    batch_id: UUID
    family_member_id: UUID
    state: str
    items: tuple[BatchItemRecord, ...]


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
                state=cast(str, item["state"]),
                error_code=cast(str | None, item["error_code"]),
                attempts=cast(int, item["attempts"]),
                ocr_state=cast(str, item["ocr_state"]),
                ocr_pages_processed=cast(int, item["ocr_pages_processed"]),
                ocr_warning_codes=tuple(cast(list[str], item["ocr_warning_codes"])),
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
        sources: tuple[ResolvedImportSource, ...],
    ) -> BatchRecord | None:
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
                for source in sources:
                    item = connection.execute(
                        """
                        INSERT INTO document_batch_items (
                            batch_id, source_id, source_key, display_label
                        )
                        VALUES (%s, %s, %s, %s)
                        RETURNING source_id, display_label, state, error_code, attempts,
                                  ocr_state, ocr_pages_processed, ocr_warning_codes
                        """,
                        (
                            row["id"],
                            source.source_id,
                            source.source_key,
                            source.display_label,
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
                    SELECT source_id, display_label, state, error_code, attempts,
                           ocr_state, ocr_pages_processed, ocr_warning_codes
                    FROM document_batch_items
                    WHERE batch_id = %s
                    ORDER BY created_at, id
                    """,
                    (batch_id,),
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
]
