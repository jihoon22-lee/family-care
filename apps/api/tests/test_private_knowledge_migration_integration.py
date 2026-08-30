"""PostgreSQL enforcement proof for the private knowledge catalog migration."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest

from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001801")
USER_ID = UUID("00000000-0000-4000-8000-000000001802")
RUN_A_ID = UUID("00000000-0000-4000-8000-000000001803")
RUN_B_ID = UUID("00000000-0000-4000-8000-000000001804")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000001805")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000001806")
SECTION_ID = UUID("00000000-0000-4000-8000-000000001807")
SEMANTIC_REVIEW_ID = UUID("00000000-0000-4000-8000-000000001808")
COVERAGE_ID = UUID("00000000-0000-4000-8000-000000001809")
MAPPING_ID = UUID("00000000-0000-4000-8000-000000001810")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _insert_run(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    run_id: UUID,
    digest_character: str,
    current: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO private_knowledge_import_runs (
          id, household_space_id, package_schema_version,
          package_digest_sha256, manifest_digest_sha256,
          importer_version, analysis_authority, state, is_current,
          manifest_counts_json, manifest_json, reconciliation_counts_json,
          projection_digest_sha256,
          baseline_digest_sha256, report_digest_sha256,
          applied_by, applied_at
        ) VALUES (
          %s, %s, 'private-analysis-package.sol-v2', %s, %s,
          'synthetic-importer-v1', 'DIRECT_REVIEW', 'APPLIED', %s,
          '{}'::jsonb, '{}'::jsonb, '{}'::jsonb, %s, %s, %s, %s,
          clock_timestamp()
        )
        """,
        (
            run_id,
            HOUSEHOLD_ID,
            digest_character * 64,
            "f" * 64,
            current,
            "e" * 64,
            "b" * 64,
            "c" * 64,
            USER_ID,
        ),
    )


