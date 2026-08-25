"""Synthetic PostgreSQL proof for receipt and benefit-calculation persistence."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.calculation_schemas import (
    ReceiptLineCreateRequest,
    ReceiptLineUpdateRequest,
)
from familycare_api.decisions.calculation_service import CalculationService
from familycare_api.decisions.errors import ReceiptLineNotFound
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.router import get_calculation_service, router
from familycare_api.decisions.service import DecisionService
from familycare_api.errors import install_error_handlers
from familycare_api.policies.errors import VersionConflict
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


_RESET_TABLES = (
    "benefit_calculation_steps",
    "benefit_calculations",
    "receipt_lines",
    "rule_evaluation_evidence",
    "rule_evaluations",
    "claim_candidates",
    "decision_runs",
    "medical_events",
    "coverage_rule_evidence",
    "coverage_rule_versions",
    "coverage_rules",
    "rider_clause_link_evidence",
    "rider_clause_links",
    "clause_evidence",
    "clause_search_synonyms",
    "clauses",
    "terms_editions",
    "analysis_candidate_evidence",
    "analysis_candidate_fields",
    "analysis_candidate_versions",
    "policy_status_snapshots",
    "riders",
    "policy_parties",
    "policy_contracts",
    "evidence",
    "family_members",
    "household_spaces",
    "extraction_cells",
    "extraction_tables",
    "extraction_blocks",
    "extraction_pages",
    "extractions",
    "analysis_jobs",
    "document_versions",
    "documents",
)


@dataclass(frozen=True)
class BenefitSeed:
    scope_a: HouseholdScope
    scope_b: HouseholdScope
    member_a: UUID
    fixed_rider_id: UUID
    indemnity_rider_id: UUID
    fixed_calc_rule_id: UUID
    fixed_calc_rule_version_id: UUID
    fixed_calc_link_id: UUID
    indemnity_calc_rule_id: UUID
    indemnity_calc_rule_version_id: UUID
    indemnity_calc_link_id: UUID
    policy_id: UUID
    policy_document_version_id: UUID
    terms_document_version_id: UUID
    policy_evidence_id: UUID
    terms_evidence_id: UUID


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _reset_database(database_url: str) -> None:
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(f"TRUNCATE TABLE {', '.join(_RESET_TABLES)} RESTART IDENTITY CASCADE")


def _insert_source(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    document_id: UUID,
    document_version_id: UUID,
    extraction_id: UUID,
    source_key: str,
    document_kind: str,
    content_sha256: str,
) -> None:
    connection.execute(
        """
        INSERT INTO documents (
          id, source_key, document_kind, media_type, byte_size, page_count, status
        ) VALUES (%s, %s, %s, 'application/pdf', 512, 2, 'ready')
        """,
        (document_id, source_key, document_kind),
    )
    connection.execute(
        """
        INSERT INTO document_versions (
          id, document_id, version_number, content_sha256, byte_size, page_count
        ) VALUES (%s, %s, 1, %s, 512, 2)
        """,
        (document_version_id, document_id, content_sha256),
    )
    connection.execute(
        """
        INSERT INTO extractions (
          id, document_version_id, extractor_name, extractor_version,
          extractor_config_hash, quality_rule_version, status, succeeded_at
        ) VALUES (%s, %s, 'synthetic', '1', %s, 'quality-v1', 'succeeded', clock_timestamp())
        """,
        (extraction_id, document_version_id, ("a" if document_kind == "policy" else "b") * 64),
    )
    for page_number in (1, 2):
        connection.execute(
            """
            INSERT INTO extraction_pages (
              extraction_id, page_number, width_points, height_points,
              non_whitespace_chars, alphanumeric_ratio,
              replacement_character_ratio, maximum_repeated_character_run,
              classification
            ) VALUES (%s, %s, 612, 792, 80, 0.8, 0, 1, 'TEXT_SUFFICIENT')
            """,
            (extraction_id, page_number),
        )


def _insert_rule_graph(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    household_id: UUID,
    rider_id: UUID,
    policy_document_version_id: UUID,
    terms_document_version_id: UUID,
    policy_evidence_id: UUID,
    terms_evidence_id: UUID,
    graph_number: int,
    rule_id: UUID,
    rule_version_id: UUID,
    link_id: UUID,
    link_candidate_id: UUID,
    rule_candidate_id: UUID,
    rule_kind: str,
    input_field_paths: list[str],
    rule_document: dict[str, object],
) -> None:
    terms_edition_id = _uuid(1_000 + graph_number)
    clause_id = _uuid(2_000 + graph_number)
    connection.execute(
        """
        INSERT INTO terms_editions (
          id, household_space_id, document_version_id,
          insurer_display, insurer_key, product_display, product_key,
          applicability_start, applicability_end, content_sha256, normalization_version
        ) VALUES (
          %s, %s, %s, 'Synthetic Insurer', 'synthetic-insurer',
          'Sample Policy', 'sample-policy-%s', '2025-01-01', '2025-12-31', %s,
          'unicode-nfc-v1'
        )
        """,
        (
            terms_edition_id,
            household_id,
            terms_document_version_id,
            graph_number,
            f"{graph_number:064x}",
        ),
    )
    connection.execute(
        """
        INSERT INTO clauses (
          id, household_space_id, terms_edition_id, clause_type, label,
          normalized_title, normalized_text, physical_page_start,
          physical_page_end, normalization_version
        ) VALUES (
          %s, %s, %s, 'article', %s,
          'Synthetic benefit clause', 'Synthetic coverage condition', 1, 1,
          'unicode-nfc-v1'
        )
        """,
        (clause_id, household_id, terms_edition_id, f"Article {graph_number}"),
    )
    connection.execute(
        "INSERT INTO clause_evidence (clause_id, evidence_id) VALUES (%s, %s)",
        (clause_id, terms_evidence_id),
    )
    for candidate_id, review_item_id, candidate_kind, aggregate_id, schema_version in (
        (
            link_candidate_id,
            _uuid(3_000 + graph_number),
            "rider_clause",
            link_id,
            "rider-clause-v1",
        ),
        (
            rule_candidate_id,
            _uuid(4_000 + graph_number),
            "coverage_rule",
            rule_id,
            "coverage-rule-v1",
        ),
    ):
        connection.execute(
            """
            INSERT INTO analysis_candidate_versions (
              id, review_item_id, household_space_id, candidate_kind, aggregate_id,
              version, is_current, status, schema_version, generator_version,
              verifier_version, provider_request_id, issues, published_at
            ) VALUES (
              %s, %s, %s, %s, %s, 1, true, 'AI_VERIFIED', %s,
              'synthetic-generator-v1', 'synthetic-verifier-v1',
              'synthetic-provider-request', '[]'::jsonb, clock_timestamp()
            )
            """,
            (
                candidate_id,
                review_item_id,
                household_id,
                candidate_kind,
                aggregate_id,
                schema_version,
            ),
        )
        connection.execute(
            """
            INSERT INTO analysis_candidate_evidence (
              candidate_version_id, field_id, document_version_id,
              evidence_id, physical_page, bounded_excerpt
            ) VALUES
              (%s, 'rule_kind', %s, %s, 1, 'Synthetic bounded evidence excerpt'),
              (%s, 'fact_field', %s, %s, 1, 'Synthetic bounded evidence excerpt')
            """,
            (
                candidate_id,
                policy_document_version_id,
                policy_evidence_id,
                candidate_id,
                terms_document_version_id,
                terms_evidence_id,
            ),
        )
    connection.execute(
        """
        INSERT INTO rider_clause_links (
          id, household_space_id, rider_id, terms_edition_id, clause_id,
          candidate_version_id, review_state, applicability_reason_code
        ) VALUES (%s, %s, %s, %s, %s, %s, 'USER_CONFIRMED', 'SYNTHETIC_APPLICABLE')
        """,
        (
            link_id,
            household_id,
            rider_id,
            terms_edition_id,
            clause_id,
            link_candidate_id,
        ),
    )
    connection.execute(
        """
        INSERT INTO rider_clause_link_evidence (rider_clause_link_id, evidence_id)
        VALUES (%s, %s), (%s, %s)
        """,
        (link_id, policy_evidence_id, link_id, terms_evidence_id),
    )
    connection.execute(
        """
        INSERT INTO coverage_rules (
          id, household_space_id, rider_clause_link_id, rule_key,
          current_status, version
        ) VALUES (%s, %s, %s, %s, 'published', 1)
        """,
        (rule_id, household_id, link_id, f"synthetic-benefit-rule-{graph_number}"),
    )
    connection.execute(
        """
        INSERT INTO coverage_rule_versions (
          id, coverage_rule_id, candidate_version_id, version_number,
          schema_version, rule_kind, required, input_field_paths,
          expression_json, result_reason_code, review_state, executable,
          generator_version, verifier_version, published_at
        ) VALUES (
          %s, %s, %s, 1, 'coverage-rule-v1', %s, true,
          %s, %s, %s, 'AI_VERIFIED', true,
          'synthetic-generator-v1', 'synthetic-verifier-v1', clock_timestamp()
        )
        """,
        (
            rule_version_id,
            rule_id,
            rule_candidate_id,
            rule_kind,
            Jsonb(input_field_paths),
            Jsonb(rule_document),
            str(rule_document["result_reason_code"]),
        ),
    )
    connection.execute(
        """
        INSERT INTO coverage_rule_evidence (coverage_rule_version_id, evidence_id)
        VALUES (%s, %s), (%s, %s)
        """,
        (rule_version_id, policy_evidence_id, rule_version_id, terms_evidence_id),
    )


def _eligibility_document(policy_evidence_id: UUID, terms_evidence_id: UUID) -> dict[str, object]:
    return {
        "schema_version": "coverage-rule-v1",
        "rule_kind": "eligibility",
        "required": True,
        "input_field_paths": ["MedicalEvent.classification", "Rider.status"],
        "expression": {
            "op": "all",
            "args": [
                {
                    "op": "equals",
                    "field": "MedicalEvent.classification",
                    "value": "injury",
                },
                {"op": "equals", "field": "Rider.status", "value": "active"},
            ],
        },
        "result_reason_code": "SYNTHETIC_ELIGIBLE",
        "evidence_ids": [str(policy_evidence_id), str(terms_evidence_id)],
    }


def _fixed_calculation_document(
    policy_evidence_id: UUID,
    terms_evidence_id: UUID,
    *,
    rate: float,
) -> dict[str, object]:
    return {
        "schema_version": "coverage-rule-v1",
        "rule_kind": "fixed_amount",
        "required": True,
        "input_field_paths": ["Rider.insured_amount"],
        "calculation": {
            "op": "round",
            "args": [
                {
                    "op": "multiply",
                    "args": [{"field": "Rider.insured_amount"}, {"value": rate}],
                }
            ],
            "rounding": "half_up",
        },
        "result_reason_code": "SYNTHETIC_FIXED_CALCULATION",
        "evidence_ids": [str(policy_evidence_id), str(terms_evidence_id)],
    }


def _indemnity_calculation_document(
    policy_evidence_id: UUID,
    terms_evidence_id: UUID,
) -> dict[str, object]:
    return {
        "schema_version": "coverage-rule-v1",
        "rule_kind": "rate_amount",
        "required": True,
        "input_field_paths": ["Receipt.confirmed_amount"],
        "calculation": {
            "op": "round",
            "args": [
                {
                    "op": "min",
                    "args": [
                        {
                            "op": "multiply",
                            "args": [
                                {
                                    "op": "max",
                                    "args": [
                                        {
                                            "op": "subtract",
                                            "args": [
                                                {"field": "Receipt.confirmed_amount"},
                                                {"value": 10000},
                                            ],
                                        },
                                        {"value": 0},
                                    ],
                                },
                                {"value": 0.8},
                            ],
                        },
                        {"value": 100000},
                    ],
                }
            ],
            "rounding": "half_up",
        },
        "result_reason_code": "SYNTHETIC_INDEMNITY_CALCULATION",
        "evidence_ids": [str(policy_evidence_id), str(terms_evidence_id)],
    }


def _seed(database_url: str) -> BenefitSeed:
    scope_a_id = _uuid(101)
    scope_b_id = _uuid(102)
    member_a = _uuid(103)
    member_b = _uuid(104)
    policy_id = _uuid(110)
    fixed_rider_id = _uuid(120)
    indemnity_rider_id = _uuid(121)
    policy_document_id = _uuid(140)
    policy_document_version_id = _uuid(141)
    policy_extraction_id = _uuid(142)
    terms_document_id = _uuid(143)
    terms_document_version_id = _uuid(144)
    terms_extraction_id = _uuid(145)
    policy_evidence_id = _uuid(150)
    terms_evidence_id = _uuid(151)
    fixed_eligibility_rule_id = _uuid(160)
    fixed_eligibility_rule_version_id = _uuid(161)
    fixed_eligibility_link_id = _uuid(162)
    fixed_calc_rule_id = _uuid(163)
    fixed_calc_rule_version_id = _uuid(164)
    fixed_calc_link_id = _uuid(165)
    indemnity_eligibility_rule_id = _uuid(170)
    indemnity_eligibility_rule_version_id = _uuid(171)
    indemnity_eligibility_link_id = _uuid(172)
    indemnity_calc_rule_id = _uuid(173)
    indemnity_calc_rule_version_id = _uuid(174)
    indemnity_calc_link_id = _uuid(175)

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-benefit-household-a', 'Synthetic Household A'),
                   (%s, 'synthetic-benefit-household-b', 'Synthetic Household B')
            """,
            (scope_a_id, scope_b_id),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Family Member A', 'synthetic-member-a'),
                   (%s, %s, 'Family Member B', 'synthetic-member-b')
            """,
            (member_a, scope_a_id, member_b, scope_b_id),
        )
        _insert_source(
            connection,
            document_id=policy_document_id,
            document_version_id=policy_document_version_id,
            extraction_id=policy_extraction_id,
            source_key="synthetic/benefit-policy.pdf",
            document_kind="policy",
            content_sha256="a" * 64,
        )
        _insert_source(
            connection,
            document_id=terms_document_id,
            document_version_id=terms_document_version_id,
            extraction_id=terms_extraction_id,
            source_key="synthetic/benefit-terms.pdf",
            document_kind="terms",
            content_sha256="b" * 64,
        )
        connection.execute(
            """
            INSERT INTO evidence (
              id, household_space_id, document_version_id, extraction_id,
              content_sha256, physical_page, review_state
            ) VALUES
              (%s, %s, %s, %s, %s, 1, 'USER_CONFIRMED'),
              (%s, %s, %s, %s, %s, 1, 'USER_CONFIRMED')
            """,
            (
                policy_evidence_id,
                scope_a_id,
                policy_document_version_id,
                policy_extraction_id,
                "a" * 64,
                terms_evidence_id,
                scope_a_id,
                terms_document_version_id,
                terms_extraction_id,
                "b" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_contracts (
              id, household_space_id, source_document_version_id, source_evidence_id,
              insurer_display, insurer_key, product_display, product_key,
              contract_date, coverage_start_date, coverage_end_date, status,
              status_evidence_id
            ) VALUES (
              %s, %s, %s, %s, 'Synthetic Insurer', 'synthetic-insurer',
              'Sample Policy', 'sample-policy', '2025-01-01', '2025-01-01',
              '2025-12-31', 'active', %s
            )
            """,
            (
                policy_id,
                scope_a_id,
                policy_document_version_id,
                policy_evidence_id,
                policy_evidence_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_parties (
              id, household_space_id, policy_contract_id, family_member_id,
              role, effective_from, effective_to, evidence_id
            ) VALUES (%s, %s, %s, %s, 'primary_insured', '2025-01-01', '2025-12-31', %s)
            """,
            (_uuid(180), scope_a_id, policy_id, member_a, policy_evidence_id),
        )
        connection.execute(
            """
            INSERT INTO riders (
              id, household_space_id, policy_contract_id, source_evidence_id,
              display_name, normalized_key, benefit_type, insured_amount, currency,
              coverage_start_date, coverage_end_date, renewable, status,
              status_checked_at, status_evidence_id
            ) VALUES
              (%s, %s, %s, %s, 'Synthetic Fixed Rider', 'synthetic-fixed-rider',
               'fixed', 200000.00, 'KRW', '2025-01-01', '2025-12-31', false,
               'active', '2025-01-01T00:00:00+00:00', %s),
              (%s, %s, %s, %s, 'Synthetic Indemnity Rider', 'synthetic-indemnity-rider',
               'indemnity', NULL, NULL, '2025-01-01', '2025-12-31', false,
               'active', '2025-01-01T00:00:00+00:00', %s)
            """,
            (
                fixed_rider_id,
                scope_a_id,
                policy_id,
                policy_evidence_id,
                policy_evidence_id,
                indemnity_rider_id,
                scope_a_id,
                policy_id,
                policy_evidence_id,
                policy_evidence_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_status_snapshots (
              id, household_space_id, policy_contract_id, status,
              effective_at, evidence_id
            ) VALUES (%s, %s, %s, 'active', '2025-01-01T00:00:00+00:00', %s)
            """,
            (_uuid(190), scope_a_id, policy_id, policy_evidence_id),
        )
        for snapshot_id, rider_id in (
            (_uuid(191), fixed_rider_id),
            (_uuid(192), indemnity_rider_id),
        ):
            connection.execute(
                """
                INSERT INTO policy_status_snapshots (
                  id, household_space_id, rider_id, status,
                  effective_at, evidence_id
                ) VALUES (%s, %s, %s, 'active', '2025-01-01T00:00:00+00:00', %s)
                """,
                (snapshot_id, scope_a_id, rider_id, policy_evidence_id),
            )

        _insert_rule_graph(
            connection,
            household_id=scope_a_id,
            rider_id=fixed_rider_id,
            policy_document_version_id=policy_document_version_id,
            terms_document_version_id=terms_document_version_id,
            policy_evidence_id=policy_evidence_id,
            terms_evidence_id=terms_evidence_id,
            graph_number=1,
            rule_id=fixed_eligibility_rule_id,
            rule_version_id=fixed_eligibility_rule_version_id,
            link_id=fixed_eligibility_link_id,
            link_candidate_id=_uuid(166),
            rule_candidate_id=_uuid(167),
            rule_kind="eligibility",
            input_field_paths=["MedicalEvent.classification", "Rider.status"],
            rule_document=_eligibility_document(policy_evidence_id, terms_evidence_id),
        )
        _insert_rule_graph(
            connection,
            household_id=scope_a_id,
            rider_id=fixed_rider_id,
            policy_document_version_id=policy_document_version_id,
            terms_document_version_id=terms_document_version_id,
            policy_evidence_id=policy_evidence_id,
            terms_evidence_id=terms_evidence_id,
            graph_number=2,
            rule_id=fixed_calc_rule_id,
            rule_version_id=fixed_calc_rule_version_id,
            link_id=fixed_calc_link_id,
            link_candidate_id=_uuid(168),
            rule_candidate_id=_uuid(169),
            rule_kind="fixed_amount",
            input_field_paths=["Rider.insured_amount"],
            rule_document=_fixed_calculation_document(
                policy_evidence_id,
                terms_evidence_id,
                rate=0.5,
            ),
        )
        _insert_rule_graph(
            connection,
            household_id=scope_a_id,
            rider_id=indemnity_rider_id,
            policy_document_version_id=policy_document_version_id,
            terms_document_version_id=terms_document_version_id,
            policy_evidence_id=policy_evidence_id,
            terms_evidence_id=terms_evidence_id,
            graph_number=3,
            rule_id=indemnity_eligibility_rule_id,
            rule_version_id=indemnity_eligibility_rule_version_id,
            link_id=indemnity_eligibility_link_id,
            link_candidate_id=_uuid(176),
            rule_candidate_id=_uuid(177),
            rule_kind="eligibility",
            input_field_paths=["MedicalEvent.classification", "Rider.status"],
            rule_document=_eligibility_document(policy_evidence_id, terms_evidence_id),
        )
        _insert_rule_graph(
            connection,
            household_id=scope_a_id,
            rider_id=indemnity_rider_id,
            policy_document_version_id=policy_document_version_id,
            terms_document_version_id=terms_document_version_id,
            policy_evidence_id=policy_evidence_id,
            terms_evidence_id=terms_evidence_id,
            graph_number=4,
            rule_id=indemnity_calc_rule_id,
            rule_version_id=indemnity_calc_rule_version_id,
            link_id=indemnity_calc_link_id,
            link_candidate_id=_uuid(178),
            rule_candidate_id=_uuid(179),
            rule_kind="rate_amount",
            input_field_paths=["Receipt.confirmed_amount"],
            rule_document=_indemnity_calculation_document(
                policy_evidence_id,
                terms_evidence_id,
            ),
        )
    return BenefitSeed(
        scope_a=HouseholdScope(scope_a_id),
        scope_b=HouseholdScope(scope_b_id),
        member_a=member_a,
        fixed_rider_id=fixed_rider_id,
        indemnity_rider_id=indemnity_rider_id,
        fixed_calc_rule_id=fixed_calc_rule_id,
        fixed_calc_rule_version_id=fixed_calc_rule_version_id,
        fixed_calc_link_id=fixed_calc_link_id,
        indemnity_calc_rule_id=indemnity_calc_rule_id,
        indemnity_calc_rule_version_id=indemnity_calc_rule_version_id,
        indemnity_calc_link_id=indemnity_calc_link_id,
        policy_id=policy_id,
        policy_document_version_id=policy_document_version_id,
        terms_document_version_id=terms_document_version_id,
        policy_evidence_id=policy_evidence_id,
        terms_evidence_id=terms_evidence_id,
    )


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    _reset_database(value)
    return value


