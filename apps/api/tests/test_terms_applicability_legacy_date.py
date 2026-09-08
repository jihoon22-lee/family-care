"""Legacy candidate publication does not turn coverage start into the actual contract date."""

from collections.abc import Iterator
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from apps.api.tests import test_policy_candidate_integration as candidate_seed
from apps.api.tests.test_rider_clause_rules_integration import database_url as database_url

pytestmark = pytest.mark.integration


@pytest.fixture()
def legacy_date_database(database_url: str) -> Iterator[str]:
    try:
        yield database_url
    finally:
        candidate_seed._reset_database(database_url)


def test_legacy_published_coverage_start_preserves_explicit_contract_date(
    legacy_date_database: str,
) -> None:
    url = legacy_date_database
    seed = candidate_seed._seed(url)
    _, result, _ = candidate_seed._persist_worker_candidate(
        url, seed, candidate_kind="policy_contract", needs_review=False
    )
    assert result.candidates[0].status == "AI_VERIFIED"
    household_id = seed.scope_a.household_space_id
    member_id, user_id, batch_id = uuid4(), uuid4(), uuid4()
    policy_text = (
        "보험증권\n보험사: Sample Insurer\n상품명: Sample Policy\n상품코드: SAMPLE-A\n"
        "계약일: 2025-12-15\n보험시작일: 2026-01-01"
    )
    terms_text = (
        "보험약관\n보험사: Sample Insurer\n상품명: Sample Policy\n상품코드: SAMPLE-A\n"
        "약관코드: TERMS-A\n판본코드: EDITION-A\n"
        "적용시작일: 2025-01-01\n적용종료일: 2025-12-31"
    )
    sources: list[dict[str, Any]] = []
    with psycopg.connect(candidate_seed._psycopg_url(url), row_factory=dict_row) as connection:
        policy = connection.execute(
            "SELECT p.id,p.contract_date,p.coverage_start_date,f.value AS published_start "
            "FROM policy_contracts p JOIN analysis_candidate_versions candidate "
            "ON candidate.aggregate_id=p.id AND candidate.household_space_id=p.household_space_id "
            "AND candidate.candidate_kind='policy_contract' AND candidate.published_at IS NOT NULL "
            "JOIN analysis_candidate_fields f ON f.candidate_version_id=candidate.id "
            "AND f.field_id='contract_start' WHERE p.household_space_id=%s",
            (household_id,),
        ).fetchone()
        assert policy is not None
        assert policy["contract_date"] == policy["coverage_start_date"] == date(2026, 1, 1)
        assert policy["published_start"] == "2026-01-01"
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM range_enrollment_publications "
                "WHERE policy_contract_id=%s",
                (policy["id"],),
            ).fetchone()["n"]
            == 0
        )
        connection.execute(
            "INSERT INTO family_members(id,household_space_id,display_name,internal_alias) "
            "VALUES(%s,%s,'Family Member A','synthetic-legacy-date-member')",
            (member_id, household_id),
        )
        connection.execute(
            "INSERT INTO policy_parties(household_space_id,policy_contract_id,family_member_id,"
            "role,evidence_id) VALUES(%s,%s,%s,'primary_insured',%s)",
            (household_id, policy["id"], member_id, seed.policy_evidence_id),
        )
        connection.execute(
            "INSERT INTO app_users(id,household_space_id,username,display_name,password_hash) "
            "VALUES(%s,%s,'synthetic-legacy-date-admin','Admin A','$argon2id$synthetic')",
            (user_id, household_id),
        )
        connection.execute(
            "INSERT INTO document_batches(id,household_space_id,family_member_id,created_by,"
            "state,completed_at) VALUES(%s,%s,%s,%s,'succeeded',clock_timestamp())",
            (batch_id, household_id, member_id, user_id),
        )
        for evidence_id, text in (
            (seed.policy_evidence_id, policy_text),
            (seed.terms_evidence_id, terms_text),
        ):
            source = connection.execute(
                "SELECT e.document_version_id,e.extraction_id,e.content_sha256,v.document_id,"
                "d.source_key,d.document_kind FROM evidence e "
                "JOIN document_versions v ON v.id=e.document_version_id "
                "JOIN documents d ON d.id=v.document_id WHERE e.id=%s",
                (evidence_id,),
            ).fetchone()
            assert source is not None
            source["batch_item_id"], source["text"] = uuid4(), text
            connection.execute(
                "INSERT INTO document_batch_items(id,batch_id,document_id,source_id,source_key,"
                "display_label,document_kind,state,processed_document_version_id,available_at,"
                "completed_at) VALUES(%s,%s,%s,%s,%s,'Synthetic Legacy Date Source',%s,"
                "'succeeded',%s,clock_timestamp(),clock_timestamp())",
                (
                    source["batch_item_id"],
                    batch_id,
                    source["document_id"],
                    source["content_sha256"],
                    source["source_key"],
                    source["document_kind"],
                    source["document_version_id"],
                ),
            )
            sources.append(source)

    for source in sources:
        structure = build_document_structure(
            {
                "document_version_id": str(source["document_version_id"]),
                "content_sha256": source["content_sha256"],
                "pages": [
                    {
                        "page_number": 1,
                        "quality": {"classification": "TEXT_SUFFICIENT"},
                        "blocks": [
                            {"text": source["text"], "reading_order": 0, "bbox": [10, 10, 400, 180]}
                        ],
                        "tables": [],
                    }
                ],
            },
            extraction_id=source["extraction_id"],
            extraction_revision="synthetic-legacy-date-origin-v1",
        )
        DocumentStructureRepository(url).prepare(
            household_space_id=household_id,
            family_member_id=member_id,
            batch_item_id=source["batch_item_id"],
            structure=structure,
            plan=plan_structure_chunks(
                structure, max_content_chars=4096, max_context_chars=4096, max_chunks=10
            ),
        )
        assert DocumentMetadataRunner(url).run_once("synthetic-legacy-date-worker")
    assert DocumentMetadataProjector(url).project_pending() == 2
    assert ComponentTermsProjector(url).project_pending() == 1
    assert TermsApplicabilityProjector(url).refresh_pending() == 1

    with psycopg.connect(candidate_seed._psycopg_url(url), row_factory=dict_row) as connection:
        context = connection.execute(
            "SELECT policy_terms_input_context(%s,%s,%s) AS context",
            (policy["id"], member_id, household_id),
        ).fetchone()["context"]
        assert context["policy"]["contract_date"] == "2026-01-01"
        assert context["policy"]["contract_date_origin"] == "COVERAGE_START_DERIVED"
        assessment = connection.execute(
            "SELECT a.status,a.matched_by,policy_terms_edition_applies("
            "a.policy_contract_id,a.terms_edition_id,a.household_space_id) AS applies "
            "FROM current_policy_terms_applicability a WHERE a.policy_contract_id=%s",
            (policy["id"],),
        ).fetchone()
        assert assessment is not None
        assert assessment["status"] == "MATCH"
        assert assessment["matched_by"] == "PRODUCT_CODE_PRINTED_PERIOD"
        assert assessment["applies"]
