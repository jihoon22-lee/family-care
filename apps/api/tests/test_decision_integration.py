"""Synthetic PostgreSQL proof for the end-to-end coverage decision boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


_RESET_TABLES = (
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
class DecisionSeed:
    scope_a: HouseholdScope
    scope_b: HouseholdScope
    member_a: UUID
    other_member_a: UUID
    member_b: UUID
    policy_id: UUID
    other_policy_id: UUID
    good_rider_id: UUID
    bad_rider_id: UUID
    uninsured_rider_id: UUID
    good_rule_id: UUID
    good_rule_version_id: UUID
    bad_rule_id: UUID
    bad_rule_version_id: UUID
    policy_evidence_id: UUID
    good_terms_evidence_id: UUID
    bad_terms_evidence_id: UUID


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
        (extraction_id, document_version_id, ("c" if document_kind == "policy" else "d") * 64),
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


def _insert_evidence(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    evidence_id: UUID,
    household_id: UUID,
    document_version_id: UUID,
    extraction_id: UUID,
    content_sha256: str,
    page: int,
) -> None:
    connection.execute(
        """
        INSERT INTO evidence (
          id, household_space_id, document_version_id, extraction_id,
          content_sha256, physical_page, review_state
        ) VALUES (%s, %s, %s, %s, %s, %s, 'USER_CONFIRMED')
        """,
        (
            evidence_id,
            household_id,
            document_version_id,
            extraction_id,
            content_sha256,
            page,
        ),
    )


def _insert_candidate(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    candidate_id: UUID,
    review_item_id: UUID,
    household_id: UUID,
    candidate_kind: str,
    aggregate_id: UUID,
    schema_version: str,
    field_id: str,
    evidence: tuple[tuple[UUID, UUID, int], ...],
) -> None:
    connection.execute(
        """
        INSERT INTO analysis_candidate_versions (
          id, review_item_id, household_space_id, candidate_kind, aggregate_id,
          version, is_current, status, schema_version, generator_version,
          verifier_version, provider_request_id, issues
        ) VALUES (
          %s, %s, %s, %s, %s, 1, true, 'AI_VERIFIED', %s,
          'synthetic-generator-v1', 'synthetic-verifier-v1',
          'synthetic-provider-request', '[]'::jsonb
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
    for document_version_id, evidence_id, physical_page in evidence:
        connection.execute(
            """
            INSERT INTO analysis_candidate_evidence (
              candidate_version_id, field_id, document_version_id, evidence_id,
              physical_page, bounded_excerpt
            ) VALUES (%s, %s, %s, %s, %s, 'Synthetic bounded evidence excerpt')
            """,
            (
                candidate_id,
                field_id,
                document_version_id,
                evidence_id,
                physical_page,
            ),
        )


def _insert_rule_graph(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    household_id: UUID,
    policy_document_version_id: UUID,
    rider_id: UUID,
    terms_document_version_id: UUID,
    terms_evidence_id: UUID,
    policy_evidence_id: UUID,
    suffix: str,
    rule_id: UUID,
    rule_version_id: UUID,
    link_id: UUID,
    link_candidate_id: UUID,
    rule_candidate_id: UUID,
    valid_expression: bool,
) -> None:
    terms_edition_id = _uuid(300 if suffix == "good" else 301)
    clause_id = _uuid(310 if suffix == "good" else 311)
    connection.execute(
        """
        INSERT INTO terms_editions (
          id, household_space_id, document_version_id,
          insurer_display, insurer_key, product_display, product_key,
          applicability_start, applicability_end, content_sha256, normalization_version
        ) VALUES (
          %s, %s, %s, 'Synthetic Insurer', 'synthetic-insurer',
          'Sample Policy', 'sample-policy', '2025-01-01', '2025-12-31', %s,
          'unicode-nfc-v1'
        )
        """,
        (
            terms_edition_id,
            household_id,
            terms_document_version_id,
            ("b" if suffix == "good" else "c") * 64,
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
          'Synthetic coverage clause', 'Synthetic coverage condition', 1, 1,
          'unicode-nfc-v1'
        )
        """,
        (clause_id, household_id, terms_edition_id, f"Article {suffix}"),
    )
    connection.execute(
        """
        INSERT INTO clause_evidence (clause_id, evidence_id)
        VALUES (%s, %s)
        """,
        (clause_id, terms_evidence_id),
    )
    connection.execute(
        """
        INSERT INTO analysis_candidate_versions (
          id, review_item_id, household_space_id, candidate_kind, aggregate_id,
          version, is_current, status, schema_version, generator_version,
          verifier_version, provider_request_id, issues
        ) VALUES (
          %s, %s, %s, 'rider_clause', %s, 1, true, 'AI_VERIFIED',
          'rider-clause-v1', 'synthetic-generator-v1', 'synthetic-verifier-v1',
          'synthetic-provider-request', '[]'::jsonb
        )
        """,
        (link_candidate_id, _uuid(320 if suffix == "good" else 321), household_id, link_id),
    )
    _insert_candidate(
        connection,
        candidate_id=rule_candidate_id,
        review_item_id=_uuid(330 if suffix == "good" else 331),
        household_id=household_id,
        candidate_kind="coverage_rule",
        aggregate_id=rule_id,
        schema_version="coverage-rule-v1",
        field_id="rule_kind",
        evidence=(
            (policy_document_version_id, policy_evidence_id, 1),
            (terms_document_version_id, terms_evidence_id, 1),
        ),
    )
    connection.execute(
        """
        INSERT INTO analysis_candidate_evidence (
          candidate_version_id, field_id, document_version_id, evidence_id,
          physical_page, bounded_excerpt
        ) VALUES (%s, 'rider_id', %s, %s, 1, 'Synthetic bounded evidence excerpt')
        """,
        (link_candidate_id, terms_document_version_id, terms_evidence_id),
    )
    connection.execute(
        """
        INSERT INTO rider_clause_links (
          id, household_space_id, rider_id, terms_edition_id, clause_id,
          candidate_version_id, review_state, applicability_reason_code
        ) VALUES (
          %s, %s, %s, %s, %s, %s, 'USER_CONFIRMED', 'SYNTHETIC_APPLICABLE'
        )
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
        (rule_id, household_id, link_id, f"synthetic-{suffix}-rule"),
    )
    expression = (
        {
            "op": "all",
            "args": [
                {
                    "op": "equals",
                    "field": "MedicalEvent.classification",
                    "value": "injury",
                },
                {"op": "equals", "field": "Rider.status", "value": "active"},
            ],
        }
        if valid_expression
        else {
            "op": "unsupported_synthetic_operator",
            "field": "MedicalEvent.classification",
        }
    )
    input_field_paths = (
        ["MedicalEvent.classification", "Rider.status"]
        if valid_expression
        else ["MedicalEvent.classification"]
    )
    rule_document = {
        "schema_version": "coverage-rule-v1",
        "rule_kind": "eligibility",
        "required": True,
        "input_field_paths": input_field_paths,
        "expression": expression,
        "result_reason_code": f"SYNTHETIC_{suffix.upper()}_RULE",
        "evidence_ids": [str(policy_evidence_id), str(terms_evidence_id)],
    }
    connection.execute(
        """
        INSERT INTO coverage_rule_versions (
          id, coverage_rule_id, candidate_version_id, version_number,
          schema_version, rule_kind, required, input_field_paths,
          expression_json, result_reason_code, review_state, executable,
          generator_version, verifier_version, published_at
        ) VALUES (
          %s, %s, %s, 1, 'coverage-rule-v1', 'eligibility', true,
          %s, %s, %s, 'AI_VERIFIED', true,
          'synthetic-generator-v1', 'synthetic-verifier-v1', clock_timestamp()
        )
        """,
        (
            rule_version_id,
            rule_id,
            rule_candidate_id,
            Jsonb(input_field_paths),
            Jsonb(rule_document),
            f"SYNTHETIC_{suffix.upper()}_RULE",
        ),
    )
    connection.execute(
        """
        INSERT INTO coverage_rule_evidence (coverage_rule_version_id, evidence_id)
        VALUES (%s, %s), (%s, %s)
        """,
        (rule_version_id, policy_evidence_id, rule_version_id, terms_evidence_id),
    )


def _seed(database_url: str) -> DecisionSeed:
    scope_a_id = _uuid(101)
    scope_b_id = _uuid(102)
    member_a = _uuid(103)
    other_member_a = _uuid(104)
    member_b = _uuid(105)
    policy_id = _uuid(110)
    other_policy_id = _uuid(111)
    good_rider_id = _uuid(120)
    bad_rider_id = _uuid(121)
    uninsured_rider_id = _uuid(122)
    good_rule_id = _uuid(130)
    good_rule_version_id = _uuid(131)
    bad_rule_id = _uuid(132)
    bad_rule_version_id = _uuid(133)
    policy_document_id = _uuid(140)
    policy_document_version_id = _uuid(141)
    policy_extraction_id = _uuid(142)
    terms_good_document_id = _uuid(143)
    terms_good_document_version_id = _uuid(144)
    terms_good_extraction_id = _uuid(145)
    terms_bad_document_id = _uuid(146)
    terms_bad_document_version_id = _uuid(147)
    terms_bad_extraction_id = _uuid(148)
    policy_evidence_id = _uuid(150)
    good_terms_evidence_id = _uuid(151)
    bad_terms_evidence_id = _uuid(152)
    good_link_id = _uuid(160)
    bad_link_id = _uuid(161)
    good_link_candidate_id = _uuid(170)
    bad_link_candidate_id = _uuid(171)
    good_rule_candidate_id = _uuid(172)
    bad_rule_candidate_id = _uuid(173)

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-decision-household-a', 'Synthetic Household A'),
                   (%s, 'synthetic-decision-household-b', 'Synthetic Household B')
            """,
            (scope_a_id, scope_b_id),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Family Member A', 'member-a'),
                   (%s, %s, 'Other Family Member A', 'other-member-a'),
                   (%s, %s, 'Family Member B', 'member-b')
            """,
            (member_a, scope_a_id, other_member_a, scope_a_id, member_b, scope_b_id),
        )
        _insert_source(
            connection,
            document_id=policy_document_id,
            document_version_id=policy_document_version_id,
            extraction_id=policy_extraction_id,
            source_key="synthetic/decision-policy.pdf",
            document_kind="policy",
            content_sha256="a" * 64,
        )
        _insert_source(
            connection,
            document_id=terms_good_document_id,
            document_version_id=terms_good_document_version_id,
            extraction_id=terms_good_extraction_id,
            source_key="synthetic/decision-terms-good.pdf",
            document_kind="terms",
            content_sha256="b" * 64,
        )
        _insert_source(
            connection,
            document_id=terms_bad_document_id,
            document_version_id=terms_bad_document_version_id,
            extraction_id=terms_bad_extraction_id,
            source_key="synthetic/decision-terms-bad.pdf",
            document_kind="terms",
            content_sha256="c" * 64,
        )
        _insert_evidence(
            connection,
            evidence_id=policy_evidence_id,
            household_id=scope_a_id,
            document_version_id=policy_document_version_id,
            extraction_id=policy_extraction_id,
            content_sha256="a" * 64,
            page=1,
        )
        _insert_evidence(
            connection,
            evidence_id=good_terms_evidence_id,
            household_id=scope_a_id,
            document_version_id=terms_good_document_version_id,
            extraction_id=terms_good_extraction_id,
            content_sha256="b" * 64,
            page=1,
        )
        _insert_evidence(
            connection,
            evidence_id=bad_terms_evidence_id,
            household_id=scope_a_id,
            document_version_id=terms_bad_document_version_id,
            extraction_id=terms_bad_extraction_id,
            content_sha256="c" * 64,
            page=1,
        )
        connection.execute(
            """
            INSERT INTO policy_contracts (
              id, household_space_id, source_document_version_id, source_evidence_id,
              insurer_display, insurer_key, product_display, product_key,
              contract_date, coverage_start_date, coverage_end_date, status,
              status_evidence_id
            ) VALUES
              (%s, %s, %s, %s, 'Synthetic Insurer', 'synthetic-insurer',
               'Sample Policy', 'sample-policy', '2025-01-01', '2025-01-01',
               '2025-12-31', 'active', %s),
              (%s, %s, %s, %s, 'Synthetic Insurer', 'synthetic-insurer-other',
               'Other Sample Policy', 'other-sample-policy', '2025-01-01',
               '2025-01-01', '2025-12-31', 'active', %s)
            """,
            (
                policy_id,
                scope_a_id,
                policy_document_version_id,
                policy_evidence_id,
                policy_evidence_id,
                other_policy_id,
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
            ) VALUES
              (%s, %s, %s, %s, 'primary_insured', '2025-01-01', '2025-12-31', %s),
              (%s, %s, %s, %s, 'primary_insured', '2025-01-01', '2025-12-31', %s)
            """,
            (
                _uuid(180),
                scope_a_id,
                policy_id,
                member_a,
                policy_evidence_id,
                _uuid(181),
                scope_a_id,
                other_policy_id,
                other_member_a,
                policy_evidence_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO riders (
              id, household_space_id, policy_contract_id, source_evidence_id,
              display_name, normalized_key, benefit_type, insured_amount, currency,
              coverage_start_date, coverage_end_date, renewable, status,
              status_checked_at, status_evidence_id
            ) VALUES
              (%s, %s, %s, %s, 'Synthetic Good Rider', 'synthetic-good-rider',
               'fixed', 100000.00, 'KRW', '2025-01-01', '2025-12-31', false,
               'active', '2025-01-01T00:00:00+00:00', %s),
              (%s, %s, %s, %s, 'Synthetic Broken Rider', 'synthetic-broken-rider',
               'fixed', 200000.00, 'KRW', '2025-01-01', '2025-12-31', false,
               'active', '2025-01-01T00:00:00+00:00', %s),
              (%s, %s, %s, %s, 'Synthetic Uninsured Rider', 'synthetic-uninsured-rider',
               'indemnity', NULL, NULL, '2025-01-01', '2025-12-31', NULL,
               'active', '2025-01-01T00:00:00+00:00', %s)
            """,
            (
                good_rider_id,
                scope_a_id,
                policy_id,
                policy_evidence_id,
                policy_evidence_id,
                bad_rider_id,
                scope_a_id,
                policy_id,
                policy_evidence_id,
                policy_evidence_id,
                uninsured_rider_id,
                scope_a_id,
                other_policy_id,
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
        for snapshot_id, rider_id, evidence_id in (
            (_uuid(191), good_rider_id, policy_evidence_id),
            (_uuid(192), bad_rider_id, policy_evidence_id),
            (_uuid(193), uninsured_rider_id, policy_evidence_id),
        ):
            connection.execute(
                """
                INSERT INTO policy_status_snapshots (
                  id, household_space_id, rider_id, status,
                  effective_at, evidence_id
                ) VALUES (%s, %s, %s, 'active', '2025-01-01T00:00:00+00:00', %s)
                """,
                (snapshot_id, scope_a_id, rider_id, evidence_id),
            )
        _insert_rule_graph(
            connection,
            household_id=scope_a_id,
            policy_document_version_id=policy_document_version_id,
            rider_id=good_rider_id,
            terms_document_version_id=terms_good_document_version_id,
            terms_evidence_id=good_terms_evidence_id,
            policy_evidence_id=policy_evidence_id,
            suffix="good",
            rule_id=good_rule_id,
            rule_version_id=good_rule_version_id,
            link_id=good_link_id,
            link_candidate_id=good_link_candidate_id,
            rule_candidate_id=good_rule_candidate_id,
            valid_expression=True,
        )
        _insert_rule_graph(
            connection,
            household_id=scope_a_id,
            policy_document_version_id=policy_document_version_id,
            rider_id=bad_rider_id,
            terms_document_version_id=terms_bad_document_version_id,
            terms_evidence_id=bad_terms_evidence_id,
            policy_evidence_id=policy_evidence_id,
            suffix="bad",
            rule_id=bad_rule_id,
            rule_version_id=bad_rule_version_id,
            link_id=bad_link_id,
            link_candidate_id=bad_link_candidate_id,
            rule_candidate_id=bad_rule_candidate_id,
            valid_expression=False,
        )
    return DecisionSeed(
        scope_a=HouseholdScope(scope_a_id),
        scope_b=HouseholdScope(scope_b_id),
        member_a=member_a,
        other_member_a=other_member_a,
        member_b=member_b,
        policy_id=policy_id,
        other_policy_id=other_policy_id,
        good_rider_id=good_rider_id,
        bad_rider_id=bad_rider_id,
        uninsured_rider_id=uninsured_rider_id,
        good_rule_id=good_rule_id,
        good_rule_version_id=good_rule_version_id,
        bad_rule_id=bad_rule_id,
        bad_rule_version_id=bad_rule_version_id,
        policy_evidence_id=policy_evidence_id,
        good_terms_evidence_id=good_terms_evidence_id,
        bad_terms_evidence_id=bad_terms_evidence_id,
    )


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    _reset_database(value)
    return value


@pytest.fixture()
def seed(database_url: str) -> DecisionSeed:
    return _seed(database_url)


def _row(database_url: str, query: str, *parameters: object) -> dict[str, Any]:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        row = connection.execute(query, parameters).fetchone()
    assert row is not None
    return row


def _service(database_url: str, scope: HouseholdScope) -> Any:
    try:
        from familycare_api.decisions.repository import DecisionRepository
        from familycare_api.decisions.service import DecisionService
    except (ImportError, ModuleNotFoundError) as error:
        pytest.fail(
            "RED: Task 4 production interface is missing; expected "
            "DecisionRepository and DecisionService: "
            f"{type(error).__name__}"
        )
    repository = DecisionRepository(database_url)
    return DecisionService(scope=scope, repository=repository)


def _create_event(service: Any, member_id: UUID) -> Any:
    return service.create_medical_event(
        family_member_id=member_id,
        mode="post_treatment",
        situation="Synthetic Member received outpatient treatment.",
        event_date=date(2025, 6, 15),
        visit_date=date(2025, 6, 16),
        facts={"MedicalEvent.classification": "injury"},
        confirmation={"MedicalEvent.classification": "user"},
    )


def test_decision_analysis_selects_insured_riders_and_persists_immutable_runs(
    database_url: str,
    seed: DecisionSeed,
) -> None:
    service = _service(database_url, seed.scope_a)
    event = _create_event(service, seed.member_a)
    assert event.version == 1

    updated = service.update_medical_event(
        event.id,
        expected_version=event.version,
        facts={
            "MedicalEvent.classification": "injury",
            "MedicalEvent.admission_days": 2,
        },
        confirmation={
            "MedicalEvent.classification": "user",
            "MedicalEvent.admission_days": "user",
        },
    )
    assert updated.version == 2
    assert service.get_medical_event(event.id).version == 2
    with pytest.raises(RuntimeError):
        service.update_medical_event(
            event.id,
            expected_version=event.version,
            facts={"MedicalEvent.classification": "injury"},
            confirmation={"MedicalEvent.classification": "user"},
        )

    pointer_rows = _row(
        database_url,
        """
        SELECT
          (SELECT current_status FROM coverage_rules WHERE id = %s) AS good_status,
          (SELECT version FROM coverage_rules WHERE id = %s) AS good_pointer,
          (SELECT version_number FROM coverage_rule_versions WHERE id = %s)
            AS good_version_number,
          (SELECT executable FROM coverage_rule_versions WHERE id = %s) AS good_executable,
          (SELECT review_state FROM rider_clause_links WHERE id = %s) AS good_link_state
        """,
        seed.good_rule_id,
        seed.good_rule_id,
        seed.good_rule_version_id,
        seed.good_rule_version_id,
        _uuid(160),
    )
    assert pointer_rows == {
        "good_status": "published",
        "good_pointer": 1,
        "good_version_number": 1,
        "good_executable": True,
        "good_link_state": "USER_CONFIRMED",
    }

    first = service.analyze_medical_event(event.id)
    assert first.event_version == 2
    assert {candidate.rider_id for candidate in first.candidates} == {
        seed.good_rider_id,
        seed.bad_rider_id,
    }
    candidates = {candidate.rider_id: candidate for candidate in first.candidates}
    assert candidates[seed.good_rider_id].aggregate_result == "MATCH"
    assert candidates[seed.good_rider_id].rider_label == "Synthetic Good Rider"
    assert candidates[seed.bad_rider_id].aggregate_result == "UNKNOWN"
    assert {evaluation.rider_id for evaluation in first.evaluations} == {
        seed.good_rider_id,
        seed.bad_rider_id,
    }
    assert all(evaluation.evidence_ids for evaluation in first.evaluations)

    fetched = service.get_decision_result(event.id, first.event_version)
    assert fetched.event_version == first.event_version
    assert fetched.run_id == first.run_id
    fetched_candidates = {candidate.rider_id: candidate for candidate in fetched.candidates}
    assert fetched_candidates[seed.good_rider_id].rider_label == "Synthetic Good Rider"
    fetched_good = next(
        evaluation
        for evaluation in fetched.evaluations
        if evaluation.rider_id == seed.good_rider_id
    )
    assert fetched_good.facts["Rider.status"].evidence_ids == (seed.policy_evidence_id,)

    first_rows = _row(
        database_url,
        """
        SELECT
          (SELECT count(*) FROM decision_runs WHERE id = %s) AS runs,
          (SELECT count(*) FROM rule_evaluations WHERE decision_run_id = %s) AS evaluations,
          (SELECT count(*) FROM claim_candidates WHERE decision_run_id = %s) AS candidates,
          (SELECT count(*) FROM rule_evaluation_evidence re
             JOIN rule_evaluations e ON e.id = re.rule_evaluation_id
             WHERE e.decision_run_id = %s) AS evidence_links
        """,
        first.run_id,
        first.run_id,
        first.run_id,
        first.run_id,
    )
    assert first_rows == {"runs": 1, "evaluations": 2, "candidates": 2, "evidence_links": 4}
    first_evidence_snapshot = _row(
        database_url,
        """
        SELECT evidence_snapshot_json
        FROM rule_evaluations
        WHERE decision_run_id = %s AND rider_id = %s
        """,
        first.run_id,
        seed.bad_rider_id,
    )["evidence_snapshot_json"]
    assert {item["content_sha256"] for item in first_evidence_snapshot} == {"a" * 64, "c" * 64}

    second = service.analyze_medical_event(event.id)
    assert second.run_id != first.run_id
    assert second.event_version == first.event_version
    assert _row(
        database_url,
        "SELECT event_version, status FROM decision_runs WHERE id = %s",
        first.run_id,
    ) == {"event_version": 2, "status": "succeeded"}
    assert _row(
        database_url,
        "SELECT count(*) AS count FROM decision_runs WHERE medical_event_id = %s",
        event.id,
    ) == {"count": 2}

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE evidence SET content_sha256 = %s WHERE id = %s",
            ("e" * 64, seed.bad_terms_evidence_id),
        )
    third = service.analyze_medical_event(event.id)
    third_candidates = {candidate.rider_id: candidate for candidate in third.candidates}
    assert third.stale is True
    assert third_candidates[seed.good_rider_id].aggregate_result == "MATCH"
    assert third_candidates[seed.bad_rider_id].aggregate_result == "UNKNOWN"
    assert (
        _row(
            database_url,
            """
        SELECT evidence_snapshot_json
        FROM rule_evaluations
        WHERE decision_run_id = %s AND rider_id = %s
        """,
            first.run_id,
            seed.bad_rider_id,
        )["evidence_snapshot_json"]
        == first_evidence_snapshot
    )


def test_decision_service_denies_cross_scope_reads_and_preserves_soft_deleted_event(
    database_url: str,
    seed: DecisionSeed,
) -> None:
    service_a = _service(database_url, seed.scope_a)
    event = _create_event(service_a, seed.member_a)
    service_b = _service(database_url, seed.scope_b)

    with pytest.raises(RuntimeError):
        service_b.get_medical_event(event.id)
    with pytest.raises(RuntimeError):
        service_b.analyze_medical_event(event.id)

    trashed = service_a.delete_medical_event(event.id, expected_version=event.version)
    assert trashed.deleted_at is not None
    with pytest.raises(RuntimeError):
        service_a.get_medical_event(event.id)
    deleted = service_a.get_medical_event(event.id, deleted_only=True)
    assert deleted.deleted_at is not None
    restored = service_a.restore_medical_event(event.id, expected_version=deleted.version)
    assert restored.deleted_at is None
    assert service_a.get_medical_event(event.id).version == restored.version
