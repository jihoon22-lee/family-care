"""Migration contract for scoped, immutable analysis assistance."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0022_analysis_assistance.py"

EXPECTED_TABLES = [
    "analysis_assistance_jobs",
    "analysis_assistance_runs",
    "analysis_recommendations",
]
FORBIDDEN_COLUMN_FRAGMENTS = {
    "api_key",
    "prompt",
    "response",
    "query",
    "situation",
    "fact_value",
    "raw_statement",
    "source_path",
    "source_alias",
}


class RecordingOperations:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.operations: list[tuple[str, str, str | None]] = []
        self.executed_sql: list[str] = []

    def create_table(self, name: str, *elements: Any, **kwargs: Any) -> sa.Table:
        table = sa.Table(name, self.metadata, *elements, **kwargs)
        self.tables[name] = table
        self.operations.append(("create_table", name, None))
        return table

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **kwargs: Any,
    ) -> None:
        self.indexes[name] = {"table_name": table_name, "columns": columns, **kwargs}
        self.operations.append(("create_index", name, table_name))

    def drop_index(self, name: str, **kwargs: Any) -> None:
        self.operations.append(("drop_index", name, cast(str | None, kwargs.get("table_name"))))

    def drop_table(self, name: str) -> None:
        self.operations.append(("drop_table", name, None))

    def execute(self, statement: str) -> None:
        self.executed_sql.append(statement)
        self.operations.append(("execute", "sql", None))


def _load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("analysis_assistance", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_upgrade() -> tuple[ModuleType, RecordingOperations]:
    migration = cast(Any, _load_migration())
    operations = RecordingOperations()
    migration.op = operations
    migration.upgrade()
    return migration, operations


def _checks(table: sa.Table) -> dict[str, str]:
    return {
        str(constraint.name or ""): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def _foreign_keys(
    table: sa.Table,
) -> set[tuple[tuple[str, ...], tuple[str, ...], str | None]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }


def _unique_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_revision_and_tables_are_additive() -> None:
    migration, operations = _run_upgrade()

    assert migration.revision == "0022_analysis_assistance"
    assert migration.down_revision == "0021_private_knowledge_decisions"
    assert list(operations.tables) == EXPECTED_TABLES


def test_jobs_are_event_version_digest_deduplicated_and_one_attempt_only() -> None:
    _, operations = _run_upgrade()
    table = operations.tables["analysis_assistance_jobs"]
    table_checks = _checks(table)

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "event_version",
        "candidate_digest_sha256",
        "state",
        "attempts",
        "outcome_code",
        "claimed_at",
        "completed_at",
        "created_at",
    } <= set(table.columns.keys())
    assert table_checks["ck_analysis_assistance_jobs_state"] == (
        "state IN ('QUEUED', 'RUNNING', 'SUCCEEDED')"
    )
    assert table_checks["ck_analysis_assistance_jobs_attempts"] == (
        "attempts >= 0 AND attempts <= 1"
    )
    assert (
        "household_space_id",
        "medical_event_id",
        "event_version",
        "candidate_digest_sha256",
    ) in _unique_columns(table)
    assert (
        ("medical_event_id", "household_space_id"),
        ("medical_events.id", "medical_events.household_space_id"),
        "RESTRICT",
    ) in _foreign_keys(table)


def test_runs_keep_bounded_sanitized_provenance_and_exact_decision_scope() -> None:
    _, operations = _run_upgrade()
    table = operations.tables["analysis_assistance_runs"]
    table_checks = _checks(table)

    assert {
        "id",
        "analysis_job_id",
        "household_space_id",
        "medical_event_id",
        "decision_run_id",
        "event_version",
        "candidate_digest_sha256",
        "mode",
        "state",
        "provider_label",
        "model_label",
        "config_version",
        "outcome_code",
        "created_at",
    } <= set(table.columns.keys())
    assert table_checks["ck_analysis_assistance_runs_mode"] == (
        "mode IN ('STRUCTURED_SEARCH', 'LLM_ASSISTED', 'NONE')"
    )
    assert table_checks["ck_analysis_assistance_runs_state"] == (
        "state IN ('SEARCH_READY', 'LLM_PENDING', 'LLM_READY')"
    )
    assert ("decision_run_id", "mode", "state") in _unique_columns(table)
    assert (
        (
            "decision_run_id",
            "household_space_id",
            "medical_event_id",
            "event_version",
        ),
        (
            "decision_runs.id",
            "decision_runs.household_space_id",
            "decision_runs.medical_event_id",
            "decision_runs.event_version",
        ),
        "RESTRICT",
    ) in _foreign_keys(table)


def test_recommendations_are_bounded_and_same_run_enrolled_citations() -> None:
    _, operations = _run_upgrade()
    table = operations.tables["analysis_recommendations"]
    table_checks = _checks(table)

    assert {
        "id",
        "analysis_assistance_run_id",
        "household_space_id",
        "decision_run_id",
        "private_claim_candidate_id",
        "knowledge_import_run_id",
        "knowledge_coverage_id",
        "enrollment_decision_snapshot",
        "terms_section_id",
        "knowledge_fact_id",
        "source_clause_id",
        "fact_citation_id",
        "rank",
        "score",
        "contract_label_snapshot",
        "coverage_label_snapshot",
        "clause_label_snapshot",
        "excerpt",
        "page_start",
        "page_end",
        "citation_kind",
        "reason_code",
        "explanation_code",
        "question_code",
        "created_at",
    } <= set(table.columns.keys())
    assert table_checks["ck_analysis_recommendations_rank"] == "rank >= 1 AND rank <= 12"
    assert table_checks["ck_analysis_recommendations_excerpt"] == (
        "btrim(excerpt) <> '' AND char_length(excerpt) <= 240"
    )
    assert table_checks["ck_analysis_recommendations_pages"] == (
        "page_start >= 1 AND page_end >= page_start"
    )
    assert table_checks["ck_analysis_recommendations_enrollment"] == (
        "enrollment_decision_snapshot = 'MATCH'"
    )
    assert ("analysis_assistance_run_id", "rank") in _unique_columns(table)
    assert (
        (
            "private_claim_candidate_id",
            "decision_run_id",
            "household_space_id",
            "knowledge_import_run_id",
            "knowledge_coverage_id",
        ),
        (
            "private_knowledge_claim_candidates.id",
            "private_knowledge_claim_candidates.decision_run_id",
            "private_knowledge_claim_candidates.household_space_id",
            "private_knowledge_claim_candidates.knowledge_import_run_id",
            "private_knowledge_claim_candidates.knowledge_coverage_id",
        ),
        "RESTRICT",
    ) in _foreign_keys(table)
    assert (
        (
            "knowledge_coverage_id",
            "knowledge_import_run_id",
            "enrollment_decision_snapshot",
        ),
        (
            "private_knowledge_coverages.id",
            "private_knowledge_coverages.import_run_id",
            "private_knowledge_coverages.enrollment_decision",
        ),
        "RESTRICT",
    ) in _foreign_keys(table)


def test_assistance_schema_cannot_store_secrets_or_raw_inputs() -> None:
    _, operations = _run_upgrade()
    column_names = {
        column.name.lower() for table in operations.tables.values() for column in table.columns
    }

    for column_name in column_names:
        assert all(fragment not in column_name for fragment in FORBIDDEN_COLUMN_FRAGMENTS)


def test_downgrade_removes_assistance_before_supporting_indexes() -> None:
    migration = cast(Any, _load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    dropped_tables = [
        name for operation, name, _ in operations.operations if operation == "drop_table"
    ]
    assert dropped_tables == list(reversed(EXPECTED_TABLES))
