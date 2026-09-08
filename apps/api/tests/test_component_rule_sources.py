"""Current rules honor component state while historical version reads survive."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.domain import ClauseSearchFilters
from familycare_api.clauses.errors import RiderClauseLinkInvalid
from familycare_api.clauses.repository import (
    ClauseRepository,
    ClauseSearchRepository,
    CoverageRuleRepository,
    RiderClauseLinkRepository,
)
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_rider_clause_rules_integration import (
    _psycopg_url,
    _seed,
    database_url,  # noqa: F401
)

pytestmark = pytest.mark.integration


def _bounded_rule_source(url: str, *, explicit_references: bool = False) -> tuple[Any, Any]:
    seed = _seed(url)
    user, batch, item = uuid4(), uuid4(), uuid4()
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        source = connection.execute(
            "SELECT e.extraction_id,v.document_id,m.id AS member_id FROM evidence e "
            "JOIN document_versions v ON v.id=e.document_version_id "
            "JOIN family_members m ON m.household_space_id=e.household_space_id "
            "WHERE e.id=%s",
            (seed.terms_evidence_id,),
        ).fetchone()
        connection.execute(
            "INSERT INTO app_users(id,household_space_id,username,display_name,password_hash) "
            "VALUES(%s,%s,'synthetic-component-review','Admin A','$argon2id$synthetic')",
            (user, seed.scope_a.household_space_id),
        )
        connection.execute(
            "INSERT INTO document_batches(id,household_space_id,family_member_id,created_by,state) "
            "VALUES(%s,%s,%s,%s,'created')",
            (batch, seed.scope_a.household_space_id, source["member_id"], user),
        )
        connection.execute(
            "INSERT INTO document_batch_items(id,batch_id,document_id,source_id,source_key,"
            "display_label,document_kind,state,completed_at) "
            "VALUES(%s,%s,%s,%s,'synthetic/component-rules.pdf','Sample Terms',"
            "'terms','succeeded',clock_timestamp())",
            (item, batch, source["document_id"], "d" * 64),
        )
    structure = build_document_structure(
        {
            "document_version_id": str(seed.terms_document_version_id),
            "content_sha256": "b" * 64,
            "pages": [
                {
                    "page_number": 1,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [
                        {
                            "text": "Synthetic frontmatter",
                            "reading_order": 0,
                            "bbox": [10, 10, 400, 30],
                        }
                    ],
                    "tables": [],
                },
                {
                    "page_number": 2,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [
                        {
                            "text": (
                                "보험약관\n보험사: Synthetic Insurer\n상품명: Sample Alternate "
                                "Display\n"
                                "약관코드: TERMS-A\n판본코드: EDITION-A"
                                if explicit_references
                                else "보험약관\n보험사: Synthetic Insurer\n상품명: Sample Policy\n"
                                "적용시작일: 2025-01-01\n적용종료일: 2025-12-31"
                            ),
                            "reading_order": 0,
                            "bbox": [10, 10, 400, 80],
                        }
                    ],
                    "tables": [],
                },
            ],
        },
        extraction_id=source["extraction_id"],
        extraction_revision="synthetic-component-rule-v1",
    )
    DocumentStructureRepository(url).prepare(
        household_space_id=seed.scope_a.household_space_id,
        family_member_id=source["member_id"],
        batch_item_id=item,
        structure=structure,
        plan=plan_structure_chunks(
            structure, max_content_chars=4096, max_context_chars=4096, max_chunks=10
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        # Explicit synthetic fixture adaptation preserves the old legacy row and creates
        # a separate bounded edition before any link/rule publication takes place.
        row = connection.execute(
            "INSERT INTO terms_editions(household_space_id,document_version_id,insurer_display,"
            "insurer_key,product_display,product_key,applicability_start,applicability_end,"
            "content_sha256,normalization_version,source_component_id,source_page_start,"
            "source_page_end,source_metadata_json) "
            "SELECT e.household_space_id,e.document_version_id,e.insurer_display,e.insurer_key,"
            "e.product_display,e.product_key,e.applicability_start,e.applicability_end,"
            "e.content_sha256,e.normalization_version,c.id,c.page_start,c.page_end,p.proof_json "
            "FROM terms_editions e JOIN insurance_document_components c "
            "ON c.document_version_id=e.document_version_id "
            "JOIN document_metadata_publications p ON p.id=c.metadata_publication_id "
            "WHERE e.id=%s RETURNING id,source_component_id",
            (seed.terms_edition_id,),
        ).fetchone()
        connection.execute(
            "UPDATE clauses SET terms_edition_id=%s WHERE id=%s", (row["id"], seed.clause_id)
        )
        connection.execute(
            "UPDATE rider_clause_links SET terms_edition_id=%s WHERE id=%s",
            (row["id"], seed.link_id),
        )
        if explicit_references:
            connection.execute(
                "UPDATE terms_editions SET applicability_start=NULL,applicability_end=NULL,"
                "product_display='Sample Alternate Display',product_key='sample-alternate-display' "
                "WHERE id=%s",
                (row["id"],),
            )
            connection.execute(
                "UPDATE policy_contracts SET contract_date=NULL WHERE id=("
                "SELECT policy_contract_id FROM riders WHERE id=%s)",
                (seed.rider_id,),
            )
    return seed, row


@pytest.mark.parametrize("calculation", [False, True])
def test_source_rejection_stops_current_rules_but_keeps_historical_versions(
    database_url: str,  # noqa: F811 -- shared synthetic DB fixture
    calculation: bool,
) -> None:
    seed, edition = _bounded_rule_source(database_url)
    if calculation:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute(
                "UPDATE coverage_rule_versions SET rule_kind='rate_amount',input_field_paths=%s,"
                "expression_json=(expression_json-'expression') || %s WHERE id=%s",
                (
                    Jsonb(["Rider.insured_amount"]),
                    Jsonb(
                        {
                            "rule_kind": "rate_amount",
                            "input_field_paths": ["Rider.insured_amount"],
                            "calculation": {
                                "op": "multiply",
                                "args": [{"field": "Rider.insured_amount"}, {"value": 1}],
                            },
                        }
                    ),
                    seed.rule_version_id,
                ),
            )
    links = RiderClauseLinkRepository(database_url)
    links.confirm(seed.scope_a, seed.link_id, expected_version=1)
    published = CoverageRuleRepository(database_url).publish(
        seed.scope_a,
        seed.rule_id,
        seed.rule_version_id,
        expected_version=1,
    )
    decisions = DecisionRepository(database_url)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        assert decisions._rule_versions(connection, seed.scope_a, seed.rider_id)
        if calculation:
            assert CalculationRepository._calculation_rules(
                connection, seed.scope_a, seed.rider_id, datetime.now(UTC)
            )
        connection.execute(
            "UPDATE insurance_document_components SET review_state='REJECTED' WHERE id=%s",
            (edition["source_component_id"],),
        )
    assert not links.list_for_rider(seed.scope_a, seed.rider_id)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        assert not decisions._rule_versions(connection, seed.scope_a, seed.rider_id)
        if calculation:
            assert not CalculationRepository._calculation_rules(
                connection, seed.scope_a, seed.rider_id, datetime.now(UTC)
            )
        historical = decisions._rule_versions_by_ids(
            connection, seed.scope_a, seed.rider_id, (published.id,)
        )
        assert [version.id for version in historical] == [published.id]
    with pytest.raises(RiderClauseLinkInvalid):
        links.confirm(seed.scope_a, seed.link_id, expected_version=2)
    assert not ClauseSearchRepository(database_url).search(
        seed.scope_a, "synthetic", ClauseSearchFilters(), limit=1
    )
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        assert not ClauseRepository(database_url)._hierarchy_with_connection(
            # The public catalog reports an unavailable edition; the retained hierarchy
            # reader used by rule publication must also reject the source.
            connection,
            seed.scope_a,
            edition["id"],
        )
