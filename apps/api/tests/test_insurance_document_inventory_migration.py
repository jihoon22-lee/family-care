"""Migration contract for reviewed insurance-document inventory metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0017_insurance_document_inventory.py"


class RecordingOperations:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.checks: dict[str, dict[str, str]] = {}
        self.added_columns: dict[str, list[sa.Column[Any]]] = {}
        self.executed_statements: list[str] = []
        self.dropped_columns: list[tuple[str, str]] = []
        self.operations: list[tuple[str, str, str | None]] = []

    def create_table(self, name: str, *elements: Any, **kwargs: Any) -> sa.Table:
        table = sa.Table(name, self.metadata, *elements, **kwargs)
        self.tables[name] = table
        self.operations.append(("create_table", name, None))
        return table

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        self.indexes[name] = {"table_name": table_name, "columns": columns, **kwargs}
        self.operations.append(("create_index", name, table_name))

    def create_check_constraint(self, name: str, table_name: str, condition: str, **_: Any) -> None:
        self.checks.setdefault(table_name, {})[name] = condition
        self.operations.append(("create_check", name, table_name))

    def drop_constraint(self, name: str, table_name: str, **_: Any) -> None:
        self.operations.append(("drop_constraint", name, table_name))

    def alter_column(self, table_name: str, column_name: str, **_: Any) -> None:
        self.operations.append(("alter_column", column_name, table_name))

    def add_column(self, table_name: str, column: sa.Column[Any], **_: Any) -> None:
        self.added_columns.setdefault(table_name, []).append(column)
        self.operations.append(("add_column", column.name, table_name))

    def execute(self, statement: str, **_: Any) -> None:
        self.executed_statements.append(statement)
        self.operations.append(("execute", statement, None))

    def drop_column(self, table_name: str, column_name: str, **_: Any) -> None:
        self.dropped_columns.append((table_name, column_name))

    def drop_index(self, name: str, **_: Any) -> None:
        self.operations.append(("drop_index", name, None))

    def drop_table(self, name: str) -> None:
        self.operations.append(("drop_table", name, None))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("insurance_document_inventory", MIGRATION_PATH)
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


def test_revision_extends_both_source_kind_constraints() -> None:
    migration, operations = run_upgrade()

    assert migration.revision == "0017_insurance_inventory"
    assert migration.down_revision == "0016_policy_structuring_jobs"
    assert operations.checks["documents"]["ck_documents_document_kind"] == (
        "document_kind IN ('policy', 'terms', 'product_explanation', 'application', "
        "'amendment', 'claim', 'supporting')"
    )
    assert operations.checks["document_batch_items"]["ck_document_batch_items_document_kind"] == (
        "document_kind IN ('policy', 'terms', 'product_explanation', 'application', 'supporting')"
    )
    processed_version = operations.added_columns["document_batch_items"][0]
    assert processed_version.name == "processed_document_version_id"
    assert processed_version.nullable is True
    foreign_key = next(iter(processed_version.foreign_keys))
    assert foreign_key.target_fullname == "document_versions.id"
    assert foreign_key.ondelete == "RESTRICT"
    backfill_sql = "\n".join(operations.executed_statements)
    assert "policy_structuring_jobs" in backfill_sql
    assert "managed_archives" in backfill_sql
    assert "archive.created_at <= item.completed_at" in backfill_sql
    assert "archive.retired_at IS NULL" in backfill_sql
    assert "archive.retired_at >= item.completed_at" in backfill_sql


def test_upgrade_creates_scoped_component_set_and_item_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == {
        "insurance_document_components",
        "insurance_document_sets",
        "insurance_document_set_items",
    }
    components = operations.tables["insurance_document_components"]
    sets = operations.tables["insurance_document_sets"]
    items = operations.tables["insurance_document_set_items"]

    assert foreign_keys(components) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "document_batch_item_id": ("document_batch_items.id", "RESTRICT"),
        "document_version_id": ("document_versions.id", "RESTRICT"),
        "evidence_id": ("evidence.id", "RESTRICT"),
        "created_by": ("app_users.id", "RESTRICT"),
    }
    assert foreign_keys(sets) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "policy_contract_id": ("policy_contracts.id", "RESTRICT"),
        "created_by": ("app_users.id", "RESTRICT"),
    }
    assert foreign_keys(items) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "insurance_document_set_id": ("insurance_document_sets.id", "RESTRICT"),
        "policy_contract_id": ("policy_contracts.id", "RESTRICT"),
        "insurance_document_component_id": (
            "insurance_document_components.id",
            "RESTRICT",
        ),
        "document_batch_item_id": ("document_batch_items.id", "RESTRICT"),
        "document_version_id": ("document_versions.id", "RESTRICT"),
        "evidence_id": ("evidence.id", "RESTRICT"),
        "confirmed_by": ("app_users.id", "RESTRICT"),
    }


def test_component_and_item_checks_keep_roles_pages_reviews_and_versions_bounded() -> None:
    _, operations = run_upgrade()
    component_checks = checks(operations.tables["insurance_document_components"])
    item_checks = checks(operations.tables["insurance_document_set_items"])

    assert (
        "role IN ('policy', 'terms', 'product_explanation', 'application', 'supporting')"
        in component_checks
    )
    assert "page_start >= 1 AND page_end >= page_start" in component_checks
    assert (
        "review_state IN ('SUGGESTED', 'USER_CONFIRMED', 'CONFLICT', 'REJECTED')"
        in component_checks
    )
    assert "version >= 1" in component_checks
    assert "match_state IN ('SUGGESTED', 'USER_CONFIRMED', 'CONFLICT', 'REJECTED')" in item_checks
    assert "version >= 1" in item_checks
    assert any("match_state = 'USER_CONFIRMED'" in value for value in item_checks)


def test_active_identity_indexes_and_safe_metadata_only_columns() -> None:
    _, operations = run_upgrade()
    assert operations.indexes["uq_insurance_document_components_active_identity"]["unique"] is True
    assert operations.indexes["uq_insurance_document_sets_active_policy"]["unique"] is True
    assert operations.indexes["uq_insurance_document_sets_active_policy"]["columns"] == [
        "household_space_id",
        "family_member_id",
        "policy_contract_id",
    ]
    assert operations.indexes["uq_insurance_document_set_items_active_link"]["unique"] is True

    forbidden = {
        "source_key",
        "path",
        "raw_text",
        "ocr_text",
        "password",
        "policy_number",
        "provider_payload",
    }
    all_columns = {column.name for table in operations.tables.values() for column in table.columns}
    assert forbidden.isdisjoint(all_columns)


def test_downgrade_removes_inventory_before_restoring_old_constraints() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    dropped_tables = [item[1] for item in operations.operations if item[0] == "drop_table"]
    assert dropped_tables == [
        "insurance_document_set_items",
        "insurance_document_sets",
        "insurance_document_components",
    ]
    assert operations.dropped_columns == [("document_batch_items", "processed_document_version_id")]
    downgrade_sql = "\n".join(operations.executed_statements)
    assert (
        "UPDATE document_batch_items SET document_kind = 'supporting' "
        "WHERE document_kind IN ('product_explanation', 'application')" in downgrade_sql
    )
    assert (
        "UPDATE documents SET document_kind = 'supporting' "
        "WHERE document_kind = 'product_explanation'" in downgrade_sql
    )
    execute_indexes = [
        index for index, operation in enumerate(operations.operations) if operation[0] == "execute"
    ]
    alter_index = next(
        index
        for index, operation in enumerate(operations.operations)
        if operation == ("alter_column", "document_kind", "document_batch_items")
    )
    assert execute_indexes and max(execute_indexes) < alter_index
