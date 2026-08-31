"""PostgreSQL downgrade safety proof for advisory publication history."""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import DBAPIError

from apps.api.tests.test_analysis_assistance_migration_integration import (
    CITATION_ID,
    CLAUSE_ID,
    FACT_ID,
    RECOMMENDATION_ID,
    REVIEW_ID,
    SECTION_ID,
    _insert_recommendation,
    _seed_assistance_foundation,
    _seed_source_projection,
)
from apps.api.tests.test_private_knowledge_decision_migration_integration import (
    CALCULATION_ID,
    CALCULATION_PUBLICATION_ID,
    CONTRACT_ID,
    COVERAGE_ID,
    DECISION_RUN_ID,
    HOUSEHOLD_ID,
    RULE_PUBLICATION_ID,
    RULE_RUN_ID,
    RUN_ID,
    USER_ID,
    _insert_decision_results,
    _seed_decision_foundation,
)
from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
OTHER_SAME_HOUSEHOLD_USER_ID = "00000000-0000-4000-8000-000000003901"
RULE_CITATION_ID = "00000000-0000-4000-8000-000000003902"
CALCULATION_CITATION_ID = "00000000-0000-4000-8000-000000003903"
STATUS_INTERVAL_ID = "00000000-0000-4000-8000-000000003904"
NORMALIZER_ID = "00000000-0000-4000-8000-000000003905"
IMMUTABLE_TRIGGER_NAMES = (
    "trg_private_knowledge_dispositions_immutable",
    "trg_private_knowledge_status_intervals_immutable",
    "trg_private_knowledge_fact_normalizers_immutable",
    "trg_private_knowledge_rule_publications_immutable",
    "trg_private_knowledge_rule_citations_immutable",
    "trg_private_knowledge_calculation_publications_immutable",
    "trg_private_knowledge_calculation_citations_immutable",
)


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _reset_safe_database() -> None:
    with psycopg.connect(_database_url()) as connection:
        row = connection.execute("SELECT current_database()").fetchone()
        assert row is not None
        assert is_safe_integration_database_name(str(row[0]))
        connection.execute("TRUNCATE TABLE household_spaces, documents RESTART IDENTITY CASCADE")


