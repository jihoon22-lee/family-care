"""Migration contract for immutable private-knowledge decision results."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0021_private_knowledge_decisions.py"

EXPECTED_TABLES = [
    "private_knowledge_rule_evaluations",
    "private_knowledge_claim_candidates",
    "private_knowledge_benefit_calculations",
    "private_knowledge_calculation_steps",
]


class RecordingOperations:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.added_columns: dict[str, list[sa.Column[Any]]] = {}
        self.created_checks: dict[str, tuple[str, str]] = {}
        self.created_foreign_keys: dict[str, dict[str, Any]] = {}
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

    def add_column(self, table_name: str, column: sa.Column[Any]) -> None:
        self.added_columns.setdefault(table_name, []).append(column)
        self.operations.append(("add_column", column.name or "", table_name))

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.operations.append(("drop_column", column_name, table_name))

    def create_check_constraint(
        self,
        name: str,
        table_name: str,
        condition: str,
    ) -> None:
        self.created_checks[name] = (table_name, condition)
        self.operations.append(("create_check", name, table_name))

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kwargs: Any,
    ) -> None:
        self.created_foreign_keys[name] = {
            "source_table": source_table,
            "referent_table": referent_table,
            "local_cols": local_cols,
            "remote_cols": remote_cols,
            **kwargs,
        }
        self.operations.append(("create_foreign_key", name, source_table))

    def drop_constraint(self, name: str, table_name: str, **kwargs: Any) -> None:
        self.operations.append(("drop_constraint", name, table_name))

    def drop_table(self, name: str) -> None:
        self.operations.append(("drop_table", name, None))

    def execute(self, statement: str) -> None:
        self.executed_sql.append(statement)
        self.operations.append(("execute", "sql", None))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location(
        "private_knowledge_decisions",
        MIGRATION_PATH,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_upgrade() -> tuple[ModuleType, RecordingOperations]:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations
    migration.upgrade()
    return migration, operations


def checks(table: sa.Table) -> dict[str, str]:
    return {
        str(constraint.name or ""): str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def composite_foreign_keys(
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


def unique_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_revision_chains_from_private_publications() -> None:
    migration, operations = run_upgrade()

    assert migration.revision == "0021_private_knowledge_decisions"
    assert migration.down_revision == "0020_private_publications"
    assert list(operations.tables) == EXPECTED_TABLES


def test_decision_runs_capture_private_snapshot_identity_and_partial_status() -> None:
    _, operations = run_upgrade()
    columns = {column.name: column for column in operations.added_columns["decision_runs"]}

    assert set(columns) == {
        "knowledge_import_run_id",
        "knowledge_rule_import_run_id",
        "knowledge_status_projection_digest",
        "event_fact_schema_version",
        "analysis_completeness",
        "source_failure_codes_json",
    }
    assert columns["knowledge_import_run_id"].nullable is True
    assert columns["knowledge_rule_import_run_id"].nullable is True
    assert columns["event_fact_schema_version"].nullable is False
    assert columns["analysis_completeness"].nullable is False
    assert columns["source_failure_codes_json"].nullable is False
    assert operations.created_checks["ck_decision_runs_status"] == (
        "decision_runs",
        "status IN ('running', 'succeeded', 'partial', 'failed')",
    )
    assert operations.created_checks["ck_decision_runs_analysis_completeness"] == (
        "decision_runs",
        "analysis_completeness IN ('COMPLETE', 'PARTIAL', 'UNAVAILABLE')",
    )
    assert operations.created_foreign_keys["fk_decision_runs_knowledge_rule_run"]["local_cols"] == [
        "knowledge_rule_import_run_id",
        "knowledge_import_run_id",
        "household_space_id",
    ]


def test_private_evaluations_keep_exact_rule_coverage_and_citation_snapshot() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_rule_evaluations"]
    table_checks = checks(table)

    assert {
        "decision_run_id",
        "rule_publication_id",
        "knowledge_import_run_id",
        "knowledge_rule_import_run_id",
        "knowledge_coverage_id",
        "result",
        "required",
        "reason_code",
        "fact_paths_json",
        "missing_fields_json",
        "conflicting_fields_json",
        "citation_snapshot_json",
        "evaluator_version",
    } <= set(table.columns.keys())
    assert table_checks["ck_pk_rule_evaluations_result"] == (
        "result IN ('MATCH', 'NO_MATCH', 'UNKNOWN')"
    )
    for column in (
        "fact_paths_json",
        "missing_fields_json",
        "conflicting_fields_json",
        "citation_snapshot_json",
    ):
        assert any(column in value and "array" in value for value in table_checks.values())
    assert (
        (
            "rule_publication_id",
            "knowledge_rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
            "knowledge_coverage_id",
        ),
        (
            "private_knowledge_rule_publications.id",
            "private_knowledge_rule_publications.rule_import_run_id",
            "private_knowledge_rule_publications.knowledge_import_run_id",
            "private_knowledge_rule_publications.household_space_id",
            "private_knowledge_rule_publications.knowledge_coverage_id",
        ),
        "RESTRICT",
    ) in composite_foreign_keys(table)
    assert unique_columns(table) == {("decision_run_id", "rule_publication_id")}


def test_private_candidates_keep_contract_coverage_counts_and_safe_labels() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_claim_candidates"]
    table_checks = checks(table)

    assert {
        "knowledge_contract_id",
        "knowledge_coverage_id",
        "contract_label_snapshot",
        "coverage_label_snapshot",
        "benefit_type",
        "aggregate_result",
        "required_match_count",
        "required_unknown_count",
        "required_no_match_count",
        "questions_json",
        "hold_reason_codes_json",
        "claim_start_ready",
    } <= set(table.columns.keys())
    assert table_checks["ck_pk_candidates_benefit_type"] == (
        "benefit_type IN ('FIXED', 'INDEMNITY', 'UNKNOWN')"
    )
    assert table_checks["ck_pk_candidates_result"] == (
        "aggregate_result IN ('MATCH', 'NO_MATCH', 'UNKNOWN')"
    )
    assert table_checks["ck_pk_candidates_claim_ready"] == "claim_start_ready = false"
    assert isinstance(table.c.contract_label_snapshot.type, sa.String)
    assert isinstance(table.c.coverage_label_snapshot.type, sa.String)
    assert unique_columns(table) == {
        (
            "id",
            "decision_run_id",
            "household_space_id",
            "knowledge_import_run_id",
            "knowledge_coverage_id",
        )
    }
    one_per_coverage = operations.indexes["uq_pk_candidates_run_coverage"]
    assert one_per_coverage["unique"] is True
    assert one_per_coverage["columns"] == [
        "decision_run_id",
        "knowledge_coverage_id",
    ]


def test_private_calculations_use_decimal_amounts_and_one_row_per_candidate() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_benefit_calculations"]
    table_checks = checks(table)

    assert table_checks["ck_pk_calculations_status"] == (
        "calculation_status IN ('CALCULATED', 'UNKNOWN', 'NOT_APPLICABLE', 'FAILED')"
    )
    assert table_checks["ck_pk_calculations_kind"] == (
        "calculation_kind IN ('FIXED', 'INDEMNITY', 'NONE', 'UNKNOWN')"
    )
    for column_name in (
        "confirmed_amount",
        "conditional_amount",
        "excluded_amount",
        "deductible_amount",
        "applied_limit",
    ):
        column_type = table.c[column_name].type
        assert isinstance(column_type, sa.Numeric)
        assert column_type.precision == 20
        assert column_type.scale == 4
    assert isinstance(table.c.applied_rate.type, sa.Numeric)
    assert table.c.applied_rate.type.precision == 9
    assert table.c.applied_rate.type.scale == 6
    assert unique_columns(table) == {("private_claim_candidate_id",)}


def test_private_calculation_steps_are_ordered_decimal_trace_rows() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_calculation_steps"]
    table_checks = checks(table)

    assert unique_columns(table) == {("private_benefit_calculation_id", "step_number")}
    assert table_checks["ck_pk_calculation_steps_number"] == "step_number >= 1"
    for column_name in ("input_amount", "output_amount"):
        column_type = table.c[column_name].type
        assert isinstance(column_type, sa.Numeric)
        assert column_type.precision == 20
        assert column_type.scale == 4


def test_migration_never_adds_private_source_or_provider_content() -> None:
    _, operations = run_upgrade()
    forbidden = {
        "source_path",
        "source_alias",
        "raw_statement",
        "raw_text",
        "prompt",
        "provider_response",
        "api_key",
        "diagnosis",
        "medical_text",
    }

    all_columns = {
        column.name for table in operations.tables.values() for column in table.columns
    } | {column.name for columns in operations.added_columns.values() for column in columns}
    assert not forbidden & all_columns


def test_downgrade_reverses_private_tables_and_decision_run_extensions() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    dropped_tables = [item[1] for item in operations.operations if item[0] == "drop_table"]
    assert dropped_tables == list(reversed(EXPECTED_TABLES))
    dropped_columns = [
        item[1]
        for item in operations.operations
        if item[0] == "drop_column" and item[2] == "decision_runs"
    ]
    assert dropped_columns == [
        "source_failure_codes_json",
        "analysis_completeness",
        "event_fact_schema_version",
        "knowledge_status_projection_digest",
        "knowledge_rule_import_run_id",
        "knowledge_import_run_id",
    ]
    assert any("status = 'failed'" in statement for statement in operations.executed_sql)
