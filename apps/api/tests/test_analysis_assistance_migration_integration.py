"""PostgreSQL enforcement proof for scoped analysis assistance."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import psycopg
import pytest
from alembic import command
from alembic.config import Config

from apps.api.tests.test_private_knowledge_decision_migration_integration import (
    CANDIDATE_ID,
    DECISION_RUN_ID,
    EVENT_ID,
    _insert_decision_results,
    _seed_decision_foundation,
)
from apps.api.tests.test_private_knowledge_publication_migration_integration import (
    COVERAGE_ID,
    HOUSEHOLD_ID,
    OTHER_COVERAGE_ID,
    OTHER_RUN_ID,
    RUN_ID,
)
from scripts.integration_test_database import is_safe_integration_database_name

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]
SECTION_ID = UUID("00000000-0000-4000-8000-000000003101")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000003102")
REVIEW_ID = UUID("00000000-0000-4000-8000-000000003103")
FACT_ID = UUID("00000000-0000-4000-8000-000000003104")
CITATION_ID = UUID("00000000-0000-4000-8000-000000003105")
SECOND_CITATION_ID = UUID("00000000-0000-4000-8000-000000003116")
OTHER_SECTION_ID = UUID("00000000-0000-4000-8000-000000003106")
OTHER_CLAUSE_ID = UUID("00000000-0000-4000-8000-000000003107")
OTHER_REVIEW_ID = UUID("00000000-0000-4000-8000-000000003108")
OTHER_FACT_ID = UUID("00000000-0000-4000-8000-000000003109")
OTHER_CITATION_ID = UUID("00000000-0000-4000-8000-000000003110")
JOB_ID = UUID("00000000-0000-4000-8000-000000003111")
ASSISTANCE_RUN_ID = UUID("00000000-0000-4000-8000-000000003112")
RECOMMENDATION_ID = UUID("00000000-0000-4000-8000-000000003113")
DIGEST = "a" * 64


def _database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _assert_safe_database(connection: psycopg.Connection[object]) -> None:
    row = connection.execute("SELECT current_database()").fetchone()
    assert row is not None
    assert is_safe_integration_database_name(str(row[0]))


def _seed_source_projection(
    connection: psycopg.Connection[tuple[object, ...]],
    *,
    run_id: UUID,
    section_id: UUID,
    clause_id: UUID,
    review_id: UUID,
    fact_id: UUID,
    citation_id: UUID,
    suffix: str,
) -> None:
    connection.execute(
        """
        INSERT INTO private_knowledge_terms_sections (
          id, import_run_id, source_section_key, terms_source_alias,
          terms_source_alias_digest_sha256, section_kind, heading,
          page_start, page_end, review_state, source_record_json,
          source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, 'BENEFIT', %s, 2, 3,
          'DIRECT_REVIEWED', '{}'::jsonb, %s
        )
        """,
        (
            section_id,
            run_id,
            f"synthetic-section-{suffix}",
            f"Synthetic Terms {suffix}",
            suffix * 64,
            f"Sample section {suffix}",
            suffix * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_source_clauses (
          id, import_run_id, terms_section_id, source_clause_key,
          clause_label, title, page_start, page_end, source_text_sha256,
          review_state, source_record_json, source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, %s, 2, 2, %s,
          'DIRECT_REVIEWED', '{}'::jsonb, %s
        )
        """,
        (
            clause_id,
            run_id,
            section_id,
            f"synthetic-clause-{suffix}",
            f"Sample clause {suffix}",
            f"Sample title {suffix}",
            suffix * 64,
            suffix * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_semantic_reviews (
          id, import_run_id, terms_section_id, source_review_key,
          section_summary, analysis_status, confidence, review_state,
          found_categories_json, missing_categories_json, warnings_json,
          source_clause_count, classified_clause_count, unclassified_clause_count,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, 'complete', 'high', 'DIRECT_REVIEWED',
          '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, 1, 1, 0,
          '{}'::jsonb, %s
        )
        """,
        (
            review_id,
            run_id,
            section_id,
            f"synthetic-review-{suffix}",
            f"Sample reviewed summary {suffix}",
            suffix * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_facts (
          id, import_run_id, terms_section_id, semantic_review_id,
          source_fact_key, fact_type, statement, conditions_json,
          numeric_terms_json, review_state, executable,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, %s, 'PAYMENT_TRIGGER', %s,
          '{}'::jsonb, '[]'::jsonb, 'DIRECT_REVIEWED', false,
          '{}'::jsonb, %s
        )
        """,
        (
            fact_id,
            run_id,
            section_id,
            review_id,
            f"synthetic-fact-{suffix}",
            f"Sample reviewed trigger {suffix}",
            suffix * 64,
        ),
    )
    connection.execute(
        """
        INSERT INTO private_knowledge_fact_citations (
          id, import_run_id, fact_id, source_clause_id, citation_ordinal,
          page_start, page_end, source_text_sha256, locator_json,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %s, %s, %s, %s, 1, 2, 2, %s, '{}'::jsonb, '{}'::jsonb, %s
        )
        """,
        (citation_id, run_id, fact_id, clause_id, suffix * 64, suffix * 64),
    )


