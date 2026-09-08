"""Legacy and paged source projections keep complete page/lineage boundaries."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_worker import document_structure_repository as storage
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from workers.analyzer.tests.test_document_structure_repository import (
    _inputs,
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def stored_structure(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("structure_database")


def test_scoped_projection_preserves_every_legacy_page_node(stored_structure: Any) -> None:
    url, job = stored_structure
    structure, plan = _inputs(job)
    generation = _prepare(DocumentStructureRepository(url), job, structure, plan)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        source = connection.execute(
            "SELECT document_structure_projection(%s,%s,%s) AS structure",
            (generation, job.household_space_id, [1]),
        ).fetchone()["structure"]
        assert source == {
            "lineage": structure.to_dict()["lineage"],
            "nodes": structure.to_dict()["nodes"],
        }
        assert (
            connection.execute(
                "SELECT document_structure_projection(%s,%s,%s) AS structure",
                (generation, uuid4(), [1]),
            ).fetchone()["structure"]
            is None
        )
        assert (
            connection.execute(
                "SELECT document_structure_projection(%s,%s,%s) AS structure",
                (generation, job.household_space_id, [2]),
            ).fetchone()["structure"]["nodes"]
            == []
        )


def test_paged_retention_restores_original_payload_and_identity(
    stored_structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker.structure_storage import structure_identity

    url, job = stored_structure
    structure, plan = _inputs(job)
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    repository = DocumentStructureRepository(url)
    generation = _prepare(repository, job, structure, plan)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT structure_json,identity_sha256 FROM document_structure_generations WHERE id=%s",
            (generation,),
        ).fetchone()
        assert row["structure_json"].get("storage_layout") == "page-v1"
        assert row["identity_sha256"] == structure_identity(structure, plan)[0]
        assert connection.execute(
            "SELECT part_number FROM document_structure_page_payloads WHERE generation_id=%s "
            "ORDER BY part_number",
            (generation,),
        ).fetchall() == [{"part_number": 0}, {"part_number": 1}]
        projected = connection.execute(
            "SELECT document_structure_projection(%s,%s,%s) AS structure",
            (generation, job.household_space_id, [1]),
        ).fetchone()["structure"]
        assert projected["nodes"] == structure.to_dict()["nodes"]
    assert (
        repository.read_structure_payload(job.household_space_id, job.family_member_id, generation)
        == structure.to_dict()
    )
    # The physical storage format is not part of logical document identity.
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 64 * 1024 * 1024)
    assert _prepare(repository, job, structure, plan) == generation


def test_incomplete_page_write_rolls_back_and_keeps_previous_current(
    stored_structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker.document_structure import build_document_structure, plan_structure_chunks

    url, job = stored_structure
    structure, plan = _inputs(job)
    repository = DocumentStructureRepository(url)
    previous = _prepare(repository, job, structure, plan)
    newer = build_document_structure(
        structure.source_extraction,
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-page-storage-new",
    )
    new_plan = plan_structure_chunks(
        newer, max_content_chars=240, max_context_chars=240, max_chunks=200
    )
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    monkeypatch.setattr(storage, "iter_structure_pages", lambda _: iter(()))
    with pytest.raises(storage.StructureScopeError):
        _prepare(repository, job, newer, new_plan)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT id FROM document_structure_generations WHERE is_current"
        ).fetchall() == [(previous,)]
        assert connection.execute(
            "SELECT count(*) FROM document_structure_page_payloads"
        ).fetchone() == (0,)


def test_page_json_retains_original_numeric_serialization(
    stored_structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import math

    from familycare_worker.document_structure import build_document_structure, plan_structure_chunks

    url, job = stored_structure
    structure, _ = _inputs(job)
    source = structure.to_dict()["source_extraction"]
    source["synthetic_metadata"] = {"negative_zero": -0.0, "wide_float": 1e30}
    source["pages"][0]["synthetic_metadata"] = {"negative_zero": -0.0, "wide_float": 1e30}
    structure = build_document_structure(
        source, extraction_id=job.extraction_id, extraction_revision="synthetic-number-storage"
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=240, max_chunks=200
    )
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    repository = DocumentStructureRepository(url)
    generation = _prepare(repository, job, structure, plan)
    restored = repository.read_structure_payload(
        job.household_space_id, job.family_member_id, generation
    )
    assert restored == structure.to_dict()
    for item in (restored["source_extraction"], restored["source_extraction"]["pages"][0]):
        numbers = item["synthetic_metadata"]
        assert math.copysign(1, numbers["negative_zero"]) == -1
        assert type(numbers["wide_float"]) is float


def test_paged_history_prevents_downgrade_and_remains_readable(
    stored_structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy.exc import DBAPIError

    url, job = stored_structure
    structure, plan = _inputs(job)
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    repository = DocumentStructureRepository(url)
    generation = _prepare(repository, job, structure, plan)
    config = Config(Path(__file__).resolve().parents[3] / "apps/api/alembic.ini")
    with pytest.raises(DBAPIError, match="paged document structure history must be retained"):
        command.downgrade(config, "0035_user_identity_proof")
    assert (
        repository.read_structure_payload(job.household_space_id, job.family_member_id, generation)
        == structure.to_dict()
    )


@pytest.mark.parametrize("operation", ["update", "delete", "extra_part"])
def test_published_pages_cannot_be_rewritten_or_extended(
    stored_structure: Any, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    url, job = stored_structure
    structure, plan = _inputs(job)
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    generation = _prepare(DocumentStructureRepository(url), job, structure, plan)
    with (
        psycopg.connect(_psycopg_url(url)) as connection,
        pytest.raises(psycopg.errors.CheckViolation),
        connection.transaction(),
    ):
        if operation == "update":
            connection.execute(
                "UPDATE document_structure_page_payloads SET payload_json='{}' "
                "WHERE generation_id=%s",
                (generation,),
            )
        elif operation == "delete":
            connection.execute(
                "DELETE FROM document_structure_page_payloads WHERE generation_id=%s",
                (generation,),
            )
        else:
            connection.execute(
                "INSERT INTO document_structure_page_payloads "
                "(generation_id,part_number,payload_json) VALUES (%s,2,'{}')",
                (generation,),
            )


def test_large_synthetic_document_survives_the_old_whole_json_limit(
    stored_structure: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
    from familycare_worker.structure_storage import structure_identity

    url, job = stored_structure
    source = {
        "document_version_id": str(job.document_version_id),
        "content_sha256": "a" * 64,
        "page_count": 100,
        "pages": [
            {
                "page_number": number,
                "quality": {"classification": "TEXT_SUFFICIENT"},
                "blocks": [
                    {
                        "text": "Sample",
                        "reading_order": word,
                        "bbox": [
                            10 + word % 20 * 10,
                            10 + word // 20 * 10,
                            19 + word % 20 * 10,
                            18 + word // 20 * 10,
                        ],
                    }
                    for word in range(1000)
                ],
                "tables": [],
            }
            for number in range(1, 101)
        ],
    }
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_versions SET page_count=100 WHERE id=%s", (job.document_version_id,)
        )
    structure = build_document_structure(
        source, extraction_id=job.extraction_id, extraction_revision="synthetic-large-page-storage"
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=240, max_chunks=16384
    )
    assert structure_identity(structure, plan)[2] > 64 * 1024 * 1024
    repository = DocumentStructureRepository(url)
    # Reproduce the previous monolithic storage rejection, then use page storage.
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 128 * 1024 * 1024)
    with pytest.raises(storage.StructureScopeError):
        _prepare(repository, job, structure, plan)
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 8 * 1024 * 1024)
    generation = _prepare(repository, job, structure, plan)
    restored = repository.read_structure_payload(
        job.household_space_id, job.family_member_id, generation
    )
    assert len(restored["nodes"]) == len(structure.nodes) == 105000
    for page in (1, 50, 100):
        assert restored["source_extraction"]["pages"][page - 1] == source["pages"][page - 1]
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            projection = connection.execute(
                "SELECT document_structure_projection(%s,%s,%s) AS structure",
                (generation, job.household_space_id, [page]),
            ).fetchone()["structure"]
            assert len(projection["nodes"]) == 1050
            assert {node["page_number"] for node in projection["nodes"]} == {page}
    assert plan.complete and not plan.unprocessed
    assert (
        repository.progress(job.household_space_id, job.family_member_id, generation).total_chunks
        == 5000
    )


def test_paged_projection_includes_prior_page_table_context(
    stored_structure: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_worker.document_structure import build_document_structure, plan_structure_chunks

    from workers.analyzer.tests.test_policy_table_continuation import _continued

    url, job = stored_structure
    source = _continued().to_dict()["source_extraction"]
    source["document_version_id"] = str(job.document_version_id)
    source["content_sha256"] = "a" * 64
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s", (job.document_version_id,)
        )
    structure = build_document_structure(
        source, extraction_id=job.extraction_id, extraction_revision="synthetic-paged-continuation"
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=50
    )
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0)
    generation = _prepare(DocumentStructureRepository(url), job, structure, plan)
    contexts = {
        ref for node in structure.nodes if node.page_number == 2 for ref in node.context_node_ids
    }
    assert contexts
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        projection = connection.execute(
            "SELECT document_structure_projection(%s,%s,%s) AS structure",
            (generation, job.household_space_id, [2]),
        ).fetchone()["structure"]
    assert projection["nodes"] == [
        node
        for node in structure.to_dict()["nodes"]
        if node["page_number"] == 2 or node["node_id"] in contexts
    ]
    assert any(
        node["page_number"] == 1 and node["row_role"] == "header" for node in projection["nodes"]
    )
