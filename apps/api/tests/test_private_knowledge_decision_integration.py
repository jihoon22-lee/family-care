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
from familycare_api.decisions.schemas import CoverageDecisionResponse
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
    convert_to_v2_advisory_publication_package,
    mutate_publication_jsonl,
    set_v2_coverage_disposition,
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
SECOND_MAPPING_ID = UUID("00000000-0000-4000-8000-000000008002")


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
    *,
    advisory: bool = False,
    user_confirmed_enrollment: bool = False,
    without_coverage_mapping: bool = False,
    additional_mapping_authority: str | None = None,
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
        if user_confirmed_enrollment:
            dict_connection.execute(
                """
                UPDATE private_knowledge_coverages
                SET enrollment_decision = 'UNKNOWN'
                WHERE import_run_id = %s AND household_space_id = %s
                """,
                (applied.run_id, seed.scope_a.household_space_id),
            )
        if without_coverage_mapping:
            dict_connection.execute(
                """
                DELETE FROM private_knowledge_coverage_terms_mappings
                WHERE import_run_id = %s
                  AND coverage_id IN (
                    SELECT id FROM private_knowledge_coverages
                    WHERE import_run_id = %s AND household_space_id = %s
                  )
                """,
                (
                    applied.run_id,
                    applied.run_id,
                    seed.scope_a.household_space_id,
                ),
            )
        if additional_mapping_authority is not None:
            if additional_mapping_authority == "NOT_APPLICABLE":
                mapping_values = (
                    True,
                    "NOT_APPLICABLE",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                    "UNKNOWN",
                )
            elif additional_mapping_authority == "NO_MATCH":
                mapping_values = (
                    False,
                    "APPLICABLE",
                    "NO_MATCH",
                    "NO_MATCH",
                    "NO_MATCH",
                    "NO_MATCH",
                    "NO_MATCH",
                )
            else:
                mapping_values = (
                    False,
                    "APPLICABLE",
                    "MATCH",
                    "MATCH",
                    "MATCH",
                    "MATCH",
                    "MATCH",
                )
            dict_connection.execute(
                """
                INSERT INTO private_knowledge_coverage_terms_mappings (
                  id, import_run_id, coverage_id, terms_section_id,
                  source_mapping_key, mapping_applicability,
                  selected_terms_source_alias,
                  selected_terms_source_alias_digest_sha256,
                  enrollment_decision, document_identity_decision,
                  edition_applicability_decision, section_mapping_decision,
                  overall_decision, reason_codes_json, executable,
                  source_record_json, source_record_digest_sha256, created_at
                )
                SELECT %s, mapping.import_run_id, mapping.coverage_id,
                       CASE WHEN %s THEN NULL ELSE mapping.terms_section_id END,
                       mapping.source_mapping_key || '-secondary', %s,
                       CASE WHEN %s THEN NULL
                            ELSE mapping.selected_terms_source_alias END,
                       CASE WHEN %s THEN NULL
                            ELSE mapping.selected_terms_source_alias_digest_sha256 END,
                       %s, %s, %s, %s, %s,
                       '[]'::jsonb, false, mapping.source_record_json,
                       mapping.source_record_digest_sha256, clock_timestamp()
                FROM private_knowledge_coverage_terms_mappings AS mapping
                WHERE mapping.import_run_id = %s
                LIMIT 1
                """,
                (
                    SECOND_MAPPING_ID,
                    mapping_values[0],
                    mapping_values[1],
                    mapping_values[0],
                    mapping_values[0],
                    *mapping_values[2:],
                    applied.run_id,
                ),
            )
    assert run is not None

    publication_root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    if advisory:
        convert_to_v2_advisory_publication_package(
            publication_root,
            include_reviewed_artifacts=True,
        )
    if user_confirmed_enrollment:
        set_v2_coverage_disposition(
            publication_root,
            disposition="ADVISORY",
            enrollment_authority="USER_CONFIRMED_COVERAGE_ENROLLMENT",
            reason_codes=["USER_CONFIRMED_COVERAGE_ENROLLMENT"],
        )

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


