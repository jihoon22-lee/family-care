"""Migration contract for the claim workflow persistence boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0010_claim_workflow.py"

EXPECTED_TABLES = {
    "claim_cases",
    "claim_case_snapshots",
    "claim_checklist_items",
    "claim_status_events",
    "claim_history",
}

FORBIDDEN_COLUMNS = {
    "path",
    "file",
    "blob",
    "text",
    "raw_text",
    "document_text",
    "ocr",
    "ocr_text",
    "image",
    "image_bytes",
    "source_path",
    "absolute_path",
    "external_document_id",
    "password",
    "diagnosis",
    "medical_text",
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
    spec = importlib.util.spec_from_file_location("claim_workflow_migration", MIGRATION_PATH)
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


def test_revision_is_exactly_chained_from_event_structuring() -> None:
    migration = load_migration()

    assert migration.revision == "0010_claim_workflow"
    assert migration.down_revision == "0009_event_structuring"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_creates_only_the_five_claim_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES


def test_claim_cases_are_scoped_and_have_bounded_claim_metadata() -> None:
    _, operations = run_upgrade()
    table = operations.tables["claim_cases"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "family_member_id",
        "policy_contract_id",
        "insurer_key",
        "status",
        "receipt_number",
        "submitted_at",
        "claimed_amount",
        "paid_amount",
        "currency",
        "outcome_reason_code",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    } == set(table.c.keys())
    assert foreign_keys(table) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "policy_contract_id": ("policy_contracts.id", "RESTRICT"),
    }
    assert table.c.insurer_key.type.length == 160
    assert table.c.receipt_number.type.length == 160
    assert table.c.outcome_reason_code.type.length == 64
    for amount_name in ("claimed_amount", "paid_amount"):
        amount = table.c[amount_name].type
        assert isinstance(amount, sa.Numeric)
        assert amount.precision == 18
        assert amount.scale == 2
    table_checks = checks(table)
    assert (
        "status IN ('preparing', 'submitted', 'supplementation_requested', 'paid', "
        "'partially_paid', 'denied', 'closed')" in table_checks.values()
    )
    assert "claimed_amount IS NULL OR claimed_amount >= 0" in table_checks.values()
    assert "paid_amount IS NULL OR paid_amount >= 0" in table_checks.values()
    assert "currency IS NULL OR currency ~ '^[A-Z]{3}$'" in table_checks.values()
    assert "version >= 1" in table_checks.values()


def test_claim_snapshots_are_structured_immutable_versions() -> None:
    _, operations = run_upgrade()
    table = operations.tables["claim_case_snapshots"]

    assert {
        "id",
        "claim_case_id",
        "snapshot_version",
        "candidate_snapshot_json",
        "rule_snapshot_json",
        "policy_snapshot_json",
        "evidence_snapshot_json",
        "calculation_snapshot_json",
        "snapshot_sha256",
        "created_at",
    } == set(table.c.keys())
    assert foreign_keys(table) == {
        "claim_case_id": ("claim_cases.id", "RESTRICT"),
    }
    assert unique_columns(table) == {("claim_case_id", "snapshot_version")}
    for name in (
        "candidate_snapshot_json",
        "rule_snapshot_json",
        "policy_snapshot_json",
        "evidence_snapshot_json",
    ):
        assert isinstance(table.c[name].type, postgresql.JSONB)
    assert isinstance(table.c.calculation_snapshot_json.type, postgresql.JSONB)
    table_checks = checks(table)
    assert "snapshot_version >= 1" in table_checks.values()
    assert "jsonb_typeof(candidate_snapshot_json) = 'object'" in table_checks.values()
    assert "jsonb_typeof(rule_snapshot_json) = 'object'" in table_checks.values()
    assert "jsonb_typeof(policy_snapshot_json) = 'object'" in table_checks.values()
    assert "jsonb_typeof(evidence_snapshot_json) = 'object'" in table_checks.values()
    assert "jsonb_typeof(calculation_snapshot_json) = 'object'" in table_checks.values()
    assert "snapshot_sha256 ~ '^[a-f0-9]{64}$'" in table_checks.values()


def test_checklist_and_status_events_store_metadata_not_files() -> None:
    _, operations = run_upgrade()
    checklist = operations.tables["claim_checklist_items"]
    status_events = operations.tables["claim_status_events"]

    assert {
        "id",
        "claim_case_id",
        "document_kind",
        "requirement_code",
        "required",
        "conditional",
        "prepared",
        "note_code",
        "source_rule_version_id",
        "source_evidence_id",
        "version",
        "created_at",
        "updated_at",
    } == set(checklist.c.keys())
    assert foreign_keys(checklist) == {
        "claim_case_id": ("claim_cases.id", "RESTRICT"),
        "source_rule_version_id": ("coverage_rule_versions.id", "RESTRICT"),
        "source_evidence_id": ("evidence.id", "RESTRICT"),
    }
    assert {
        "id",
        "claim_case_id",
        "from_status",
        "to_status",
        "occurred_at",
        "reason_code",
        "metadata_json",
        "created_at",
    } == set(status_events.c.keys())
    assert foreign_keys(status_events) == {
        "claim_case_id": ("claim_cases.id", "RESTRICT"),
    }
    assert isinstance(status_events.c.metadata_json.type, postgresql.JSONB)
    checklist_checks = checks(checklist)
    assert "version >= 1" in checklist_checks.values()
    assert "note_code IS NULL OR note_code ~ '^[A-Z][A-Z0-9_]{0,63}$'" in checklist_checks.values()
    event_checks = checks(status_events)
    assert (
        "to_status IN ('preparing', 'submitted', 'supplementation_requested', 'paid', "
        "'partially_paid', 'denied', 'closed')" in event_checks.values()
    )
    assert (
        "from_status IS NULL OR from_status IN ('preparing', 'submitted', "
        "'supplementation_requested', 'paid', 'partially_paid', 'denied', 'closed')"
        in event_checks.values()
    )
    assert "jsonb_typeof(metadata_json) = 'object'" in event_checks.values()


def test_claim_history_has_explicit_outcome_and_payment_bounds() -> None:
    _, operations = run_upgrade()
    table = operations.tables["claim_history"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "family_member_id",
        "policy_contract_id",
        "rider_id",
        "outcome",
        "payment_date",
        "counted_occurrence",
        "amount",
        "currency",
        "reason_code",
        "created_at",
    } == set(table.c.keys())
    assert foreign_keys(table) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "policy_contract_id": ("policy_contracts.id", "RESTRICT"),
        "rider_id": ("riders.id", "RESTRICT"),
    }
    assert isinstance(table.c.amount.type, sa.Numeric)
    assert table.c.amount.type.precision == 18
    assert table.c.amount.type.scale == 2
    history_checks = checks(table)
    assert "outcome IN ('paid', 'partially_paid', 'denied')" in history_checks.values()
    assert "amount IS NULL OR amount >= 0" in history_checks.values()
    assert "currency IS NULL OR currency ~ '^[A-Z]{3}$'" in history_checks.values()
    assert (
        "reason_code IS NULL OR reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'" in history_checks.values()
    )


def test_migration_never_adds_private_document_or_medical_columns() -> None:
    _, operations = run_upgrade()

    columns = {
        column.name.lower() for table in operations.tables.values() for column in table.columns
    }
    assert not FORBIDDEN_COLUMNS & columns


def test_downgrade_drops_tables_in_reverse_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "claim_history",
        "claim_status_events",
        "claim_checklist_items",
        "claim_case_snapshots",
        "claim_cases",
    ]
    assert operations.dropped_indexes
