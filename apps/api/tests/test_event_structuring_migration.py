"""Migration contract for the non-authoritative event structuring boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0009_event_structuring.py"

EXPECTED_TABLES = {
    "medical_event_structuring_jobs",
    "medical_event_fact_versions",
    "medical_event_fact_audit",
}

FORBIDDEN_COLUMNS = {
    "document_id",
    "document_version_id",
    "file_id",
    "file_path",
    "absolute_path",
    "source_path",
    "password",
    "archive_key",
    "raw_provider_response",
    "provider_response",
    "provider_payload",
    "tri_state",
    "result",
    "amount",
    "payment",
}


class RecordingOperations:
    """Alembic spy that records additive columns and created SQLAlchemy tables."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.check_constraints: dict[str, dict[str, str]] = {}
        self.added_columns: dict[str, list[sa.Column[Any]]] = {}
        self.dropped_columns: list[tuple[str, str]] = []
        self.dropped_check_constraints: list[tuple[str, str]] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []

    def create_table(self, name: str, *elements: Any, **kwargs: Any) -> sa.Table:
        table = sa.Table(name, self.metadata, *elements, **kwargs)
        self.tables[name] = table
        return table

    def add_column(self, table_name: str, column: sa.Column[Any], **kwargs: Any) -> None:
        self.added_columns.setdefault(table_name, []).append(column)

    def drop_column(self, table_name: str, column_name: str, **kwargs: Any) -> None:
        self.dropped_columns.append((table_name, column_name))

    def create_check_constraint(
        self,
        name: str,
        table_name: str,
        condition: str,
        **kwargs: Any,
    ) -> None:
        self.check_constraints.setdefault(table_name, {})[name] = condition

    def drop_constraint(self, name: str, table_name: str, **kwargs: Any) -> None:
        self.dropped_check_constraints.append((table_name, name))

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
    """Load the revision through Alembic-compatible file discovery."""

    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("event_structuring_migration", MIGRATION_PATH)
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


def checks(table: sa.Table) -> list[str]:
    return [
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]


def unique_constraints(table: sa.Table) -> set[tuple[str, ...]]:
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


def test_revision_is_chained_after_benefit_calculations() -> None:
    migration = load_migration()

    assert migration.revision == "0009_event_structuring"
    assert migration.down_revision == "0008_benefit_calculations"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_adds_bounded_event_and_result_snapshot_metadata() -> None:
    _, operations = run_upgrade()

    assert set(operations.added_columns) == {"claim_candidates", "medical_events"}
    columns = {column.name: column for column in operations.added_columns["medical_events"]}
    assert set(columns) == {"situation_text", "situation_retention_until"}
    assert isinstance(columns["situation_text"].type, sa.String)
    assert columns["situation_text"].type.length == 2000
    assert columns["situation_text"].nullable is True
    assert isinstance(columns["situation_retention_until"].type, sa.DateTime)
    assert columns["situation_retention_until"].type.timezone is True
    assert columns["situation_retention_until"].nullable is True
    assert operations.check_constraints["medical_events"] == {
        "ck_medical_events_situation_text_nonempty": (
            "situation_text IS NULL OR btrim(situation_text) <> ''"
        )
    }
    label_column = operations.added_columns["claim_candidates"][0]
    assert label_column.name == "rider_label_snapshot"
    assert isinstance(label_column.type, sa.String)
    assert label_column.type.length == 160
    assert label_column.nullable is True


def test_upgrade_creates_separate_scoped_structuring_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert all(
        not {"documents", "analysis_jobs", "decision_runs", "rule_evaluations"}
        & set(operations.tables)
        for _ in (0,)
    )
    for table in operations.tables.values():
        assert len(table.c) == len(set(table.c.keys()))
        assert table.c.id.primary_key is True
        assert isinstance(table.c.id.type, sa.UUID)