def test_advisory_calculation_uses_reviewed_assigned_terms_without_exact_mapping(
    database_url: str,
    tmp_path: Path,
) -> None:
    seed = _seed(database_url)
    _seed_private_publication(
        database_url,
        seed,
        tmp_path,
        advisory=True,
        without_coverage_mapping=True,
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

    result = service.analyze_medical_event(event.id)

    assert result.knowledge_result is not None, result.source_failure_codes
    candidate = result.knowledge_result.candidates[0]
    calculation = result.knowledge_result.calculations[0]
    assert candidate.result == "UNKNOWN"
    assert "COVERAGE_AUTHORITY_INCOMPLETE" in candidate.hold_reason_codes
    assert calculation.status == "CALCULATED"
    assert calculation.conditional_amount == 1
    assert calculation.confirmed_amount is None
    assert calculation.hold_reason_code == "COVERAGE_PUBLICATION_ADVISORY"


@pytest.mark.parametrize("authority", ["NO_MATCH", "NOT_APPLICABLE"])
def test_advisory_multiple_mappings_preserve_explicit_authority_rejection(
    database_url: str,
    tmp_path: Path,
    authority: str,
) -> None:
    seed = _seed(database_url)
    _seed_private_publication(
        database_url,
        seed,
        tmp_path,
        advisory=True,
        additional_mapping_authority=authority,
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

    result = service.analyze_medical_event(event.id)

    assert result.knowledge_result is not None, result.source_failure_codes
    candidate = result.knowledge_result.candidates[0]
    calculation = result.knowledge_result.calculations[0]
    assert candidate.result == "NO_MATCH"
    assert "COVERAGE_AUTHORITY_NO_MATCH" in candidate.hold_reason_codes
    assert calculation.status == "NOT_APPLICABLE"
    assert calculation.conditional_amount is None
    assert calculation.confirmed_amount is None


def test_published_multiple_exact_mappings_remain_executable(
    database_url: str,
    tmp_path: Path,
) -> None:
    seed = _seed(database_url)
    _seed_private_publication(
        database_url,
        seed,
        tmp_path,
        additional_mapping_authority="MATCH",
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

    result = service.analyze_medical_event(event.id)

    assert result.knowledge_result is not None, result.source_failure_codes
    assert result.knowledge_result.candidates[0].result == "MATCH"
    calculation = result.knowledge_result.calculations[0]
    assert calculation.status == "CALCULATED"
    assert calculation.conditional_amount == 1


def test_user_confirmation_admits_raw_unknown_without_overriding_mapping_authority(
    database_url: str,
    tmp_path: Path,
) -> None:
    seed = _seed(database_url)
    knowledge_run_id, _ = _seed_private_publication(
        database_url,
        seed,
        tmp_path,
        advisory=True,
        user_confirmed_enrollment=True,
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
    admitted = service.analyze_medical_event(event.id)

    assert admitted.knowledge_result is not None, admitted.source_failure_codes
    admitted_candidate = admitted.knowledge_result.candidates[0]
    admitted_calculation = admitted.knowledge_result.calculations[0]
    assert admitted_candidate.result == "UNKNOWN"
    assert "COVERAGE_PUBLICATION_ADVISORY" in admitted_candidate.hold_reason_codes
    assert admitted_calculation.status == "CALCULATED"
    assert admitted_calculation.conditional_amount == 1
    assert admitted_calculation.confirmed_amount is None

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            UPDATE private_knowledge_coverage_terms_mappings
            SET mapping_applicability = 'UNKNOWN',
                enrollment_decision = 'UNKNOWN',
                document_identity_decision = 'UNKNOWN',
                edition_applicability_decision = 'UNKNOWN',
                section_mapping_decision = 'UNKNOWN',
                overall_decision = 'UNKNOWN'
            WHERE import_run_id = %s
            """,
            (knowledge_run_id,),
        )

    mapping_unknown = service.analyze_medical_event(event.id)

    assert mapping_unknown.knowledge_result is not None
    unknown_candidate = mapping_unknown.knowledge_result.candidates[0]
    unknown_calculation = mapping_unknown.knowledge_result.calculations[0]
    assert unknown_candidate.result == "UNKNOWN"
    assert "COVERAGE_PUBLICATION_ADVISORY" in unknown_candidate.hold_reason_codes
    assert "COVERAGE_AUTHORITY_INCOMPLETE" in unknown_candidate.hold_reason_codes
    assert unknown_calculation.status == "CALCULATED"
    assert unknown_calculation.conditional_amount == 1
    assert unknown_calculation.confirmed_amount is None
    assert unknown_calculation.hold_reason_code == "COVERAGE_PUBLICATION_ADVISORY"


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
    assert combined.status == "succeeded", combined.source_failure_codes
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
    wire = CoverageDecisionResponse.from_value(combined).model_dump(mode="json")
    assert wire["schema_version"] == "2"
    assert {item["source"]["kind"] for item in wire["candidates"]} == {
        "OPERATIONAL_RIDER",
        "PRIVATE_KNOWLEDGE_COVERAGE",
    }
    private_candidate = next(
        item
        for item in wire["candidates"]
        if item["source"]["kind"] == "PRIVATE_KNOWLEDGE_COVERAGE"
    )
    assert private_candidate["claim_start_ready"] is False
    assert private_candidate["calculation"]["conditional_amount"] == "1"
    assert wire["conditional_fixed_subtotals"][0]["amount"] == "1"
    assert "provider_request_id" not in str(wire)

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
    assert CoverageDecisionResponse.from_value(unavailable).analysis_completeness == "UNAVAILABLE"

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
    assert CoverageDecisionResponse.from_value(partial).analysis_completeness == "UNAVAILABLE"
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
