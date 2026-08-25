"""Migration contract for receipt lines and benefit calculation traces."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0008_benefit_calculations.py"

EXPECTED_TABLES = {
    "receipt_lines",
    "benefit_calculations",
    "benefit_calculation_steps",
}
PHASE_ONE_AND_DECISION_TABLES = {
    "documents",
    "document_versions",
    "extractions",
    "extraction_pages",
    "extraction_blocks",
    "extraction_tables",
    "extraction_cells",
    "analysis_jobs",
    "medical_events",
    "decision_runs",
    "rule_evaluations",
    "rule_evaluation_evidence",
    "claim_candidates",
}


class RecordingOperations:
    """Alembic spy that materializes created SQLAlchemy tables."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []

    def create_table(self, name: str, *elements: Any, **kwargs: Any) -> sa.Table:
        table = sa.Table(name, self.metadata, *elements, **kwargs)
        self.tables[name] = table
        return table

    def create_index(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **kwargs: Any,
    ) -> None:
        self.indexes[name] = {"table_name": table_name, "columns": columns, **kwargs}

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)

    def drop_index(self, name: str, **kwargs: Any) -> None:
        self.dropped_indexes.append(name)


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("benefit_calculations_migration", MIGRATION_PATH)
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


def unique_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def foreign_keys(table: sa.Table) -> dict[str, tuple[str, str | None]]:
    return {
        column.name: (
            next(iter(column.foreign_keys)).target_fullname,
            next(iter(column.foreign_keys)).ondelete,
        )
        for column in table.columns
        if column.foreign_keys
    }


def test_revision_is_exactly_chained_from_coverage_decision_engine() -> None:
    migration = load_migration()

    assert migration.revision == "0008_benefit_calculations"
    assert migration.down_revision == "0007_coverage_decision_engine"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_creates_only_the_three_benefit_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert not PHASE_ONE_AND_DECISION_TABLES & set(operations.tables)


def test_receipt_lines_are_scoped_soft_deletable_and_decimal() -> None:
    _, operations = run_upgrade()
    receipt_lines = operations.tables["receipt_lines"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "category",
        "coverage_category",
        "amount",
        "currency",
        "confirmation_level",
        "note_code",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    } == set(receipt_lines.c.keys())
    assert foreign_keys(receipt_lines) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
    }
    assert isinstance(receipt_lines.c.amount.type, sa.Numeric)
    assert receipt_lines.c.amount.type.precision == 18
    assert receipt_lines.c.amount.type.scale == 2
    assert receipt_lines.c.currency.type.length == 3
    receipt_checks = checks(receipt_lines)
    assert all(
        category in receipt_checks["ck_receipt_lines_category"]
        for category in ("outpatient", "inpatient", "pharmacy")
    )
    assert all(
        category in receipt_checks["ck_receipt_lines_coverage_category"]
        for category in ("covered", "possible_excluded", "excluded", "unknown")
    )
    assert all(
        level in receipt_checks["ck_receipt_lines_confirmation_level"]
        for level in ("user", "ai_structured", "unconfirmed")
    )
    assert receipt_checks["ck_receipt_lines_amount"] == "amount >= 0"
    assert receipt_checks["ck_receipt_lines_currency"] == "currency ~ '^[A-Z]{3}$'"
    assert receipt_checks["ck_receipt_lines_version"] == "version >= 1"


