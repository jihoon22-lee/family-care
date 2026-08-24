"""Unit contracts for the Phase 1 document-ingestion migration."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0002_document_ingestion.py"

EXPECTED_TABLES = {
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
    """Small Alembic operations spy that materializes created SQLAlchemy tables."""

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
        self.indexes[name] = {
            "table_name": table_name,
            "columns": columns,
            **kwargs,
        }

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)

    def drop_index(self, name: str, **kwargs: Any) -> None:
        self.dropped_indexes.append(name)


def load_migration() -> ModuleType:
    """Load the migration by path, matching Alembic's file-based discovery."""

    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("document_ingestion_migration", MIGRATION_PATH)
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


def test_revision_is_chained_from_foundation() -> None:
    migration = load_migration()

    assert migration.revision == "0002_document_ingestion"
    assert migration.down_revision == "0001_foundation"


def test_upgrade_creates_exactly_the_eight_ingestion_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert not {
        "app_users",
        "household_spaces",
        "family_members",
        "policy_contracts",
        "policy_parties",
        "riders",
        "clauses",
        "coverage_rules",
        "medical_events",
        "claim_candidates",
        "claim_cases",
    } & set(operations.tables)


def test_table_columns_are_unique_and_page_shape_is_exact() -> None:
    _, operations = run_upgrade()

    for table in operations.tables.values():
        assert len(table.c) == len(set(table.c.keys()))

    assert set(operations.tables["extraction_pages"].c.keys()) == {
        "id",
        "extraction_id",
        "page_number",
        "width_points",
        "height_points",
        "non_whitespace_chars",
        "alphanumeric_ratio",
        "replacement_character_ratio",
        "maximum_repeated_character_run",
        "classification",
        "warning_codes",
    }


def test_source_keys_use_contract_length_and_reject_empty_values() -> None:
    _, operations = run_upgrade()

    for table_name in ("documents", "analysis_jobs"):
        source_key = operations.tables[table_name].c.source_key
        assert cast(Any, source_key.type).length == 512
        assert "source_key <> ''" in constraints(operations.tables[table_name])


def test_all_primary_keys_and_foreign_keys_use_uuid() -> None:
    _, operations = run_upgrade()

    for table in operations.tables.values():
        primary_key = next(iter(table.primary_key.columns))
        assert isinstance(primary_key.type, sa.UUID)
        assert primary_key.nullable is False

        for column in table.columns:
            if column.foreign_keys:
                assert isinstance(column.type, sa.UUID)

    assert foreign_keys(operations.tables["document_versions"]) == {
        "document_id": "documents.id",
    }
    assert foreign_keys(operations.tables["extractions"]) == {
        "document_version_id": "document_versions.id",
    }
    assert foreign_keys(operations.tables["extraction_pages"]) == {
        "extraction_id": "extractions.id",
    }
    assert foreign_keys(operations.tables["extraction_blocks"]) == {
        "page_id": "extraction_pages.id",
    }
    assert foreign_keys(operations.tables["extraction_tables"]) == {
        "page_id": "extraction_pages.id",
    }
    assert foreign_keys(operations.tables["extraction_cells"]) == {
        "table_id": "extraction_tables.id",
    }
    assert foreign_keys(operations.tables["analysis_jobs"]) == {
        "document_id": "documents.id",
    }


def test_timestamps_are_timezone_aware_and_required() -> None:
    _, operations = run_upgrade()

    timestamp_columns = {
        "documents": {"created_at", "updated_at"},
        "document_versions": {"created_at"},
        "extractions": {"succeeded_at", "created_at"},
        "analysis_jobs": {
            "available_at",
            "lease_expires_at",
            "heartbeat_at",
            "created_at",
            "updated_at",
        },
    }

    for table_name, names in timestamp_columns.items():
        table = operations.tables[table_name]
        for name in names:
            column = table.c[name]
            assert isinstance(column.type, sa.DateTime)
            assert column.type.timezone is True
            if name not in {"succeeded_at", "lease_expires_at", "heartbeat_at"}:
                assert column.nullable is False


