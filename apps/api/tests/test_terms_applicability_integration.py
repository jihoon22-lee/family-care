"""Synthetic source-backed terms applicability without fabricated user review."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from apps.api.tests import test_insurance_document_inventory_integration as inventory_seed
from apps.api.tests.test_rider_clause_rules_integration import database_url as database_url

pytestmark = pytest.mark.integration


def _sources(
    url: str,
    *,
    references: bool = False,
    generic: bool = False,
    separate_terms_pages: bool = False,
    printed_period: bool = False,
) -> dict[str, Any]:
    inventory_seed._seed(url)
    terms_extraction = uuid4()
    with psycopg.connect(inventory_seed._psycopg_url(url)) as connection:
        connection.execute(
            "INSERT INTO extractions(id,document_version_id,extractor_name,extractor_version,"
            "extractor_config_hash,quality_rule_version,status,succeeded_at) "
            "VALUES(%s,%s,'synthetic','v1',%s,'quality-v1','succeeded',clock_timestamp())",
            (terms_extraction, inventory_seed.TERMS_VERSION_ID, "f" * 64),
        )
        for extraction in (inventory_seed.POLICY_EXTRACTION_ID, terms_extraction):
            connection.execute(
                "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
                "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
                "maximum_repeated_character_run,classification) "
                "VALUES(%s,1,612,792,80,0.8,0,1,'TEXT_SUFFICIENT')",
                (extraction,),
            )
        if separate_terms_pages:
            connection.execute(
                "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
                "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
                "maximum_repeated_character_run,classification) "
                "SELECT %s,number,612,792,80,0.8,0,1,'TEXT_SUFFICIENT' "
                "FROM generate_series(2,3) number",
                (terms_extraction,),
            )
    policy_text = "보험증권\n보험사: Sample Insurer\n상품명: Sample Policy\n상품코드: SAMPLE-A"
    terms_text = (
        "보험약관\n보험사: Sample Insurer\n상품명: Sample Alternate Display\n"
        "상품코드: SAMPLE-A\n약관코드: TERMS-A\n판본코드: EDITION-A"
    )
    if references:
        policy_text += "\n참조약관코드: TERMS-A" if generic else "\n적용약관코드: TERMS-A"
        policy_text += "\n적용판본코드: EDITION-A"
    else:
        policy_text += "\n계약일: 2025-02-01"
    if not references or printed_period:
        terms_text += "\n적용시작일: 2025-01-01\n적용종료일: 2025-12-31"
    for item, version, extraction, digest, text in (
        (
            inventory_seed.POLICY_BATCH_ITEM_ID,
            inventory_seed.POLICY_VERSION_ID,
            inventory_seed.POLICY_EXTRACTION_ID,
            "a" * 64,
            policy_text,
        ),
        (
            inventory_seed.TERMS_BATCH_ITEM_ID,
            inventory_seed.TERMS_VERSION_ID,
            terms_extraction,
            "b" * 64,
            terms_text,
        ),
    ):
        structure = build_document_structure(
            {
                "document_version_id": str(version),
                "content_sha256": digest,
                "pages": [
                    {
                        "page_number": number,
                        "quality": {"classification": "TEXT_SUFFICIENT"},
                        "blocks": [
                            {
                                "text": text if number != 2 else "Synthetic section separator",
                                "reading_order": 0,
                                "bbox": [10, 10, 400, 180],
                            }
                        ],
                        "tables": [],
                    }
                    for number in (
                        (1, 2, 3)
                        if separate_terms_pages and extraction == terms_extraction
                        else (1,)
                    )
                ],
            },
            extraction_id=extraction,
            extraction_revision="synthetic-applicability-v1",
        )
        DocumentStructureRepository(url).prepare(
            household_space_id=inventory_seed.HOUSEHOLD_ID,
            family_member_id=inventory_seed.MEMBER_A_ID,
            batch_item_id=item,
            structure=structure,
            plan=plan_structure_chunks(
                structure, max_content_chars=4096, max_context_chars=4096, max_chunks=10
            ),
        )
        assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 2
    assert ComponentTermsProjector(url).project_pending() == (2 if separate_terms_pages else 1)
    with psycopg.connect(inventory_seed._psycopg_url(url), row_factory=dict_row) as connection:
        return connection.execute(
            "SELECT id AS edition_id,source_component_id AS terms_component_id FROM terms_editions"
        ).fetchone()


@pytest.mark.parametrize("references", [False, True])
def test_verified_sources_automatically_establish_applicability(
    database_url: str, references: bool
) -> None:
    from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector

    sources = _sources(database_url, references=references)
    projector = TermsApplicabilityProjector(database_url)
    assert projector.refresh_pending() == 1
    assert projector.refresh_pending() == 0
    from familycare_api.insurance_documents.repository import InsuranceDocumentRepository
    from familycare_api.insurance_documents.schemas import MemberInsuranceDocumentInventoryResponse

    inventory = InsuranceDocumentRepository(database_url).get_inventory(
        HouseholdScope(inventory_seed.HOUSEHOLD_ID), inventory_seed.MEMBER_A_ID
    )
    assert inventory is not None
    response = MemberInsuranceDocumentInventoryResponse.from_domain(inventory)
    assert response.registered_policies[0].completeness == "CERTIFICATE_AND_TERMS"
    assert response.registered_policies[0].terms_applicability[0].status == "MATCH"
    with psycopg.connect(
        inventory_seed._psycopg_url(database_url), row_factory=dict_row
    ) as connection:
        assessment = connection.execute("SELECT * FROM policy_terms_applicability").fetchone()
        assert assessment is not None and assessment["status"] == "MATCH"
        assert assessment["terms_edition_id"] == sources["edition_id"]
        assert assessment["matched_by"] == (
            "EXPLICIT_EDITION_REFERENCE" if references else "PRODUCT_CODE_PRINTED_PERIOD"
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM insurance_document_set_items").fetchone()[
                "n"
            ]
            == 0
        )
        assert (
            connection.execute(
                "SELECT policy_terms_edition_applies(%s,%s,%s) AS applies",
                (inventory_seed.POLICY_ID, sources["edition_id"], inventory_seed.HOUSEHOLD_ID),
            ).fetchone()["applies"]
            is True
        )
        assert (
            connection.execute(
                "SELECT policy_terms_edition_applies(%s,%s,%s) AS applies",
                (inventory_seed.POLICY_ID, sources["edition_id"], uuid4()),
            ).fetchone()["applies"]
            is False
        )


def test_general_citation_is_not_contract_applicability(database_url: str) -> None:
    from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector

    sources = _sources(database_url, references=True, generic=True)
    assert TermsApplicabilityProjector(database_url).refresh_pending() == 1
    with psycopg.connect(
        inventory_seed._psycopg_url(database_url), row_factory=dict_row
    ) as connection:
        assert (
            connection.execute("SELECT status FROM policy_terms_applicability").fetchone()["status"]
            == "UNKNOWN"
        )
        assert not connection.execute(
            "SELECT policy_terms_edition_applies(%s,%s,%s) AS applies",
            (inventory_seed.POLICY_ID, sources["edition_id"], inventory_seed.HOUSEHOLD_ID),
        ).fetchone()["applies"]


def test_equal_metadata_in_distinct_page_ranges_does_not_choose_two_editions(
    database_url: str,
) -> None:
    from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector

    _sources(database_url, separate_terms_pages=True)
    assert TermsApplicabilityProjector(database_url).refresh_pending() == 1
    with psycopg.connect(
        inventory_seed._psycopg_url(database_url), row_factory=dict_row
    ) as connection:
        rows = connection.execute(
            "SELECT status,reason_codes FROM current_policy_terms_applicability"
        ).fetchall()
    assert len(rows) == 2
    assert all(
        row["status"] == "UNKNOWN" and "AMBIGUOUS_MATCHING_EDITIONS" in row["reason_codes"]
        for row in rows
    )


def test_integrated_catalog_consumes_the_same_program_terms_relationship(database_url: str) -> None:
    from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
    from familycare_api.insurance_reconciliation.repository import InsuranceReconciliationRepository

    from apps.api.tests.test_insurance_reconciliation_migration_integration import _seed_knowledge

    _sources(database_url)
    with psycopg.connect(inventory_seed._psycopg_url(database_url)) as connection:
        _seed_knowledge(connection)
    assert TermsApplicabilityProjector(database_url).refresh_pending() == 1
    inventory = InsuranceReconciliationRepository(database_url).get_member(
        HouseholdScope(inventory_seed.HOUSEHOLD_ID), inventory_seed.MEMBER_A_ID
    )
    assert inventory is not None
    assert inventory.orphan_operational_contracts[0].completeness == "CERTIFICATE_AND_TERMS"
