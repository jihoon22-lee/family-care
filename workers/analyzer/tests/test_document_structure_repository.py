"""Synthetic PostgreSQL proof for complete local structure and durable ranges."""

from __future__ import annotations

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import (
    DocumentStructureRepository,
    StructureScopeError,
)
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workers.analyzer.tests.test_policy_structuring_jobs import (
    _psycopg_url,
    seeded_policy_database,  # noqa: F401 -- shared synthetic fixture
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def structure_database(request: pytest.FixtureRequest) -> Iterator[tuple[str, Any]]:
    database_url, _, job_ids, _ = request.getfixturevalue("seeded_policy_database")
    job = PolicyStructuringJobQueue(database_url).get_job(job_ids[0])
    assert job is not None
    try:
        yield database_url, job
    finally:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute("TRUNCATE document_structure_generations CASCADE")


def _inputs(job: Any, *, maximum_chunks: int = 200) -> tuple[Any, Any]:
    source = {
        "document_version_id": str(job.document_version_id),
        "content_sha256": "a" * 64,
        "page_count": 1,
        "pages": [
            {
                "page_number": 1,
                "quality": {"classification": "TEXT_SUFFICIENT"},
                "blocks": [
                    {
                        "text": "Synthetic preface " * 30 + "Late Sample Rider 317",
                        "reading_order": 0,
                        "bbox": [10, 10, 400, 20],
                    },
                    *[
                        {
                            "text": f"Synthetic block {number}",
                            "reading_order": number,
                            "bbox": [10, 20 + number * 5, 400, 24 + number * 5],
                        }
                        for number in range(1, 70)
                    ],
                ],
                "tables": [],
            }
        ],
    }
    structure = build_document_structure(
        source, extraction_id=job.extraction_id, extraction_revision="synthetic-extractor-v1"
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=240, max_chunks=maximum_chunks
    )
    return structure, plan


def _prepare(repository: Any, job: Any, structure: Any, plan: Any) -> UUID:
    return repository.prepare(
        household_space_id=job.household_space_id,
        family_member_id=job.family_member_id,
        batch_item_id=job.batch_item_id,
        structure=structure,
        plan=plan,
    )


def test_full_local_source_and_range_identity_survive_repeat_preparation(
    structure_database: tuple[str, Any],
) -> None:
    database_url, job = structure_database
    repository = DocumentStructureRepository(database_url)
    structure, plan = _inputs(job)
    generation_id = _prepare(repository, job, structure, plan)
    assert _prepare(repository, job, structure, plan) == generation_id
    forged = replace(
        plan,
        chunks=(replace(plan.chunks[0], text="Synthetic forged range"), *plan.chunks[1:]),
    )
    with pytest.raises(StructureScopeError):
        _prepare(repository, job, structure, forged)
    forged_structure = replace(
        structure,
        nodes=(replace(structure.nodes[0], text="Synthetic forged node"), *structure.nodes[1:]),
    )
    forged_plan = plan_structure_chunks(
        forged_structure, max_content_chars=240, max_context_chars=240, max_chunks=200
    )
    with pytest.raises(StructureScopeError):
        _prepare(repository, job, forged_structure, forged_plan)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        stored = connection.execute(
            "SELECT * FROM document_structure_generations WHERE id = %s", (generation_id,)
        ).fetchone()
        assert stored is not None
        assert stored["structure_json"] == structure.to_dict()
        assert stored["plan_json"] == plan.to_dict()
        assert stored["range_plan_complete"]
        assert stored["is_current"]
        rows = connection.execute(
            "SELECT chunk_key, chunk_json FROM document_structure_chunks "
            "WHERE generation_id = %s ORDER BY position",
            (generation_id,),
        ).fetchall()
        assert len(rows) == len(plan.chunks) > 64
        assert len({row["chunk_key"] for row in rows}) == len(rows)
        text = "".join(row["chunk_json"]["text"] for row in rows)
        assert "Late Sample Rider 317" in text
        assert "Synthetic block 69" in text
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "UPDATE document_structure_generations SET structure_json = '{}' WHERE id = %s",
                (generation_id,),
            )


def test_partial_new_plan_keeps_the_previous_complete_generation_and_exposes_omissions(
    structure_database: tuple[str, Any],
) -> None:
    database_url, job = structure_database
    repository = DocumentStructureRepository(database_url)
    structure, complete = _inputs(job)
    current_id = _prepare(repository, job, structure, complete)
    _, partial = _inputs(job, maximum_chunks=2)
    partial_id = _prepare(repository, job, structure, partial)
    assert partial_id != current_id
    progress = repository.progress(job.household_space_id, job.family_member_id, partial_id)
    assert not progress.range_plan_complete
    assert progress.unprocessed_ranges > 0
    assert progress.total_chunks == 2
    assert progress.completed_chunks == 0
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT id FROM document_structure_generations WHERE is_current"
        ).fetchall() == [(current_id,)]
    with pytest.raises(StructureScopeError):
        repository.progress(uuid4(), job.family_member_id, current_id)
    with pytest.raises(StructureScopeError):
        _prepare(repository, replace(job, family_member_id=uuid4()), structure, complete)