def test_postgresql_enforces_current_run_lineage_and_non_executable_facts() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        connection.execute("TRUNCATE TABLE household_spaces RESTART IDENTITY CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-private-knowledge', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (%s, %s, 'synthetic-knowledge-admin', 'Admin A',
                      '$argon2id$synthetic')
            """,
            (USER_ID, HOUSEHOLD_ID),
        )
        _insert_run(connection, run_id=RUN_A_ID, digest_character="a", current=True)

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            _insert_run(connection, run_id=RUN_B_ID, digest_character="d", current=True)

        _insert_run(connection, run_id=RUN_B_ID, digest_character="d", current=False)
        connection.execute(
            """
            INSERT INTO private_knowledge_subjects (
              id, import_run_id, household_space_id, source_subject_key, family_alias,
              family_alias_digest_sha256, binding_decision, binding_conflict,
              binding_reason_code, source_record_json, source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, 'synthetic-subject-001', 'Family Member A', %s,
              'UNKNOWN', false, 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (SUBJECT_ID, RUN_A_ID, HOUSEHOLD_ID, "1" * 64, "2" * 64),
        )

        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            connection.execute(
                """
                    INSERT INTO private_knowledge_contracts (
                      import_run_id, household_space_id, subject_id, source_contract_key,
                      insurer_display, product_display, certificate_decision,
                      current_status, operational_binding_decision,
                      operational_binding_reason_code,
                      source_record_json, source_record_digest_sha256
                    ) VALUES (
                      %s, %s, %s, 'synthetic-policy-cross-run',
                      'Sample Insurer', 'Sample Policy', 'MATCH', 'unknown',
                      'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
                    )
                    """,
                (RUN_B_ID, HOUSEHOLD_ID, SUBJECT_ID, "3" * 64),
            )

        connection.execute(
            """
            INSERT INTO private_knowledge_contracts (
              id, import_run_id, household_space_id, subject_id, source_contract_key,
              insurer_display, product_display, certificate_decision,
              current_status, operational_binding_decision,
              operational_binding_reason_code,
              source_record_json, source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, %s, 'synthetic-policy-001',
              'Sample Insurer', 'Sample Policy', 'MATCH', 'unknown',
              'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (CONTRACT_ID, RUN_A_ID, HOUSEHOLD_ID, SUBJECT_ID, "4" * 64),
        )
        connection.execute(
            """
            INSERT INTO private_knowledge_terms_sections (
              id, import_run_id, source_section_key, terms_source_alias,
              terms_source_alias_digest_sha256, section_kind, heading,
              page_start, page_end, review_state,
              source_record_json, source_record_digest_sha256
            ) VALUES (
              %s, %s, 'synthetic-section-001', 'Sample Terms', %s,
              'BENEFIT', 'Sample Benefit Section', 1, 2, 'DIRECT_REVIEWED',
              '{}'::jsonb, %s
            )
            """,
            (SECTION_ID, RUN_A_ID, "5" * 64, "6" * 64),
        )
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
              %s, %s, %s, %s, 'synthetic-unknown-component',
              'Synthetic Unknown Component', 'RIDER', 'UNKNOWN', 'UNKNOWN',
              'UNKNOWN', 'UNKNOWN', 'unknown', 'UNKNOWN', 'NO_EXACT_BINDING',
              '{}'::jsonb, %s
            )
            """,
            (COVERAGE_ID, RUN_A_ID, HOUSEHOLD_ID, CONTRACT_ID, "b" * 64),
        )
        connection.execute(
            """
            INSERT INTO private_knowledge_coverage_terms_mappings (
              id, import_run_id, coverage_id, source_mapping_key,
              mapping_applicability, enrollment_decision,
              document_identity_decision, edition_applicability_decision,
              section_mapping_decision, overall_decision, executable,
              source_record_json, source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, 'synthetic-no-match-mapping', 'UNKNOWN', 'UNKNOWN',
              'UNKNOWN', 'UNKNOWN', 'NO_MATCH', 'UNKNOWN', false,
              '{"mapping_decision":"NO_MATCH"}'::jsonb, %s
            )
            """,
            (MAPPING_ID, RUN_A_ID, COVERAGE_ID, "c" * 64),
        )
        connection.execute(
            """
            INSERT INTO private_knowledge_semantic_reviews (
              id, import_run_id, terms_section_id, source_review_key,
              section_summary, analysis_status, confidence, review_state,
              source_clause_count, classified_clause_count,
              unclassified_clause_count, source_record_json,
              source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, 'synthetic-review-001',
              'Synthetic section summary.', 'complete', 'high', 'DIRECT_REVIEWED',
              1, 1, 0, '{}'::jsonb, %s
            )
            """,
            (SEMANTIC_REVIEW_ID, RUN_A_ID, SECTION_ID, "a" * 64),
        )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                    INSERT INTO private_knowledge_facts (
                      import_run_id, terms_section_id, semantic_review_id, source_fact_key,
                      fact_type, statement, review_state, executable,
                      source_record_json, source_record_digest_sha256
                    ) VALUES (
                      %s, %s, %s, 'synthetic-fact-001', 'PAYMENT_TRIGGER',
                      'Synthetic payment condition.', 'DIRECT_REVIEWED', true,
                      '{}'::jsonb, %s
                    )
                    """,
                (RUN_A_ID, SECTION_ID, SEMANTIC_REVIEW_ID, "7" * 64),
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                    INSERT INTO private_knowledge_document_bindings (
                      import_run_id, household_space_id,
                      source_alias, source_alias_digest_sha256,
                      binding_decision, binding_conflict, binding_reason_code,
                      content_digest_decision, page_count_decision,
                      document_kind_decision,
                      source_record_json, source_record_digest_sha256
                    ) VALUES (
                      %s, %s, 'Synthetic Source', %s, 'MATCH', false, 'EXACT_MATCH',
                      'MATCH', 'MATCH', 'MATCH', '{}'::jsonb, %s
                    )
                    """,
                (RUN_A_ID, HOUSEHOLD_ID, "8" * 64, "9" * 64),
            )

        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM private_knowledge_import_runs),
              (SELECT count(*) FROM private_knowledge_subjects),
              (SELECT count(*) FROM private_knowledge_contracts),
              (SELECT count(*) FROM private_knowledge_coverages),
              (SELECT count(*) FROM private_knowledge_terms_sections),
              (SELECT count(*) FROM private_knowledge_semantic_reviews),
              (SELECT count(*) FROM private_knowledge_facts),
              (SELECT count(*) FROM private_knowledge_coverage_terms_mappings),
              (SELECT count(*) FROM private_knowledge_document_bindings)
            """
        ).fetchone()
        assert counts == (2, 1, 1, 1, 1, 1, 0, 1, 0)
