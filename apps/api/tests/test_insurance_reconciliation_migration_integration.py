"""PostgreSQL enforcement proof for insurance reconciliation histories."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

from apps.api.tests.test_insurance_document_inventory_integration import (
    HOUSEHOLD_ID,
    LOCKED_ITEM_ID,
    MEMBER_A_ID,
    POLICY_BATCH_ITEM_ID,
    POLICY_ID,
    USER_ID,
    _seed,
)
from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
RUN_ID = UUID("00000000-0000-4000-8000-000000002401")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000002402")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000002403")
LINK_ID = UUID("00000000-0000-4000-8000-000000002404")
RESOLUTION_ID = UUID("00000000-0000-4000-8000-000000002405")
OTHER_HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000002406")
OTHER_USER_ID = UUID("00000000-0000-4000-8000-000000002407")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed_knowledge(connection: psycopg.Connection[tuple[object, ...]]) -> None:
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
          %s, %s, 'private-analysis-package.sol-v2', %s, %s,
          'synthetic-importer-v1', 'DIRECT_REVIEW', 'APPLIED', true,
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
          family_alias_digest_sha256, family_member_id, binding_decision,
          binding_conflict, binding_reason_code, binding_confirmed_by,
          binding_confirmed_at, source_record_json, source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, 'synthetic-subject-001', 'Family Member A', %s, %s,
          'MATCH', false, 'USER_CONFIRMED_FAMILY_BINDING', %s,
          clock_timestamp(), '{}'::jsonb, %s
        )
        """,
        (
            SUBJECT_ID,
            RUN_ID,
            HOUSEHOLD_ID,
            "6" * 64,
            MEMBER_A_ID,
            USER_ID,
            "7" * 64,
        ),
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


def _insert_link(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    link_id: UUID,
    actor_id: UUID = USER_ID,
    decision: str = "MATCH",
    policy_id: UUID | None = POLICY_ID,
    digest: str = "9",
) -> None:
    connection.execute(
        """
        INSERT INTO private_knowledge_operational_links (
          id, import_run_id, household_space_id, family_member_id,
          knowledge_contract_id, policy_contract_id, decision, link_conflict,
          authority, reason_code, confirmed_by, link_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, false,
          'USER_CONFIRMED_OPERATIONAL_IDENTITY', 'USER_CONFIRMED_SAME_CONTRACT',
          %s, %s
        )
        """,
        (
            link_id,
            RUN_ID,
            HOUSEHOLD_ID,
            MEMBER_A_ID,
            CONTRACT_ID,
            policy_id,
            decision,
            actor_id,
            digest * 64,
        ),
    )


def _insert_resolution(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    resolution_id: UUID,
    resolution: str = "REPLACED",
    replacement_id: UUID | None = POLICY_BATCH_ITEM_ID,
    actor_id: UUID = USER_ID,
    digest: str = "a",
) -> None:
    connection.execute(
        """
        INSERT INTO document_batch_item_resolutions (
          id, household_space_id, family_member_id, failed_item_id,
          replacement_item_id, resolution, authority, reason_code,
          confirmed_by, resolution_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, %s,
          'USER_CONFIRMED_DOCUMENT_RESOLUTION', 'USER_CONFIRMED_REPLACEMENT',
          %s, %s
        )
        """,
        (
            resolution_id,
            HOUSEHOLD_ID,
            MEMBER_A_ID,
            LOCKED_ITEM_ID,
            replacement_id,
            resolution,
            actor_id,
            digest * 64,
        ),
    )


def _assert_history_mutation_rejected(sql: str, row_id: UUID) -> None:
    with (
        psycopg.connect(_database_url()) as connection,
        pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
    ):
        connection.execute(sql, (row_id,))


