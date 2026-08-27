"""Synthetic PostgreSQL proof for household-scoped document batch persistence."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator, Sequence
from dataclasses import asdict, dataclass
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.documents.batch_repository import (
    BatchRecord,
    BatchRepository,
    BatchSourceSelection,
)
from familycare_api.documents.import_sources import ResolvedImportSource

pytestmark = pytest.mark.integration

SOURCE_ID_A = "a" * 64
SOURCE_ID_B = "b" * 64
SOURCE_ID_C = "c" * 64
FORBIDDEN_PROJECTION_KEYS = {
    "absolute_path",
    "archive_master_key",
    "bbox",
    "coordinates",
    "image_path",
    "ocr_text",
    "password",
    "raw_error",
    "raw_pdf",
    "source_key",
    "stderr",
}


@dataclass(frozen=True)
class _Seed:
    household_a: UUID
    household_b: UUID
    member_a: UUID
    member_b: UUID
    admin_a: UUID
    admin_b: UUID


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _source(source_id: str, source_key: str, display_label: str) -> ResolvedImportSource:
    return ResolvedImportSource(
        source_id=source_id,
        source_key=source_key,
        display_label=display_label,
        size_bytes=256,
        encrypted=True,
    )


def _sources(count: int = 2) -> tuple[ResolvedImportSource, ...]:
    values = (
        _source(SOURCE_ID_A, "synthetic/member-a/Sample Policy A.pdf", "Sample Policy A.pdf"),
        _source(SOURCE_ID_B, "synthetic/member-a/Sample Policy B.pdf", "Sample Policy B.pdf"),
        _source(SOURCE_ID_C, "synthetic/member-a/Sample Policy C.pdf", "Sample Policy C.pdf"),
    )
    return values[:count]


def _seed(database_url: str) -> _Seed:
    seed = _Seed(
        household_a=uuid4(),
        household_b=uuid4(),
        member_a=uuid4(),
        member_b=uuid4(),
        admin_a=uuid4(),
        admin_b=uuid4(),
    )
    suffix = seed.household_a.hex[:12]
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES
              (%s, %s, 'Synthetic Batch Household A'),
              (%s, %s, 'Synthetic Batch Household B')
            """,
            (
                seed.household_a,
                f"synthetic-batch-household-a-{suffix}",
                seed.household_b,
                f"synthetic-batch-household-b-{suffix}",
            ),
        )
        connection.execute(
            """
            INSERT INTO app_users (
                id, household_space_id, username, display_name, password_hash
            )
            VALUES
              (%s, %s, %s, 'Synthetic Batch Admin A', '$argon2id$synthetic-test-hash'),
              (%s, %s, %s, 'Synthetic Batch Admin B', '$argon2id$synthetic-test-hash')
            """,
            (
                seed.admin_a,
                seed.household_a,
                f"synthetic-batch-admin-a-{suffix}",
                seed.admin_b,
                seed.household_b,
                f"synthetic-batch-admin-b-{suffix}",
            ),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES
              (%s, %s, 'Family Member A', %s),
              (%s, %s, 'Family Member B', %s)
            """,
            (
                seed.member_a,
                seed.household_a,
                f"synthetic-member-a-{suffix}",
                seed.member_b,
                seed.household_b,
                f"synthetic-member-b-{suffix}",
            ),
        )
    return seed


def _cleanup(database_url: str, seed: _Seed) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            DELETE FROM document_batch_items
            WHERE batch_id IN (
                SELECT id FROM document_batches
                WHERE household_space_id IN (%s, %s)
            )
            """,
            (seed.household_a, seed.household_b),
        )
        connection.execute(
            """
            DELETE FROM document_batches
            WHERE household_space_id IN (%s, %s)
            """,
            (seed.household_a, seed.household_b),
        )
        connection.execute(
            "DELETE FROM app_users WHERE id IN (%s, %s)",
            (seed.admin_a, seed.admin_b),
        )
        connection.execute(
            "DELETE FROM family_members WHERE id IN (%s, %s)",
            (seed.member_a, seed.member_b),
        )
        connection.execute(
            "DELETE FROM household_spaces WHERE id IN (%s, %s)",
            (seed.household_a, seed.household_b),
        )


@pytest.fixture()
def synthetic_database() -> Iterator[tuple[str, _Seed]]:
    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    seed = _seed(database_url)
    try:
        yield database_url, seed
    finally:
        _cleanup(database_url, seed)


