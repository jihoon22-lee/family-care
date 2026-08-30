"""Repository contract for scoped search persistence and read-only reloads."""

from __future__ import annotations

import os
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.assistance import normalize_search_tokens
from familycare_api.decisions.assistance_repository import AnalysisAssistanceRepository
from familycare_api.decisions.domain import FactValue, MedicalEvent
from psycopg.rows import dict_row


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


HOUSEHOLD_ID = _uuid(101)
MEMBER_ID = _uuid(102)
EVENT_ID = _uuid(103)
DECISION_RUN_ID = _uuid(104)
JOB_ID = _uuid(105)
ASSISTANCE_RUN_ID = _uuid(106)
RECOMMENDATION_ID = _uuid(107)
CREATED_AT = datetime(2026, 8, 31, tzinfo=UTC)


class _Result:
    def __init__(
        self,
        *,
        one: dict[str, Any] | None = None,
        many: list[dict[str, Any]] | None = None,
    ) -> None:
        self.one = one
        self.many = many or []

    def fetchone(self) -> dict[str, Any] | None:
        return self.one

    def fetchall(self) -> list[dict[str, Any]]:
        return self.many


class _Connection:
    def __init__(
        self,
        *,
        search_rows: list[dict[str, Any]],
        job_state: str = "QUEUED",
    ) -> None:
        self.search_rows = search_rows
        self.job_state = job_state
        self.calls: list[tuple[str, object]] = []

    def execute(self, query: str, params: object = None) -> _Result:
        normalized = " ".join(query.split())
        self.calls.append((normalized, params))
        if normalized.startswith("WITH query_tokens"):
            return _Result(many=self.search_rows)
        if normalized.startswith("INSERT INTO analysis_assistance_jobs"):
            return _Result(one={"id": JOB_ID, "state": self.job_state})
        if normalized.startswith("INSERT INTO analysis_assistance_runs"):
            return _Result(one={"id": ASSISTANCE_RUN_ID, "created_at": CREATED_AT})
        if normalized.startswith("INSERT INTO analysis_recommendations"):
            return _Result()
        if normalized.startswith("SELECT assistance.*"):
            return _Result(
                one={
                    "id": ASSISTANCE_RUN_ID,
                    "analysis_job_id": JOB_ID,
                    "decision_run_id": DECISION_RUN_ID,
                    "event_version": 1,
                    "candidate_digest_sha256": "a" * 64,
                    "mode": "STRUCTURED_SEARCH",
                    "state": "LLM_PENDING",
                    "provider_label": None,
                    "model_label": None,
                    "config_version": None,
                    "outcome_code": "LOCAL_SEARCH_READY",
                    "created_at": CREATED_AT,
                }
            )
        if normalized.startswith("SELECT recommendation.*"):
            return _Result(many=self.search_rows)
        raise AssertionError(f"unexpected SQL: {normalized}")


def _event(
    situation: str = "sensitive_event_marker sample category",
    *,
    include_fact: bool = True,
) -> MedicalEvent:
    return MedicalEvent(
        id=EVENT_ID,
        household_space_id=HOUSEHOLD_ID,
        family_member_id=MEMBER_ID,
        mode="post_treatment",
        situation=situation,
        event_date=date(2026, 8, 30),
        visit_date=date(2026, 8, 30),
        facts=(
            {
                "MedicalEvent.procedure_kind": FactValue(
                    value="sample procedure",
                    confirmation="user",
                    evidence_ids=(),
                )
            }
            if include_fact
            else {}
        ),
        version=1,
    )


def _search_row() -> dict[str, Any]:
    return {
        "id": RECOMMENDATION_ID,
        "private_claim_candidate_id": _uuid(108),
        "knowledge_import_run_id": _uuid(114),
        "knowledge_coverage_id": _uuid(109),
        "terms_section_id": _uuid(110),
        "knowledge_fact_id": _uuid(111),
        "source_clause_id": _uuid(112),
        "fact_citation_id": _uuid(113),
        "score": 3,
        "contract_label": "Sample Policy",
        "coverage_label": "Sample Coverage",
        "clause_label": "Sample Clause",
        "excerpt": "Sample reviewed trigger",
        "page_start": 2,
        "page_end": 2,
        "citation_kind": "FACT_CITATION",
        "reason_code": "TOKEN_OVERLAP",
    }