def test_generation_progress_reads_only_counts_without_source_payloads(
    structure_database: tuple[str, Any],
) -> None:
    database_url, job = structure_database
    repository = DocumentStructureRepository(database_url)
    structure, plan = _inputs(job, maximum_chunks=2)
    generation_id = _prepare(repository, job, structure, plan)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        metadata = repository._scoped_generation(
            connection, job.household_space_id, job.family_member_id, generation_id
        )
        assert metadata == {
            "range_plan_complete": False,
            "unprocessed_ranges": len(plan.unprocessed),
        }
    progress = repository.progress(job.household_space_id, job.family_member_id, generation_id)
    assert progress.unprocessed_ranges == len(plan.unprocessed) > 0
    assert progress.total_chunks == 2
    assert progress.completed_chunks == progress.cancelled_chunks == 0
    repository.cancel(job.household_space_id, job.family_member_id, generation_id)
    cancelled = repository.progress(job.household_space_id, job.family_member_id, generation_id)
    assert cancelled.cancelled_chunks == cancelled.total_chunks == 2
    assert cancelled.unprocessed_ranges == progress.unprocessed_ranges
    repository.resume(job.household_space_id, job.family_member_id, generation_id)
    assert (
        repository.progress(job.household_space_id, job.family_member_id, generation_id) == progress
    )


def test_chunk_claim_retry_cancel_and_resume_preserve_successful_ranges(
    structure_database: tuple[str, Any],
) -> None:
    database_url, job = structure_database
    repository = DocumentStructureRepository(database_url)
    structure, plan = _inputs(job, maximum_chunks=3)
    generation_id = _prepare(repository, job, structure, plan)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = tuple(
            pool.map(
                lambda worker: repository.claim_next(worker, generation_id=generation_id),
                ("synthetic-worker-a", "synthetic-worker-b"),
            )
        )
    assert first is not None and second is not None and first.chunk_id != second.chunk_id
    assert repository.complete(first, result={"schema_version": "1", "outcome": "INDEXED"})
    assert not repository.complete(
        replace(second, lease_token=uuid4()), result={"schema_version": "1", "outcome": "INDEXED"}
    )
    assert repository.fail(second, reason_code="STRUCTURE_RANGE_RETRY", retryable=True)
    repository.cancel(job.household_space_id, job.family_member_id, generation_id)
    assert repository.claim_next("synthetic-worker-c", generation_id=generation_id) is None
    cancelled = repository.progress(job.household_space_id, job.family_member_id, generation_id)
    assert cancelled.completed_chunks == 1
    assert cancelled.cancelled_chunks == 2
    repository.resume(job.household_space_id, job.family_member_id, generation_id)
    resumed = repository.claim_next("synthetic-worker-c", generation_id=generation_id)
    assert resumed is not None and resumed.chunk_id != first.chunk_id
    assert repository.complete(resumed, result={"schema_version": "1", "outcome": "INDEXED"})
    final = repository.progress(job.household_space_id, job.family_member_id, generation_id)
    assert final.completed_chunks == 2
    assert not final.processing_complete


def test_expired_range_lease_cannot_complete_or_replace_a_new_attempt(
    structure_database: tuple[str, Any],
) -> None:
    database_url, job = structure_database
    repository = DocumentStructureRepository(database_url)
    structure, plan = _inputs(job, maximum_chunks=1)
    generation_id = _prepare(repository, job, structure, plan)
    first = repository.claim_next("synthetic-worker-a", generation_id=generation_id)
    assert first is not None
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE document_structure_chunks SET lease_expires_at = clock_timestamp() "
            "- interval '1 second' WHERE id = %s",
            (first.chunk_id,),
        )
    second = repository.claim_next("synthetic-worker-b", generation_id=generation_id)
    assert second is not None and second.chunk_id == first.chunk_id
    assert second.lease_token != first.lease_token
    assert not repository.complete(first, result={"schema_version": "1", "outcome": "INDEXED"})
    assert repository.complete(second, result={"schema_version": "1", "outcome": "INDEXED"})


