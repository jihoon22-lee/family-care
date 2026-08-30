"""Synthetic PostgreSQL proof for combined operational and knowledge decisions."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_repository import (
    KnowledgeContextRead,
    PostgresKnowledgeDecisionRepository,
)
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.service import DecisionService
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.publication_package import (
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_repository import (
    PostgresRulePublicationRepository,
)
from familycare_api.private_knowledge.reconciliation import build_dry_run_report
from familycare_api.private_knowledge.repository import (
    PostgresPrivateKnowledgeRepository,
)
from psycopg.rows import dict_row

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)
from apps.api.tests.private_knowledge_publication_fixtures import (
    bind_publication_package_to_knowledge,
    mutate_publication_jsonl,
    write_synthetic_rule_publication_package,
)
from apps.api.tests.test_decision_integration import (
    DecisionSeed,
    _psycopg_url,
    _reset_database,
    _seed,
)

pytestmark = pytest.mark.integration

ACTOR_ID = UUID("00000000-0000-4000-8000-000000008001")


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    _reset_database(value)
    return value


def _seed_private_publication(
    database_url: str,
    seed: DecisionSeed,
    tmp_path: Path,
) -> tuple[UUID, UUID]:
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO app_users (
              id, household_space_id, username, display_name, password_hash
            ) VALUES (
              %s, %s, 'synthetic-combined-operator', 'Admin A',
              '$argon2id$synthetic'
            )
            """,
            (ACTOR_ID, seed.scope_a.household_space_id),
        )

    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    package = load_private_knowledge_package(
        write_synthetic_private_knowledge_package(tmp_path / "knowledge-package"),
        repository_root=repository_root,
    )
    knowledge_repository = PostgresPrivateKnowledgeRepository(database_url)
    applied = knowledge_repository.apply_snapshot(
        package,
        household_space_id=seed.scope_a.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=build_dry_run_report(
            package,
            knowledge_repository.read_baseline(seed.scope_a.household_space_id),
        ),
    )
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as dict_connection:
        dict_connection.execute(
            """
            UPDATE private_knowledge_subjects
            SET family_member_id = %s, binding_decision = 'MATCH',
                binding_conflict = false,
                binding_reason_code = 'USER_EXACT_BINDING',
                binding_confirmed_by = %s,
                binding_confirmed_at = clock_timestamp()
            WHERE import_run_id = %s AND household_space_id = %s
            """,
            (
                seed.member_a,
                ACTOR_ID,
                applied.run_id,
                seed.scope_a.household_space_id,
            ),
        )
        dict_connection.execute(
            """
            INSERT INTO private_knowledge_contract_confirmations (
              import_run_id, household_space_id, knowledge_contract_id,
              decision, confirmed_status, status_as_of, authority, reason_code,
              confirmed_by, confirmed_at, is_current,
              confirmation_digest_sha256
            )
            SELECT import_run_id, household_space_id, id, 'MATCH', 'active',
                   DATE '2025-01-01', 'USER_CONFIRMED_CURRENT_ENROLLMENT',
                   'SYNTHETIC_CURRENT_CONFIRMED', %s, clock_timestamp(), true, %s
            FROM private_knowledge_contracts
            WHERE import_run_id = %s AND household_space_id = %s
            """,
            (
                ACTOR_ID,
                "c" * 64,
                applied.run_id,
                seed.scope_a.household_space_id,
            ),
        )
        run = dict_connection.execute(
            """
            SELECT package_digest_sha256, projection_digest_sha256
            FROM private_knowledge_import_runs WHERE id = %s
            """,
            (applied.run_id,),
        ).fetchone()
    assert run is not None

    publication_root = write_synthetic_rule_publication_package(tmp_path / "publication-package")

    def event_year_interval(row: dict[str, object]) -> None:
        row["effective_from"] = "2025-01-01"
        row["effective_through"] = "2025-12-31"

    mutate_publication_jsonl(
        publication_root,
        "contract-status-intervals.jsonl",
        event_year_interval,
    )
    bind_publication_package_to_knowledge(
        publication_root,
        package_digest_sha256=str(run["package_digest_sha256"]),
        projection_digest_sha256=str(run["projection_digest_sha256"]),
    )
    publication = load_rule_publication_package(
        publication_root,
        repository_root=repository_root,
    )
    publication_repository = PostgresRulePublicationRepository(database_url)
    publication_run = publication_repository.apply(
        publication,
        household_space_id=seed.scope_a.household_space_id,
        actor_id=ACTOR_ID,
        approved_report=publication_repository.prepare_dry_run(
            publication,
            household_space_id=seed.scope_a.household_space_id,
        ),
    )
    return applied.run_id, publication_run.run_id


class _FailingKnowledgeRepository(PostgresKnowledgeDecisionRepository):
    def read_context(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
    ) -> KnowledgeContextRead:
        raise ValueError("synthetic private source failure")