def test_create_projection_searches_exact_scope_and_persists_only_bounded_results() -> None:
    connection = _Connection(search_rows=[_search_row()])
    repository = AnalysisAssistanceRepository()

    result = repository.create_search_projection(
        connection,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
        DECISION_RUN_ID,
    )

    assert result.mode == "STRUCTURED_SEARCH"
    assert result.state == "LLM_PENDING"
    assert result.job_id == JOB_ID
    assert result.recommendations[0].coverage_label == "Sample Coverage"
    search_sql = next(
        query for query, _ in connection.calls if query.startswith("WITH query_tokens")
    )
    assert "private_knowledge_import_runs" in search_sql
    assert "subject.family_member_id = %s" in search_sql
    assert "coverage.enrollment_decision = 'MATCH'" in search_sql
    assert "mapping.overall_decision = 'MATCH'" in search_sql
    assert "candidate.decision_run_id = %s" in search_sql

    insert_params = [
        params for query, params in connection.calls if query.startswith("INSERT INTO")
    ]
    assert "sensitive_event_marker" not in repr(insert_params)
    assert all(len(item.excerpt) <= 240 for item in result.recommendations)


def test_zero_token_event_stores_none_without_search_or_external_work() -> None:
    connection = _Connection(search_rows=[], job_state="SUCCEEDED")
    repository = AnalysisAssistanceRepository()

    result = repository.create_search_projection(
        connection,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        _event(" - / . ", include_fact=False),
        DECISION_RUN_ID,
    )

    assert result.mode == "NONE"
    assert result.state == "SEARCH_READY"
    assert result.recommendations == ()
    assert all(not query.startswith("WITH query_tokens") for query, _ in connection.calls)


def test_get_latest_is_read_only_and_never_enqueues() -> None:
    connection = _Connection(search_rows=[{**_search_row(), "rank": 1}])
    repository = AnalysisAssistanceRepository()

    result = repository.get_latest(
        connection,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        DECISION_RUN_ID,
    )

    assert result.run_id == ASSISTANCE_RUN_ID
    assert result.recommendations[0].rank == 1
    assert all(not query.startswith("INSERT") for query, _ in connection.calls)


