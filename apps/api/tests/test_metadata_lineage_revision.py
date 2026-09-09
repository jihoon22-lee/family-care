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


def test_v9_appends_to_same_generation_and_preserves_v8_history(publication_database: Any) -> None:
    url, job, generation = _seed(publication_database)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s",
            (generation,),
        ).fetchone()["identity_sha256"]
        previous = metadata_proposal(_source(job), generation, identity)
        previous.update(revision="document-metadata-v8", components=[], unresolved_pages=[1])
        old = connection.execute(
            "INSERT INTO document_metadata_proposals "
            "(generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v8','PREPARED',1,%s) "
            "RETURNING id,to_jsonb(document_metadata_proposals)::text AS snapshot",
            (generation, Jsonb(previous)),
        ).fetchone()
    assert DocumentMetadataRunner(url).run_once("synthetic-lineage-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert not DocumentMetadataRunner(url).run_once("synthetic-lineage-worker")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute(
            "SELECT revision FROM document_metadata_proposals ORDER BY revision"
        ).fetchall() == [
            {"revision": "document-metadata-v8"},
            {"revision": "document-metadata-v9"},
        ]
        assert connection.execute(
            "SELECT validator_revision,outcome FROM document_metadata_publications"
        ).fetchall() == [{"validator_revision": "document-metadata-api-v9", "outcome": "APPLIED"}]
        snapshots = connection.execute(
            "SELECT id,to_jsonb(p)::text AS snapshot FROM document_metadata_proposals p ORDER BY id"
        ).fetchall()
        assert old in snapshots
        assert connection.execute(
            "SELECT id,identity_sha256 FROM document_structure_generations"
        ).fetchall() == [{"id": generation, "identity_sha256": identity}]
    refused = _migrate(url, "downgrade", "0066_provider_privacy_revision")
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
