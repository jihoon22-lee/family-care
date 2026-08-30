"""PostgreSQL enforcement proof for private-knowledge decision results."""

from __future__ import annotations

import os
from decimal import Decimal
from uuid import UUID

import psycopg
import pytest

from apps.api.tests.test_private_knowledge_publication_migration_integration import (
    CONTRACT_ID,
    COVERAGE_ID,
    HOUSEHOLD_ID,
    OTHER_CONTRACT_ID,
    OTHER_COVERAGE_ID,
    RULE_RUN_ID,
    RUN_ID,
    USER_ID,
    _insert_foundation,
)
from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

MEMBER_ID = UUID("00000000-0000-4000-8000-000000003001")
EVENT_ID = UUID("00000000-0000-4000-8000-000000003002")
DECISION_RUN_ID = UUID("00000000-0000-4000-8000-000000003003")
RULE_PUBLICATION_ID = UUID("00000000-0000-4000-8000-000000003004")
CALCULATION_PUBLICATION_ID = UUID("00000000-0000-4000-8000-000000003005")
EVALUATION_ID = UUID("00000000-0000-4000-8000-000000003006")
CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000003007")
CALCULATION_ID = UUID("00000000-0000-4000-8000-000000003008")
STEP_ID = UUID("00000000-0000-4000-8000-000000003009")


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _seed_decision_foundation(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    _insert_foundation(connection)
    connection.execute(
        """
        INSERT INTO family_members (
          id, household_space_id, display_name, internal_alias
        ) VALUES (%s, %s, 'Family Member A', 'synthetic-member-a')
        """,
        (MEMBER_ID, HOUSEHOLD_ID),
    )
    connection.execute(
        """
        INSERT INTO medical_events (
          id, household_space_id, family_member_id, mode,
          event_date, facts_json, confirmation_json, situation_text
        ) VALUES (
          %s, %s, %s, 'post_treatment', DATE '2026-08-30',
          '{}'::jsonb, '{}'::jsonb, 'synthetic situation'
        )
        """,
        (EVENT_ID, HOUSEHOLD_ID, MEMBER_ID),
    )
    connection.execute(
        """
        INSERT INTO decision_runs (
          id, household_space_id, medical_event_id, engine_version,
          rule_set_version, event_version, policy_snapshot_at, status, stale,
          knowledge_import_run_id, knowledge_rule_import_run_id,
          knowledge_status_projection_digest, event_fact_schema_version,
          analysis_completeness, source_failure_codes_json
        ) VALUES (
          %s, %s, %s, 'synthetic-engine-v1', 'synthetic-rules-v1', 1,
          clock_timestamp(), 'succeeded', false, %s, %s, %s,
          'medical-event-facts.v2', 'COMPLETE', '[]'::jsonb
        )
        """,
        (
            DECISION_RUN_ID,
            HOUSEHOLD_ID,
            EVENT_ID,
            RUN_ID,
            RULE_RUN_ID,
            "b" * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_coverage_execution_dispositions (
          id, rule_import_run_id, knowledge_import_run_id,
          household_space_id, knowledge_coverage_id, disposition,
          reason_codes_json
        ) VALUES (
          %s, %s, %s, %s, %s, 'PUBLISHED', '[]'::jsonb
        )
        """,
        (
            UUID("00000000-0000-4000-8000-000000003010"),
            RULE_RUN_ID,
            RUN_ID,
            HOUSEHOLD_ID,
            COVERAGE_ID,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_rule_publications (
          id, rule_import_run_id, knowledge_import_run_id,
          household_space_id, knowledge_coverage_id, rule_key,
          rule_kind, schema_version, required, rule_json,
          result_reason_code, review_state, reviewed_by, reviewed_at,
          rule_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, 'synthetic-rule-001', 'eligibility',
          'coverage-rule-dsl.v1', true, '{}'::jsonb, 'SYNTHETIC_MATCH',
          'USER_CONFIRMED', %s, clock_timestamp(), %s
        )
        """,
        (
            RULE_PUBLICATION_ID,
            RULE_RUN_ID,
            RUN_ID,
            HOUSEHOLD_ID,
            COVERAGE_ID,
            USER_ID,
            "f" * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_calculation_publications (
          id, rule_import_run_id, knowledge_import_run_id,
          household_space_id, knowledge_coverage_id, calculation_key,
          calculation_kind, schema_version, calculation_json,
          result_reason_code, review_state, reviewed_by, reviewed_at,
          calculation_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, 'synthetic-calculation-001', 'FIXED',
          'benefit-calculation-dsl.v1', '{}'::jsonb, 'SYNTHETIC_CALCULATION',
          'USER_CONFIRMED', %s, clock_timestamp(), %s
        )
        """,
        (
            CALCULATION_PUBLICATION_ID,
            RULE_RUN_ID,
            RUN_ID,
            HOUSEHOLD_ID,
            COVERAGE_ID,
            USER_ID,
            "9" * 64,
        ),
    )


def _insert_decision_results(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    connection.execute(
        """
        INSERT INTO private_knowledge_rule_evaluations (
          id, household_space_id, decision_run_id, knowledge_import_run_id,
          knowledge_rule_import_run_id, knowledge_coverage_id,
          rule_publication_id, result, required, reason_code,
          fact_paths_json, missing_fields_json, conflicting_fields_json,
          citation_snapshot_json, evaluator_version
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, 'MATCH', true,
          'SYNTHETIC_MATCH', '[]'::jsonb, '[]'::jsonb, '[]'::jsonb,
          '[]'::jsonb, 'synthetic-evaluator-v1'
        )
        """,
        (
            EVALUATION_ID,
            HOUSEHOLD_ID,
            DECISION_RUN_ID,
            RUN_ID,
            RULE_RUN_ID,
            COVERAGE_ID,
            RULE_PUBLICATION_ID,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_claim_candidates (
          id, household_space_id, decision_run_id, knowledge_import_run_id,
          knowledge_rule_import_run_id, knowledge_contract_id,
          knowledge_coverage_id, contract_label_snapshot,
          coverage_label_snapshot, benefit_type, aggregate_result,
          required_match_count, required_unknown_count,
          required_no_match_count, questions_json, hold_reason_codes_json,
          claim_start_ready
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, 'Sample Policy', 'Sample Coverage',
          'FIXED', 'MATCH', 1, 0, 0, '[]'::jsonb, '[]'::jsonb, false
        )
        """,
        (
            CANDIDATE_ID,
            HOUSEHOLD_ID,
            DECISION_RUN_ID,
            RUN_ID,
            RULE_RUN_ID,
            CONTRACT_ID,
            COVERAGE_ID,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_benefit_calculations (
          id, household_space_id, decision_run_id, private_claim_candidate_id,
          knowledge_import_run_id, knowledge_rule_import_run_id,
          knowledge_coverage_id, calculation_publication_id,
          calculation_kind, calculation_status, currency, confirmed_amount,
          conditional_amount, excluded_amount, deductible_amount, applied_rate,
          applied_limit, rounding_rule, trace_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, 'FIXED', 'CALCULATED',
          'KRW', %s, %s, 0, 0, 1, %s, 'NONE', %s
        )
        """,
        (
            CALCULATION_ID,
            HOUSEHOLD_ID,
            DECISION_RUN_ID,
            CANDIDATE_ID,
            RUN_ID,
            RULE_RUN_ID,
            COVERAGE_ID,
            CALCULATION_PUBLICATION_ID,
            Decimal("10.0000"),
            Decimal("10.0000"),
            Decimal("10.0000"),
            "8" * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_calculation_steps (
          id, private_benefit_calculation_id, step_number, operation,
          input_amount, input_currency, output_amount, output_currency,
          rounding_rule, reason_code
        ) VALUES (
          %s, %s, 1, 'FIXED_AMOUNT', %s, 'KRW', %s, 'KRW',
          'NONE', 'SYNTHETIC_FIXED_AMOUNT'
        )
        """,
        (STEP_ID, CALCULATION_ID, Decimal("10.0000"), Decimal("10.0000")),
    )


def test_postgresql_enforces_decision_scope_uniqueness_and_amount_bounds() -> None:
    with psycopg.connect(_database_url()) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()
        assert database_name is not None
        assert is_safe_integration_database_name(str(database_name[0]))
        _seed_decision_foundation(connection)
        _insert_decision_results(connection)

        stored = connection.execute(
            """
            SELECT c.aggregate_result, b.calculation_status, b.conditional_amount,
                   s.step_number
            FROM private_knowledge_claim_candidates AS c
            JOIN private_knowledge_benefit_calculations AS b
              ON b.private_claim_candidate_id = c.id
            JOIN private_knowledge_calculation_steps AS s
              ON s.private_benefit_calculation_id = b.id
            WHERE c.id = %s
            """,
            (CANDIDATE_ID,),
        ).fetchone()
        assert stored == ("MATCH", "CALCULATED", Decimal("10.0000"), 1)

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO private_knowledge_claim_candidates (
                  id, household_space_id, decision_run_id, knowledge_import_run_id,
                  knowledge_rule_import_run_id, knowledge_contract_id,
                  knowledge_coverage_id, contract_label_snapshot,
                  coverage_label_snapshot, benefit_type, aggregate_result,
                  required_match_count, required_unknown_count,
                  required_no_match_count, questions_json, hold_reason_codes_json,
                  claim_start_ready
                ) SELECT
                  %s, household_space_id, decision_run_id, knowledge_import_run_id,
                  knowledge_rule_import_run_id, knowledge_contract_id,
                  knowledge_coverage_id, contract_label_snapshot,
                  coverage_label_snapshot, benefit_type, aggregate_result,
                  required_match_count, required_unknown_count,
                  required_no_match_count, questions_json, hold_reason_codes_json,
                  claim_start_ready
                FROM private_knowledge_claim_candidates WHERE id = %s
                """,
                (
                    UUID("00000000-0000-4000-8000-000000003011"),
                    CANDIDATE_ID,
                ),
            )

        with (
            pytest.raises(
                psycopg.errors.ForeignKeyViolation,
            ),
            connection.transaction(),
        ):
            connection.execute(
                """
                INSERT INTO private_knowledge_claim_candidates (
                  id, household_space_id, decision_run_id, knowledge_import_run_id,
                  knowledge_rule_import_run_id, knowledge_contract_id,
                  knowledge_coverage_id, contract_label_snapshot,
                  coverage_label_snapshot, benefit_type, aggregate_result,
                  required_match_count, required_unknown_count,
                  required_no_match_count, questions_json, hold_reason_codes_json,
                  claim_start_ready
                ) VALUES (
                  %s, %s, %s, %s, %s, %s, %s, 'Sample Policy',
                  'Sample Coverage', 'FIXED', 'UNKNOWN', 0, 1, 0,
                  '[]'::jsonb, '[]'::jsonb, false
                )
                """,
                (
                    UUID("00000000-0000-4000-8000-000000003012"),
                    HOUSEHOLD_ID,
                    DECISION_RUN_ID,
                    RUN_ID,
                    RULE_RUN_ID,
                    OTHER_CONTRACT_ID,
                    OTHER_COVERAGE_ID,
                ),
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                UPDATE private_knowledge_claim_candidates
                SET claim_start_ready = true
                WHERE id = %s
                """,
                (CANDIDATE_ID,),
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                UPDATE private_knowledge_benefit_calculations
                SET confirmed_amount = -1
                WHERE id = %s
                """,
                (CALCULATION_ID,),
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                UPDATE private_knowledge_benefit_calculations
                SET applied_rate = 1.000001
                WHERE id = %s
                """,
                (CALCULATION_ID,),
            )

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO private_knowledge_calculation_steps (
                  id, private_benefit_calculation_id, step_number, operation,
                  reason_code
                ) VALUES (%s, %s, 1, 'FIXED_AMOUNT', 'SYNTHETIC_DUPLICATE')
                """,
                (
                    UUID("00000000-0000-4000-8000-000000003013"),
                    CALCULATION_ID,
                ),
            )
