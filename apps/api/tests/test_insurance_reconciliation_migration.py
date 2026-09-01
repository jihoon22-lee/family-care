"""Migration contract for insurance ledger reconciliation histories."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0024_insurance_reconciliation.py"


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

    def execute(self, statement: str) -> None:
        self.operations.append(("execute", statement, None))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("insurance_reconciliation", MIGRATION_PATH)
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


def test_revision_adds_two_append_only_reconciliation_histories() -> None:
    migration, operations = run_upgrade()

    assert migration.revision == "0024_insurance_reconciliation"
    assert migration.down_revision == "0023_advisory_disposition"
    assert len(migration.revision) <= 32
    assert list(operations.tables) == [
        "private_knowledge_operational_links",
        "document_batch_item_resolutions",
    ]


def test_operational_links_keep_scope_authority_and_current_history() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_operational_links"]

    assert set(table.columns.keys()) == {
        "id",
        "import_run_id",
        "household_space_id",
        "family_member_id",
        "knowledge_contract_id",
        "policy_contract_id",
        "decision",
        "link_conflict",
        "authority",
        "reason_code",
        "confirmed_by",
        "confirmed_at",
        "is_current",
        "superseded_at",
        "link_digest_sha256",
        "created_at",
    }
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
            ("family_member_id", "household_space_id"),
            ("family_members.id", "family_members.household_space_id"),
            "RESTRICT",
        ),
        (
            ("policy_contract_id", "household_space_id"),
            ("policy_contracts.id", "policy_contracts.household_space_id"),
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
    assert "authority = 'USER_CONFIRMED_OPERATIONAL_IDENTITY'" in table_checks
    assert any(
        "decision = 'MATCH'" in value
        and "policy_contract_id IS NOT NULL" in value
        and "decision <> 'MATCH'" in value
        and "policy_contract_id IS NULL" in value
        for value in table_checks
    )
    assert any(
        "link_conflict = true" in value
        and "decision = 'UNKNOWN'" in value
        and "policy_contract_id IS NULL" in value
        for value in table_checks
    )
    assert any(
        "is_current = true" in value and "superseded_at IS NULL" in value for value in table_checks
    )
    assert "link_digest_sha256 ~ '^[0-9a-f]{64}$'" in table_checks


def test_resolution_history_requires_valid_replacement_shape_and_scope() -> None:
    _, operations = run_upgrade()
    table = operations.tables["document_batch_item_resolutions"]

    assert set(table.columns.keys()) == {
        "id",
        "household_space_id",
        "family_member_id",
        "failed_item_id",
        "replacement_item_id",
        "resolution",
        "authority",
        "reason_code",
        "confirmed_by",
        "confirmed_at",
        "is_current",
        "superseded_at",
        "resolution_digest_sha256",
        "created_at",
    }
    assert composite_foreign_keys(table) >= {
        (
            ("family_member_id", "household_space_id"),
            ("family_members.id", "family_members.household_space_id"),
            "RESTRICT",
        ),
        (
            ("confirmed_by", "household_space_id"),
            ("app_users.id", "app_users.household_space_id"),
            "RESTRICT",
        ),
    }
    table_checks = checks(table)
    assert "resolution IN ('REPLACED', 'DISMISSED', 'REOPENED')" in table_checks
    assert "authority = 'USER_CONFIRMED_DOCUMENT_RESOLUTION'" in table_checks
    assert any(
        "resolution = 'REPLACED'" in value
        and "replacement_item_id IS NOT NULL" in value
        and "resolution IN ('DISMISSED', 'REOPENED')" in value
        and "replacement_item_id IS NULL" in value
        for value in table_checks
    )
    assert any("failed_item_id <> replacement_item_id" in value for value in table_checks)
    assert any(
        "is_current = true" in value and "superseded_at IS NULL" in value for value in table_checks
    )
    assert "resolution_digest_sha256 ~ '^[0-9a-f]{64}$'" in table_checks


def test_partial_indexes_enforce_idempotency_and_single_current_targets() -> None:
    _, operations = run_upgrade()

    link_digest = operations.indexes["uq_pk_operational_links_digest"]
    assert link_digest["unique"] is True
    assert link_digest["columns"] == ["import_run_id", "link_digest_sha256"]

    current_link = operations.indexes["uq_pk_operational_links_current_contract"]
    assert current_link["unique"] is True
    assert current_link["columns"] == ["knowledge_contract_id"]
    assert str(current_link["postgresql_where"]) == "is_current"

    current_policy = operations.indexes["uq_pk_operational_links_current_policy"]
    assert current_policy["unique"] is True
    assert current_policy["columns"] == ["policy_contract_id"]
    assert "is_current" in str(current_policy["postgresql_where"])
    assert "decision = 'MATCH'" in str(current_policy["postgresql_where"])

    resolution_digest = operations.indexes["uq_document_resolutions_digest"]
    assert resolution_digest["unique"] is True
    assert resolution_digest["columns"] == ["failed_item_id", "resolution_digest_sha256"]

    current_resolution = operations.indexes["uq_document_resolutions_current"]
    assert current_resolution["unique"] is True
    assert current_resolution["columns"] == ["failed_item_id"]
    assert str(current_resolution["postgresql_where"]) == "is_current"


def test_history_mutation_guard_allows_only_supersede_and_downgrade_fails_closed() -> None:
    migration, operations = run_upgrade()
    upgrade_sql = "\n".join(item[1] for item in operations.operations if item[0] == "execute")

    assert "CREATE FUNCTION enforce_insurance_reconciliation_history()" in upgrade_sql
    assert "TG_OP = 'DELETE'" in upgrade_sql
    assert "OLD.is_current = true" in upgrade_sql
    assert "NEW.is_current = false" in upgrade_sql
    assert "trg_pk_operational_links_history" in upgrade_sql
    assert "trg_document_resolutions_history" in upgrade_sql

    downgrade_operations = RecordingOperations()
    cast(Any, migration).op = downgrade_operations
    migration.downgrade()
    downgrade_sql = "\n".join(
        item[1] for item in downgrade_operations.operations if item[0] == "execute"
    )
    assert "cannot downgrade insurance reconciliation with history" in downgrade_sql
    assert "DROP TRIGGER trg_pk_operational_links_history" in downgrade_sql
    assert "DROP TRIGGER trg_document_resolutions_history" in downgrade_sql
    assert "DROP FUNCTION enforce_insurance_reconciliation_history()" in downgrade_sql
    assert "DELETE FROM" not in downgrade_sql.upper()