def _create_batch(
    repository: BatchRepository,
    seed: _Seed,
    *,
    count: int = 2,
) -> BatchRecord:
    batch = repository.create(
        household_space_id=seed.household_a,
        created_by=seed.admin_a,
        family_member_id=seed.member_a,
        sources=tuple(
            BatchSourceSelection(
                source=source,
                document_kind=("policy", "terms", "supporting")[index],
            )
            for index, source in enumerate(_sources(count))
        ),
    )
    assert batch is not None
    return batch


def _set_batch_state(database_url: str, batch_id: UUID, state: str) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE document_batches
            SET state = %s, updated_at = clock_timestamp()
            WHERE id = %s
            """,
            (state, batch_id),
        )


def _set_item_states(
    database_url: str,
    batch_id: UUID,
    states: Sequence[tuple[str, str, str | None]],
) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        for source_id, state, error_code in states:
            updated = connection.execute(
                """
                UPDATE document_batch_items
                SET state = %s,
                    error_code = %s,
                    attempts = 1,
                    completed_at = CASE WHEN %s THEN clock_timestamp() ELSE NULL END,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    heartbeat_at = NULL,
                    updated_at = clock_timestamp()
                WHERE batch_id = %s AND source_id = %s
                """,
                (
                    state,
                    error_code,
                    state in {"succeeded", "permanently_failed", "cancelled"},
                    batch_id,
                    source_id,
                ),
            )
            assert updated.rowcount == 1


def _items_by_source(batch: BatchRecord) -> dict[str, object]:
    return {item.source_id: item for item in batch.items}


def test_create_and_lookup_are_household_member_scoped_and_metadata_only(
    synthetic_database: tuple[str, _Seed],
) -> None:
    database_url, seed = synthetic_database
    repository = BatchRepository(database_url)

    created = _create_batch(repository, seed)
    assert created.state == "created"
    assert [item.source_id for item in created.items] == [SOURCE_ID_A, SOURCE_ID_B]
    assert [item.document_kind for item in created.items] == ["policy", "terms"]

    scoped = repository.get(
        household_space_id=seed.household_a,
        batch_id=created.batch_id,
    )
    assert scoped is not None
    assert scoped.batch_id == created.batch_id
    assert scoped.family_member_id == created.family_member_id
    assert scoped.state == created.state
    assert {item.source_id: asdict(item) for item in scoped.items} == {
        item.source_id: asdict(item) for item in created.items
    }
    assert (
        repository.get(
            household_space_id=seed.household_b,
            batch_id=created.batch_id,
        )
        is None
    )

    cross_member = repository.create(
        household_space_id=seed.household_a,
        created_by=seed.admin_a,
        family_member_id=seed.member_b,
        sources=(BatchSourceSelection(source=_sources(1)[0], document_kind="policy"),),
    )
    assert cross_member is None

    cross_household_admin = repository.create(
        household_space_id=seed.household_a,
        created_by=seed.admin_b,
        family_member_id=seed.member_a,
        sources=(BatchSourceSelection(source=_sources(1)[0], document_kind="policy"),),
    )
    assert cross_household_admin is None

    projection = json.dumps(asdict(scoped), default=str, sort_keys=True)
    assert FORBIDDEN_PROJECTION_KEYS.isdisjoint(projection)
    assert "synthetic-batch-password" not in projection
    assert {field for item in scoped.items for field in asdict(item)} == {
        "source_id",
        "display_label",
        "document_kind",
        "state",
        "error_code",
        "attempts",
        "ocr_state",
        "ocr_pages_processed",
        "ocr_warning_codes",
    }
    assert all(item.ocr_state == "pending" for item in scoped.items)
    assert all(item.ocr_pages_processed == 0 for item in scoped.items)
    assert all(item.ocr_warning_codes == () for item in scoped.items)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        rows = connection.execute(
            """
            SELECT source_key, display_label
            FROM document_batch_items
            WHERE batch_id = %s
            ORDER BY source_id
            """,
            (created.batch_id,),
        ).fetchall()
    assert rows == [
        ("synthetic/member-a/Sample Policy A.pdf", "Sample Policy A.pdf"),
        ("synthetic/member-a/Sample Policy B.pdf", "Sample Policy B.pdf"),
    ]


def test_requeue_changes_only_password_required_items(
    synthetic_database: tuple[str, _Seed],
) -> None:
    database_url, seed = synthetic_database
    repository = BatchRepository(database_url)
    created = _create_batch(repository, seed, count=3)
    _set_batch_state(database_url, created.batch_id, "running")
    _set_item_states(
        database_url,
        created.batch_id,
        (
            (SOURCE_ID_A, "succeeded", None),
            (SOURCE_ID_B, "password_required", "PASSWORD_REQUIRED"),
            (SOURCE_ID_C, "retryable_failed", "EXTRACTION_TIMEOUT"),
        ),
    )

    requeued = repository.requeue_password_required(
        household_space_id=seed.household_a,
        batch_id=created.batch_id,
    )

    assert requeued is not None
    assert requeued.state == "running"
    items = _items_by_source(requeued)
    assert items[SOURCE_ID_A].state == "succeeded"
    assert items[SOURCE_ID_A].attempts == 1
    assert items[SOURCE_ID_B].state == "queued"
    assert items[SOURCE_ID_B].error_code is None
    assert items[SOURCE_ID_B].attempts == 1
    assert items[SOURCE_ID_C].state == "retryable_failed"
    assert items[SOURCE_ID_C].error_code == "EXTRACTION_TIMEOUT"
    assert items[SOURCE_ID_C].attempts == 1


def test_item_aggregate_transitions_from_partial_to_succeeded(
    synthetic_database: tuple[str, _Seed],
) -> None:
    database_url, seed = synthetic_database
    repository = BatchRepository(database_url)
    created = _create_batch(repository, seed)
    _set_item_states(
        database_url,
        created.batch_id,
        (
            (SOURCE_ID_A, "succeeded", None),
            (SOURCE_ID_B, "password_required", "PASSWORD_REQUIRED"),
        ),
    )

    partial = repository.get(
        household_space_id=seed.household_a,
        batch_id=created.batch_id,
    )
    assert partial is not None
    assert partial.state == "partial"

    _set_item_states(
        database_url,
        created.batch_id,
        ((SOURCE_ID_B, "succeeded", None),),
    )
    succeeded = repository.get(
        household_space_id=seed.household_a,
        batch_id=created.batch_id,
    )
    assert succeeded is not None
    assert succeeded.state == "succeeded"
    assert all(item.state == "succeeded" for item in succeeded.items)


def test_lookup_projects_ocr_progress_and_stable_warnings(
    synthetic_database: tuple[str, _Seed],
) -> None:
    database_url, seed = synthetic_database
    repository = BatchRepository(database_url)
    created = _create_batch(repository, seed)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        updated = connection.execute(
            """
            UPDATE document_batch_items
            SET ocr_state = 'warning', ocr_pages_processed = 2,
                ocr_warning_codes = '["NO_TEXT_DETECTED"]'::jsonb
            WHERE batch_id = %s AND source_id = %s
            """,
            (created.batch_id, SOURCE_ID_A),
        )
        assert updated.rowcount == 1

    status = repository.get(household_space_id=seed.household_a, batch_id=created.batch_id)

    assert status is not None
    item = _items_by_source(status)[SOURCE_ID_A]
    assert item.ocr_state == "warning"
    assert item.ocr_pages_processed == 2
    assert item.ocr_warning_codes == ("NO_TEXT_DETECTED",)


def test_cancel_is_household_scoped_and_preserves_completed_items(
    synthetic_database: tuple[str, _Seed],
) -> None:
    database_url, seed = synthetic_database
    repository = BatchRepository(database_url)
    created = _create_batch(repository, seed)
    _set_batch_state(database_url, created.batch_id, "running")
    _set_item_states(
        database_url,
        created.batch_id,
        (
            (SOURCE_ID_A, "succeeded", None),
            (SOURCE_ID_B, "queued", None),
        ),
    )

    assert (
        repository.cancel(
            household_space_id=seed.household_b,
            batch_id=created.batch_id,
        )
        is None
    )

    cancelled = repository.cancel(
        household_space_id=seed.household_a,
        batch_id=created.batch_id,
    )
    assert cancelled is not None
    assert cancelled.state == "cancelled"
    items = _items_by_source(cancelled)
    assert items[SOURCE_ID_A].state == "succeeded"
    assert items[SOURCE_ID_B].state == "cancelled"
