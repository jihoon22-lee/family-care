"""Migration contract for the private policy structuring queue."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0016_policy_structuring_jobs.py"


class RecordingOperations:
    """Small Alembic spy that materializes tables and additive operations."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.added_columns: dict[str, list[sa.Column[Any]]] = {}
        self.check_constraints: dict[str, dict[str, str]] = {}
        self.unique_constraints: dict[str, dict[str, tuple[str, ...]]] = {}
        self.operations: list[tuple[str, str, str | None]] = []

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

    def add_column(self, table_name: str, column: sa.Column[Any], **_: Any) -> None:
        self.added_columns.setdefault(table_name, []).append(column)
        self.operations.append(("add_column", table_name, column.name))

    def create_check_constraint(
        self,
        name: str,
        table_name: str,
        condition: str,
        **_: Any,
    ) -> None:
        self.check_constraints.setdefault(table_name, {})[name] = condition
        self.operations.append(("create_check", name, table_name))

    def create_unique_constraint(
        self,
        name: str,
        table_name: str,
        columns: list[str],
        **_: Any,
    ) -> None:
        self.unique_constraints.setdefault(table_name, {})[name] = tuple(columns)
        self.operations.append(("create_unique", name, table_name))

    def drop_index(self, name: str, **_: Any) -> None:
        self.operations.append(("drop_index", name, None))

    def drop_constraint(self, name: str, table_name: str, **_: Any) -> None:
        self.operations.append(("drop_constraint", name, table_name))

    def drop_column(self, table_name: str, column_name: str, **_: Any) -> None:
        self.operations.append(("drop_column", table_name, column_name))

    def drop_table(self, name: str) -> None:
        self.operations.append(("drop_table", name, None))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location(
        "policy_structuring_job_migration", MIGRATION_PATH
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


def checks(table: sa.Table) -> set[str]:
    return {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
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


def unique_columns(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def server_default_arg(column: sa.Column[Any]) -> str:
    default = cast(Any, column.server_default)
    assert default is not None
    return str(default.arg)


def test_revision_is_chained_after_private_batch_document_kind() -> None:
    migration = load_migration()

    assert migration.revision == "0016_policy_structuring_jobs"
    assert migration.down_revision == "0015_private_batch_document_kind"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_creates_only_the_policy_job_table_and_safe_columns() -> None:
    _, operations = run_upgrade()
    assert set(operations.tables) == {"policy_structuring_jobs"}
    table = operations.tables["policy_structuring_jobs"]

    assert set(table.c.keys()) == {
        "id",
        "household_space_id",
        "batch_item_id",
        "family_member_id",
        "document_version_id",
        "extraction_id",
        "policy_aggregate_id",
        "state",
        "pipeline_version",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "attempts",
        "max_attempts",
        "error_code",
        "created_at",
        "updated_at",
        "completed_at",
    }
    forbidden = {"source_key", "path", "source_path", "text", "provider_payload", "raw_response"}
    assert not forbidden & set(table.c.keys())
    assert table.c.id.primary_key is True
    assert table.c.id.nullable is False
    assert server_default_arg(table.c.id) == "gen_random_uuid()"
    assert server_default_arg(table.c.policy_aggregate_id) == "gen_random_uuid()"
    assert server_default_arg(table.c.state) == "'queued'"
    assert server_default_arg(table.c.attempts) == "0"
    assert server_default_arg(table.c.max_attempts) == "5"
    assert server_default_arg(table.c.available_at) == "CURRENT_TIMESTAMP"
    assert server_default_arg(table.c.created_at) == "CURRENT_TIMESTAMP"
    assert server_default_arg(table.c.updated_at) == "CURRENT_TIMESTAMP"


def test_upgrade_scopes_lineage_and_enforces_queue_invariants() -> None:
    _, operations = run_upgrade()
    table = operations.tables["policy_structuring_jobs"]

    assert foreign_keys(table) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "batch_item_id": ("document_batch_items.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "document_version_id": ("document_versions.id", "RESTRICT"),
        "extraction_id": ("extractions.id", "RESTRICT"),
    }
    assert unique_columns(table) == {
        ("batch_item_id",),
        ("extraction_id",),
        ("policy_aggregate_id",),
    }
    table_checks = checks(table)
    assert (
        "state IN ('queued', 'running', 'succeeded', 'retryable_failed', "
        "'permanently_failed', 'cancelled')"
    ) in table_checks
    assert "btrim(pipeline_version) <> ''" in table_checks
    assert (
        "attempts >= 0 AND attempts <= max_attempts AND max_attempts >= 1 AND max_attempts <= 5"
        in table_checks
    )
    assert any("error_code IS NULL OR error_code IN (" in item for item in table_checks)
    assert (
        "((state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND heartbeat_at IS NOT NULL) OR (state <> 'running' AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND heartbeat_at IS NULL))"
    ) in table_checks
    assert (
        "((state IN ('succeeded', 'permanently_failed', 'cancelled') AND completed_at IS NOT NULL) "
        "OR (state NOT IN ('succeeded', 'permanently_failed', 'cancelled') "
        "AND completed_at IS NULL))"
    ) in table_checks


def test_upgrade_adds_job_link_to_candidate_versions_without_breaking_manual_rows() -> None:
    _, operations = run_upgrade()

    columns = {
        column.name: column for column in operations.added_columns["analysis_candidate_versions"]
    }
    assert set(columns) == {"structuring_job_id", "source_candidate_id"}
    assert columns["structuring_job_id"].nullable is True
    assert columns["source_candidate_id"].nullable is True
    assert foreign_keys(
        sa.Table("analysis_candidate_versions", sa.MetaData(), columns["structuring_job_id"])
    ) == {"structuring_job_id": ("policy_structuring_jobs.id", "RESTRICT")}
    assert operations.check_constraints["analysis_candidate_versions"] == {
        "ck_candidate_versions_structuring_source_pair": (
            "((structuring_job_id IS NULL AND source_candidate_id IS NULL) OR "
            "(structuring_job_id IS NOT NULL AND source_candidate_id IS NOT NULL))"
        )
    }
    assert operations.unique_constraints["analysis_candidate_versions"] == {
        "uq_candidate_versions_structuring_source": (
            "structuring_job_id",
            "source_candidate_id",
        )
    }
    assert operations.indexes["ix_candidate_versions_structuring_job_id"] == {
        "table_name": "analysis_candidate_versions",
        "columns": ["structuring_job_id"],
        "unique": False,
    }


def test_queue_and_candidate_objects_are_removed_in_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations
    migration.downgrade()

    drop_job = next(
        i
        for i, item in enumerate(operations.operations)
        if item == ("drop_table", "policy_structuring_jobs", None)
    )
    drop_candidate_columns = [
        i
        for i, item in enumerate(operations.operations)
        if item[0] in {"drop_column", "drop_constraint", "drop_index"}
        and (item[1] == "analysis_candidate_versions" or item[2] == "analysis_candidate_versions")
    ]
    assert drop_candidate_columns
    assert max(drop_candidate_columns) < drop_job
    assert operations.operations[-1] == ("drop_table", "policy_structuring_jobs", None)
