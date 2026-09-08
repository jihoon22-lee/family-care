"""Synthetic caption refinements preserve old v3 deferred edition decisions."""

from typing import Any

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
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
from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_metadata_repository import _seed, _source
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


def test_insurer_caption_refines_v3_without_rewriting_the_deferred_edition_decision(
    publication_database: Any,
) -> None:
    text = "Sample Assurance\nSample Policy\n보험약관"
    url, job, generation = _seed(publication_database, text=text)
    source = _source(job, text=text)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
        payload = metadata_proposal(source, generation, identity)
        payload["revision"] = "document-metadata-v3"
        component = payload["components"][0]
        component["facts"] = [fact for fact in component["facts"] if fact["field"] != "insurer"]
        _legacy_identity(component, source.to_dict(), revision="document-metadata-v3")
        connection.execute(
            "INSERT INTO document_metadata_proposals("
            "generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v3','PREPARED',1,%s)",
            (generation, Jsonb(payload)),
        )
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert connection.execute("SELECT * FROM terms_editions").fetchone() is None
        previous = connection.execute("SELECT * FROM component_terms_publications").fetchone()
        original_publication = connection.execute(
            "SELECT * FROM document_metadata_publications"
        ).fetchone()
    assert previous["outcome"] == "DEFERRED"
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT * FROM component_terms_publications WHERE id=%s", (previous["id"],)
            ).fetchone()
            == previous
        )
        assert (
            connection.execute(
                "SELECT * FROM document_metadata_publications WHERE id=%s",
                (original_publication["id"],),
            ).fetchone()
            == original_publication
        )
        current = connection.execute("SELECT * FROM terms_editions").fetchone()
        assert current["insurer_display"] == "Sample Assurance"
        assert current["product_display"] == "Sample Policy"
        assert connection.execute(
            "SELECT outcome FROM document_metadata_publications "
            "WHERE validator_revision='document-metadata-api-v4'"
        ).fetchone() == {"outcome": "APPLIED"}