@pytest.fixture()
def seed(database_url: str) -> BenefitSeed:
    return _seed(database_url)


def _decision_service(database_url: str, scope: HouseholdScope) -> DecisionService:
    return DecisionService(scope=scope, repository=DecisionRepository(database_url))


def _calculation_service(database_url: str, scope: HouseholdScope) -> CalculationService:
    return CalculationService(scope=scope, repository=CalculationRepository(database_url))


def _create_event(database_url: str, seed: BenefitSeed) -> Any:
    return _decision_service(database_url, seed.scope_a).create_medical_event(
        family_member_id=seed.member_a,
        mode="post_treatment",
        event_date=date(2025, 6, 15),
        visit_date=date(2025, 6, 16),
        facts={"MedicalEvent.classification": "injury"},
        confirmation={"MedicalEvent.classification": "user"},
    )


def _receipt_request(
    *,
    amount: str,
    coverage_category: str = "covered",
    confirmation_level: str = "user",
    note_code: str = "USER_ENTERED",
) -> ReceiptLineCreateRequest:
    return ReceiptLineCreateRequest(
        category="outpatient",
        coverage_category=coverage_category,  # type: ignore[arg-type]
        amount=amount,
        currency="KRW",
        confirmation_level=confirmation_level,  # type: ignore[arg-type]
        note_code=note_code,
    )


