"""Synthetic PostgreSQL proofs for Rider-Clause confirmation and rule publication."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date
from typing import Any, cast
from uuid import UUID

import psycopg
import pytest
from familycare_api.clauses.errors import (
    ClauseVersionConflict,
    CoverageRuleInvalid,
    RiderClauseLinkInvalid,
)
from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


_RESET_TABLES = (
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
class Seed:
    scope_a: HouseholdScope
    scope_b: HouseholdScope
    policy_document_version_id: UUID
    terms_document_version_id: UUID
    policy_evidence_id: UUID
    terms_evidence_id: UUID
    terms_edition_id: UUID
    clause_id: UUID
    rider_id: UUID
    link_id: UUID
    rule_id: UUID
    rule_version_id: UUID
    rule_candidate_id: UUID

    @property
    def link_candidate_id(self) -> UUID:
        return UUID("00000000-0000-4000-8000-000000000112")

    @property
    def evidence_ids(self) -> frozenset[UUID]:
        return frozenset({self.policy_evidence_id, self.terms_evidence_id})


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _reset_database(database_url: str) -> None:
    table_list = ", ".join(_RESET_TABLES)
    with psycopg.connect(_psycopg_url(database_url), autocommit=True) as connection:
        connection.execute(f"TRUNCATE TABLE {table_list} CASCADE")


def _insert_document(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    document_id: UUID,
    document_version_id: UUID,
    extraction_id: UUID,
    source_key: str,
    document_kind: str,
    content_sha256: str,
    pages: tuple[int, ...],
) -> None:
    connection.execute(
        """
        INSERT INTO documents (id, source_key, document_kind, status)
        VALUES (%s, %s, %s, 'ready')
        """,
        (document_id, source_key, document_kind),
    )
    connection.execute(
        """
        INSERT INTO document_versions (
          id, document_id, version_number, content_sha256, byte_size, page_count
        ) VALUES (%s, %s, 1, %s, 512, 4)
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
    for page_number in pages:
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
    evidence: tuple[tuple[str, UUID, UUID, int], ...],
) -> None:
    connection.execute(
        """
        INSERT INTO analysis_candidate_versions (
          id, review_item_id, household_space_id, candidate_kind, aggregate_id,
          version, is_current, status, schema_version, generator_version,
          verifier_version, provider_request_id, issues
        ) VALUES (
          %s, %s, %s, %s, %s, 1, true, 'AI_VERIFIED',
          %s, 'synthetic-generator-v1', 'synthetic-verifier-v1',
          'synthetic-provider-request', '[]'::jsonb
        )
        """,
        (
            candidate_id,
            review_item_id,
            household_id,
            candidate_kind,
            aggregate_id,
            "rider-clause-v1" if candidate_kind == "rider_clause" else "coverage-rule-v1",
        ),
    )
    for field_id, document_version_id, evidence_id, physical_page in evidence:
        connection.execute(
            """
            INSERT INTO analysis_candidate_evidence (
              candidate_version_id, field_id, document_version_id, evidence_id,
              physical_page, bounded_excerpt
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                candidate_id,
                field_id,
                document_version_id,
                evidence_id,
                physical_page,
                "Synthetic bounded evidence excerpt",
            ),
        )


def _seed(database_url: str) -> Seed:
    household_a = _uuid(101)
    household_b = _uuid(102)
    policy_document_id = _uuid(103)
    terms_document_id = _uuid(104)
    policy_document_version_id = _uuid(105)
    terms_document_version_id = _uuid(106)
    policy_extraction_id = _uuid(107)
    terms_extraction_id = _uuid(108)
    policy_evidence_id = _uuid(109)
    terms_evidence_id = _uuid(110)
    link_candidate_id = _uuid(112)
    link_id = _uuid(113)
    terms_edition_id = _uuid(114)
    clause_id = _uuid(115)
    policy_contract_id = _uuid(116)
    rider_id = _uuid(117)
    family_member_id = _uuid(118)
    rule_id = _uuid(119)
    rule_candidate_id = _uuid(120)
    rule_version_id = _uuid(121)

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-rules-household-a', 'Synthetic Household A'),
                   (%s, 'synthetic-rules-household-b', 'Synthetic Household B')
            """,
            (household_a, household_b),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Family Member A', 'member-a')
            """,
            (family_member_id, household_a),
        )
        _insert_document(
            connection,
            document_id=policy_document_id,
            document_version_id=policy_document_version_id,
            extraction_id=policy_extraction_id,
            source_key="synthetic/rules-policy-a.pdf",
            document_kind="policy",
            content_sha256="a" * 64,
            pages=(1,),
        )
        _insert_document(
            connection,
            document_id=terms_document_id,
            document_version_id=terms_document_version_id,
            extraction_id=terms_extraction_id,
            source_key="synthetic/rules-terms-a.pdf",
            document_kind="terms",
            content_sha256="b" * 64,
            pages=(2,),
        )
        _insert_evidence(
            connection,
            evidence_id=policy_evidence_id,
            household_id=household_a,
            document_version_id=policy_document_version_id,
            extraction_id=policy_extraction_id,
            content_sha256="a" * 64,
            page=1,
        )
        _insert_evidence(
            connection,
            evidence_id=terms_evidence_id,
            household_id=household_a,
            document_version_id=terms_document_version_id,
            extraction_id=terms_extraction_id,
            content_sha256="b" * 64,
            page=2,
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
              'Sample Policy', 'sample-policy', %s, %s, %s, 'active', %s
            )
            """,
            (
                policy_contract_id,
                household_a,
                policy_document_version_id,
                policy_evidence_id,
                date(2025, 6, 1),
                date(2025, 1, 1),
                date(2025, 12, 31),
                policy_evidence_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO policy_parties (
              id, household_space_id, policy_contract_id, family_member_id,
              role, effective_from, effective_to, evidence_id
            ) VALUES (%s, %s, %s, %s, 'primary_insured', %s, %s, %s)
            """,
            (
                _uuid(122),
                household_a,
                policy_contract_id,
                family_member_id,
                date(2025, 1, 1),
                date(2025, 12, 31),
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
            ) VALUES (
              %s, %s, %s, %s, 'Sample Rider', 'sample-rider', 'fixed',
              1000, 'USD', %s, %s, true, 'active', clock_timestamp(), %s
            )
            """,
            (
                rider_id,
                household_a,
                policy_contract_id,
                policy_evidence_id,
                date(2025, 1, 1),
                date(2025, 12, 31),
                policy_evidence_id,
            ),
        )
        connection.execute(
            """
            INSERT INTO terms_editions (
              id, household_space_id, document_version_id,
              insurer_display, insurer_key, product_display, product_key,
              applicability_start, applicability_end, content_sha256,
              normalization_version
            ) VALUES (
              %s, %s, %s, 'Synthetic Insurer', 'synthetic-insurer',
              'Sample Policy', 'sample-policy', %s, %s, %s, 'unicode-nfc-v1'
            )
            """,
            (
                terms_edition_id,
                household_a,
                terms_document_version_id,
                date(2025, 1, 1),
                date(2025, 12, 31),
                "b" * 64,
            ),
        )
        connection.execute(
            """
            INSERT INTO clauses (
              id, household_space_id, terms_edition_id, clause_type, label,
              normalized_title, normalized_text, physical_page_start,
              physical_page_end, normalization_version
            ) VALUES (
              %s, %s, %s, 'article', 'Article A',
              'synthetic eligibility', 'Synthetic clause body for testing only',
              2, 2, 'unicode-nfc-v1'
            )
            """,
            (clause_id, household_a, terms_edition_id),
        )
        connection.execute(
            """
            INSERT INTO clause_evidence (clause_id, evidence_id)
            VALUES (%s, %s)
            """,
            (clause_id, terms_evidence_id),
        )
        _insert_candidate(
            connection,
            candidate_id=link_candidate_id,
            review_item_id=_uuid(123),
            household_id=household_a,
            candidate_kind="rider_clause",
            aggregate_id=link_id,
            evidence=(
                ("rider_id", policy_document_version_id, policy_evidence_id, 1),
                ("clause_id", terms_document_version_id, terms_evidence_id, 2),
            ),
        )
        connection.execute(
            """
            INSERT INTO rider_clause_links (
              id, household_space_id, rider_id, terms_edition_id, clause_id,
              candidate_version_id, review_state, applicability_reason_code
            ) VALUES (%s, %s, %s, %s, %s, %s, 'AI_VERIFIED', 'APPLICABLE')
            """,
            (
                link_id,
                household_a,
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
              id, household_space_id, rider_clause_link_id, rule_key
            ) VALUES (%s, %s, %s, 'synthetic-temporal-rule')
            """,
            (rule_id, household_a, link_id),
        )
        _insert_candidate(
            connection,
            candidate_id=rule_candidate_id,
            review_item_id=_uuid(124),
            household_id=household_a,
            candidate_kind="coverage_rule",
            aggregate_id=rule_id,
            evidence=(
                ("rule_kind", policy_document_version_id, policy_evidence_id, 1),
                ("rule_kind", terms_document_version_id, terms_evidence_id, 2),
            ),
        )
        rule_document = {
            "schema_version": "coverage-rule-v1",
            "rule_kind": "temporal",
            "required": True,
            "input_field_paths": ["MedicalEvent.event_date"],
            "expression": {
                "op": "date_between",
                "field": "MedicalEvent.event_date",
                "value": {"start": "2025-01-01", "end": "2025-12-31"},
                "unit": "date",
            },
            "result_reason_code": "SYNTHETIC_TEMPORAL_MATCH",
            "evidence_ids": [str(policy_evidence_id), str(terms_evidence_id)],
        }
        connection.execute(
            """
            INSERT INTO coverage_rule_versions (
              id, coverage_rule_id, candidate_version_id, version_number,
              schema_version, rule_kind, required, input_field_paths,
              expression_json, result_reason_code, review_state,
              generator_version, verifier_version
            ) VALUES (
              %s, %s, %s, 1, 'coverage-rule-v1', 'temporal', true,
              %s, %s, 'SYNTHETIC_TEMPORAL_MATCH', 'AI_VERIFIED',
              'synthetic-generator-v1', 'synthetic-verifier-v1'
            )
            """,
            (
                rule_version_id,
                rule_id,
                rule_candidate_id,
                Jsonb(["MedicalEvent.event_date"]),
                Jsonb(rule_document),
            ),
        )
        connection.execute(
            """
            INSERT INTO coverage_rule_evidence (coverage_rule_version_id, evidence_id)
            VALUES (%s, %s), (%s, %s)
            """,
            (rule_version_id, policy_evidence_id, rule_version_id, terms_evidence_id),
        )

    return Seed(
        scope_a=HouseholdScope(household_a),
        scope_b=HouseholdScope(household_b),
        policy_document_version_id=policy_document_version_id,
        terms_document_version_id=terms_document_version_id,
        policy_evidence_id=policy_evidence_id,
        terms_evidence_id=terms_evidence_id,
        terms_edition_id=terms_edition_id,
        clause_id=clause_id,
        rider_id=rider_id,
        link_id=link_id,
        rule_id=rule_id,
        rule_version_id=rule_version_id,
        rule_candidate_id=rule_candidate_id,
    )


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    _reset_database(value)
    return value


@pytest.fixture()
def seed(database_url: str) -> Seed:
    return _seed(database_url)


def _row(database_url: str, query: str, *parameters: object) -> dict[str, Any]:
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        result = connection.execute(query, parameters).fetchone()
    assert result is not None
    return result


def test_valid_link_confirmation_and_rule_publication_commit_exact_evidence(
    database_url: str,
    seed: Seed,
) -> None:
    rule_repository = CoverageRuleRepository(database_url)
    initial_versions = rule_repository.list_versions(seed.scope_a, seed.rule_id)
    assert initial_versions.expected_version == 1
    assert tuple(item.id for item in initial_versions.versions) == (seed.rule_version_id,)

    link = RiderClauseLinkRepository(database_url).confirm(
        seed.scope_a,
        seed.link_id,
        expected_version=1,
    )
    assert link.review_state == "USER_CONFIRMED"
    assert link.version == 2
    assert {item.evidence_id for item in link.evidence} == seed.evidence_ids

    published = rule_repository.publish(
        seed.scope_a,
        seed.rule_id,
        seed.rule_version_id,
        expected_version=1,
    )
    assert published.executable is True
    assert published.version_number == 2
    assert {item.evidence_id for item in published.evidence} == seed.evidence_ids
    current_versions = rule_repository.list_versions(seed.scope_a, seed.rule_id)
    assert current_versions.expected_version == 2
    assert tuple(item.version_number for item in current_versions.versions) == (1, 2)

    link_row = _row(
        database_url,
        """
        SELECT review_state, version
        FROM rider_clause_links
        WHERE id = %s AND household_space_id = %s
        """,
        seed.link_id,
        seed.scope_a.household_space_id,
    )
    assert link_row == {"review_state": "USER_CONFIRMED", "version": 2}
    rule_row = _row(
        database_url,
        """
        SELECT current_status, version
        FROM coverage_rules
        WHERE id = %s AND household_space_id = %s
        """,
        seed.rule_id,
        seed.scope_a.household_space_id,
    )
    assert rule_row == {"current_status": "published", "version": 2}
    counts = _row(
        database_url,
        """
        SELECT
          (SELECT count(*) FROM coverage_rule_versions WHERE coverage_rule_id = %s) AS versions,
          (SELECT count(*) FROM coverage_rule_versions
             WHERE coverage_rule_id = %s AND executable) AS executable_versions
        """,
        seed.rule_id,
        seed.rule_id,
    )
    assert counts == {"versions": 2, "executable_versions": 1}
    evidence_rows = _row(
        database_url,
        """
        SELECT array_agg(evidence_id ORDER BY evidence_id) AS evidence_ids
        FROM coverage_rule_evidence
        WHERE coverage_rule_version_id = %s
        """,
        published.id,
    )
    assert set(cast(list[UUID], evidence_rows["evidence_ids"])) == seed.evidence_ids


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            """
            UPDATE terms_editions
            SET applicability_start = '2026-01-01', applicability_end = '2026-12-31'
            WHERE id = %s
            """,
            "TERMS_EDITION_NOT_APPLICABLE",
        ),
        (
            "UPDATE terms_editions SET document_version_id = %s WHERE id = %s",
            "CLAUSE_DOCUMENT_MISMATCH",
        ),
    ],
)
def test_link_confirmation_rejects_wrong_edition_or_document(
    database_url: str,
    seed: Seed,
    mutation: str,
    reason: str,
) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        if "document_version_id" in mutation:
            connection.execute(
                mutation,
                (seed.policy_document_version_id, seed.terms_edition_id),
            )
        else:
            connection.execute(mutation, (seed.terms_edition_id,))

    with pytest.raises(RiderClauseLinkInvalid) as raised:
        RiderClauseLinkRepository(database_url).confirm(
            seed.scope_a,
            seed.link_id,
            expected_version=1,
        )
    assert raised.value.reason_code == reason
    row = _row(
        database_url,
        "SELECT review_state, version FROM rider_clause_links WHERE id = %s",
        seed.link_id,
    )
    assert row == {"review_state": "NEEDS_REVIEW", "version": 2}


def test_link_confirmation_rejects_incomplete_exact_evidence_without_confirming(
    database_url: str,
    seed: Seed,
) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            DELETE FROM rider_clause_link_evidence
            WHERE rider_clause_link_id = %s AND evidence_id = %s
            """,
            (seed.link_id, seed.terms_evidence_id),
        )

    with pytest.raises(RiderClauseLinkInvalid) as raised:
        RiderClauseLinkRepository(database_url).confirm(
            seed.scope_a,
            seed.link_id,
            expected_version=1,
        )
    assert raised.value.reason_code == "LINK_EVIDENCE_INCOMPLETE"
    row = _row(
        database_url,
        "SELECT review_state, version FROM rider_clause_links WHERE id = %s",
        seed.link_id,
    )
    assert row == {"review_state": "NEEDS_REVIEW", "version": 2}


def test_publication_rejects_unsupported_dsl_and_creates_no_executable_clone(
    database_url: str,
    seed: Seed,
) -> None:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE coverage_rule_versions
            SET expression_json = jsonb_set(expression_json, '{expression,op}', '"python"')
            WHERE id = %s
            """,
            (seed.rule_version_id,),
        )

    with pytest.raises(CoverageRuleInvalid) as raised:
        CoverageRuleRepository(database_url).publish(
            seed.scope_a,
            seed.rule_id,
            seed.rule_version_id,
            expected_version=1,
        )
    assert raised.value.reason_code == "RULE_DSL_INVALID"
    row = _row(
        database_url,
        """
        SELECT
          (SELECT count(*) FROM coverage_rule_versions WHERE coverage_rule_id = %s) AS versions,
          (SELECT count(*) FROM coverage_rule_versions
             WHERE coverage_rule_id = %s AND executable) AS executable_versions,
          (SELECT current_status FROM coverage_rules WHERE id = %s) AS current_status
        """,
        seed.rule_id,
        seed.rule_id,
        seed.rule_id,
    )
    assert row == {"versions": 1, "executable_versions": 0, "current_status": "generated"}


def test_cross_household_objects_are_not_confirmable_or_publishable(
    database_url: str,
    seed: Seed,
) -> None:
    assert (
        RiderClauseLinkRepository(database_url).list_for_rider(
            seed.scope_b,
            seed.rider_id,
        )
        == ()
    )
    with pytest.raises(ClauseVersionConflict):
        RiderClauseLinkRepository(database_url).confirm(
            seed.scope_b,
            seed.link_id,
            expected_version=1,
        )
    with pytest.raises(ClauseVersionConflict):
        CoverageRuleRepository(database_url).publish(
            seed.scope_b,
            seed.rule_id,
            seed.rule_version_id,
            expected_version=1,
        )


def test_stale_expected_versions_leave_link_and_rule_aggregates_unchanged(
    database_url: str,
    seed: Seed,
) -> None:
    with pytest.raises(ClauseVersionConflict):
        RiderClauseLinkRepository(database_url).confirm(
            seed.scope_a,
            seed.link_id,
            expected_version=2,
        )
    with pytest.raises(ClauseVersionConflict):
        CoverageRuleRepository(database_url).publish(
            seed.scope_a,
            seed.rule_id,
            seed.rule_version_id,
            expected_version=2,
        )

    link_row = _row(
        database_url,
        "SELECT review_state, version FROM rider_clause_links WHERE id = %s",
        seed.link_id,
    )
    rule_row = _row(
        database_url,
        "SELECT current_status, version FROM coverage_rules WHERE id = %s",
        seed.rule_id,
    )
    assert link_row == {"review_state": "AI_VERIFIED", "version": 1}
    assert rule_row == {"current_status": "generated", "version": 1}
