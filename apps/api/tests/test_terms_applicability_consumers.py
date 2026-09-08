"""Applicable source references reach actual Clause, rule and calculation consumers."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_component_rule_sources import _bounded_rule_source
from apps.api.tests.test_rider_clause_rules_integration import _psycopg_url
from apps.api.tests.test_rider_clause_rules_integration import database_url as database_url

pytestmark = pytest.mark.integration


def _policy_metadata(url: str, seed: Any) -> None:
    item = uuid4()
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        source = connection.execute(
            "SELECT source.extraction_id,v.document_id,party.family_member_id,b.id AS batch_id "
            "FROM evidence source JOIN document_versions v ON v.id=source.document_version_id "
            "JOIN policy_parties party ON party.policy_contract_id=("
            "SELECT policy_contract_id FROM riders WHERE id=%s) "
            "AND party.household_space_id=source.household_space_id "
            "JOIN document_batches b ON b.household_space_id=source.household_space_id "
            "AND b.family_member_id=party.family_member_id WHERE source.id=%s LIMIT 1",
            (seed.rider_id, seed.policy_evidence_id),
        ).fetchone()
        connection.execute(
            "INSERT INTO document_batch_items(id,batch_id,document_id,source_id,source_key,"
            "display_label,document_kind,state,completed_at,processed_document_version_id) "
            "VALUES(%s,%s,%s,%s,'synthetic/applicable-policy.pdf','Sample Policy','policy',"
            "'succeeded',clock_timestamp(),%s)",
            (
                item,
                source["batch_id"],
                source["document_id"],
                "f" * 64,
                seed.policy_document_version_id,
            ),
        )
    structure = build_document_structure(
        {
            "document_version_id": str(seed.policy_document_version_id),
            "content_sha256": "a" * 64,
            "pages": [
                {
                    "page_number": 1,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [
                        {
                            "text": "보험증권\n보험사: Synthetic Insurer\n상품명: Sample Policy\n"
                            "적용약관코드: TERMS-A\n적용판본코드: EDITION-A",
                            "reading_order": 0,
                            "bbox": [10, 10, 400, 160],
                        }
                    ],
                    "tables": [],
                }
            ],
        },
        extraction_id=source["extraction_id"],
        extraction_revision="synthetic-applicable-policy-v1",
    )
    DocumentStructureRepository(url).prepare(
        household_space_id=seed.scope_a.household_space_id,
        family_member_id=source["family_member_id"],
        batch_item_id=item,
        structure=structure,
        plan=plan_structure_chunks(
            structure, max_content_chars=4096, max_context_chars=4096, max_chunks=10
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1


@pytest.mark.parametrize("calculation", [False, True])
def test_explicit_applicability_reaches_rule_publication_and_execution(
    database_url: str, calculation: bool
) -> None:
    seed, _ = _bounded_rule_source(database_url, explicit_references=True)
    _policy_metadata(database_url, seed)
    assert TermsApplicabilityProjector(database_url).refresh_pending() == 1
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
    RiderClauseLinkRepository(database_url).confirm(seed.scope_a, seed.link_id, expected_version=1)
    published = CoverageRuleRepository(database_url).publish(
        seed.scope_a,
        seed.rule_id,
        seed.rule_version_id,
        expected_version=1,
    )
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        assert DecisionRepository(database_url)._rule_versions(
            connection, seed.scope_a, seed.rider_id
        )
        if calculation:
            assert CalculationRepository._calculation_rules(
                connection, seed.scope_a, seed.rider_id, datetime.now(UTC)
            )
        assert DecisionRepository(database_url)._rule_versions_by_ids(
            connection, seed.scope_a, seed.rider_id, (published.id,)
        )

        connection.execute(
            "UPDATE policy_contracts SET product_display='Sample Corrected "
            "Contract',version=version+1 "
            "WHERE id=(SELECT policy_contract_id FROM riders WHERE id=%s)",
            (seed.rider_id,),
        )
        assert not DecisionRepository(database_url)._rule_versions(
            connection, seed.scope_a, seed.rider_id
        )
        if calculation:
            assert not CalculationRepository._calculation_rules(
                connection, seed.scope_a, seed.rider_id, datetime.now(UTC)
            )
        assert DecisionRepository(database_url)._rule_versions_by_ids(
            connection, seed.scope_a, seed.rider_id, (published.id,)
        )
