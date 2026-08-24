"""Unit contracts for the Phase 2 policy-ledger migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0003_policy_ledger.py"

EXPECTED_TABLES = {
    "household_spaces",
    "family_members",
    "evidence",
    "policy_contracts",
    "policy_parties",
    "riders",
    "policy_status_snapshots",
}
PHASE_ONE_TABLES = {
    "documents",
    "document_versions",
    "extractions",
    "extraction_pages",
    "extraction_blocks",
    "extraction_tables",
    "extraction_cells",
    "analysis_jobs",
}


class RecordingOperations:
    """Alembic operations spy that materializes created SQLAlchemy tables."""

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
    """Load the migration through Alembic-compatible file discovery."""

    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("policy_ledger_migration", MIGRATION_PATH)
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


def constraints(table: sa.Table) -> list[str]:
    return [
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    ]


def foreign_keys(table: sa.Table) -> dict[str, str]:
    return {
        column.name: next(iter(column.foreign_keys)).target_fullname
        for column in table.columns
        if column.foreign_keys
    }


def test_revision_is_chained_from_document_ingestion() -> None:
    migration = load_migration()

    assert migration.revision == "0003_policy_ledger"
    assert migration.down_revision == "0002_document_ingestion"


def test_upgrade_creates_exactly_the_seven_policy_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert not PHASE_ONE_TABLES & set(operations.tables)


def test_primary_and_foreign_keys_are_uuid_with_exact_lineage() -> None:
    _, operations = run_upgrade()

    for table in operations.tables.values():
        primary_key = next(iter(table.primary_key.columns))
        assert isinstance(primary_key.type, sa.UUID)
        assert primary_key.nullable is False
        for column in table.columns:
            if column.foreign_keys:
                assert isinstance(column.type, sa.UUID)

    assert foreign_keys(operations.tables["family_members"]) == {
        "household_space_id": "household_spaces.id"
    }
    assert foreign_keys(operations.tables["evidence"]) == {
        "household_space_id": "household_spaces.id",
        "document_version_id": "document_versions.id",
        "extraction_id": "extractions.id",
    }
    assert foreign_keys(operations.tables["policy_contracts"]) == {
        "household_space_id": "household_spaces.id",
        "source_document_version_id": "document_versions.id",
        "source_evidence_id": "evidence.id",
        "status_evidence_id": "evidence.id",
    }
    assert foreign_keys(operations.tables["policy_parties"]) == {
        "household_space_id": "household_spaces.id",
        "policy_contract_id": "policy_contracts.id",
        "family_member_id": "family_members.id",
        "evidence_id": "evidence.id",
    }
    assert foreign_keys(operations.tables["riders"]) == {
        "household_space_id": "household_spaces.id",
        "policy_contract_id": "policy_contracts.id",
        "source_evidence_id": "evidence.id",
        "status_evidence_id": "evidence.id",
    }


def test_scoped_mutable_tables_have_version_and_soft_delete_columns() -> None:
    _, operations = run_upgrade()

    for table_name in EXPECTED_TABLES - {"household_spaces", "evidence"}:
        table = operations.tables[table_name]
        assert "household_space_id" in table.c
        assert "version" in table.c
        assert "updated_at" in table.c
        assert "deleted_at" in table.c
        assert "version >= 1" in constraints(table)

    household = operations.tables["household_spaces"]
    assert {"version", "created_at", "updated_at", "deleted_at"} <= set(household.c.keys())


def test_evidence_requires_document_hash_page_extraction_and_valid_bbox() -> None:
    _, operations = run_upgrade()
    evidence = operations.tables["evidence"]
    checks = "\n".join(constraints(evidence))

    assert evidence.c.document_version_id.nullable is False
    assert evidence.c.extraction_id.nullable is False
    assert evidence.c.content_sha256.nullable is False
    assert evidence.c.physical_page.nullable is False
    assert "content_sha256 ~ '^[0-9a-f]{64}$'" in checks
    assert "physical_page >= 1" in checks
    assert "x0 IS NULL" in checks
    assert "x1 > x0" in checks
    assert "y1 > y0" in checks


def test_policy_and_rider_constraints_preserve_unknown_current_state() -> None:
    _, operations = run_upgrade()
    policy_checks = "\n".join(constraints(operations.tables["policy_contracts"]))
    rider_checks = "\n".join(constraints(operations.tables["riders"]))

    assert "'unknown'" in policy_checks
    assert "'unknown'" in rider_checks
    assert "insured_amount IS NULL OR insured_amount >= 0" in rider_checks
    assert "currency IS NULL OR currency ~ '^[A-Z]{3}$'" in rider_checks
    assert "coverage_end_date IS NULL OR coverage_end_date >= coverage_start_date" in rider_checks


def test_status_snapshot_references_exactly_one_policy_target() -> None:
    _, operations = run_upgrade()
    snapshots = operations.tables["policy_status_snapshots"]
    checks = "\n".join(constraints(snapshots))

    assert foreign_keys(snapshots) == {
        "household_space_id": "household_spaces.id",
        "policy_contract_id": "policy_contracts.id",
        "rider_id": "riders.id",
        "evidence_id": "evidence.id",
    }
    assert "policy_contract_id IS NOT NULL AND rider_id IS NULL" in checks
    assert "policy_contract_id IS NULL AND rider_id IS NOT NULL" in checks


def test_scope_and_active_lookup_indexes_are_explicit() -> None:
    _, operations = run_upgrade()

    for table_name in EXPECTED_TABLES - {"household_spaces"}:
        assert any(
            index["table_name"] == table_name and index["columns"][0] == "household_space_id"
            for index in operations.indexes.values()
        )

    active_alias = operations.indexes["uq_family_members_active_alias"]
    assert active_alias["columns"] == ["household_space_id", "internal_alias"]
    assert active_alias["unique"] is True
    assert str(active_alias["postgresql_where"]) == "deleted_at IS NULL"


def test_downgrade_drops_indexes_and_tables_in_reverse_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "policy_status_snapshots",
        "riders",
        "policy_parties",
        "policy_contracts",
        "evidence",
        "family_members",
        "household_spaces",
    ]
    assert operations.dropped_indexes