def test_benefit_calculations_store_bounded_results_and_rule_lineage() -> None:
    _, operations = run_upgrade()
    calculations = operations.tables["benefit_calculations"]

    assert {
        "id",
        "household_space_id",
        "claim_candidate_id",
        "calculation_kind",
        "status",
        "currency",
        "confirmed_amount",
        "additional_amount",
        "excluded_amount",
        "deductible_amount",
        "applied_rate",
        "applied_limit",
        "rounding_rule",
        "hold_reason_code",
        "rule_version_id",
        "engine_version",
        "version",
        "created_at",
    } == set(calculations.c.keys())
    assert foreign_keys(calculations) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "claim_candidate_id": ("claim_candidates.id", "RESTRICT"),
        "rule_version_id": ("coverage_rule_versions.id", "RESTRICT"),
    }
    for column_name in (
        "confirmed_amount",
        "additional_amount",
        "excluded_amount",
        "deductible_amount",
        "applied_limit",
    ):
        column_type = calculations.c[column_name].type
        assert isinstance(column_type, sa.Numeric)
        assert column_type.precision == 18
        assert column_type.scale == 2
    assert isinstance(calculations.c.applied_rate.type, sa.Numeric)
    assert calculations.c.applied_rate.type.precision == 9
    assert calculations.c.applied_rate.type.scale == 6
    assert calculations.c.currency.type.length == 3
    calculation_checks = checks(calculations)
    assert all(
        kind in calculation_checks["ck_benefit_calculations_kind"]
        for kind in ("fixed", "indemnity")
    )
    assert all(
        status in calculation_checks["ck_benefit_calculations_status"]
        for status in ("computed", "partial", "unknown")
    )
    assert calculation_checks["ck_benefit_calculations_currency"] == (
        "currency IS NULL OR currency ~ '^[A-Z]{3}$'"
    )
    assert "confirmed_amount IS NULL OR confirmed_amount >= 0" in calculation_checks.values()
    assert "additional_amount IS NULL OR additional_amount >= 0" in calculation_checks.values()
    assert "excluded_amount IS NULL OR excluded_amount >= 0" in calculation_checks.values()
    assert "deductible_amount IS NULL OR deductible_amount >= 0" in calculation_checks.values()
    assert "applied_limit IS NULL OR applied_limit >= 0" in calculation_checks.values()
    applied_rate_check = calculation_checks["ck_benefit_calculations_applied_rate"]
    assert "applied_rate IS NULL" in applied_rate_check
    assert "applied_rate >= 0" in applied_rate_check
    assert "applied_rate <= 1" in applied_rate_check
    assert calculation_checks["ck_benefit_calculations_engine_version"] == "engine_version <> ''"
    assert calculation_checks["ck_benefit_calculations_version"] == "version >= 1"


def test_calculation_steps_are_immutable_trace_rows_with_decimal_precision() -> None:
    _, operations = run_upgrade()
    steps = operations.tables["benefit_calculation_steps"]

    assert {
        "id",
        "benefit_calculation_id",
        "step_number",
        "operation",
        "input_amount",
        "input_currency",
        "output_amount",
        "output_currency",
        "rounding_rule",
        "reason_code",
    } == set(steps.c.keys())
    assert foreign_keys(steps) == {
        "benefit_calculation_id": ("benefit_calculations.id", "RESTRICT"),
    }
    assert isinstance(steps.c.input_amount.type, sa.Numeric)
    assert steps.c.input_amount.type.precision == 18
    assert steps.c.input_amount.type.scale == 6
    assert isinstance(steps.c.output_amount.type, sa.Numeric)
    assert steps.c.output_amount.type.precision == 18
    assert steps.c.output_amount.type.scale == 6
    assert unique_columns(steps) == {("benefit_calculation_id", "step_number")}
    assert steps.c.step_number.nullable is False
    step_checks = checks(steps)
    assert step_checks["ck_benefit_calculation_steps_number"] == "step_number >= 1"
    assert step_checks["ck_benefit_calculation_steps_input_amount"] == (
        "input_amount IS NULL OR input_amount >= 0"
    )
    assert step_checks["ck_benefit_calculation_steps_output_amount"] == (
        "output_amount IS NULL OR output_amount >= 0"
    )
    assert step_checks["ck_benefit_calculation_steps_input_currency"] == (
        "input_currency IS NULL OR input_currency ~ '^[A-Z]{3}$'"
    )
    assert step_checks["ck_benefit_calculation_steps_output_currency"] == (
        "output_currency IS NULL OR output_currency ~ '^[A-Z]{3}$'"
    )


def test_migration_never_adds_private_files_paths_or_raw_content() -> None:
    _, operations = run_upgrade()
    forbidden = {
        "source_path",
        "absolute_path",
        "file_path",
        "file_id",
        "raw_text",
        "document_text",
        "ocr_text",
        "pdf_bytes",
        "image_bytes",
        "password",
        "diagnosis",
        "medical_text",
    }

    assert not forbidden & {
        column.name for table in operations.tables.values() for column in table.columns
    }


def test_downgrade_drops_tables_in_reverse_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "benefit_calculation_steps",
        "benefit_calculations",
        "receipt_lines",
    ]
    assert operations.dropped_indexes