@pytest.mark.integration
def test_analyze_persists_scoped_projection_and_get_is_read_only(tmp_path: Path) -> None:
    from familycare_api.decisions.repository import DecisionRepository
    from familycare_api.decisions.service import DecisionService

    from apps.api.tests.test_decision_integration import _psycopg_url, _reset_database, _seed
    from apps.api.tests.test_private_knowledge_decision_integration import (
        _seed_private_publication,
    )

    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    _reset_database(database_url)
    seed = _seed(database_url)
    _seed_private_publication(database_url, seed, tmp_path)
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

    first = service.analyze_medical_event(event.id)

    assert first.assistance is not None
    assert first.assistance.mode == "STRUCTURED_SEARCH"
    assert first.assistance.recommendations
    assert all(item.page_start >= 1 for item in first.assistance.recommendations)
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        before_get = connection.execute(
            """
            SELECT (SELECT count(*) FROM analysis_assistance_jobs) AS jobs,
                   (SELECT count(*) FROM analysis_assistance_runs) AS runs
            """
        ).fetchone()

    loaded = service.get_decision_result(event.id, event.version)

    assert loaded.assistance == first.assistance
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        after_get = connection.execute(
            """
            SELECT (SELECT count(*) FROM analysis_assistance_jobs) AS jobs,
                   (SELECT count(*) FROM analysis_assistance_runs) AS runs
            """
        ).fetchone()
    assert after_get == before_get

    repeated = service.analyze_medical_event(event.id)
    assert repeated.assistance is not None
    assert repeated.assistance.job_id == first.assistance.job_id
    assert repeated.assistance.run_id != first.assistance.run_id

    tokens = normalize_search_tokens(event.situation, ("sample_category",))
    repository = AnalysisAssistanceRepository()
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        baseline = repository._search_rows(  # noqa: SLF001
            connection,
            seed.scope_a,
            event,
            first.run_id,
            tokens,
        )
        assert baseline

        assert (
            repository._search_rows(  # noqa: SLF001
                connection,
                seed.scope_b,
                event,
                first.run_id,
                tokens,
            )
            == []
        )
        assert (
            repository._search_rows(  # noqa: SLF001
                connection,
                seed.scope_a,
                replace(event, family_member_id=seed.other_member_a),
                first.run_id,
                tokens,
            )
            == []
        )

        connection.execute(
            """
            UPDATE private_knowledge_import_runs
            SET is_current = false
            WHERE id = %s
            """,
            (first.knowledge_import_run_id,),
        )
        assert (
            repository._search_rows(  # noqa: SLF001
                connection, seed.scope_a, event, first.run_id, tokens
            )
            == []
        )
        connection.rollback()

        unknown_coverage_id = _uuid(901)
        unknown_candidate_id = _uuid(902)
        unknown_mapping_id = _uuid(903)
        connection.execute(
            """
            INSERT INTO private_knowledge_coverages (
              id, import_run_id, household_space_id, knowledge_contract_id,
              source_coverage_key, display_name, component_role,
              component_classification, enrollment_decision, benefit_type,
              insured_amount, currency, coverage_start, coverage_end,
              renewal_state, current_status, certificate_evidence_json,
              review_issues_json, rider_id, operational_binding_decision,
              operational_binding_reason_code, source_record_json,
              source_record_digest_sha256
            )
            SELECT %s, import_run_id, household_space_id, knowledge_contract_id,
                   'synthetic-unenrolled-coverage', 'Unenrolled Sample Coverage',
                   component_role, component_classification, 'UNKNOWN', benefit_type,
                   insured_amount, currency, coverage_start, coverage_end,
                   renewal_state, current_status, certificate_evidence_json,
                   review_issues_json, NULL, 'UNKNOWN', 'NO_EXACT_BINDING',
                   '{}'::jsonb, %s
            FROM private_knowledge_coverages
            WHERE import_run_id = %s
            ORDER BY id
            LIMIT 1
            """,
            (unknown_coverage_id, "6" * 64, first.knowledge_import_run_id),
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
            )
            SELECT %s, household_space_id, decision_run_id, knowledge_import_run_id,
                   knowledge_rule_import_run_id, knowledge_contract_id, %s,
                   contract_label_snapshot, 'Unenrolled Sample Coverage',
                   benefit_type, 'UNKNOWN', 0, 1, 0, '[]'::jsonb, '[]'::jsonb, false
            FROM private_knowledge_claim_candidates
            WHERE decision_run_id = %s
            ORDER BY id
            LIMIT 1
            """,
            (unknown_candidate_id, unknown_coverage_id, first.run_id),
        )
        connection.execute(
            """
            INSERT INTO private_knowledge_coverage_terms_mappings (
              id, import_run_id, coverage_id, terms_section_id,
              source_mapping_key, mapping_applicability,
              selected_terms_source_alias,
              selected_terms_source_alias_digest_sha256,
              enrollment_decision, document_identity_decision,
              edition_applicability_decision, section_mapping_decision,
              overall_decision, reason_codes_json, executable,
              source_record_json, source_record_digest_sha256
            )
            SELECT %s, import_run_id, %s, terms_section_id,
                   'synthetic-unenrolled-mapping', mapping_applicability,
                   selected_terms_source_alias,
                   selected_terms_source_alias_digest_sha256,
                   enrollment_decision, document_identity_decision,
                   edition_applicability_decision, section_mapping_decision,
                   overall_decision, '[]'::jsonb, false, '{}'::jsonb, %s
            FROM private_knowledge_coverage_terms_mappings
            WHERE import_run_id = %s AND overall_decision = 'MATCH'
            ORDER BY id
            LIMIT 1
            """,
            (
                unknown_mapping_id,
                unknown_coverage_id,
                "7" * 64,
                first.knowledge_import_run_id,
            ),
        )
        unenrolled_rows = repository._search_rows(  # noqa: SLF001
            connection, seed.scope_a, event, first.run_id, tokens
        )
        assert unenrolled_rows
        assert all(row["knowledge_coverage_id"] != unknown_coverage_id for row in unenrolled_rows)
        connection.rollback()

        connection.execute(
            """
            UPDATE private_knowledge_coverage_terms_mappings
            SET overall_decision = 'UNKNOWN'
            WHERE import_run_id = %s
            """,
            (first.knowledge_import_run_id,),
        )
        assert (
            repository._search_rows(  # noqa: SLF001
                connection, seed.scope_a, event, first.run_id, tokens
            )
            == []
        )
        connection.rollback()

    edited = service.update_medical_event(
        event.id,
        expected_version=event.version,
        facts={"MedicalEvent.classification": "sample_category_changed"},
        confirmation={"MedicalEvent.classification": "user"},
    )
    after_edit = service.analyze_medical_event(edited.id)
    assert after_edit.assistance is not None
    assert after_edit.assistance.job_id != first.assistance.job_id
    assert after_edit.assistance.event_version == 2
