"""A new lineage proof appends metadata without rewriting historical revisions."""

from typing import Any

import psycopg
import pytest
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.runtime_schema import SUPPORTED_SCHEMA_REVISION
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_document_metadata_publication import (
    publication_database as publication_database,
)
from apps.api.tests.test_document_metadata_publication import (
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_metadata_navigation_publication import _migrate
from workers.analyzer.tests.test_document_metadata_repository import _seed, _source
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _historical_v10_producer(monkeypatch):
    """Keep the v10 source-flow acceptance independent of later producers."""
    from familycare_worker import document_metadata, document_metadata_repository

    monkeypatch.setattr(document_metadata, "REVISION", "document-metadata-v10")
    monkeypatch.setattr(document_metadata_repository, "REVISION", "document-metadata-v10")


def test_v10_appends_to_same_generation_and_preserves_v9_history(publication_database: Any) -> None:
    url, job, generation = _seed(publication_database)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s",
            (generation,),
        ).fetchone()["identity_sha256"]
        previous = metadata_proposal(_source(job), generation, identity)
        previous.update(revision="document-metadata-v9", components=[], unresolved_pages=[1])
        old = connection.execute(
            "INSERT INTO document_metadata_proposals "
            "(generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v9','PREPARED',1,%s) "
            "RETURNING id,to_jsonb(document_metadata_proposals)::text AS snapshot",
            (generation, Jsonb(previous)),
        ).fetchone()
    assert DocumentMetadataRunner(url).run_once("synthetic-lineage-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert not DocumentMetadataRunner(url).run_once("synthetic-lineage-worker")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute(
            "SELECT revision FROM document_metadata_proposals ORDER BY created_at,id"
        ).fetchall() == [
            {"revision": "document-metadata-v9"},
            {"revision": "document-metadata-v10"},
        ]
        assert connection.execute(
            "SELECT validator_revision,outcome FROM document_metadata_publications"
        ).fetchall() == [{"validator_revision": "document-metadata-api-v10", "outcome": "APPLIED"}]
        snapshots = connection.execute(
            "SELECT id,to_jsonb(p)::text AS snapshot FROM document_metadata_proposals p ORDER BY id"
        ).fetchall()
        assert old in snapshots
        assert connection.execute(
            "SELECT id,identity_sha256 FROM document_structure_generations"
        ).fetchall() == [{"id": generation, "identity_sha256": identity}]
    refused = _migrate(url, "downgrade", "0071_source_calculations")
    assert refused.returncode != 0
    assert "metadata revision history prevents downgrade" in refused.stderr
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT id,to_jsonb(p)::text AS snapshot "
                "FROM document_metadata_proposals p ORDER BY id"
            ).fetchall()
            == snapshots
        )
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == {
            "version_num": SUPPORTED_SCHEMA_REVISION
        }


def test_v10_keeps_v9_publication_when_the_new_proposal_does_not_refine_it(
    publication_database: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from familycare_worker import document_metadata, document_metadata_repository

    url, _, _ = _seed(publication_database)
    with monkeypatch.context() as legacy:
        legacy.setattr(document_metadata, "REVISION", "document-metadata-v9")
        legacy.setattr(document_metadata_repository, "REVISION", "document-metadata-v9")
        assert DocumentMetadataRunner(url).run_once("synthetic-history-worker")
        assert DocumentMetadataProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        old_proposal = connection.execute(
            "SELECT to_jsonb(p) AS value FROM document_metadata_proposals p "
            "WHERE revision='document-metadata-v9'"
        ).fetchone()
        old_publication = connection.execute(
            "SELECT to_jsonb(p) AS value FROM document_metadata_publications p "
            "WHERE validator_revision='document-metadata-api-v9'"
        ).fetchone()
        old_component = connection.execute(
            "SELECT to_jsonb(c) AS value FROM insurance_document_components c"
        ).fetchone()
    assert DocumentMetadataRunner(url).run_once("synthetic-history-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert not DocumentMetadataRunner(url).run_once("synthetic-history-worker")
    assert DocumentMetadataProjector(url).project_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_metadata_proposals p "
                "WHERE revision='document-metadata-v9'"
            ).fetchone()
            == old_proposal
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(p) AS value FROM document_metadata_publications p "
                "WHERE validator_revision='document-metadata-api-v9'"
            ).fetchone()
            == old_publication
        )
        assert (
            connection.execute(
                "SELECT to_jsonb(c) AS value FROM insurance_document_components c"
            ).fetchone()
            == old_component
        )
        assert connection.execute(
            "SELECT outcome FROM document_metadata_publications "
            "WHERE validator_revision='document-metadata-api-v10'"
        ).fetchone() == {"outcome": "DEFERRED"}


def test_physical_flow_schema_round_trip_without_v10_history(publication_database: Any) -> None:
    url, _ = publication_database
    down = _migrate(url, "downgrade", "0071_source_calculations")
    assert down.returncode == 0, down.stderr
    up = _migrate(url, "upgrade", "head")
    assert up.returncode == 0, up.stderr
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == {
            "version_num": SUPPORTED_SCHEMA_REVISION
        }