def _seed_assistance_foundation(
    connection: psycopg.Connection[tuple[object, ...]],
) -> None:
    _seed_decision_foundation(connection)
    _insert_decision_results(connection)
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
    _seed_source_projection(
        connection,
        run_id=OTHER_RUN_ID,
        section_id=OTHER_SECTION_ID,
        clause_id=OTHER_CLAUSE_ID,
        review_id=OTHER_REVIEW_ID,
        fact_id=OTHER_FACT_ID,
        citation_id=OTHER_CITATION_ID,
        suffix="4",
    )
    connection.execute(
        """
        INSERT INTO analysis_assistance_jobs (
          id, household_space_id, medical_event_id, event_version,
          candidate_digest_sha256, state, attempts
        ) VALUES (%s, %s, %s, 1, %s, 'QUEUED', 0)
        """,
        (JOB_ID, HOUSEHOLD_ID, EVENT_ID, DIGEST),
    )
    connection.execute(
        """
        INSERT INTO analysis_assistance_runs (
          id, analysis_job_id, household_space_id, medical_event_id,
          decision_run_id, event_version, candidate_digest_sha256,
          mode, state, outcome_code
        ) VALUES (
          %s, %s, %s, %s, %s, 1, %s,
          'STRUCTURED_SEARCH', 'LLM_PENDING', 'LOCAL_SEARCH_READY'
        )
        """,
        (
            ASSISTANCE_RUN_ID,
            JOB_ID,
            HOUSEHOLD_ID,
            EVENT_ID,
            DECISION_RUN_ID,
            DIGEST,
        ),
    )


def _insert_recommendation(
    connection: psycopg.Connection[tuple[object, ...]],
    recommendation_id: UUID,
    *,
    rank: int = 1,
    candidate_id: UUID = CANDIDATE_ID,
    coverage_id: UUID = COVERAGE_ID,
    import_run_id: UUID = RUN_ID,
    section_id: UUID = SECTION_ID,
    fact_id: UUID = FACT_ID,
    clause_id: UUID = CLAUSE_ID,
    citation_id: UUID = CITATION_ID,
    enrollment_decision: str = "MATCH",
) -> None:
    connection.execute(
        """
        INSERT INTO analysis_recommendations (
          id, analysis_assistance_run_id, household_space_id, decision_run_id,
          private_claim_candidate_id, knowledge_import_run_id,
          knowledge_coverage_id, enrollment_decision_snapshot,
          terms_section_id, knowledge_fact_id,
          source_clause_id, fact_citation_id, candidate_digest_sha256,
          rank, score, contract_label_snapshot, coverage_label_snapshot,
          clause_label_snapshot, excerpt, page_start, page_end,
          citation_kind, reason_code
        ) VALUES (
          %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
          %s, %s, %s, 1, 'Sample Policy', 'Sample Coverage', 'Sample Clause',
          'Sample bounded excerpt', 2, 2, 'FACT_CITATION', 'TOKEN_OVERLAP'
        )
        """,
        (
            recommendation_id,
            ASSISTANCE_RUN_ID,
            HOUSEHOLD_ID,
            DECISION_RUN_ID,
            candidate_id,
            import_run_id,
            coverage_id,
            enrollment_decision,
            section_id,
            fact_id,
            clause_id,
            citation_id,
            DIGEST,
            rank,
        ),
    )


