"""Migration contract for private-knowledge enrollment confirmations."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0019_private_confirmations.py"


class RecordingOperations:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
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

    def drop_index(self, name: str, **kwargs: Any) -> None:
        self.operations.append(("drop_index", name, cast(str | None, kwargs.get("table_name"))))

    def drop_table(self, name: str) -> None:
        self.operations.append(("drop_table", name, None))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location(
        "private_knowledge_confirmations",
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


def checks(table: sa.Table) -> set[str]:
    return {
        str(constraint.sqltext)
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


def test_revision_adds_one_append_only_confirmation_table() -> None:
    migration, operations = run_upgrade()

    assert migration.revision == "0019_private_confirmations"
    assert migration.down_revision == "0018_private_knowledge_catalog"
    assert list(operations.tables) == ["private_knowledge_contract_confirmations"]


def test_confirmation_keeps_actor_date_authority_and_snapshot_lineage() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_contract_confirmations"]

    assert {
        "id",
        "import_run_id",
        "household_space_id",
        "knowledge_contract_id",
        "decision",
        "confirmed_status",
        "status_as_of",
        "authority",
        "reason_code",
        "confirmed_by",
        "confirmed_at",
        "is_current",
        "superseded_at",
        "confirmation_digest_sha256",
        "created_at",
    } == set(table.columns.keys())

    assert composite_foreign_keys(table) >= {
        (
            ("knowledge_contract_id", "import_run_id"),
            (
                "private_knowledge_contracts.id",
                "private_knowledge_contracts.import_run_id",
            ),
            "RESTRICT",
        ),
        (
            ("import_run_id", "household_space_id"),
            (
                "private_knowledge_import_runs.id",
                "private_knowledge_import_runs.household_space_id",
            ),
            "RESTRICT",
        ),
        (
            ("confirmed_by", "household_space_id"),
            ("app_users.id", "app_users.household_space_id"),
            "RESTRICT",
        ),
    }

    table_checks = checks(table)
    assert "decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')" in table_checks
    assert (
        "confirmed_status IN ('active', 'inactive', 'lapsed', 'terminated', 'unknown')"
        in table_checks
    )
    assert any(
        "decision = 'MATCH'" in value
        and "confirmed_status <> 'unknown'" in value
        and "decision <> 'MATCH'" in value
        and "confirmed_status = 'unknown'" in value
        for value in table_checks
    )
    assert "authority = 'USER_CONFIRMED_CURRENT_ENROLLMENT'" in table_checks
    assert any(
        "is_current = true" in value
        and "superseded_at IS NULL" in value
        and "is_current = false" in value
        and "superseded_at IS NOT NULL" in value
        for value in table_checks
    )
    assert "confirmation_digest_sha256 ~ '^[0-9a-f]{64}$'" in table_checks
    assert any("status_as_of" in value and "confirmed_at" in value for value in table_checks)


def test_confirmation_indexes_enforce_idempotency_and_one_current_row() -> None:
    _, operations = run_upgrade()

    digest = operations.indexes["uq_private_knowledge_confirmation_digest"]
    assert digest["unique"] is True
    assert digest["columns"] == ["import_run_id", "confirmation_digest_sha256"]

    current = operations.indexes["uq_private_knowledge_confirmation_current"]
    assert current["unique"] is True
    assert current["columns"] == ["knowledge_contract_id"]
    assert str(current["postgresql_where"]) == "is_current"

    lookup = operations.indexes["ix_private_knowledge_confirmation_household"]
    assert lookup["columns"] == [
        "household_space_id",
        "import_run_id",
        "knowledge_contract_id",
    ]