def test_postgresql_enforces_reconciliation_scope_shape_and_append_only_history() -> None:
    database_url = _database_url()
    _seed(database_url)
    with psycopg.connect(database_url) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        _seed_knowledge(connection)
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-reconciliation-other', 'Synthetic Other Household')
            """,
            (OTHER_HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (
              %s, %s, 'synthetic-reconciliation-other', 'Admin B',
              '$argon2id$synthetic'
            )
            """,
            (OTHER_USER_ID, OTHER_HOUSEHOLD_ID),
        )
        _insert_link(connection, link_id=LINK_ID)
        _insert_resolution(connection, resolution_id=RESOLUTION_ID)

    _assert_history_mutation_rejected(
        "UPDATE private_knowledge_operational_links "
        "SET reason_code = 'SYNTHETIC_CHANGED' WHERE id = %s",
        LINK_ID,
    )
    _assert_history_mutation_rejected(
        "DELETE FROM private_knowledge_operational_links WHERE id = %s",
        LINK_ID,
    )
    _assert_history_mutation_rejected(
        "UPDATE document_batch_item_resolutions "
        "SET reason_code = 'SYNTHETIC_CHANGED' WHERE id = %s",
        RESOLUTION_ID,
    )
    _assert_history_mutation_rejected(
        "DELETE FROM document_batch_item_resolutions WHERE id = %s",
        RESOLUTION_ID,
    )

    with psycopg.connect(database_url) as connection:
        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            _insert_link(
                connection,
                link_id=UUID("00000000-0000-4000-8000-000000002408"),
                digest="b",
            )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            _insert_link(
                connection,
                link_id=UUID("00000000-0000-4000-8000-000000002409"),
                decision="UNKNOWN",
                digest="c",
            )
        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            connection.execute(
                """
                UPDATE private_knowledge_operational_links
                SET is_current = false, superseded_at = clock_timestamp()
                WHERE id = %s
                """,
                (LINK_ID,),
            )
            _insert_link(
                connection,
                link_id=UUID("00000000-0000-4000-8000-000000002410"),
                actor_id=OTHER_USER_ID,
                digest="d",
            )

        connection.execute(
            """
            UPDATE private_knowledge_operational_links
            SET is_current = false, superseded_at = clock_timestamp()
            WHERE id = %s
            """,
            (LINK_ID,),
        )
        replacement_link_id = UUID("00000000-0000-4000-8000-000000002411")
        _insert_link(connection, link_id=replacement_link_id, digest="e")

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            _insert_resolution(
                connection,
                resolution_id=UUID("00000000-0000-4000-8000-000000002412"),
                digest="f",
            )
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            _insert_resolution(
                connection,
                resolution_id=UUID("00000000-0000-4000-8000-000000002413"),
                resolution="DISMISSED",
                digest="0",
            )
        connection.execute(
            """
            UPDATE document_batch_item_resolutions
            SET is_current = false, superseded_at = clock_timestamp()
            WHERE id = %s
            """,
            (RESOLUTION_ID,),
        )
        reopened_id = UUID("00000000-0000-4000-8000-000000002414")
        _insert_resolution(
            connection,
            resolution_id=reopened_id,
            resolution="REOPENED",
            replacement_id=None,
            digest="1",
        )

        link_rows = connection.execute(
            """
            SELECT id, is_current
            FROM private_knowledge_operational_links
            ORDER BY created_at, id
            """
        ).fetchall()
        resolution_rows = connection.execute(
            """
            SELECT id, is_current
            FROM document_batch_item_resolutions
            ORDER BY created_at, id
            """
        ).fetchall()
    assert link_rows == [(LINK_ID, False), (replacement_link_id, True)]
    assert resolution_rows == [(RESOLUTION_ID, False), (reopened_id, True)]


def test_migration_downgrade_is_clean_only_without_history() -> None:
    database_url = _database_url()
    _seed(database_url)
    with psycopg.connect(database_url) as connection:
        _seed_knowledge(connection)
        _insert_link(connection, link_id=LINK_ID)
        _insert_resolution(connection, resolution_id=RESOLUTION_ID)

    config = Config(str(ROOT / "apps/api/alembic.ini"))
    with pytest.raises(DBAPIError, match="cannot downgrade insurance reconciliation with history"):
        command.downgrade(config, "0023_advisory_disposition")

    with psycopg.connect(database_url) as connection:
        connection.execute("TRUNCATE TABLE private_knowledge_operational_links")
        connection.execute("TRUNCATE TABLE document_batch_item_resolutions")

    command.downgrade(config, "0023_advisory_disposition")
    command.upgrade(config, "0024_insurance_reconciliation")