def test_postgresql_enforces_dedupe_attempt_scope_and_immutability() -> None:
    with psycopg.connect(_database_url()) as connection:
        _assert_safe_database(connection)
        _seed_assistance_foundation(connection)
        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            _insert_recommendation(
                connection,
                UUID("00000000-0000-4000-8000-000000003114"),
                enrollment_decision="UNKNOWN",
            )
        _insert_recommendation(connection, RECOMMENDATION_ID)

        with pytest.raises(psycopg.errors.UniqueViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO analysis_assistance_jobs (
                  household_space_id, medical_event_id, event_version,
                  candidate_digest_sha256, state, attempts
                ) VALUES (%s, %s, 1, %s, 'QUEUED', 0)
                """,
                (HOUSEHOLD_ID, EVENT_ID, DIGEST),
            )

        with pytest.raises(psycopg.errors.CheckViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO analysis_assistance_jobs (
                  household_space_id, medical_event_id, event_version,
                  candidate_digest_sha256, state, attempts, claimed_at
                ) VALUES (%s, %s, 2, %s, 'RUNNING', 2, clock_timestamp())
                """,
                (HOUSEHOLD_ID, EVENT_ID, "b" * 64),
            )

        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            _insert_recommendation(
                connection,
                UUID("00000000-0000-4000-8000-000000003117"),
                rank=2,
                coverage_id=OTHER_COVERAGE_ID,
                import_run_id=OTHER_RUN_ID,
                section_id=OTHER_SECTION_ID,
                fact_id=OTHER_FACT_ID,
                clause_id=OTHER_CLAUSE_ID,
                citation_id=OTHER_CITATION_ID,
            )

        with pytest.raises(psycopg.errors.ForeignKeyViolation), connection.transaction():
            connection.execute(
                """
                INSERT INTO private_knowledge_fact_citations (
                  id, import_run_id, fact_id, source_clause_id, citation_ordinal,
                  page_start, page_end, source_text_sha256, locator_json,
                  source_record_json, source_record_digest_sha256
                ) VALUES (
                  %s, %s, %s, %s, 2, 2, 2, %s,
                  '{}'::jsonb, '{}'::jsonb, %s
                )
                """,
                (
                    SECOND_CITATION_ID,
                    RUN_ID,
                    FACT_ID,
                    CLAUSE_ID,
                    "5" * 64,
                    "5" * 64,
                ),
            )
            _insert_recommendation(
                connection,
                UUID("00000000-0000-4000-8000-000000003115"),
                rank=2,
                candidate_id=UUID("00000000-0000-4000-8000-000000003199"),
                citation_id=SECOND_CITATION_ID,
            )

        with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState), connection.transaction():
            connection.execute(
                "UPDATE analysis_recommendations SET rank = 2 WHERE id = %s",
                (RECOMMENDATION_ID,),
            )


def test_migration_downgrade_upgrade_round_trip() -> None:
    config = Config(str(ROOT / "apps/api/alembic.ini"))
    try:
        command.downgrade(config, "0021_private_knowledge_decisions")
        with psycopg.connect(_database_url()) as connection:
            _assert_safe_database(connection)
            row = connection.execute(
                "SELECT to_regclass('public.analysis_assistance_jobs')"
            ).fetchone()
            assert row == (None,)
    finally:
        command.upgrade(config, "0022_analysis_assistance")

    with psycopg.connect(_database_url()) as connection:
        row = connection.execute(
            """
            SELECT to_regclass('public.analysis_assistance_jobs'),
                   to_regclass('public.analysis_assistance_runs'),
                   to_regclass('public.analysis_recommendations')
            """
        ).fetchone()
        assert row == (
            "analysis_assistance_jobs",
            "analysis_assistance_runs",
            "analysis_recommendations",
        )
