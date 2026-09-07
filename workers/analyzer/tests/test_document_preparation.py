"""Stored-source preparation runs locally, once per immutable source revision."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.document_preparation import DocumentPreparationRunner
from psycopg.rows import dict_row

from workers.analyzer.tests.test_document_structure_repository import (
    structure_database,  # noqa: F401 -- synthetic database fixture
)
from workers.analyzer.tests.test_policy_structuring_jobs import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401 -- dependency of shared fixture
)

pytestmark = pytest.mark.integration


def _seed_page(database_url: str, job: Any) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        page = connection.execute(
            """
            INSERT INTO extraction_pages (
              extraction_id, page_number, width_points, height_points, non_whitespace_chars,
              alphanumeric_ratio, replacement_character_ratio, maximum_repeated_character_run,
              classification
            ) VALUES (%s, 1, 600, 800, 500, 1, 0, 1, 'TEXT_SUFFICIENT') RETURNING id
            """,
            (job.extraction_id,),
        ).fetchone()
        assert page is not None
        connection.execute(
            "INSERT INTO extraction_blocks (page_id, text, bbox, reading_order) "
            "VALUES (%s, %s, '[10,10,400,20]', 0)",
            (page[0], "Synthetic preface " * 40 + "Late Sample Rider"),
        )


def test_runtime_prepares_once_without_key_or_source_files(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    database_url, job = request.getfixturevalue("structure_database")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    _seed_page(database_url, job)
    runner = DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id)
    assert runner.run_once("synthetic-worker-a")
    assert not runner.run_once("synthetic-worker-a")
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        attempt = connection.execute("SELECT * FROM document_structure_preparations").fetchone()
        assert attempt is not None and attempt["state"] == "PREPARED"
        assert attempt["attempts"] == 1
        stored = connection.execute(
            "SELECT structure_json, range_plan_complete FROM document_structure_generations "
            "WHERE id = %s",
            (attempt["generation_id"],),
        ).fetchone()
        assert stored is not None and stored["range_plan_complete"]
        assert "Late Sample Rider" in stored["structure_json"]["nodes"][0]["text"]
        assert (
            connection.execute(
                "SELECT count(*) FROM document_structure_chunks WHERE state <> 'PENDING'"
            ).fetchone()["count"]
            == 0
        )


def test_missing_extraction_pages_are_a_processing_failure_not_missing_documents(
    request: pytest.FixtureRequest,
) -> None:
    database_url, job = request.getfixturevalue("structure_database")
    runner = DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id)
    assert runner.run_once("synthetic-worker-a")
    assert not runner.run_once("synthetic-worker-a")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT state, error_code, generation_id FROM document_structure_preparations"
        ).fetchall() == [("FAILED", "STRUCTURE_SOURCE_INVALID", None)]
        assert connection.execute(
            "SELECT state FROM document_batch_items WHERE id = %s", (job.batch_item_id,)
        ).fetchone() == ("succeeded",)


def test_incomplete_page_storage_records_a_durable_bounded_retry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker import document_structure_repository as storage

    database_url, job = request.getfixturevalue("structure_database")
    _seed_page(database_url, job)
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    monkeypatch.setattr(storage, "iter_structure_pages", lambda _: iter(()))
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT to_regprocedure('validate_structure_page_manifest(uuid)') IS NOT NULL"
        ).fetchone() == (True,)
    runner = DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id)
    for attempt in range(1, 4):
        assert runner.run_once("synthetic-worker-a")
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            assert connection.execute(
                "SELECT state,attempts,generation_id FROM document_structure_preparations"
            ).fetchall() == [("RETRYABLE_FAILED" if attempt < 3 else "FAILED", attempt, None)]
            assert connection.execute(
                "SELECT count(*) FROM document_structure_generations"
            ).fetchone() == (0,)
        assert not runner.run_once("synthetic-worker-a")
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute(
                "UPDATE document_structure_preparations SET available_at=clock_timestamp() "
                "WHERE state='RETRYABLE_FAILED'"
            )
    assert not runner.run_once("synthetic-worker-a")


def test_concurrent_preparation_is_single_and_preserves_history(
    request: pytest.FixtureRequest,
) -> None:
    database_url, job = request.getfixturevalue("structure_database")
    _seed_page(database_url, job)
    runner = DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(runner.run_once, ("synthetic-worker-a", "synthetic-worker-b")))
    assert sorted(results) == [False, True]
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM document_structure_generations"
        ).fetchone() == (1,)
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("DELETE FROM document_structure_preparations")


def test_deleted_document_is_not_reprepared(request: pytest.FixtureRequest) -> None:
    database_url, job = request.getfixturevalue("structure_database")
    _seed_page(database_url, job)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE documents SET deleted_at = clock_timestamp() WHERE id = "
            "(SELECT document_id FROM document_versions WHERE id = %s)",
            (job.document_version_id,),
        )
    runner = DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id)
    assert not runner.run_once("synthetic-worker-a")


def test_batch_status_exposes_scoped_preparation_without_source_content(
    request: pytest.FixtureRequest,
) -> None:
    from familycare_api.documents.batch_repository import BatchRepository

    database_url, job = request.getfixturevalue("structure_database")
    _seed_page(database_url, job)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        row = connection.execute(
            "SELECT batch_id, source_id FROM document_batch_items WHERE id = %s",
            (job.batch_item_id,),
        ).fetchone()
        assert row is not None
        batch_id, source_id = row
    repository = BatchRepository(database_url)
    before = repository.get(household_space_id=job.household_space_id, batch_id=batch_id)
    assert before is not None
    item = next(item for item in before.items if item.source_id == source_id)
    assert item.structure_state == "PENDING"
    assert item.structure_planned_chunks is None
    assert DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id).run_once(
        "worker"
    )
    after = repository.get(household_space_id=job.household_space_id, batch_id=batch_id)
    assert after is not None
    item = next(item for item in after.items if item.source_id == source_id)
    assert item.structure_state == "PREPARED"
    assert item.structure_planned_chunks == 1
    assert item.structure_unprocessed_ranges == 0
    assert item.structure_error_code is None
    assert repository.get(household_space_id=uuid4(), batch_id=batch_id) is None
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO document_versions (
              document_id, version_number, content_sha256, byte_size, page_count
            ) SELECT document_id, 2, %s, 128, 1 FROM document_versions WHERE id = %s
            """,
            ("c" * 64, job.document_version_id),
        )
    changed = repository.get(household_space_id=job.household_space_id, batch_id=batch_id)
    assert changed is not None
    latest = next(item for item in changed.items if item.source_id == source_id)
    assert latest.structure_state == "PENDING"
    assert latest.structure_planned_chunks is None


def test_transient_local_failure_retries_without_reprocessing_success(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_worker import document_preparation

    database_url, job = request.getfixturevalue("structure_database")
    _seed_page(database_url, job)
    original = document_preparation.load_stored_structure
    runner = DocumentPreparationRunner(database_url, batch_item_id=job.batch_item_id)

    def unavailable(*args: Any, **kwargs: Any) -> None:
        raise psycopg.errors.QueryCanceled("synthetic timeout")

    monkeypatch.setattr(document_preparation, "load_stored_structure", unavailable)
    assert runner.run_once("worker")
    assert not runner.run_once("worker")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT state, attempts FROM document_structure_preparations"
        ).fetchone() == ("RETRYABLE_FAILED", 1)
        connection.execute(
            "UPDATE document_structure_preparations SET available_at = clock_timestamp()"
        )
    monkeypatch.setattr(document_preparation, "load_stored_structure", original)
    assert runner.run_once("worker")
    assert not runner.run_once("worker")
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT state, attempts, error_code FROM document_structure_preparations"
        ).fetchone() == ("PREPARED", 2, None)
