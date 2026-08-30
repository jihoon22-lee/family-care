"""Atomic PostgreSQL apply and verification for private knowledge snapshots."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from familycare_api.private_knowledge import repository as repository_module
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.reconciliation import (
    build_dry_run_report,
    canonical_report_digest,
)
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
    PrivateKnowledgeRepositoryError,
    PrivateKnowledgeRepositoryErrorCode,
)

from apps.api.tests.private_knowledge_fixtures import (
    mutate_jsonl,
    write_synthetic_private_knowledge_package,
)

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001931")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000001932")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert "test" in str(database_name[0]).lower()
        connection.execute("TRUNCATE TABLE household_spaces, documents RESTART IDENTITY CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-knowledge-apply', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (
              %s, %s, 'synthetic-knowledge-operator', 'Admin A',
              '$argon2id$synthetic'
            )
            """,
            (ACTOR_ID, HOUSEHOLD_ID),
        )


def _package(tmp_path: Path, name: str = "private-package"):
    root = write_synthetic_private_knowledge_package(tmp_path / name)
    return root, load_private_knowledge_package(
        root,
        repository_root=tmp_path / "repository",
    )


def _report(repository: PostgresPrivateKnowledgeRepository, package):
    return build_dry_run_report(
        package,
        repository.read_baseline(HOUSEHOLD_ID),
    )


def _run_counts() -> tuple[int, int]:
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE is_current)
            FROM private_knowledge_import_runs
            WHERE household_space_id = %s
            """,
            (HOUSEHOLD_ID,),
        ).fetchone()
        assert row is not None
        return int(row[0]), int(row[1])


def test_apply_verify_and_same_current_digest_are_atomic_and_idempotent(
    tmp_path: Path,
) -> None:
    _seed()
    _, package = _package(tmp_path)
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    report = _report(repository, package)

    applied = repository.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=report,
    )

    assert applied.is_current is True
    assert applied.package_digest_sha256 == package.package_digest_sha256
    assert applied.counts == report.expected_current_counts
    assert _run_counts() == (1, 1)
    verified = repository.verify_current(HOUSEHOLD_ID)
    assert verified.run_id == applied.run_id
    assert verified.counts == report.expected_current_counts
    assert verified.executable_fact_count == 0
    assert verified.executable_mapping_count == 0
    assert verified.unsafe_operational_binding_count == 0

    no_op_report = _report(repository, package)
    assert no_op_report.operation == "NO_OP"
    repeated = repository.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=no_op_report,
    )
    assert repeated.run_id == applied.run_id
    assert _run_counts() == (1, 1)


def test_verify_rejects_indexed_decision_matrix_drift(tmp_path: Path) -> None:
    _seed()
    _, package = _package(tmp_path)
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    repository.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=_report(repository, package),
    )

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_coverage_terms_mappings
            SET enrollment_decision = 'UNKNOWN', overall_decision = 'UNKNOWN'
            WHERE import_run_id = (
              SELECT id FROM private_knowledge_import_runs
              WHERE household_space_id = %s AND is_current
            )
            """,
            (HOUSEHOLD_ID,),
        )

    with pytest.raises(PrivateKnowledgeRepositoryError) as invalid:
        repository.verify_current(HOUSEHOLD_ID)
    assert invalid.value.code is PrivateKnowledgeRepositoryErrorCode.VERIFICATION_FAILED


def test_database_rejects_cross_household_member_binding(tmp_path: Path) -> None:
    _seed()
    _, package = _package(tmp_path)
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    applied = repository.apply_snapshot(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=_report(repository, package),
    )
    other_household = UUID("00000000-0000-4000-8000-000000001933")
    other_member = UUID("00000000-0000-4000-8000-000000001934")

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-other-household', 'Other Synthetic Household')
            """,
            (other_household,),
        )
        connection.execute(
            """
            INSERT INTO family_members (
              id, household_space_id, display_name, internal_alias
            ) VALUES (%s, %s, 'Family Member B', 'synthetic-member-b')
            """,
            (other_member, other_household),
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            connection.execute(
                """
                UPDATE private_knowledge_subjects
                SET family_member_id = %s, binding_decision = 'MATCH'
                WHERE import_run_id = %s
                """,
                (other_member, applied.run_id),
            )

    assert repository.verify_current(HOUSEHOLD_ID).run_id == applied.run_id


def test_apply_blocks_approved_report_with_unresolved_snapshot_conflict(
    tmp_path: Path,
) -> None:
    _seed()
    _, package = _package(tmp_path)
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    report = _report(repository, package).model_copy(
        update={"snapshot_conflict_count": 1, "report_digest_sha256": "0" * 64}
    )
    report = report.model_copy(update={"report_digest_sha256": canonical_report_digest(report)})

    with pytest.raises(PrivateKnowledgeRepositoryError) as blocked:
        repository.apply_snapshot(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=report,
        )
    assert blocked.value.code is PrivateKnowledgeRepositoryErrorCode.APPLY_BLOCKED
    assert _run_counts() == (0, 0)


def test_apply_rolls_back_if_inserted_entity_count_does_not_match_approval(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _seed()
    _, package = _package(tmp_path)
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    report = _report(repository, package)
    real_insert = repository_module.insert_private_knowledge_snapshot

    def wrong_count(*args, **kwargs):
        counts = real_insert(*args, **kwargs)
        return counts.model_copy(update={"contracts": counts.contracts + 1})

    monkeypatch.setattr(
        repository_module,
        "insert_private_knowledge_snapshot",
        wrong_count,
    )
    with pytest.raises(PrivateKnowledgeRepositoryError) as mismatch:
        repository.apply_snapshot(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=report,
        )
    assert mismatch.value.code is PrivateKnowledgeRepositoryErrorCode.COUNT_MISMATCH
    assert _run_counts() == (0, 0)


def test_apply_rejects_tampered_report_stale_baseline_and_invalid_actor(
    tmp_path: Path,
) -> None:
    _seed()
    _, package = _package(tmp_path)
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    report = _report(repository, package)

    tampered = report.model_copy(update={"report_digest_sha256": "0" * 64})
    with pytest.raises(PrivateKnowledgeRepositoryError) as invalid_report:
        repository.apply_snapshot(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=tampered,
        )
    assert invalid_report.value.code is PrivateKnowledgeRepositoryErrorCode.APPROVAL_INVALID
    assert _run_counts() == (0, 0)

    with pytest.raises(PrivateKnowledgeRepositoryError) as invalid_actor:
        repository.apply_snapshot(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=UUID("00000000-0000-4000-8000-000000001999"),
            approved_report=report,
        )
    assert invalid_actor.value.code is PrivateKnowledgeRepositoryErrorCode.ACTOR_NOT_FOUND
    assert _run_counts() == (0, 0)

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE household_spaces
            SET display_name = 'Changed Synthetic Household',
                version = version + 1,
                updated_at = clock_timestamp()
            WHERE id = %s
            """,
            (HOUSEHOLD_ID,),
        )
    with pytest.raises(PrivateKnowledgeRepositoryError) as stale:
        repository.apply_snapshot(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=report,
        )
    assert stale.value.code is PrivateKnowledgeRepositoryErrorCode.STALE_DRY_RUN
    assert _run_counts() == (0, 0)


