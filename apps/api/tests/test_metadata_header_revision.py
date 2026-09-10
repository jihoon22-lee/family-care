"""Header proofs append independently validated metadata and preserve prior history."""

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.runtime_schema import SUPPORTED_SCHEMA_REVISION
from familycare_worker import document_metadata, document_metadata_repository
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_document_metadata_publication import (
    publication_database as publication_database,
)
from apps.api.tests.test_metadata_header_regions import _source as header_source
from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_document_metadata_repository import _seed, _source
from workers.analyzer.tests.test_document_structure_repository import (
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


def test_independent_header_reaches_a_published_terms_identity(publication_database):
    url, job = publication_database
    extraction = header_source().to_dict()["source_extraction"]
    extraction["document_version_id"] = str(job.document_version_id)
    source = build_document_structure(
        extraction, extraction_id=job.extraction_id, extraction_revision="synthetic-header-v1"
    )
    _prepare(
        DocumentStructureRepository(url),
        job,
        source,
        plan_structure_chunks(
            source, max_content_chars=4096, max_context_chars=4096, max_chunks=100
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-header-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute(
            "SELECT validator_revision,outcome FROM document_metadata_publications"
        ).fetchall() == [{"validator_revision": "document-metadata-api-v11", "outcome": "APPLIED"}]
        assert connection.execute("SELECT count(*) AS count FROM terms_editions").fetchone() == {
            "count": 1
        }
    assert not DocumentMetadataRunner(url).run_once("synthetic-header-worker")
    assert DocumentMetadataProjector(url).project_pending() == 0


def test_v11_keeps_v10_publication_when_only_the_revision_changes(
    publication_database, monkeypatch
):
    url, _, _ = _seed(publication_database)
    with monkeypatch.context() as legacy:
        legacy.setattr(document_metadata, "REVISION", "document-metadata-v10")
        legacy.setattr(document_metadata_repository, "REVISION", "document-metadata-v10")
        assert DocumentMetadataRunner(url).run_once("synthetic-history-worker")
        assert DocumentMetadataProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        old = connection.execute(
            "SELECT to_jsonb(p) AS value FROM document_metadata_publications p"
        ).fetchall()
        component = connection.execute(
            "SELECT to_jsonb(c) AS value FROM insurance_document_components c"
        ).fetchall()
    assert DocumentMetadataRunner(url).run_once("synthetic-history-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_metadata_publications p "
                "WHERE validator_revision='document-metadata-api-v10'"
            ).fetchall()
            == old
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(c) AS value FROM insurance_document_components c"
            ).fetchall()
            == component
        )
        assert connection.execute(
            "SELECT outcome FROM document_metadata_publications "
            "WHERE validator_revision='document-metadata-api-v11'"
        ).fetchone() == {"outcome": "DEFERRED"}


def test_even_unpublished_v11_history_prevents_downgrade(publication_database):
    url, job, generation = _seed(publication_database)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
        proposal = metadata_proposal(_source(job), generation, identity)
        proposal.update(revision="document-metadata-v11", components=[], unresolved_pages=[1])
        old = connection.execute(
            "INSERT INTO document_metadata_proposals "
            "(generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v11','PREPARED',1,%s) "
            "RETURNING to_jsonb(document_metadata_proposals) AS value",
            (generation, Jsonb(proposal)),
        ).fetchone()
    refused = _migrate(url, "downgrade", "0072_metadata_physical_flow")
    assert refused.returncode != 0
    assert "metadata header history prevents downgrade" in refused.stderr
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_metadata_proposals p"
            ).fetchone()
            == old
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == {
            "version_num": SUPPORTED_SCHEMA_REVISION
        }


def test_header_schema_round_trip_without_v11_history(publication_database):
    url, _ = publication_database
    down = _migrate(url, "downgrade", "0072_metadata_physical_flow")
    assert down.returncode == 0, down.stderr
    up = _migrate(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
