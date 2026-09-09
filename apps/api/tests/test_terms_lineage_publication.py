"""Constructor-valid native words reach durable API source readers through metadata v9."""

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
from apps.api.tests.test_terms_metadata_lineage_revision import _source
from workers.analyzer.tests.test_document_structure_repository import (
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("change", ["alternating-baselines", "short-middle-word"])
def test_native_lineage_revision_reaches_published_clause_and_semantic_sources(
    publication_database, change
):
    url, job = publication_database
    extraction = _source(change).to_dict()["source_extraction"]
    extraction["document_version_id"] = str(job.document_version_id)
    blocks = extraction["pages"][0]["blocks"]
    for block in blocks:
        block["reading_order"] += 1
        block["bbox"][1] += 60
        block["bbox"][3] += 60
    blocks.insert(
        0,
        {
            "text": "보험약관\n보험사: Sample Assurance\n상품코드: SAMPLE-LINEAGE",
            "reading_order": 0,
            "bbox": [10, 10, 400, 70],
        },
    )
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
        ).fetchone() == {"validator_revision": "document-metadata-api-v9", "outcome": "APPLIED"}
    plan = TermsSemanticRepository(url).source_plan(HouseholdScope(job.household_space_id), edition)
    article = next(region for region in plan.snapshot.layout.regions if region.kind == "article")
    assert article.complete, article.reason_codes
    body = "회사는 보험수익자에게 보험금을 지급합니다."
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