@pytest.mark.parametrize(
    "failure_stage",
    [
        "import_run",
        "subjects",
        "contracts",
        "coverages",
        "terms_assignments",
        "terms_assignment_sources",
        "terms_sections",
        "source_clauses",
        "semantic_reviews",
        "facts",
        "fact_citations",
        "coverage_terms_mappings",
        "document_bindings",
        "before_current_switch",
    ],
)
def test_failure_after_each_entity_group_rolls_back_every_row(
    tmp_path: Path,
    failure_stage: str,
) -> None:
    _seed()
    _, first_package = _package(tmp_path, "first-package")
    baseline_repository = PostgresPrivateKnowledgeRepository(_database_url())
    first = baseline_repository.apply_snapshot(
        first_package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=_report(baseline_repository, first_package),
    )
    second_root, _ = _package(tmp_path, "second-package")
    mutate_jsonl(
        second_root,
        "contracts.jsonl",
        lambda row: row.__setitem__("monthly_premium_krw", 2000),
    )
    package = load_private_knowledge_package(
        second_root,
        repository_root=tmp_path / "repository",
    )
    report = _report(baseline_repository, package)
    assert report.operation == "SUPERSEDE"

    def fail(stage: str) -> None:
        if stage == failure_stage:
            raise RuntimeError("synthetic failure")

    repository = PostgresPrivateKnowledgeRepository(
        _database_url(),
        failure_injector=fail,
    )
    with pytest.raises(PrivateKnowledgeRepositoryError) as failed:
        repository.apply_snapshot(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=report,
        )
    assert failed.value.code is PrivateKnowledgeRepositoryErrorCode.APPLY_FAILED
    assert _run_counts() == (1, 1)
    assert baseline_repository.verify_current(HOUSEHOLD_ID).run_id == first.run_id


def test_new_digest_supersedes_once_and_historical_digest_is_blocked(
    tmp_path: Path,
) -> None:
    _seed()
    _, first_package = _package(tmp_path, "first-package")
    repository = PostgresPrivateKnowledgeRepository(_database_url())
    first = repository.apply_snapshot(
        first_package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=_report(repository, first_package),
    )

    second_root, second_package = _package(tmp_path, "second-package")
    mutate_jsonl(
        second_root,
        "contracts.jsonl",
        lambda row: row.__setitem__("monthly_premium_krw", 2000),
    )
    second_package = load_private_knowledge_package(
        second_root,
        repository_root=tmp_path / "repository",
    )
    second_report = _report(repository, second_package)
    assert second_report.operation == "SUPERSEDE"
    second = repository.apply_snapshot(
        second_package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=second_report,
    )

    assert first.run_id != second.run_id
    assert _run_counts() == (2, 1)
    with psycopg.connect(_database_url()) as connection:
        states = connection.execute(
            """
            SELECT id, state, is_current, superseded_at IS NOT NULL
            FROM private_knowledge_import_runs
            WHERE household_space_id = %s
            ORDER BY created_at, id
            """,
            (HOUSEHOLD_ID,),
        ).fetchall()
    assert states == [
        (first.run_id, "SUPERSEDED", False, True),
        (second.run_id, "APPLIED", True, False),
    ]

    historical_report = _report(repository, first_package)
    assert historical_report.operation == "BLOCKED"
    with pytest.raises(PrivateKnowledgeRepositoryError) as blocked:
        repository.apply_snapshot(
            first_package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=historical_report,
        )
    assert blocked.value.code is PrivateKnowledgeRepositoryErrorCode.APPLY_BLOCKED
    assert _run_counts() == (2, 1)
