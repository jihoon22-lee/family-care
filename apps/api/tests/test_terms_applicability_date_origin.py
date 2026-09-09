"""A real enrollment publication preserves the distinction between two source dates."""

from collections.abc import Iterator
from datetime import date
from typing import Any

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker.ai.schemas import CandidateField
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from apps.api.tests.test_range_enrollment_integration import (
    WORKER,
    PolicyRangeRepository,
    _one_contract,
    _psycopg_url,
    enrollment_database,  # noqa: F401 -- shared synthetic fixture
    ranges_database,  # noqa: F401 -- shared synthetic fixture
    seeded_policy_database,  # noqa: F401 -- shared synthetic fixture
    structure_database,  # noqa: F401 -- shared synthetic fixture
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def date_origin_database(request: pytest.FixtureRequest) -> Iterator[Any]:
    url, job = request.getfixturevalue("enrollment_database")
    # The parent owns source cleanup. Truncating generations here also deletes
    # its already-claimed job through the retained-source foreign key.
    try:
        yield url, job
    finally:
        # The parent fixture removes policies before their retained source generations.
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE policy_terms_applicability,policy_terms_refresh_checks")


@pytest.mark.parametrize("explicit_contract_date", [True, False])
def test_range_derived_coverage_start_never_substitutes_for_actual_contract_date(
    date_origin_database: Any,
    explicit_contract_date: bool,
) -> None:
    url, job = date_origin_database
    policy_text = (
        "보험증권\n보험사: Sample Insurer\n상품명: Sample Plan\n상품코드: SAMPLE-A\n"
        "증권번호: synthetic-policy-001\n"
        "피보험자: Family Member A\n보험시작일: 2025-02-01\n가입금액"
    )
    if explicit_contract_date:
        policy_text = policy_text.replace("증권번호:", "계약일: 2024-12-15\n증권번호:")
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET display_name='Family Member A' WHERE id=%s",
            (job.family_member_id,),
        )
        connection.execute(
            "UPDATE extraction_blocks SET text=%s WHERE reading_order=0 "
            "AND page_id IN (SELECT id FROM extraction_pages WHERE extraction_id=%s)",
            (policy_text, job.extraction_id),
        )

    ranges = PolicyRangeRepository(url)
    work = ranges.next(job, WORKER, sensitive_terms=("Family Member A",))
    assert work is not None
    batch, result = _one_contract(work)
    fields = (
        *batch.candidates[0].fields,
        CandidateField(
            field_id="contract_start",
            value="2025-02-01",
            evidence_ids=(work.envelope.primary_evidence_ids[0],),
        ),
    )
    batch = batch.model_copy(
        update={"candidates": (batch.candidates[0].model_copy(update={"fields": fields}),)}
    )
    result = result.model_copy(
        update={"candidates": (result.candidates[0].model_copy(update={"fields": fields}),)}
    )
    ranges.save(job, WORKER, work, batch, result)
    assert RangeEnrollmentProjector(url).project_pending() == 1

    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        policy = connection.execute(
            "SELECT p.id,p.contract_date,p.coverage_start_date,publication.field_values "
            "FROM policy_contracts p JOIN range_enrollment_publications publication "
            "ON publication.policy_contract_id=p.id AND publication.rider_id IS NULL "
            "WHERE p.household_space_id=%s",
            (job.household_space_id,),
        ).fetchone()
        assert policy is not None
        assert policy["contract_date"] == policy["coverage_start_date"] == date(2025, 2, 1)
        assert policy["field_values"]["contract_start"] == "2025-02-01"
        version = connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s RETURNING content_sha256",
            (job.document_version_id,),
        ).fetchone()
        assert version is not None
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "VALUES(%s,2,600,800,200,1,0,1,'TEXT_SUFFICIENT')",
            (job.extraction_id,),
        )

    terms_text = (
        "보험약관\n보험사: Sample Insurer\n상품명: Sample Plan\n상품코드: SAMPLE-A\n"
        "약관코드: TERMS-A\n판본코드: EDITION-A\n"
        "적용시작일: 2024-01-01\n적용종료일: 2024-12-31"
    )
    if not explicit_contract_date:
        terms_text = terms_text.replace("2024-", "2025-")
    structure = build_document_structure(
        {
            "document_version_id": str(job.document_version_id),
            "content_sha256": version["content_sha256"],
            "page_count": 2,
            "pages": [
                {
                    "page_number": number,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [{"text": text, "reading_order": 0, "bbox": [10, 10, 400, 180]}],
                    "tables": [],
                }
                for number, text in enumerate((policy_text, terms_text), start=1)
            ],
        },
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-contract-date-origin-v1",
    )
    DocumentStructureRepository(url).prepare(
        household_space_id=job.household_space_id,
        family_member_id=job.family_member_id,
        batch_item_id=job.batch_item_id,
        structure=structure,
        plan=plan_structure_chunks(
            structure, max_content_chars=4096, max_context_chars=4096, max_chunks=10
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-date-origin-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    assert TermsApplicabilityProjector(url).refresh_pending() == 1

    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        context = connection.execute(
            "SELECT policy_terms_input_context(%s,%s,%s) AS context",
            (policy["id"], job.family_member_id, job.household_space_id),
        ).fetchone()["context"]
        assert context["policy"]["contract_date"] == "2025-02-01"
        assert context["policy"]["contract_date_origin"] == "COVERAGE_START_DERIVED"
        assessment = connection.execute(
            "SELECT a.status,a.matched_by,policy_terms_edition_applies("
            "a.policy_contract_id,a.terms_edition_id,a.household_space_id) AS applies,"
            "policy_terms_link_applicability(a.policy_contract_id,a.terms_edition_id,"
            "a.household_space_id) AS link_applies "
            "FROM current_policy_terms_applicability a WHERE a.policy_contract_id=%s",
            (policy["id"],),
        ).fetchone()
        assert assessment is not None
        assert assessment["status"] == ("MATCH" if explicit_contract_date else "UNKNOWN")
        assert assessment["matched_by"] == (
            "PRODUCT_CODE_PRINTED_PERIOD" if explicit_contract_date else None
        )
        assert assessment["applies"] is explicit_contract_date
        assert assessment["link_applies"] is explicit_contract_date