def _calculation_by_kind(
    calculations: tuple[dict[str, object], ...],
) -> dict[str, dict[str, object]]:
    return {str(value["kind"]): value for value in calculations}


def _row(database_url: str, query: str, *parameters: object) -> dict[str, Any]:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        row = connection.execute(query, parameters).fetchone()
    assert row is not None
    return row


def _rows(database_url: str, query: str, *parameters: object) -> list[dict[str, Any]]:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        return list(connection.execute(query, parameters).fetchall())


def _get_client(service: CalculationService) -> TestClient:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_calculation_service] = lambda: service
    app.dependency_overrides[resolve_household_scope] = lambda: service.scope
    return TestClient(app)


def _publish_changed_fixed_rule(database_url: str, seed: BenefitSeed) -> UUID:
    new_rule_version_id = _uuid(500)
    new_candidate_id = _uuid(501)
    new_review_item_id = _uuid(502)
    document = _fixed_calculation_document(
        seed.policy_evidence_id,
        seed.terms_evidence_id,
        rate=0.25,
    )
    document["result_reason_code"] = "SYNTHETIC_FIXED_CALCULATION_V2"
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO analysis_candidate_versions (
              id, review_item_id, household_space_id, candidate_kind, aggregate_id,
              version, is_current, status, schema_version, generator_version,
              verifier_version, provider_request_id, issues, published_at
            ) VALUES (
              %s, %s, %s, 'coverage_rule', %s, 1, true, 'AI_VERIFIED',
              'coverage-rule-v1', 'synthetic-generator-v2', 'synthetic-verifier-v2',
              'synthetic-provider-request-v2', '[]'::jsonb, clock_timestamp()
            )
            """,
            (
                new_candidate_id,
                new_review_item_id,
                seed.scope_a.household_space_id,
                seed.fixed_calc_rule_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO analysis_candidate_evidence (
              candidate_version_id, field_id, document_version_id,
              evidence_id, physical_page, bounded_excerpt
            ) VALUES
              (%s, 'rule_kind', %s, %s, 1, 'Synthetic bounded evidence excerpt'),
              (%s, 'fact_field', %s, %s, 1, 'Synthetic bounded evidence excerpt')
            """,
            (
                new_candidate_id,
                seed.policy_document_version_id,
                seed.policy_evidence_id,
                new_candidate_id,
                seed.terms_document_version_id,
                seed.terms_evidence_id,
            ),
        )
        connection.execute(
            """
            UPDATE coverage_rules
            SET version = 2, updated_at = clock_timestamp()
            WHERE id = %s
            """,
            (seed.fixed_calc_rule_id,),
        )
        connection.execute(
            """
            INSERT INTO coverage_rule_versions (
              id, coverage_rule_id, candidate_version_id, version_number,
              schema_version, rule_kind, required, input_field_paths,
              expression_json, result_reason_code, review_state, executable,
              generator_version, verifier_version, published_at
            ) VALUES (
              %s, %s, %s, 2, 'coverage-rule-v1', 'fixed_amount', true,
              %s, %s, 'SYNTHETIC_FIXED_CALCULATION_V2', 'AI_VERIFIED', true,
              'synthetic-generator-v2', 'synthetic-verifier-v2', clock_timestamp()
            )
            """,
            (
                new_rule_version_id,
                seed.fixed_calc_rule_id,
                new_candidate_id,
                Jsonb(["Rider.insured_amount"]),
                Jsonb(document),
            ),
        )
        connection.execute(
            """
            INSERT INTO coverage_rule_evidence (coverage_rule_version_id, evidence_id)
            VALUES (%s, %s), (%s, %s)
            """,
            (
                new_rule_version_id,
                seed.policy_evidence_id,
                new_rule_version_id,
                seed.terms_evidence_id,
            ),
        )
    return new_rule_version_id