def test_document_and_version_identity_constraints_are_explicit() -> None:
    _, operations = run_upgrade()

    documents = operations.tables["documents"]
    versions = operations.tables["document_versions"]

    assert {index.name for index in documents.indexes if index.unique} == set()
    assert "uq_documents_active_source_key" in operations.indexes
    source_index = operations.indexes["uq_documents_active_source_key"]
    assert source_index["table_name"] == "documents"
    assert source_index["columns"] == ["source_key"]
    assert source_index["unique"] is True
    assert str(source_index["postgresql_where"]) == "deleted_at IS NULL"

    unique_constraints = {
        tuple(column.name for column in constraint.columns)
        for constraint in versions.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique_constraints == {
        ("document_id", "version_number"),
        ("document_id", "content_sha256"),
    }


def test_extraction_and_coordinate_uniqueness_rules_are_explicit() -> None:
    _, operations = run_upgrade()

    partial = operations.indexes["uq_extractions_succeeded_config"]
    assert partial["table_name"] == "extractions"
    assert partial["columns"] == ["document_version_id", "extractor_config_hash"]
    assert partial["unique"] is True
    assert str(partial["postgresql_where"]) == "status = 'succeeded'"

    expected_constraints = {
        "extraction_pages": {("extraction_id", "page_number")},
        "extraction_blocks": {("page_id", "reading_order")},
        "extraction_cells": {("table_id", "row_index", "column_index")},
    }
    for table_name, expected in expected_constraints.items():
        table = operations.tables[table_name]
        actual = {
            tuple(column.name for column in constraint.columns)
            for constraint in table.constraints
            if isinstance(constraint, sa.UniqueConstraint)
        }
        assert actual == expected


def test_fk_and_queue_lookup_indexes_are_explicit() -> None:
    _, operations = run_upgrade()

    expected_indexes = {
        "ix_document_versions_document_id": ("document_versions", ["document_id"]),
        "ix_extractions_document_version_id": ("extractions", ["document_version_id"]),
        "ix_extraction_pages_extraction_id": ("extraction_pages", ["extraction_id"]),
        "ix_extraction_blocks_page_id": ("extraction_blocks", ["page_id"]),
        "ix_extraction_tables_page_id": ("extraction_tables", ["page_id"]),
        "ix_extraction_cells_table_id": ("extraction_cells", ["table_id"]),
        "ix_analysis_jobs_document_id": ("analysis_jobs", ["document_id"]),
        "ix_analysis_jobs_claim": ("analysis_jobs", ["state", "available_at"]),
        "ix_analysis_jobs_lease_expiry": (
            "analysis_jobs",
            ["state", "lease_expires_at"],
        ),
    }
    assert set(operations.indexes) >= set(expected_indexes)
    for name, (table_name, columns) in expected_indexes.items():
        index = operations.indexes[name]
        assert index["table_name"] == table_name
        assert index["columns"] == columns
        assert index["unique"] is False


def test_required_state_and_coordinate_checks_are_present() -> None:
    _, operations = run_upgrade()

    job_states = (
        "state IN ('queued', 'running', 'succeeded', 'retryable_failed', "
        "'permanently_failed', 'cancelled')"
    )
    assert job_states in constraints(operations.tables["analysis_jobs"])
    extraction_states = "status IN ('running', 'succeeded', 'failed')"
    assert extraction_states in constraints(operations.tables["extractions"])
    assert (
        "(status = 'succeeded' AND succeeded_at IS NOT NULL) OR "
        "(status <> 'succeeded' AND succeeded_at IS NULL)"
        in constraints(operations.tables["extractions"])
    )
    assert "status IN ('pending', 'ready', 'failed')" in constraints(operations.tables["documents"])
    assert "classification IN ('TEXT_SUFFICIENT', 'OCR_REQUIRED')" in constraints(
        operations.tables["extraction_pages"]
    )
    review_states = "review_state IN ('candidate', 'confirmed', 'rejected')"
    assert review_states in constraints(operations.tables["extraction_tables"])
    assert review_states in constraints(operations.tables["extraction_cells"])
    assert "page_number >= 1" in constraints(operations.tables["extraction_pages"])
    assert "reading_order >= 0" in constraints(operations.tables["extraction_blocks"])
    assert "row_index >= 0" in constraints(operations.tables["extraction_cells"])
    assert "column_index >= 0" in constraints(operations.tables["extraction_cells"])
    assert "page_count >= 1" in constraints(operations.tables["document_versions"])


def test_job_error_and_attempt_constraints_are_explicit() -> None:
    _, operations = run_upgrade()

    job_checks = constraints(operations.tables["analysis_jobs"])
    assert "attempts <= max_attempts" in job_checks
    assert any(
        check.startswith("error_code IS NULL OR error_code IN (")
        and "'PASSWORD_REQUIRED'" in check
        and "'TEMP_CLEANUP_FAILED'" in check
        and "'ANALYSIS_JOB_NOT_FOUND'" in check
        for check in job_checks
    )


def test_forbidden_payload_and_redundant_hash_columns_are_absent() -> None:
    _, operations = run_upgrade()

    forbidden = {"password", "absolute_path", "raw_pdf", "raw_pdf_bytes", "document_body"}
    for table in operations.tables.values():
        assert not forbidden & set(table.c)
    assert "content_sha256" not in operations.tables["extractions"].c


def test_downgrade_drops_the_tables_in_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_indexes == [
        "ix_analysis_jobs_lease_expiry",
        "ix_analysis_jobs_claim",
        "ix_analysis_jobs_document_id",
        "ix_extraction_cells_table_id",
        "ix_extraction_tables_page_id",
        "ix_extraction_blocks_page_id",
        "ix_extraction_pages_extraction_id",
        "uq_extractions_succeeded_config",
        "ix_extractions_document_version_id",
        "ix_document_versions_document_id",
        "uq_documents_active_source_key",
    ]
    assert operations.dropped_tables == [
        "analysis_jobs",
        "extraction_cells",
        "extraction_blocks",
        "extraction_tables",
        "extraction_pages",
        "extractions",
        "document_versions",
        "documents",
    ]
