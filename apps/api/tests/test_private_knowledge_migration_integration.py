"""PostgreSQL enforcement proof for the private knowledge catalog migration."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001801")
USER_ID = UUID("00000000-0000-4000-8000-000000001802")
RUN_A_ID = UUID("00000000-0000-4000-8000-000000001803")
RUN_B_ID = UUID("00000000-0000-4000-8000-000000001804")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000001805")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000001806")
SECTION_ID = UUID("00000000-0000-4000-8000-000000001807")


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
          manifest_counts_json, reconciliation_counts_json,
          baseline_digest_sha256, report_digest_sha256,
          applied_by, applied_at
        ) VALUES (
          %s, %s, 'private-analysis-package.sol-v2', %s, %s,
          'synthetic-importer-v1', 'DIRECT_REVIEW', 'APPLIED', %s,
          '{}'::jsonb, '{}'::jsonb, %s, %s, %s, clock_timestamp()
        )
        """,
        (
            run_id,
            HOUSEHOLD_ID,
            digest_character * 64,
            "f" * 64,
            current,
            "b" * 64,
            "c" * 64,
            USER_ID,
        ),
    )


def test_postgresql_enforces_current_run_lineage_and_non_executable_facts() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert "test" in str(database_name[0]).lower()
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
              id, import_run_id, source_subject_key, family_alias,
              family_alias_digest_sha256, binding_decision, binding_conflict,
              binding_reason_code, source_record_json, source_record_digest_sha256
            ) VALUES (
              %s, %s, 'synthetic-subject-001', 'Family Member A', %s,
              'UNKNOWN', false, 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (SUBJECT_ID, RUN_A_ID, "1" * 64, "2" * 64),
        )

        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            connection.execute(
                """
                    INSERT INTO private_knowledge_contracts (
                      import_run_id, subject_id, source_contract_key,
                      insurer_display, product_display, certificate_decision,
                      current_status, operational_binding_decision,
                      operational_binding_reason_code,
                      source_record_json, source_record_digest_sha256
                    ) VALUES (
                      %s, %s, 'synthetic-policy-cross-run',
                      'Sample Insurer', 'Sample Policy', 'MATCH', 'unknown',
                      'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
                    )
                    """,
                (RUN_B_ID, SUBJECT_ID, "3" * 64),
            )

        connection.execute(
            """
            INSERT INTO private_knowledge_contracts (
              id, import_run_id, subject_id, source_contract_key,
              insurer_display, product_display, certificate_decision,
              current_status, operational_binding_decision,
              operational_binding_reason_code,
              source_record_json, source_record_digest_sha256
            ) VALUES (
              %s, %s, %s, 'synthetic-policy-001',
              'Sample Insurer', 'Sample Policy', 'MATCH', 'unknown',
              'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
            )
            """,
            (CONTRACT_ID, RUN_A_ID, SUBJECT_ID, "4" * 64),
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

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                    INSERT INTO private_knowledge_facts (
                      import_run_id, terms_section_id, source_fact_key,
                      fact_type, statement, review_state, executable,
                      source_record_json, source_record_digest_sha256
                    ) VALUES (
                      %s, %s, 'synthetic-fact-001', 'PAYMENT_TRIGGER',
                      'Synthetic payment condition.', 'DIRECT_REVIEWED', true,
                      '{}'::jsonb, %s
                    )
                    """,
                (RUN_A_ID, SECTION_ID, "7" * 64),
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                    INSERT INTO private_knowledge_document_bindings (
                      import_run_id, source_alias, source_alias_digest_sha256,
                      binding_decision, binding_conflict, binding_reason_code,
                      content_digest_decision, page_count_decision,
                      document_kind_decision,
                      source_record_json, source_record_digest_sha256
                    ) VALUES (
                      %s, 'Synthetic Source', %s, 'MATCH', false, 'EXACT_MATCH',
                      'MATCH', 'MATCH', 'MATCH', '{}'::jsonb, %s
                    )
                    """,
                (RUN_A_ID, "8" * 64, "9" * 64),
            )

        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM private_knowledge_import_runs),
              (SELECT count(*) FROM private_knowledge_subjects),
              (SELECT count(*) FROM private_knowledge_contracts),
              (SELECT count(*) FROM private_knowledge_terms_sections),
              (SELECT count(*) FROM private_knowledge_facts),
              (SELECT count(*) FROM private_knowledge_document_bindings)
            """
        ).fetchone()
        assert counts == (2, 1, 1, 1, 0, 0)