def _add_second_indemnity_rider(database_url: str, seed: BenefitSeed) -> UUID:
    rider_id = _uuid(600)
    eligibility_rule_id = _uuid(610)
    eligibility_version_id = _uuid(611)
    eligibility_link_id = _uuid(612)
    calc_rule_id = _uuid(613)
    calc_version_id = _uuid(614)
    calc_link_id = _uuid(615)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO riders (
              id, household_space_id, policy_contract_id, source_evidence_id,
              display_name, normalized_key, benefit_type, insured_amount, currency,
              coverage_start_date, coverage_end_date, renewable, status,
              status_checked_at, status_evidence_id
            ) VALUES (
              %s, %s, %s, %s, 'Synthetic Indemnity Rider B',
              'synthetic-indemnity-rider-b', 'indemnity', NULL, NULL,
              '2025-01-01', '2025-12-31', false, 'active',
              '2025-01-01T00:00:00+00:00', %s
            )
            """,
            (
                rider_id,
                seed.scope_a.household_space_id,
                seed.policy_id,
                seed.policy_evidence_id,
                seed.policy_evidence_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_status_snapshots (
              id, household_space_id, rider_id, status,
              effective_at, evidence_id
            ) VALUES (%s, %s, %s, 'active', '2025-01-01T00:00:00+00:00', %s)
            """,
            (
                _uuid(620),
                seed.scope_a.household_space_id,
                rider_id,
                seed.policy_evidence_id,
            ),
        )
        _insert_rule_graph(
            connection,
            household_id=seed.scope_a.household_space_id,
            rider_id=rider_id,
            policy_document_version_id=seed.policy_document_version_id,
            terms_document_version_id=seed.terms_document_version_id,
            policy_evidence_id=seed.policy_evidence_id,
            terms_evidence_id=seed.terms_evidence_id,
            graph_number=20,
            rule_id=eligibility_rule_id,
            rule_version_id=eligibility_version_id,
            link_id=eligibility_link_id,
            link_candidate_id=_uuid(621),
            rule_candidate_id=_uuid(622),
            rule_kind="eligibility",
            input_field_paths=["MedicalEvent.classification", "Rider.status"],
            rule_document=_eligibility_document(
                seed.policy_evidence_id,
                seed.terms_evidence_id,
            ),
        )
        _insert_rule_graph(
            connection,
            household_id=seed.scope_a.household_space_id,
            rider_id=rider_id,
            policy_document_version_id=seed.policy_document_version_id,
            terms_document_version_id=seed.terms_document_version_id,
            policy_evidence_id=seed.policy_evidence_id,
            terms_evidence_id=seed.terms_evidence_id,
            graph_number=21,
            rule_id=calc_rule_id,
            rule_version_id=calc_version_id,
            link_id=calc_link_id,
            link_candidate_id=_uuid(623),
            rule_candidate_id=_uuid(624),
            rule_kind="rate_amount",
            input_field_paths=["Receipt.confirmed_amount"],
            rule_document=_indemnity_calculation_document(
                seed.policy_evidence_id,
                seed.terms_evidence_id,
            ),
        )
    return rider_id


