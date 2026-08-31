"""Atomic PostgreSQL apply proof for exact private-knowledge confirmations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from familycare_api.private_knowledge.confirmations import load_confirmation_manifest
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.reconciliation import build_dry_run_report
from familycare_api.private_knowledge.repository import PostgresPrivateKnowledgeRepository

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)
from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000002101")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000002102")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000002103")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        connection.execute("TRUNCATE TABLE household_spaces RESTART IDENTITY CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-confirmation-apply', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (
              %s, %s, 'synthetic-confirmation-operator', 'Admin A',
              '$argon2id$synthetic'
            )
            """,
            (ACTOR_ID, HOUSEHOLD_ID),
        )
        connection.execute(
            """
            INSERT INTO family_members (
              id, household_space_id, display_name, internal_alias
            ) VALUES (%s, %s, 'Family Member A', 'synthetic-member-a')
            """,
            (MEMBER_ID, HOUSEHOLD_ID),
        )


def _write_manifest(
    root: Path,
    *,
    package_digest: str,
    source_subject_key: str,
) -> Path:
    root.mkdir(mode=0o700)
    path = root / "confirmation.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "private-knowledge-confirmation.sol-v1",
                "package_digest_sha256": package_digest,
                "household_space_id": str(HOUSEHOLD_ID),
                "confirmed_by": str(ACTOR_ID),
                "status_as_of": "2026-08-30",
                "authority": "USER_CONFIRMED_CURRENT_ENROLLMENT",
                "subjects": [
                    {
                        "source_subject_key": source_subject_key,
                        "family_member_id": str(MEMBER_ID),
                    }
                ],
                "contracts": [
                    {
                        "canonical_policy_id": "synthetic-policy-001",
                        "decision": "MATCH",
                        "confirmed_status": "active",
                        "reason_code": "USER_ATTESTED_CURRENT",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_exact_bindings_and_current_confirmations_apply_atomically_and_idempotently(
    tmp_path: Path,
) -> None:
    _seed()
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    package_root = write_synthetic_private_knowledge_package(tmp_path / "package")
    package = load_private_knowledge_package(
        package_root,
        repository_root=repository_root,
    )
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    snapshot = repository.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=build_dry_run_report(
            package,
            repository.read_baseline(HOUSEHOLD_ID),
        ),
    )
    with psycopg.connect(_database_url()) as connection:
        source_subject_key = connection.execute(
            """
            SELECT source_subject_key
            FROM private_knowledge_subjects
            WHERE import_run_id = %s
            """,
            (snapshot.run_id,),
        ).fetchone()
    assert source_subject_key is not None
    manifest = load_confirmation_manifest(
        _write_manifest(
            tmp_path / "confirmation",
            package_digest=package.package_digest_sha256,
            source_subject_key=str(source_subject_key[0]),
        ),
        repository_root=repository_root,
    )

    report = repository.prepare_confirmation_dry_run(manifest)
    assert report.operation == "APPLY"
    assert report.binding_change_count == 1
    assert report.confirmation_insert_count == 1
    applied = repository.apply_confirmations(manifest, approved_report=report)
    assert applied.run_id == snapshot.run_id
    assert applied.subject_count == 1
    assert applied.current_confirmation_count == 1

    with psycopg.connect(_database_url()) as connection:
        subject = connection.execute(
            """
            SELECT family_member_id, binding_decision, binding_confirmed_by
            FROM private_knowledge_subjects
            WHERE import_run_id = %s
            """,
            (snapshot.run_id,),
        ).fetchone()
        confirmation = connection.execute(
            """
            SELECT decision, confirmed_status, status_as_of, confirmed_by
            FROM private_knowledge_contract_confirmations
            WHERE import_run_id = %s AND is_current
            """,
            (snapshot.run_id,),
        ).fetchone()
    assert subject == (MEMBER_ID, "MATCH", ACTOR_ID)
    assert confirmation is not None
    assert confirmation[0:2] == ("MATCH", "active")
    assert confirmation[3] == ACTOR_ID

    no_op = repository.prepare_confirmation_dry_run(manifest)
    assert no_op.operation == "NO_OP"
    assert no_op.binding_change_count == 0
    assert no_op.confirmation_insert_count == 0
    repeated = repository.apply_confirmations(manifest, approved_report=no_op)
    assert repeated == applied
    with psycopg.connect(_database_url()) as connection:
        count = connection.execute(
            """
            SELECT count(*)
            FROM private_knowledge_contract_confirmations
            WHERE import_run_id = %s
            """,
            (snapshot.run_id,),
        ).fetchone()
    assert count == (1,)
    assert repository.verify_current(HOUSEHOLD_ID).run_id == snapshot.run_id