def test_combined_analysis_round_trip_partial_isolation_and_staleness(
    database_url: str,
    tmp_path: Path,
) -> None:
    seed = _seed(database_url)
    knowledge_run_id, rule_run_id = _seed_private_publication(
        database_url,
        seed,
        tmp_path,
    )
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))
    event = service.create_medical_event(
        family_member_id=seed.member_a,
        mode="post_treatment",
        situation="Synthetic sample category phrase event.",
        event_date=date(2025, 6, 15),
        visit_date=date(2025, 6, 16),
        facts={"MedicalEvent.classification": "sample_category"},
        confirmation={"MedicalEvent.classification": "user"},
    )

    combined = service.analyze_medical_event(event.id)
    assert combined.status == "succeeded"
    assert combined.analysis_completeness == "COMPLETE"
    assert combined.knowledge_import_run_id == knowledge_run_id
    assert combined.knowledge_rule_import_run_id == rule_run_id
    assert combined.catalog_coverage.contract_count == 1
    assert combined.catalog_coverage.benefit_coverage_count == 1
    assert combined.catalog_coverage.published_coverage_count == 1
    assert combined.candidates
    assert combined.knowledge_result is not None
    assert combined.knowledge_result.candidates[0].result == "MATCH"
    assert combined.knowledge_result.calculations[0].conditional_amount == 1

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        counts = connection.execute(
            """
            SELECT
              (SELECT count(*) FROM rule_evaluations
               WHERE decision_run_id = %(run)s) AS legacy_evaluations,
              (SELECT count(*) FROM private_knowledge_rule_evaluations
               WHERE decision_run_id = %(run)s) AS knowledge_evaluations,
              (SELECT count(*) FROM private_knowledge_claim_candidates
               WHERE decision_run_id = %(run)s) AS knowledge_candidates,
              (SELECT count(*) FROM private_knowledge_benefit_calculations
               WHERE decision_run_id = %(run)s) AS knowledge_calculations
            """,
            {"run": combined.run_id},
        ).fetchone()
    assert counts == {
        "legacy_evaluations": 2,
        "knowledge_evaluations": 1,
        "knowledge_candidates": 1,
        "knowledge_calculations": 1,
    }

    loaded = service.get_decision_result(event.id, event.version)
    assert loaded.run_id == combined.run_id
    assert loaded.stale is False
    assert loaded.knowledge_result is not None
    assert loaded.knowledge_result.fixed_subtotals[0].amount == 1
    with pytest.raises(RuntimeError):
        DecisionService(seed.scope_b, DecisionRepository(database_url)).get_decision_result(
            event.id,
            event.version,
        )

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_contract_confirmations
            SET confirmation_digest_sha256 = %s
            WHERE import_run_id = %s AND is_current
            """,
            ("e" * 64, knowledge_run_id),
        )
    assert service.get_decision_result(event.id, event.version).stale is True

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_rule_import_runs
            SET is_current = false, state = 'SUPERSEDED',
                superseded_at = clock_timestamp()
            WHERE id = %s AND household_space_id = %s
            """,
            (rule_run_id, seed.scope_a.household_space_id),
        )
    unavailable = service.analyze_medical_event(event.id)
    assert unavailable.status == "partial"
    assert unavailable.analysis_completeness == "UNAVAILABLE"
    assert unavailable.source_failure_codes == ("KNOWLEDGE_PUBLICATION_UNAVAILABLE",)
    assert unavailable.catalog_coverage.contract_count == 1
    assert unavailable.catalog_coverage.benefit_coverage_count == 1
    assert unavailable.catalog_coverage.published_coverage_count == 0
    assert unavailable.knowledge_result is None
    assert unavailable.candidates

    failing_service = DecisionService(
        seed.scope_a,
        DecisionRepository(
            database_url,
            knowledge_repository=_FailingKnowledgeRepository(),
        ),
    )
    partial = failing_service.analyze_medical_event(event.id)
    assert partial.status == "partial"
    assert partial.source_failure_codes == ("KNOWLEDGE_SOURCE_UNAVAILABLE",)
    assert partial.candidates
    assert partial.knowledge_result is None
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        persisted = connection.execute(
            """
            SELECT status,
                   (SELECT count(*) FROM rule_evaluations
                    WHERE decision_run_id = run.id) AS legacy_evaluations,
                   (SELECT count(*) FROM private_knowledge_claim_candidates
                    WHERE decision_run_id = run.id) AS knowledge_candidates
            FROM decision_runs AS run WHERE run.id = %s
            """,
            (partial.run_id,),
        ).fetchone()
    assert persisted == {
        "status": "partial",
        "legacy_evaluations": 2,
        "knowledge_candidates": 0,
    }