def test_receipt_crud_scope_version_and_persisted_fixed_partial_calculations(
    database_url: str,
    seed: BenefitSeed,
) -> None:
    event = _create_event(database_url, seed)
    decision_service = _decision_service(database_url, seed.scope_a)
    decision = decision_service.analyze_medical_event(event.id)
    assert {candidate.rider_id for candidate in decision.candidates} == {
        seed.fixed_rider_id,
        seed.indemnity_rider_id,
    }

    calculation_service = _calculation_service(database_url, seed.scope_a)
    covered = calculation_service.create_receipt_line(
        event.id,
        _receipt_request(amount="50000.00"),
    )
    possible = calculation_service.create_receipt_line(
        event.id,
        _receipt_request(
            amount="20000.00",
            coverage_category="possible_excluded",
            confirmation_level="ai_structured",
            note_code="REVIEW_REQUIRED",
        ),
    )
    excluded = calculation_service.create_receipt_line(
        event.id,
        _receipt_request(
            amount="5000.00",
            coverage_category="excluded",
            note_code="EXCLUDED_ITEM",
        ),
    )
    assert covered.version == possible.version == excluded.version == 1

    with pytest.raises(ReceiptLineNotFound):
        _calculation_service(database_url, seed.scope_b).update_receipt_line(
            event.id,
            covered.line_id,
            ReceiptLineUpdateRequest(expected_version=1, amount="60000.00"),
        )
    with pytest.raises(VersionConflict):
        calculation_service.update_receipt_line(
            event.id,
            possible.line_id,
            ReceiptLineUpdateRequest(expected_version=2, amount="22000.00"),
        )

    first_calculations = calculation_service.get_calculations(event.id)
    first_by_kind = _calculation_by_kind(first_calculations)
    fixed = first_by_kind["fixed"]
    indemnity = first_by_kind["indemnity"]
    assert fixed["status"] == "computed"
    assert fixed["confirmed"] == {"amount": 100000, "currency": "KRW"}
    assert indemnity["status"] == "partial"
    assert indemnity["confirmed"] == {"amount": 32000, "currency": "KRW"}
    assert indemnity["additional"] == {"amount": 20000, "currency": "KRW"}
    assert indemnity["excluded"] == {"amount": 5000, "currency": "KRW"}
    assert indemnity["hold_reason_codes"] == ("ADDITIONAL_RECEIPT_REVIEW_REQUIRED",)
    assert [step["operation"] for step in indemnity["steps"]] == [
        "subtract",
        "max",
        "multiply",
        "min",
        "round",
    ]

    with _get_client(calculation_service) as client:
        response = client.get(f"/api/v1/medical-events/{event.id}/calculations")
    assert response.status_code == 200
    body = response.json()
    assert {item["kind"] for item in body["calculations"]} == {"fixed", "indemnity"}
    assert (
        next(item for item in body["calculations"] if item["kind"] == "indemnity")["status"]
        == "partial"
    )
    assert "household_space_id" not in response.text
    assert response.headers["cache-control"] == "no-store"

    old_indemnity_id = indemnity["calculation_id"]
    old_steps = _rows(
        database_url,
        """
        SELECT step_number, operation, input_amount, input_currency,
               output_amount, output_currency, rounding_rule, reason_code
        FROM benefit_calculation_steps
        WHERE benefit_calculation_id = %s
        ORDER BY step_number
        """,
        old_indemnity_id,
    )
    updated_possible = calculation_service.update_receipt_line(
        event.id,
        possible.line_id,
        ReceiptLineUpdateRequest(expected_version=1, amount="22000.00"),
    )
    assert updated_possible.version == 2
    calculation_service.delete_receipt_line(
        event.id,
        excluded.line_id,
        expected_version=excluded.version,
    )
    deleted_row = _row(
        database_url,
        "SELECT version, deleted_at FROM receipt_lines WHERE id = %s",
        excluded.line_id,
    )
    assert deleted_row["version"] == 2
    assert deleted_row["deleted_at"] is not None

    second_by_kind = _calculation_by_kind(calculation_service.get_calculations(event.id))
    assert second_by_kind["fixed"]["calculation_id"] == fixed["calculation_id"]
    assert second_by_kind["indemnity"]["calculation_id"] != old_indemnity_id
    assert second_by_kind["indemnity"]["additional"] == {"amount": 22000, "currency": "KRW"}
    assert second_by_kind["indemnity"]["excluded"] == {"amount": 0, "currency": "KRW"}
    assert (
        _rows(
            database_url,
            """
        SELECT step_number, operation, input_amount, input_currency,
               output_amount, output_currency, rounding_rule, reason_code
        FROM benefit_calculation_steps
        WHERE benefit_calculation_id = %s
        ORDER BY step_number
        """,
            old_indemnity_id,
        )
        == old_steps
    )


