"""PostgreSQL enforcement proof for private-knowledge confirmations."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest

from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001901")
OTHER_HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001902")
USER_ID = UUID("00000000-0000-4000-8000-000000001903")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000001904")
RUN_ID = UUID("00000000-0000-4000-8000-000000001905")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000001906")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000001907")
CONFIRMATION_ID = UUID("00000000-0000-4000-8000-000000001908")


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
          (%s, 'synthetic-confirmation', 'Synthetic Household'),
          (%s, 'synthetic-confirmation-other', 'Synthetic Other Household')
        """,
        (HOUSEHOLD_ID, OTHER_HOUSEHOLD_ID),
    )
    connection.execute(
        """
        INSERT INTO app_users (
          id, household_space_id, username, display_name, password_hash
        ) VALUES
          (%s, %s, 'synthetic-confirmation-admin', 'Admin A', '$argon2id$synthetic'),
          (%s, %s, 'synthetic-confirmation-other', 'Admin B', '$argon2id$synthetic')
        """,
        (USER_ID, HOUSEHOLD_ID, OTHER_USER_ID, OTHER_HOUSEHOLD_ID),
    )
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
            RUN_ID,
            HOUSEHOLD_ID,
            "1" * 64,
            "2" * 64,
            "3" * 64,
            "4" * 64,
            "5" * 64,
            USER_ID,
        ),
    )
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
        (SUBJECT_ID, RUN_ID, HOUSEHOLD_ID, "6" * 64, "7" * 64),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_contracts (
          id, import_run_id, household_space_id, subject_id, source_contract_key,
          insurer_display, product_display, certificate_decision,
          current_status, operational_binding_decision,
          operational_binding_reason_code, source_record_json,
          source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, 'synthetic-policy-001',
          'Sample Insurer', 'Sample Policy', 'MATCH', 'unknown',
          'UNKNOWN', 'NO_EXACT_BINDING', '{}'::jsonb, %s
        )
        """,
        (CONTRACT_ID, RUN_ID, HOUSEHOLD_ID, SUBJECT_ID, "8" * 64),
    )


def _insert_confirmation(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    confirmation_id: UUID,
    actor_id: UUID = USER_ID,
    decision: str = "MATCH",
    status: str = "active",
    digest: str = "9",
) -> None:
    connection.execute(
        """
        INSERT INTO private_knowledge_contract_confirmations (
          id, import_run_id, household_space_id, knowledge_contract_id,
          decision, confirmed_status, status_as_of, authority, reason_code,
          confirmed_by, confirmation_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, %s, CURRENT_DATE,
          'USER_CONFIRMED_CURRENT_ENROLLMENT', 'USER_ATTESTED_CURRENT',
          %s, %s
        )
        """,
        (
            confirmation_id,
            RUN_ID,
            HOUSEHOLD_ID,
            CONTRACT_ID,
            decision,
            status,
            actor_id,
            digest * 64,
        ),
    )


def test_postgresql_enforces_confirmation_authority_and_history() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        _insert_foundation(connection)
        _insert_confirmation(connection, confirmation_id=CONFIRMATION_ID)

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            _insert_confirmation(
                connection,
                confirmation_id=UUID("00000000-0000-4000-8000-000000001909"),
                digest="a",
            )

        connection.execute(
            """
            UPDATE private_knowledge_contract_confirmations
               SET is_current = false, superseded_at = clock_timestamp()
             WHERE id = %s
            """,
            (CONFIRMATION_ID,),
        )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            _insert_confirmation(
                connection,
                confirmation_id=UUID("00000000-0000-4000-8000-000000001910"),
                decision="UNKNOWN",
                status="active",
                digest="b",
            )

        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            _insert_confirmation(
                connection,
                confirmation_id=UUID("00000000-0000-4000-8000-000000001911"),
                actor_id=OTHER_USER_ID,
                digest="c",
            )

        replacement_id = UUID("00000000-0000-4000-8000-000000001912")
        _insert_confirmation(
            connection,
            confirmation_id=replacement_id,
            digest="d",
        )
        rows = connection.execute(
            """
            SELECT id, is_current
              FROM private_knowledge_contract_confirmations
             ORDER BY created_at, id
            """
        ).fetchall()
        assert rows == [(CONFIRMATION_ID, False), (replacement_id, True)]
