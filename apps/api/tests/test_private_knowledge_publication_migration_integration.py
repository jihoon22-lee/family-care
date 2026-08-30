"""PostgreSQL enforcement proof for private-knowledge rule publications."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest

from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000002001")
OTHER_HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000002002")
USER_ID = UUID("00000000-0000-4000-8000-000000002003")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000002004")
RUN_ID = UUID("00000000-0000-4000-8000-000000002005")
OTHER_RUN_ID = UUID("00000000-0000-4000-8000-000000002006")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000002007")
OTHER_SUBJECT_ID = UUID("00000000-0000-4000-8000-000000002008")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000002009")
OTHER_CONTRACT_ID = UUID("00000000-0000-4000-8000-000000002010")
COVERAGE_ID = UUID("00000000-0000-4000-8000-000000002011")
OTHER_COVERAGE_ID = UUID("00000000-0000-4000-8000-000000002012")
RULE_RUN_ID = UUID("00000000-0000-4000-8000-000000002013")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _insert_foundation(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    connection.execute("TRUNCATE TABLE household_spaces RESTART IDENTITY CASCADE")
    connection.execute(
        """
        INSERT INTO household_spaces (id, space_key, display_name)
        VALUES
          (%s, 'synthetic-publication', 'Synthetic Household'),
          (%s, 'synthetic-publication-other', 'Synthetic Other Household')
        """,
        (HOUSEHOLD_ID, OTHER_HOUSEHOLD_ID),
    )
    connection.execute(
        """
        INSERT INTO app_users (
          id, household_space_id, username, display_name, password_hash
        ) VALUES
          (%s, %s, 'synthetic-publication-admin', 'Admin A', '$argon2id$synthetic'),
          (%s, %s, 'synthetic-publication-other', 'Admin B', '$argon2id$synthetic')
        """,
        (USER_ID, HOUSEHOLD_ID, OTHER_USER_ID, OTHER_HOUSEHOLD_ID),
    )
    for run_id, household_id, actor_id, digest in (
        (RUN_ID, HOUSEHOLD_ID, USER_ID, "1"),
        (OTHER_RUN_ID, OTHER_HOUSEHOLD_ID, OTHER_USER_ID, "2"),
    ):
        connection.execute(
            """
            INSERT INTO private_knowledge_import_runs (
              id, household_space_id, package_schema_version,
              package_digest_sha256, manifest_digest_sha256,
              importer_version, analysis_authority, state, is_current,
              manifest_counts_json, manifest_json, reconciliation_counts_json,
              projection_digest_sha256, baseline_digest_sha256, report_digest_sha256,
              applied_by, applied_at
            ) VALUES (
              %s, %s, 'private-analysis-package.sol-v2',
              %s, %s, 'synthetic-importer-v1', 'DIRECT_REVIEW', 'APPLIED', true,
              '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, %s, %s, %s, %s,
              clock_timestamp()
            )
            """,
            (
                run_id,
                household_id,
                digest * 64,
                "a" * 64,
                "b" * 64,
                "c" * 64,
                "d" * 64,
                actor_id,
            ),
        )
    for subject_id, run_id, household_id, subject_key, digest in (
        (SUBJECT_ID, RUN_ID, HOUSEHOLD_ID, "synthetic-subject-001", "3"),
        (
            OTHER_SUBJECT_ID,
            OTHER_RUN_ID,
            OTHER_HOUSEHOLD_ID,
            "synthetic-subject-002",
            "4",
        ),
    ):
        connection.execute(
            """
            INSERT INTO private_knowledge_subjects (
              id, import_run_id, household_space_id, source_subject_key,
              family_alias, family_alias_digest_sha256, binding_decision,
              binding_conflict, binding_reason_code, source_record_json,
              source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, %s, 'Family Member A', %s,
              'UNKNOWN', false, 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (subject_id, run_id, household_id, subject_key, digest * 64, digest * 64),
        )
    for contract_id, run_id, household_id, subject_id, key, digest in (
        (CONTRACT_ID, RUN_ID, HOUSEHOLD_ID, SUBJECT_ID, "synthetic-policy-001", "5"),
        (
            OTHER_CONTRACT_ID,
            OTHER_RUN_ID,
            OTHER_HOUSEHOLD_ID,
            OTHER_SUBJECT_ID,
            "synthetic-policy-002",
            "6",
        ),
    ):
        connection.execute(
            """
            INSERT INTO private_knowledge_contracts (
              id, import_run_id, household_space_id, subject_id, source_contract_key,
              insurer_display, product_display, certificate_decision,
              current_status, operational_binding_decision,
              operational_binding_reason_code, source_record_json,
              source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, %s, %s, 'Sample Insurer', 'Sample Policy',
              'MATCH', 'active', 'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (contract_id, run_id, household_id, subject_id, key, digest * 64),
        )
    for coverage_id, run_id, household_id, contract_id, key, digest in (
        (COVERAGE_ID, RUN_ID, HOUSEHOLD_ID, CONTRACT_ID, "synthetic-cover-001", "7"),
        (
            OTHER_COVERAGE_ID,
            OTHER_RUN_ID,
            OTHER_HOUSEHOLD_ID,
            OTHER_CONTRACT_ID,
            "synthetic-cover-002",
            "8",
        ),
    ):
        connection.execute(
            """
            INSERT INTO private_knowledge_coverages (
              id, import_run_id, household_space_id, knowledge_contract_id,
              source_coverage_key, display_name, component_role,
              component_classification, enrollment_decision, benefit_type,
              renewal_state, current_status, operational_binding_decision,
              operational_binding_reason_code, source_record_json,
              source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, %s, %s, 'Sample Coverage', 'RIDER',
              'BENEFIT_COVERAGE', 'MATCH', 'FIXED', 'UNKNOWN', 'active',
              'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (coverage_id, run_id, household_id, contract_id, key, digest * 64),
        )
    connection.execute(
        """
        INSERT INTO private_knowledge_rule_import_runs (
          id, knowledge_import_run_id, household_space_id,
          package_schema_version, package_digest_sha256, manifest_digest_sha256,
          baseline_digest_sha256, report_digest_sha256, projection_digest_sha256,
          publisher_version, state, review_state, reviewed_by, reviewed_at,
          is_current
        ) VALUES (
          %s, %s, %s, 'private-knowledge-rule-publication.sol-v1',
          %s, %s, %s, %s, %s, 'synthetic-publisher-v1', 'APPLIED',
          'USER_CONFIRMED', %s, clock_timestamp(), true
        )
        """,
        (
            RULE_RUN_ID,
            RUN_ID,
            HOUSEHOLD_ID,
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "d" * 64,
            "e" * 64,
            USER_ID,
        ),
    )


def _insert_disposition(
    connection: psycopg.Connection[tuple[object, ...]],
    disposition_id: UUID,
    *,
    coverage_id: UUID = COVERAGE_ID,
) -> None:
    connection.execute(
        """
        INSERT INTO private_knowledge_coverage_execution_dispositions (
          id, rule_import_run_id, knowledge_import_run_id,
          household_space_id, knowledge_coverage_id, disposition,
          reason_codes_json
        ) VALUES (%s, %s, %s, %s, %s, 'PUBLISHED', '[]'::jsonb)
        """,
        (
            disposition_id,
            RULE_RUN_ID,
            RUN_ID,
            HOUSEHOLD_ID,
            coverage_id,
        ),
    )


def test_postgresql_enforces_publication_scope_closure_and_review() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        _insert_foundation(connection)
        _insert_disposition(
            connection,
            UUID("00000000-0000-4000-8000-000000002014"),
        )

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            _insert_disposition(
                connection,
                UUID("00000000-0000-4000-8000-000000002015"),
            )

        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            _insert_disposition(
                connection,
                UUID("00000000-0000-4000-8000-000000002016"),
                coverage_id=OTHER_COVERAGE_ID,
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO private_knowledge_rule_publications (
                  id, rule_import_run_id, knowledge_import_run_id,
                  household_space_id, knowledge_coverage_id, rule_key,
                  rule_kind, schema_version, required, rule_json,
                  result_reason_code, review_state, reviewed_by, reviewed_at,
                  rule_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, %s, 'synthetic-rule-001', 'eligibility',
                  'coverage-rule-dsl.v1', true, '{}'::jsonb, 'SYNTHETIC_MATCH',
                  'AI_VERIFIED', %s, clock_timestamp(), %s
                )
                """,
                (
                    UUID("00000000-0000-4000-8000-000000002017"),
                    RULE_RUN_ID,
                    RUN_ID,
                    HOUSEHOLD_ID,
                    COVERAGE_ID,
                    USER_ID,
                    "f" * 64,
                ),
            )