def test_changed_rule_recomputes_new_trace_without_mutating_old_steps(
    database_url: str,
    seed: BenefitSeed,
) -> None:
    event = _create_event(database_url, seed)
    decision_service = _decision_service(database_url, seed.scope_a)
    first_decision = decision_service.analyze_medical_event(event.id)
    calculation_service = _calculation_service(database_url, seed.scope_a)
    calculation_service.create_receipt_line(event.id, _receipt_request(amount="50000.00"))

    first = _calculation_by_kind(calculation_service.get_calculations(event.id))["fixed"]
    old_calculation_id = first["calculation_id"]
    old_steps = _rows(
        database_url,
        """
        SELECT step_number, operation, input_amount, input_currency,
               output_amount, output_currency, rounding_rule, reason_code
        FROM benefit_calculation_steps
        WHERE benefit_calculation_id = %s
        ORDER BY step_number
        """,
        old_calculation_id,
    )

    changed_rule_version_id = _publish_changed_fixed_rule(database_url, seed)
    second_decision = decision_service.analyze_medical_event(event.id)
    assert second_decision.run_id != first_decision.run_id
    second = _calculation_by_kind(calculation_service.get_calculations(event.id))["fixed"]

    assert second["calculation_id"] != old_calculation_id
    assert second["rule_version_id"] == changed_rule_version_id
    assert second["confirmed"] == {"amount": 50000, "currency": "KRW"}
    assert (
        _rows(
            database_url,
            """
        SELECT step_number, operation, input_amount, input_currency,
               output_amount, output_currency, rounding_rule, reason_code
        FROM benefit_calculation_steps
        WHERE benefit_calculation_id = %s
        ORDER BY step_number
        """,
            old_calculation_id,
        )
        == old_steps
    )


