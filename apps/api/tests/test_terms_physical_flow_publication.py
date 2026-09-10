"""Constructor-valid native words reach durable API source readers through metadata v10."""

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.terms_knowledge.repository import TermsSemanticRepository
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from apps.api.tests.test_clause_source_publication import _clause
from apps.api.tests.test_document_metadata_publication import (
    publication_database as publication_database,
)
from apps.api.tests.test_metadata_physical_prefix import BODY, _source
from workers.analyzer.tests.test_document_structure_repository import (
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _historical_v10_producer(monkeypatch):
    """Keep the v10 source-flow acceptance independent of later producers."""
    from familycare_worker import document_metadata, document_metadata_repository

    monkeypatch.setattr(document_metadata, "REVISION", "document-metadata-v10")
    monkeypatch.setattr(document_metadata_repository, "REVISION", "document-metadata-v10")


@pytest.mark.parametrize("labelled_insurer", [False, True])
def test_v10_physical_prefix_reaches_published_clause_and_semantic_sources(
    publication_database, labelled_insurer
):
    url, job = publication_database
    extraction = _source(labelled_insurer=labelled_insurer).to_dict()["source_extraction"]
    extraction["document_version_id"] = str(job.document_version_id)
    source = build_document_structure(
        extraction,
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-lineage-v1",
    )
    _prepare(
        DocumentStructureRepository(url),
        job,
        source,
        plan_structure_chunks(
            source, max_content_chars=4096, max_context_chars=4096, max_chunks=100
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-lineage-worker")
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        proposal = connection.execute(
            "SELECT state,proposal_json,error_code FROM document_metadata_proposals"
        ).fetchone()
    assert proposal["state"] == "PREPARED", proposal
    assert proposal["proposal_json"]["components"], proposal
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        edition = connection.execute("SELECT id FROM terms_editions").fetchone()["id"]
        assert connection.execute(
            "SELECT validator_revision,outcome FROM document_metadata_publications"
        ).fetchone() == {"validator_revision": "document-metadata-api-v10", "outcome": "APPLIED"}
    plan = TermsSemanticRepository(url).source_plan(HouseholdScope(job.household_space_id), edition)
    article = next(region for region in plan.snapshot.layout.regions if region.kind == "article")
    assert article.complete, article.reason_codes
    body = BODY
    assert article.body_text == body
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE extractions SET status='succeeded',succeeded_at=clock_timestamp() WHERE id=%s",
            (job.extraction_id,),
        )
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "VALUES(%s,1,600,800,100,0.8,0,1,'TEXT_SUFFICIENT') "
            "ON CONFLICT(extraction_id,page_number) DO UPDATE "
            "SET width_points=600,height_points=800",
            (job.extraction_id,),
        )
    clause = _clause(url, job, edition, body=body)
    assert ClauseSourceProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assessment = connection.execute(
            "SELECT status,source_region FROM current_clause_source_assessments WHERE clause_id=%s",
            (clause,),
        ).fetchone()
    assert assessment["status"] == "MATCH"
    assert assessment["source_region"]["body_text"] == body