def test_existing_stored_extraction_is_reused_without_a_pdf_or_provider(
    structure_database: tuple[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_url, job = structure_database
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    source, _ = _inputs(job)
    blocks = source.source_extraction["pages"][0]["blocks"]
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        page = connection.execute(
            """
            INSERT INTO extraction_pages (
              extraction_id, page_number, width_points, height_points, non_whitespace_chars,
              alphanumeric_ratio, replacement_character_ratio, maximum_repeated_character_run,
              classification
            ) VALUES (%s, 1, 600, 800, 1000, 1, 0, 1, 'TEXT_SUFFICIENT') RETURNING id
            """,
            (job.extraction_id,),
        ).fetchone()
        assert page is not None
        for block in blocks:
            connection.execute(
                "INSERT INTO extraction_blocks (page_id, text, bbox, reading_order) "
                "VALUES (%s,%s,%s,%s)",
                (page[0], block["text"], Jsonb(block["bbox"]), block["reading_order"]),
            )
    repository = DocumentStructureRepository(database_url)
    prepared = repository.prepare_stored(
        household_space_id=job.household_space_id,
        family_member_id=job.family_member_id,
        batch_item_id=job.batch_item_id,
    )
    assert (
        repository.prepare_stored(
            household_space_id=job.household_space_id,
            family_member_id=job.family_member_id,
            batch_item_id=job.batch_item_id,
        )
        == prepared
    )
    progress = repository.progress(job.household_space_id, job.family_member_id, prepared)
    assert progress.total_chunks > 64 and progress.range_plan_complete
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        raw = connection.execute(
            "SELECT structure_json FROM document_structure_generations WHERE id = %s", (prepared,)
        ).fetchone()
        assert raw is not None
        reconstructed = raw[0]["source_extraction"]["pages"][0]["blocks"]
        assert [block["text"] for block in reconstructed] == [block["text"] for block in blocks]
    with pytest.raises(StructureScopeError):
        repository.prepare_stored(
            household_space_id=uuid4(),
            family_member_id=job.family_member_id,
            batch_item_id=job.batch_item_id,
        )


@pytest.mark.parametrize("digest_matches", [True, False])
def test_stored_ocr_is_used_only_with_its_actual_source_digest(
    structure_database: tuple[str, Any],
    digest_matches: bool,
) -> None:
    database_url, job = structure_database
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO extraction_pages (
              extraction_id, page_number, width_points, height_points, non_whitespace_chars,
              alphanumeric_ratio, replacement_character_ratio, maximum_repeated_character_run,
              classification
            ) VALUES (%s, 1, 600, 800, 0, 0, 0, 0, 'OCR_REQUIRED')
            """,
            (job.extraction_id,),
        )
        layer = connection.execute(
            """
            INSERT INTO ocr_layers (
              extraction_id, source_layer, engine_name, engine_version,
              language_config_hash, quality_rule_version, status
            ) VALUES (%s, 'ocr', 'tesseract', 'synthetic-v1', %s, 'quality-v1', 'succeeded')
            RETURNING id
            """,
            (job.extraction_id, "c" * 64),
        ).fetchone()
        assert layer is not None
        page = connection.execute(
            """
            INSERT INTO ocr_pages (
              ocr_layer_id, document_version_id, content_sha256, page_number, rendered_dpi,
              image_width_pixels, image_height_pixels, selected_classification, status
            ) VALUES (%s,%s,%s,1,300,600,800,'OCR_REQUIRED','completed') RETURNING id
            """,
            (layer[0], job.document_version_id, ("a" if digest_matches else "f") * 64),
        ).fetchone()
        assert page is not None
        connection.execute(
            "INSERT INTO ocr_blocks (ocr_page_id,text,bbox,reading_order,confidence,"
            "source_layer,review_state) "
            "VALUES (%s,'Synthetic recognized policy text','[10,10,400,20]',"
            "0,90,'ocr','candidate')",
            (page[0],),
        )
    repository = DocumentStructureRepository(database_url)
    if digest_matches:
        generation_id = repository.prepare_stored(
            household_space_id=job.household_space_id,
            family_member_id=job.family_member_id,
            batch_item_id=job.batch_item_id,
        )
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            stored = connection.execute(
                "SELECT structure_json FROM document_structure_generations WHERE id = %s",
                (generation_id,),
            ).fetchone()
            assert stored is not None
            assert stored[0]["pages"][0]["active_layer"] == "ocr"
            assert stored[0]["nodes"][0]["text"] == "Synthetic recognized policy text"
    else:
        with pytest.raises(StructureScopeError):
            repository.prepare_stored(
                household_space_id=job.household_space_id,
                family_member_id=job.family_member_id,
                batch_item_id=job.batch_item_id,
            )