def test_multiple_indemnity_candidates_are_not_summed(
    database_url: str,
    seed: BenefitSeed,
) -> None:
    _add_second_indemnity_rider(database_url, seed)
    event = _create_event(database_url, seed)
    decision_service = _decision_service(database_url, seed.scope_a)
    decision = decision_service.analyze_medical_event(event.id)
    assert sum(candidate.rider_type == "indemnity" for candidate in decision.candidates) == 2

    calculation_service = _calculation_service(database_url, seed.scope_a)
    calculation_service.create_receipt_line(event.id, _receipt_request(amount="50000.00"))
    calculations = calculation_service.get_calculations(event.id)
    indemnities = tuple(item for item in calculations if item["kind"] == "indemnity")
    assert len(indemnities) == 2
    assert all(item["status"] == "unknown" for item in indemnities)
    assert all(item["confirmed"] is None for item in indemnities)
    assert all(
        item["hold_reason_codes"] == ("MULTIPLE_INDEMNITY_ALLOCATION_UNKNOWN",)
        for item in indemnities
    )
    assert _row(
        database_url,
        """
        SELECT count(*) AS count,
               count(*) FILTER (WHERE confirmed_amount IS NULL) AS unknown_count
        FROM benefit_calculations
        WHERE household_space_id = %s AND calculation_kind = 'indemnity'
        """,
        seed.scope_a.household_space_id,
    ) == {"count": 2, "unknown_count": 2}


