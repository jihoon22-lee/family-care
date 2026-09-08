"""Synthetic PostgreSQL preparation, retry, scope, and immutable metadata proofs."""

from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from workers.analyzer.tests.test_document_structure_repository import (
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "change", ["nonobject", "missing_identity", "invalid_identity", "duplicate"]
)
def test_malformed_prepared_component_cannot_stall_the_publisher(
    metadata_database: Any, change: str
) -> None:
    from familycare_worker.document_metadata import metadata_proposal

    url, job, generation = _seed(metadata_database)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
        payload = metadata_proposal(_source(job), generation, identity)
        if change == "nonobject":
            payload["components"] = [None]
        elif change == "missing_identity":
            del payload["components"][0]["identity"]
        elif change == "invalid_identity":
            payload["components"][0]["identity"] = "synthetic-invalid"
        else:
            payload["components"] *= 2
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO document_metadata_proposals("
                "generation_id,revision,state,attempts,proposal_json) "
                "VALUES(%s,%s,'PREPARED',1,%s)",
                (generation, payload["revision"], Jsonb(payload)),
            )


@pytest.fixture()
def metadata_database(request: pytest.FixtureRequest) -> Any:
    return request.getfixturevalue("structure_database")


def _source(job: Any, *, text: str | None = None, content_sha256: str = "a" * 64) -> Any:
    return build_document_structure(
        {
            "document_version_id": str(job.document_version_id),
            "content_sha256": content_sha256,
            "pages": [
                {
                    "page_number": 1,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [
                        {
                            "text": text
                            or "보험약관\n보험사: Sample Assurance\n상품코드: 001-SAMPLE",
                            "reading_order": 0,
                            "bbox": [10, 10, 400, 80],
                        }
                    ],
                    "tables": [],
                }
            ],
        },
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-metadata-v1",
    )


def _seed(
    database: Any, *, text: str | None = None, content_sha256: str = "a" * 64
) -> tuple[str, Any, Any]:
    url, job = database
    source = _source(job, text=text, content_sha256=content_sha256)
    generation = _prepare(
        DocumentStructureRepository(url),
        job,
        source,
        plan_structure_chunks(
            source, max_content_chars=4096, max_context_chars=4096, max_chunks=100
        ),
    )
    return url, job, generation


@pytest.mark.parametrize("paged", [False, True])
def test_metadata_runner_reads_retained_ir_and_is_idempotent(
    metadata_database: Any, monkeypatch: pytest.MonkeyPatch, paged: bool
) -> None:
    from familycare_worker import document_structure_repository
    from familycare_worker.document_metadata_repository import DocumentMetadataRunner

    if paged:
        monkeypatch.setattr(document_structure_repository, "_INLINE_STRUCTURE_BYTES", 1)

    url, job, generation = _seed(metadata_database)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        before = connection.execute(
            "SELECT (SELECT count(*) FROM policy_contracts) AS policies,"
            "(SELECT count(*) FROM terms_editions) AS editions"
        ).fetchone()
    runner = DocumentMetadataRunner(url)
    assert runner.run_once("synthetic-worker")
    assert not runner.run_once("synthetic-worker")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute("SELECT * FROM document_metadata_proposals").fetchone()
        assert row["generation_id"] == generation and row["state"] == "PREPARED"
        assert row["attempts"] == 1
        assert row["proposal_json"]["components"][0]["role"] == "terms"
        assert row["proposal_json"]["components"][0]["facts"][1]["value"] == "001-SAMPLE"
        assert (
            connection.execute(
                "SELECT (SELECT count(*) FROM policy_contracts) AS policies,"
                "(SELECT count(*) FROM terms_editions) AS editions"
            ).fetchone()
            == before
        )
        assert row["proposal_json"]["generation_id"] == str(generation)


def test_parallel_metadata_discovery_prepares_each_generation_once(metadata_database: Any) -> None:
    from familycare_worker.document_metadata_repository import DocumentMetadataRunner

    url, _, _ = _seed(metadata_database)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: DocumentMetadataRunner(url).run_once("synthetic-worker"), range(2)
            )
        )
    assert sorted(results) == [False, True]


def test_metadata_failure_retries_without_removing_ir(
    metadata_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker import document_metadata_repository as module

    url, _, generation = _seed(metadata_database)

    def fail(*args: Any, **kwargs: Any) -> Any:
        raise psycopg.OperationalError("synthetic failure")

    monkeypatch.setattr(module, "metadata_proposal", fail)
    runner = module.DocumentMetadataRunner(url)
    for attempt in range(1, 4):
        assert runner.run_once("synthetic-worker")
        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            row = connection.execute(
                "SELECT state,attempts FROM document_metadata_proposals"
            ).fetchone()
            assert row == {
                "state": "FAILED" if attempt == 3 else "RETRYABLE_FAILED",
                "attempts": attempt,
            }
            assert connection.execute(
                "SELECT id FROM document_structure_generations WHERE id=%s", (generation,)
            ).fetchone()
            connection.execute(
                "UPDATE document_metadata_proposals SET available_at=clock_timestamp() "
                "WHERE state='RETRYABLE_FAILED'"
            )
    assert not runner.run_once("synthetic-worker")


def test_successful_metadata_is_immutable_and_wrong_generation_is_rejected(
    metadata_database: Any,
) -> None:
    from familycare_worker.document_metadata_repository import DocumentMetadataRunner

    url, _, _ = _seed(metadata_database)
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    with psycopg.connect(_psycopg_url(url)) as connection:
        for command, args in (
            ("UPDATE document_metadata_proposals SET proposal_json='{}'", ()),
            ("UPDATE document_metadata_proposals SET generation_id=%s", (uuid4(),)),
            ("DELETE FROM document_metadata_proposals", ()),
        ):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(command, args)


def test_payload_storage_failure_has_durable_bounded_retries(
    metadata_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker import document_metadata_repository as module

    url, _, _ = _seed(metadata_database)
    original = module.metadata_proposal

    def invalid_payload(*args: Any, **kwargs: Any) -> Any:
        payload = original(*args, **kwargs)
        payload["structure_identity_sha256"] = "0" * 64
        return payload

    monkeypatch.setattr(module, "metadata_proposal", invalid_payload)
    assert module.DocumentMetadataRunner(url).run_once("synthetic-worker")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT state,attempts,proposal_json FROM document_metadata_proposals"
        ).fetchone()
        assert row == {"state": "RETRYABLE_FAILED", "attempts": 1, "proposal_json": None}
