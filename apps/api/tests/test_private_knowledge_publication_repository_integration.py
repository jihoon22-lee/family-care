"""Atomic PostgreSQL publication apply, rollback, idempotency, and drift proof."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.publication_package import (
    RulePublicationPackage,
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_repository import (
    PostgresRulePublicationRepository,
    RulePublicationRepositoryError,
    RulePublicationRepositoryErrorCode,
)
from familycare_api.private_knowledge.reconciliation import build_dry_run_report
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
)

from apps.api.tests.private_knowledge_fixtures import (
    append_jsonl,
    refresh_manifest,
    write_synthetic_private_knowledge_package,
)
from apps.api.tests.private_knowledge_publication_fixtures import (
    bind_publication_package_to_knowledge,
    mutate_publication_jsonl,
    write_synthetic_rule_publication_package,
)
from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000004201")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000004202")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000004203")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _add_contract_without_coverages(root: Path) -> None:
    contract_path = root / "contracts.jsonl"
    source_contract = json.loads(contract_path.read_text(encoding="utf-8").splitlines()[0])
    empty_contract = copy.deepcopy(source_contract)
    empty_contract["canonical_policy_id"] = "synthetic-policy-002"
    empty_contract["product_name"] = "Sample Policy Without Coverages"
    empty_contract["source_members"][0]["local_policy_id"] = "synthetic-local-policy-002"
    empty_contract["terms_pairing"]["canonical_policy_id"] = "synthetic-policy-002"
    empty_contract["row_reconciliation"].update(
        {
            "benefit_coverages": 0,
            "canonical_components": 0,
            "certificate_rows_detected": 0,
        }
    )
    append_jsonl(root, "contracts.jsonl", empty_contract)

    pairing_path = root / "policy-terms-pairings.jsonl"
    source_pairing = json.loads(pairing_path.read_text(encoding="utf-8").splitlines()[0])
    empty_pairing = copy.deepcopy(source_pairing)
    empty_pairing["canonical_policy_id"] = "synthetic-policy-002"
    append_jsonl(root, "policy-terms-pairings.jsonl", empty_pairing)

    reconciliation_path = root / "reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    reconciliation.update(
        {
            "policy_count": 2,
            "policy_terms_identity_match_count": 2,
            "policy_terms_edition_match_count": 2,
        }
    )
    reconciliation_path.write_text(
        json.dumps(reconciliation, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    reconciliation_path.chmod(0o600)
    refresh_manifest(root)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"] = reconciliation
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)


def _seed_current_knowledge(
    tmp_path: Path,
    *,
    include_contract_without_coverages: bool = False,
) -> tuple[str, str]:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        connection.execute("TRUNCATE TABLE household_spaces, documents RESTART IDENTITY CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, 'synthetic-rule-publication', 'Synthetic Household')
            """,
            (HOUSEHOLD_ID,),
        )
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (
              %s, %s, 'synthetic-rule-publisher', 'Admin A', '$argon2id$synthetic'
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

    knowledge_root = write_synthetic_private_knowledge_package(tmp_path / "knowledge-package")
    if include_contract_without_coverages:
        _add_contract_without_coverages(knowledge_root)
    knowledge_package = load_private_knowledge_package(
        knowledge_root,
        repository_root=tmp_path / "repository",
    )
    knowledge_repository = PostgresPrivateKnowledgeRepository(_database_url())
    knowledge_report = build_dry_run_report(
        knowledge_package,
        knowledge_repository.read_baseline(HOUSEHOLD_ID),
    )
    applied = knowledge_repository.apply_snapshot(
        knowledge_package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=knowledge_report,
    )
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_subjects
            SET family_member_id = %s, binding_decision = 'MATCH',
                binding_conflict = false, binding_reason_code = 'USER_EXACT_BINDING',
                binding_confirmed_by = %s, binding_confirmed_at = clock_timestamp()
            WHERE import_run_id = %s
            """,
            (MEMBER_ID, ACTOR_ID, applied.run_id),
        )
        connection.execute(
            """
            INSERT INTO private_knowledge_contract_confirmations (
              import_run_id, household_space_id, knowledge_contract_id,
              decision, confirmed_status, status_as_of, authority, reason_code,
              confirmed_by, confirmed_at, is_current,
              confirmation_digest_sha256
            )
            SELECT import_run_id, household_space_id, id, 'MATCH', 'active',
                   DATE '2026-08-30', 'USER_CONFIRMED_CURRENT_ENROLLMENT',
                   'SYNTHETIC_CURRENT_CONFIRMED', %s, clock_timestamp(), true,
                   lpad(row_number() OVER (ORDER BY id)::text, 64, 'c')
            FROM private_knowledge_contracts
            WHERE import_run_id = %s
            """,
            (ACTOR_ID, applied.run_id),
        )
        row = connection.execute(
            """
            SELECT package_digest_sha256, projection_digest_sha256
            FROM private_knowledge_import_runs WHERE id = %s
            """,
            (applied.run_id,),
        ).fetchone()
        assert row is not None
        return str(row[0]), str(row[1])


def _publication_package(
    tmp_path: Path,
    *,
    knowledge_package_digest: str,
    knowledge_projection_digest: str,
    name: str = "publication-package",
    priority: int = 100,
) -> RulePublicationPackage:
    root = write_synthetic_rule_publication_package(tmp_path / name)
    if priority != 100:
        mutate_publication_jsonl(
            root,
            "fact-normalizers.jsonl",
            lambda row: row.__setitem__("priority", priority),
        )
    bind_publication_package_to_knowledge(
        root,
        package_digest_sha256=knowledge_package_digest,
        projection_digest_sha256=knowledge_projection_digest,
    )
    return load_rule_publication_package(
        root,
        repository_root=tmp_path / "repository",
    )


def _publication_run_counts() -> tuple[int, int]:
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(
            """
            SELECT count(*), count(*) FILTER (WHERE is_current)
            FROM private_knowledge_rule_import_runs
            WHERE household_space_id = %s
            """,
            (HOUSEHOLD_ID,),
        ).fetchone()
        assert row is not None
        return int(row[0]), int(row[1])


def test_apply_rollback_idempotency_supersede_and_clause_drift(tmp_path: Path) -> None:
    knowledge_package_digest, knowledge_projection_digest = _seed_current_knowledge(tmp_path)
    package = _publication_package(
        tmp_path,
        knowledge_package_digest=knowledge_package_digest,
        knowledge_projection_digest=knowledge_projection_digest,
    )
    repository = PostgresRulePublicationRepository(_database_url())
    report = repository.prepare_dry_run(package, household_space_id=HOUSEHOLD_ID)
    assert report.operation == "CREATE"

    stages = (
        "publication_run",
        "status_intervals",
        "dispositions",
        "fact_normalizers",
        "rules",
        "rule_citations",
        "calculations",
        "calculation_citations",
        "before_current_switch",
    )
    for target_stage in stages:

        def fail_at(stage: str, *, expected: str = target_stage) -> None:
            if stage == expected:
                raise RuntimeError("synthetic failure")

        failing = PostgresRulePublicationRepository(
            _database_url(),
            failure_injector=fail_at,
        )
        with pytest.raises(RulePublicationRepositoryError) as failed:
            failing.apply(
                package,
                household_space_id=HOUSEHOLD_ID,
                actor_id=ACTOR_ID,
                approved_report=report,
            )
        assert failed.value.code is RulePublicationRepositoryErrorCode.APPLY_FAILED
        assert _publication_run_counts() == (0, 0)

    applied = repository.apply(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=report,
    )
    assert applied.counts == package.reconciliation
    assert _publication_run_counts() == (1, 1)
    assert repository.verify_current(HOUSEHOLD_ID).run_id == applied.run_id

    no_op_report = repository.prepare_dry_run(package, household_space_id=HOUSEHOLD_ID)
    assert no_op_report.operation == "NO_OP"
    repeated = repository.apply(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=no_op_report,
    )
    assert repeated.run_id == applied.run_id
    assert _publication_run_counts() == (1, 1)

    changed_package = _publication_package(
        tmp_path,
        knowledge_package_digest=knowledge_package_digest,
        knowledge_projection_digest=knowledge_projection_digest,
        name="changed-publication-package",
        priority=101,
    )
    changed_report = repository.prepare_dry_run(
        changed_package,
        household_space_id=HOUSEHOLD_ID,
    )
    assert changed_report.operation == "SUPERSEDE"
    changed = repository.apply(
        changed_package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=changed_report,
    )
    assert changed.run_id != applied.run_id
    assert _publication_run_counts() == (2, 1)

    historical = repository.prepare_dry_run(package, household_space_id=HOUSEHOLD_ID)
    assert historical.operation == "BLOCKED"

    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_source_clauses
            SET source_text_sha256 = %s
            WHERE import_run_id = (
              SELECT id FROM private_knowledge_import_runs
              WHERE household_space_id = %s AND is_current
            )
            """,
            ("d" * 64, HOUSEHOLD_ID),
        )
    with pytest.raises(RulePublicationRepositoryError) as drifted:
        repository.verify_current(HOUSEHOLD_ID)
    assert drifted.value.code is RulePublicationRepositoryErrorCode.VERIFICATION_FAILED