def test_invalid_evidence_chain_excludes_calculation_rules(
    database_url: str,
    seed: BenefitSeed,
) -> None:
    event = _create_event(database_url, seed)
    decision_service = _decision_service(database_url, seed.scope_a)
    decision_service.analyze_medical_event(event.id)
    calculation_service = _calculation_service(database_url, seed.scope_a)
    calculation_service.create_receipt_line(event.id, _receipt_request(amount="50000.00"))

    assert _row(
        database_url,
        """
        SELECT count(*) AS count
        FROM evidence AS evidence
        JOIN document_versions AS document_version
          ON document_version.id = evidence.document_version_id
         AND document_version.content_sha256 = evidence.content_sha256
        JOIN extractions AS extraction
          ON extraction.id = evidence.extraction_id
         AND extraction.document_version_id = evidence.document_version_id
         AND extraction.status = 'succeeded'
        JOIN extraction_pages AS page
          ON page.extraction_id = extraction.id
         AND page.page_number = evidence.physical_page
        WHERE evidence.id IN (%s, %s)
          AND evidence.review_state = 'USER_CONFIRMED'
        """,
        seed.policy_evidence_id,
        seed.terms_evidence_id,
    ) == {"count": 2}

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE evidence SET review_state = 'NEEDS_REVIEW' WHERE id = %s",
            (seed.terms_evidence_id,),
        )

    assert calculation_service.get_calculations(event.id) == ()
