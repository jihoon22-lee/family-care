"""PostgreSQL proof for private-knowledge read-only reconciliation baselines."""

from __future__ import annotations

import os
from uuid import UUID

import psycopg
import pytest
from familycare_api.private_knowledge.reconciliation import (
    KnowledgeEntityCounts,
    operational_label_key,
)
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
    PrivateKnowledgeRepositoryError,
    PrivateKnowledgeRepositoryErrorCode,
)

from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001911")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000001912")
POLICY_ID = UUID("00000000-0000-4000-8000-000000001913")
RIDER_ID = UUID("00000000-0000-4000-8000-000000001914")


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
        connection.execute("TRUNCATE TABLE household_spaces, documents RESTART IDENTITY CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-knowledge-baseline', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO family_members (
              id, household_space_id, display_name, internal_alias
            ) VALUES (%s, %s, 'Family Member A', 'family-member-a')
            """,
            (MEMBER_ID, HOUSEHOLD_ID),
        )
        document_id = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES ('synthetic/private-knowledge-policy.pdf', 'policy', 'ready')
            RETURNING id
            """
        ).fetchone()
        assert document_id is not None
        document_version_id = connection.execute(
            """
            INSERT INTO document_versions (
              document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 256, 1)
            RETURNING id
            """,
            (document_id[0], "1" * 64),
        ).fetchone()
        assert document_version_id is not None
        extraction_id = connection.execute(
            """
            INSERT INTO extractions (
              document_version_id, extractor_name, extractor_version,
              extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (
              %s, 'synthetic', '1', %s, 'quality-v1',
              'succeeded', clock_timestamp()
            ) RETURNING id
            """,
            (document_version_id[0], "2" * 64),
        ).fetchone()
        assert extraction_id is not None
        evidence_id = connection.execute(
            """
            INSERT INTO evidence (
              household_space_id, document_version_id, extraction_id,
              content_sha256, physical_page, review_state
            ) VALUES (%s, %s, %s, %s, 1, 'USER_CONFIRMED')
            RETURNING id
            """,
            (
                HOUSEHOLD_ID,
                document_version_id[0],
                extraction_id[0],
                "3" * 64,
            ),
        ).fetchone()
        assert evidence_id is not None
        connection.execute(
            """
            INSERT INTO policy_contracts (
              id, household_space_id, source_document_version_id,
              source_evidence_id, insurer_display, insurer_key,
              product_display, product_key, status
            ) VALUES (
              %s, %s, %s, %s, 'Sample Insurer', 'sample-insurer',
              'Sample Policy', 'sample-policy', 'unknown'
            )
            """,
            (
                POLICY_ID,
                HOUSEHOLD_ID,
                document_version_id[0],
                evidence_id[0],
            ),
        )
        connection.execute(
            """
            INSERT INTO riders (
              id, household_space_id, policy_contract_id, source_evidence_id,
              display_name, normalized_key, benefit_type, currency, status
            ) VALUES (
              %s, %s, %s, %s, 'Sample Hospital Benefit',
              'sample-hospital-benefit', 'fixed', 'KRW', 'unknown'
            )
            """,
            (RIDER_ID, HOUSEHOLD_ID, POLICY_ID, evidence_id[0]),
        )


def _row_counts() -> tuple[int, ...]:
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM family_members),
              (SELECT count(*) FROM policy_contracts),
              (SELECT count(*) FROM riders),
              (SELECT count(*) FROM private_knowledge_import_runs)
            """
        ).fetchone()
        assert row is not None
        return tuple(int(value) for value in row)


def test_read_baseline_is_scoped_count_only_repeatable_read_and_read_only() -> None:
    _seed()
    before = _row_counts()
    repository = PostgresPrivateKnowledgeRepository(_database_url())

    baseline = repository.read_baseline(HOUSEHOLD_ID)

    assert _row_counts() == before
    assert baseline.counts.model_dump() == {
        "family_members": 1,
        "policy_contracts": 1,
        "riders": 1,
        "document_versions": 1,
        "evidence": 1,
        "import_runs": 0,
        "current_import_runs": 0,
    }
    assert baseline.current_run_id is None
    assert baseline.current_package_digest_sha256 is None
    assert baseline.known_package_digests == ()
    assert baseline.current_snapshot_counts == KnowledgeEntityCounts.zero()
    assert baseline.policy_label_key_counts[0].key == operational_label_key(
        "Sample Insurer", "Sample Policy"
    )
    assert baseline.coverage_label_key_counts[0].key == operational_label_key(
        "Sample Insurer", "Sample Policy", "Sample Hospital Benefit"
    )
    serialized = baseline.model_dump_json()
    for private_value in (
        "Family Member A",
        "Sample Insurer",
        "Sample Policy",
        "Sample Hospital Benefit",
        "synthetic/private-knowledge-policy.pdf",
    ):
        assert private_value not in serialized


def test_baseline_digest_and_label_keys_change_with_relevant_ledger_update() -> None:
    _seed()
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    before = repository.read_baseline(HOUSEHOLD_ID)

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE riders
            SET display_name = 'Sample Surgery Benefit',
                normalized_key = 'sample-surgery-benefit',
                version = version + 1,
                updated_at = clock_timestamp()
            WHERE id = %s AND household_space_id = %s
            """,
            (RIDER_ID, HOUSEHOLD_ID),
        )

    after = repository.read_baseline(HOUSEHOLD_ID)
    assert after.baseline_digest_sha256 != before.baseline_digest_sha256
    assert after.coverage_label_key_counts != before.coverage_label_key_counts


def test_missing_household_raises_only_a_sanitized_code() -> None:
    _seed()
    repository = PostgresPrivateKnowledgeRepository(_database_url())

    with pytest.raises(PrivateKnowledgeRepositoryError) as missing:
        repository.read_baseline(UUID("00000000-0000-4000-8000-000000001999"))

    assert missing.value.code is PrivateKnowledgeRepositoryErrorCode.HOUSEHOLD_NOT_FOUND
    assert str(missing.value) == "HOUSEHOLD_NOT_FOUND"
    assert str(HOUSEHOLD_ID) not in str(missing.value)