def test_apply_counts_only_contracts_represented_by_coverage_dispositions(
    tmp_path: Path,
) -> None:
    knowledge_package_digest, knowledge_projection_digest = _seed_current_knowledge(
        tmp_path,
        include_contract_without_coverages=True,
    )
    package = _publication_package(
        tmp_path,
        knowledge_package_digest=knowledge_package_digest,
        knowledge_projection_digest=knowledge_projection_digest,
    )
    repository = PostgresRulePublicationRepository(_database_url())
    report = repository.prepare_dry_run(package, household_space_id=HOUSEHOLD_ID)

    applied = repository.apply(
        package,
        household_space_id=HOUSEHOLD_ID,
        actor_id=ACTOR_ID,
        approved_report=report,
    )

    assert applied.counts.contract_count == 1
    with psycopg.connect(_database_url()) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM private_knowledge_contracts
               WHERE import_run_id = (
                 SELECT knowledge_import_run_id
                 FROM private_knowledge_rule_import_runs WHERE id = %s
               )),
              (SELECT count(*) FROM private_knowledge_coverages
               WHERE import_run_id = (
                 SELECT knowledge_import_run_id
                 FROM private_knowledge_rule_import_runs WHERE id = %s
               ))
            """,
            (applied.run_id, applied.run_id),
        ).fetchone()
    assert counts == (2, 1)


def test_apply_rejects_changed_actor_baseline_after_approval(tmp_path: Path) -> None:
    knowledge_package_digest, knowledge_projection_digest = _seed_current_knowledge(tmp_path)
    package = _publication_package(
        tmp_path,
        knowledge_package_digest=knowledge_package_digest,
        knowledge_projection_digest=knowledge_projection_digest,
    )
    repository = PostgresRulePublicationRepository(_database_url())
    report = repository.prepare_dry_run(package, household_space_id=HOUSEHOLD_ID)
    with psycopg.connect(_database_url()) as connection:
        connection.execute(
            """
            UPDATE app_users
            SET updated_at = updated_at + interval '1 second'
            WHERE id = %s
            """,
            (ACTOR_ID,),
        )

    with pytest.raises(RulePublicationRepositoryError) as stale:
        repository.apply(
            package,
            household_space_id=HOUSEHOLD_ID,
            actor_id=ACTOR_ID,
            approved_report=report,
        )
    assert stale.value.code is RulePublicationRepositoryErrorCode.STALE_DRY_RUN
    assert _publication_run_counts() == (0, 0)