def test_structuring_jobs_have_strict_queue_state_and_event_lineage() -> None:
    _, operations = run_upgrade()
    jobs = operations.tables["medical_event_structuring_jobs"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "event_version",
        "state",
        "structurer_version",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "attempts",
        "max_attempts",
        "error_code",
        "provider_request_id",
        "created_at",
        "updated_at",
        "completed_at",
    } == set(jobs.c.keys())
    assert foreign_keys(jobs) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
    }
    job_checks = checks(jobs)
    expected_job_states = (
        "state IN ('queued', 'running', 'succeeded', 'retryable_failed', "
        "'permanently_failed', 'cancelled')"
    )
    assert expected_job_states in job_checks
    assert "event_version >= 1" in job_checks
    assert "attempts >= 0" in job_checks
    assert "max_attempts >= 1" in job_checks
    assert "max_attempts <= 10" in job_checks
    assert "attempts <= max_attempts" in job_checks
    assert "structurer_version <> ''" in job_checks
    assert any("error_code IS NULL OR error_code IN (" in check for check in job_checks)
    assert any(
        "provider_request_id IS NULL OR provider_request_id <> ''" in check for check in job_checks
    )


def test_fact_versions_preserve_scoped_source_and_parent_lineage() -> None:
    _, operations = run_upgrade()
    versions = operations.tables["medical_event_fact_versions"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "structuring_job_id",
        "parent_version_id",
        "event_version",
        "version",
        "source",
        "version_state",
        "facts_json",
        "questions_json",
        "issue_codes_json",
        "is_current",
        "created_at",
    } == set(versions.c.keys())
    assert foreign_keys(versions) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
        "structuring_job_id": (
            "medical_event_structuring_jobs.id",
            "RESTRICT",
        ),
        "parent_version_id": (
            "medical_event_fact_versions.id",
            "RESTRICT",
        ),
    }
    assert isinstance(versions.c.facts_json.type, postgresql.JSONB)
    assert isinstance(versions.c.questions_json.type, postgresql.JSONB)
    assert isinstance(versions.c.issue_codes_json.type, postgresql.JSONB)
    assert unique_constraints(versions) == {
        ("medical_event_id", "event_version", "version"),
    }
    version_checks = checks(versions)
    assert "event_version >= 1" in version_checks
    assert "version >= 1" in version_checks
    assert "source IN ('ai', 'user', 'system')" in version_checks
    assert "version_state IN ('candidate', 'applied', 'superseded')" in version_checks
    assert "jsonb_typeof(facts_json) = 'object'" in version_checks
    assert "jsonb_typeof(questions_json) = 'array'" in version_checks
    assert "jsonb_typeof(issue_codes_json) = 'array'" in version_checks


def test_fact_audit_is_append_only_metadata_without_raw_values() -> None:
    _, operations = run_upgrade()
    audit = operations.tables["medical_event_fact_audit"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "fact_version_id",
        "parent_version_id",
        "event_version",
        "action",
        "actor_kind",
        "changed_fields_json",
        "reason_code",
        "created_at",
    } == set(audit.c.keys())
    assert foreign_keys(audit) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
        "fact_version_id": ("medical_event_fact_versions.id", "RESTRICT"),
        "parent_version_id": (
            "medical_event_fact_versions.id",
            "RESTRICT",
        ),
    }
    assert isinstance(audit.c.changed_fields_json.type, postgresql.JSONB)
    audit_checks = checks(audit)
    assert "event_version >= 1" in audit_checks
    assert "action IN ('created', 'overridden', 'conflict_detected', 'superseded')" in audit_checks
    assert "actor_kind IN ('ai', 'user', 'system')" in audit_checks
    assert "jsonb_typeof(changed_fields_json) = 'array'" in audit_checks
    assert "reason_code <> ''" in audit_checks


def test_no_private_or_authoritative_columns_are_created() -> None:
    _, operations = run_upgrade()

    all_columns = {
        column.name.lower() for table in operations.tables.values() for column in table.columns
    }
    all_columns.update(
        column.name.lower() for columns in operations.added_columns.values() for column in columns
    )
    assert not FORBIDDEN_COLUMNS & all_columns


def test_downgrade_reverses_indexes_tables_and_event_columns() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "medical_event_fact_audit",
        "medical_event_fact_versions",
        "medical_event_structuring_jobs",
    ]
    assert operations.dropped_columns == [
        ("claim_candidates", "rider_label_snapshot"),
        ("medical_events", "situation_retention_until"),
        ("medical_events", "situation_text"),
    ]
    assert operations.dropped_check_constraints == [
        ("medical_events", "ck_medical_events_situation_text_nonempty")
    ]
    assert operations.dropped_indexes