def _publication_trigger_names() -> tuple[str, ...]:
    with psycopg.connect(_database_url()) as connection:
        rows = connection.execute(
            """
            SELECT tgname
            FROM pg_trigger
            WHERE NOT tgisinternal
              AND tgname = ANY(%s::text[])
            ORDER BY tgname
            """,
            (list(IMMUTABLE_TRIGGER_NAMES),),
        ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _assert_publication_mutation_rejected(sql: str, parameters: tuple[object, ...]) -> None:
    with psycopg.connect(_database_url()) as connection:
        try:
            connection.execute(sql, parameters)
        except psycopg.errors.ObjectNotInPrerequisiteState:
            connection.rollback()
            return
        connection.rollback()
    pytest.fail("private publication mutation was not rejected")


def test_publication_rows_allow_insert_but_reject_update_and_delete() -> None:
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    command.upgrade(config, "0023_advisory_disposition")
    _reset_safe_database()
    try:
        with psycopg.connect(_database_url()) as connection:
            _seed_decision_foundation(connection)
            connection.execute(
                """
                INSERT INTO private_knowledge_contract_status_intervals (
                  id, rule_import_run_id, import_run_id, household_space_id,
                  knowledge_contract_id, decision, confirmed_status,
                  effective_from, effective_through, authority, reason_code,
                  review_state, confirmed_by, confirmed_at,
                  interval_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, %s, 'MATCH', 'active',
                  DATE '2026-01-01', DATE '2026-12-31',
                  'USER_CONFIRMED_EVENT_DATE', 'SYNTHETIC_STATUS_MATCH',
                  'USER_CONFIRMED', %s, clock_timestamp(), %s
                )
                """,
                (
                    STATUS_INTERVAL_ID,
                    RULE_RUN_ID,
                    RUN_ID,
                    HOUSEHOLD_ID,
                    CONTRACT_ID,
                    USER_ID,
                    "5" * 64,
                ),
            )
            connection.execute(
                """
                INSERT INTO private_knowledge_fact_normalizer_publications (
                  id, rule_import_run_id, knowledge_import_run_id,
                  household_space_id, field_path, normalized_tokens_json,
                  normalized_value_json, match_kind, priority, review_state,
                  reviewed_by, reviewed_at, normalizer_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, 'MedicalEvent.synthetic_category',
                  '["synthetic", "category"]'::jsonb, '"sample"'::jsonb,
                  'EXACT_TOKEN_SEQUENCE', 1, 'USER_CONFIRMED',
                  %s, clock_timestamp(), %s
                )
                """,
                (
                    NORMALIZER_ID,
                    RULE_RUN_ID,
                    RUN_ID,
                    HOUSEHOLD_ID,
                    USER_ID,
                    "6" * 64,
                ),
            )

        parent_mutations = (
            (
                "private_knowledge_coverage_execution_dispositions",
                "id",
                "00000000-0000-4000-8000-000000003010",
                "reason_codes_json = '[\"SYNTHETIC_IMMUTABILITY_CHECK\"]'::jsonb",
            ),
            (
                "private_knowledge_rule_publications",
                "id",
                str(RULE_PUBLICATION_ID),
                "result_reason_code = 'SYNTHETIC_RULE_CHANGED'",
            ),
            (
                "private_knowledge_calculation_publications",
                "id",
                str(CALCULATION_PUBLICATION_ID),
                "result_reason_code = 'SYNTHETIC_CALCULATION_CHANGED'",
            ),
            (
                "private_knowledge_contract_status_intervals",
                "id",
                STATUS_INTERVAL_ID,
                "reason_code = 'SYNTHETIC_STATUS_CHANGED'",
            ),
            (
                "private_knowledge_fact_normalizer_publications",
                "id",
                NORMALIZER_ID,
                "priority = 2",
            ),
        )
        for table, key, row_id, assignment in parent_mutations:
            _assert_publication_mutation_rejected(
                f"UPDATE {table} SET {assignment} WHERE {key} = %s",
                (row_id,),
            )
            _assert_publication_mutation_rejected(
                f"DELETE FROM {table} WHERE {key} = %s",
                (row_id,),
            )

        with psycopg.connect(_database_url()) as connection:
            _seed_source_projection(
                connection,
                run_id=RUN_ID,
                section_id=SECTION_ID,
                clause_id=CLAUSE_ID,
                review_id=REVIEW_ID,
                fact_id=FACT_ID,
                citation_id=CITATION_ID,
                suffix="3",
            )
            connection.execute(
                """
                INSERT INTO private_knowledge_rule_citations (
                  id, rule_publication_id, rule_import_run_id,
                  knowledge_import_run_id, household_space_id, terms_section_id,
                  source_clause_id, fact_id, citation_key, evidence_purpose,
                  page_start, page_end, source_text_sha256, citation_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  'synthetic-rule-citation', 'ELIGIBILITY', 2, 2, %s, %s
                )
                """,
                (
                    RULE_CITATION_ID,
                    RULE_PUBLICATION_ID,
                    RULE_RUN_ID,
                    RUN_ID,
                    HOUSEHOLD_ID,
                    SECTION_ID,
                    CLAUSE_ID,
                    FACT_ID,
                    "1" * 64,
                    "2" * 64,
                ),
            )
            connection.execute(
                """
                INSERT INTO private_knowledge_calculation_citations (
                  id, calculation_publication_id, rule_import_run_id,
                  knowledge_import_run_id, household_space_id, terms_section_id,
                  source_clause_id, fact_id, citation_key, evidence_purpose,
                  page_start, page_end, source_text_sha256, citation_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, %s,
                  'synthetic-calculation-citation', 'AMOUNT', 2, 2, %s, %s
                )
                """,
                (
                    CALCULATION_CITATION_ID,
                    CALCULATION_PUBLICATION_ID,
                    RULE_RUN_ID,
                    RUN_ID,
                    HOUSEHOLD_ID,
                    SECTION_ID,
                    CLAUSE_ID,
                    FACT_ID,
                    "3" * 64,
                    "4" * 64,
                ),
            )

        citation_mutations = (
            ("private_knowledge_rule_citations", RULE_CITATION_ID),
            ("private_knowledge_calculation_citations", CALCULATION_CITATION_ID),
        )
        for table, row_id in citation_mutations:
            _assert_publication_mutation_rejected(
                f"UPDATE {table} SET page_end = page_end + 1 WHERE id = %s",
                (row_id,),
            )
            _assert_publication_mutation_rejected(
                f"DELETE FROM {table} WHERE id = %s",
                (row_id,),
            )
    finally:
        _reset_safe_database()


def test_downgrade_removes_and_upgrade_restores_publication_mutation_triggers() -> None:
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    command.upgrade(config, "0023_advisory_disposition")
    _reset_safe_database()
    try:
        assert _publication_trigger_names() == tuple(sorted(IMMUTABLE_TRIGGER_NAMES))

        command.downgrade(config, "0022_analysis_assistance")
        assert _publication_trigger_names() == ()
        with psycopg.connect(_database_url()) as connection:
            function = connection.execute(
                "SELECT to_regprocedure('reject_private_knowledge_publication_mutation()')"
            ).fetchone()
            assert function == (None,)

        command.upgrade(config, "0023_advisory_disposition")
        assert _publication_trigger_names() == tuple(sorted(IMMUTABLE_TRIGGER_NAMES))
    finally:
        command.upgrade(config, "0023_advisory_disposition")
        _reset_safe_database()


def test_clean_downgrade_round_trip_and_v2_history_fail_closed() -> None:
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    command.upgrade(config, "0023_advisory_disposition")
    _reset_safe_database()
    try:
        command.downgrade(config, "0022_analysis_assistance")
        with psycopg.connect(_database_url()) as connection:
            column = connection.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'decision_runs'
                  AND column_name = 'knowledge_advisory_coverage_count'
                """
            ).fetchone()
            assert column is None

        command.upgrade(config, "0023_advisory_disposition")
        with psycopg.connect(_database_url()) as connection:
            _seed_decision_foundation(connection)
            _insert_decision_results(connection)
            connection.execute(
                """
                UPDATE decision_runs
                SET knowledge_benefit_coverage_count = 1,
                    knowledge_advisory_coverage_count = 1
                WHERE id = %s
                """,
                (DECISION_RUN_ID,),
            )
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(
                    """
                    UPDATE private_knowledge_benefit_calculations
                    SET hold_reason_code = 'HUMAN_REVIEW_REQUIRED'
                    WHERE id = %s
                    """,
                    (CALCULATION_ID,),
                )
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(
                    """
                    UPDATE private_knowledge_benefit_calculations
                    SET calculation_status = 'UNKNOWN', conditional_amount = NULL
                    WHERE id = %s
                    """,
                    (CALCULATION_ID,),
                )

        with pytest.raises(DBAPIError, match="cannot downgrade advisory disposition"):
            command.downgrade(config, "0022_analysis_assistance")

        with psycopg.connect(_database_url()) as connection:
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            counts = connection.execute(
                """
                SELECT knowledge_benefit_coverage_count,
                       knowledge_advisory_coverage_count
                FROM decision_runs
                WHERE id = %s
                """,
                (DECISION_RUN_ID,),
            ).fetchone()
            assert version == ("0023_advisory_disposition",)
            assert counts == (1, 1)
            assert _publication_trigger_names() == tuple(sorted(IMMUTABLE_TRIGGER_NAMES))
    finally:
        command.upgrade(config, "0023_advisory_disposition")
        _reset_safe_database()


def test_postgresql_enforces_user_confirmed_enrollment_authority_matrix() -> None:
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    command.upgrade(config, "0023_advisory_disposition")
    _reset_safe_database()
    try:
        command.downgrade(config, "0022_analysis_assistance")
        with psycopg.connect(_database_url()) as connection:
            _seed_decision_foundation(connection)
            connection.execute(
                """
                DELETE FROM private_knowledge_coverage_execution_dispositions
                WHERE rule_import_run_id = %s AND knowledge_coverage_id = %s
                """,
                (RULE_RUN_ID, COVERAGE_ID),
            )
            connection.execute(
                """
                UPDATE private_knowledge_coverages
                SET enrollment_decision = 'UNKNOWN'
                WHERE id = %s AND import_run_id = %s
                """,
                (COVERAGE_ID, RUN_ID),
            )
            connection.execute(
                """
                INSERT INTO app_users (
                  id, household_space_id, username, display_name, password_hash
                ) VALUES (
                  %s, %s, 'synthetic-authority-other', 'Admin C',
                  '$argon2id$synthetic'
                )
                """,
                (OTHER_SAME_HOUSEHOLD_USER_ID, HOUSEHOLD_ID),
            )

        command.upgrade(config, "0023_advisory_disposition")
        with psycopg.connect(_database_url()) as connection:
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                connection.execute(
                    """
                    INSERT INTO private_knowledge_coverage_execution_dispositions (
                      id, rule_import_run_id, knowledge_import_run_id,
                      household_space_id, knowledge_coverage_id, disposition,
                      reason_codes_json, enrollment_decision_snapshot
                    ) VALUES (
                      gen_random_uuid(), %s, %s, %s, %s, 'ADVISORY',
                      '[]'::jsonb, 'UNKNOWN'
                    )
                    """,
                    (RULE_RUN_ID, RUN_ID, HOUSEHOLD_ID, COVERAGE_ID),
                )

            def insert_user_authority(
                *,
                snapshot: str = "UNKNOWN",
                reason_codes: str = '["USER_CONFIRMED_COVERAGE_ENROLLMENT"]',
                reason_code: str | None = "USER_CONFIRMED_COVERAGE_ENROLLMENT",
                confirmer: object = USER_ID,
            ) -> None:
                connection.execute(
                    """
                    INSERT INTO private_knowledge_coverage_execution_dispositions (
                      id, rule_import_run_id, knowledge_import_run_id,
                      household_space_id, knowledge_coverage_id, disposition,
                      reason_codes_json, enrollment_decision_snapshot,
                      enrollment_authority, enrollment_reason_code,
                      enrollment_confirmed_by
                    ) VALUES (
                      gen_random_uuid(), %s, %s, %s, %s, 'ADVISORY',
                      %s::jsonb, %s, 'USER_CONFIRMED_COVERAGE_ENROLLMENT',
                      %s, %s
                    )
                    """,
                    (
                        RULE_RUN_ID,
                        RUN_ID,
                        HOUSEHOLD_ID,
                        COVERAGE_ID,
                        reason_codes,
                        snapshot,
                        reason_code,
                        confirmer,
                    ),
                )

            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                insert_user_authority(reason_codes="[]")
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                insert_user_authority(reason_code=None)
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                insert_user_authority(confirmer=None)
            with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
                insert_user_authority(snapshot="NO_MATCH")
            with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
                insert_user_authority(confirmer=OTHER_SAME_HOUSEHOLD_USER_ID)

            insert_user_authority()
            stored = connection.execute(
                """
                SELECT enrollment_decision_snapshot, enrollment_authority,
                       enrollment_reason_code, enrollment_confirmed_by
                FROM private_knowledge_coverage_execution_dispositions
                WHERE rule_import_run_id = %s AND knowledge_coverage_id = %s
                """,
                (RULE_RUN_ID, COVERAGE_ID),
            ).fetchone()
            assert stored == (
                "UNKNOWN",
                "USER_CONFIRMED_COVERAGE_ENROLLMENT",
                "USER_CONFIRMED_COVERAGE_ENROLLMENT",
                USER_ID,
            )
    finally:
        command.upgrade(config, "0023_advisory_disposition")
        _reset_safe_database()


def test_v1_history_backfills_exact_lineage_and_cleanly_round_trips() -> None:
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    _reset_safe_database()
    try:
        command.downgrade(config, "0022_analysis_assistance")
        with psycopg.connect(_database_url()) as connection:
            _seed_assistance_foundation(connection)
            _insert_recommendation(connection, RECOMMENDATION_ID)

        command.upgrade(config, "0023_advisory_disposition")
        with psycopg.connect(_database_url()) as connection:
            lineage = connection.execute(
                """
                SELECT recommendation.enrollment_decision_snapshot,
                       recommendation.enrollment_authority_snapshot,
                       recommendation.coverage_execution_disposition_id,
                       disposition.enrollment_decision_snapshot,
                       disposition.enrollment_authority
                FROM analysis_recommendations AS recommendation
                JOIN private_knowledge_coverage_execution_dispositions AS disposition
                  ON disposition.id =
                     recommendation.coverage_execution_disposition_id
                WHERE recommendation.id = %s
                """,
                (RECOMMENDATION_ID,),
            ).fetchone()
            assert lineage is not None
            assert lineage[0:2] == ("MATCH", "CERTIFICATE_SNAPSHOT")
            assert lineage[2] is not None
            assert lineage[3:5] == ("MATCH", "CERTIFICATE_SNAPSHOT")
            with (
                pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState),
                connection.transaction(),
            ):
                connection.execute(
                    """
                    UPDATE analysis_recommendations
                    SET rank = 2
                    WHERE id = %s
                    """,
                    (RECOMMENDATION_ID,),
                )

        command.downgrade(config, "0022_analysis_assistance")
        with psycopg.connect(_database_url()) as connection:
            version = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            lineage_column = connection.execute(
                """
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'analysis_recommendations'
                  AND column_name = 'coverage_execution_disposition_id'
                """
            ).fetchone()
            recommendation = connection.execute(
                """
                SELECT enrollment_decision_snapshot
                FROM analysis_recommendations
                WHERE id = %s
                """,
                (RECOMMENDATION_ID,),
            ).fetchone()
            assert version == ("0022_analysis_assistance",)
            assert lineage_column is None
            assert recommendation == ("MATCH",)
    finally:
        command.upgrade(config, "0023_advisory_disposition")
        _reset_safe_database()
