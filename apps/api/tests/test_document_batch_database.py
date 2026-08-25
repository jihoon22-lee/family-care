"""Migration contract for encrypted document batches and managed archives."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import psycopg
import pytest
import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0012_encrypted_document_import.py"

EXPECTED_TABLES = {
    "document_batches",
    "document_batch_items",
    "managed_archives",
}
FORBIDDEN_COLUMN_PARTS = {
    "absolute_path",
    "archive_master_key",
    "document_text",
    "ocr_text",
    "password",
    "plaintext",
    "raw_pdf",
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
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("encrypted_document_import", MIGRATION_PATH)
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


def test_revision_is_chained_from_local_authentication() -> None:
    migration = load_migration()

    assert migration.revision == "0012_encrypted_document_import"
    assert migration.down_revision == "0011_local_authentication"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_creates_only_batch_and_archive_metadata_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    for table in operations.tables.values():
        column_names = set(table.c.keys())
        assert all(
            forbidden not in column_name
            for forbidden in FORBIDDEN_COLUMN_PARTS
            for column_name in column_names
        )


def test_document_batches_are_household_member_and_creator_scoped() -> None:
    _, operations = run_upgrade()
    table = operations.tables["document_batches"]

    assert set(table.c.keys()) == {
        "id",
        "household_space_id",
        "family_member_id",
        "created_by",
        "state",
        "created_at",
        "updated_at",
        "completed_at",
    }
    assert foreign_keys(table) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "created_by": ("app_users.id", "RESTRICT"),
    }
    assert (
        "state IN ('created', 'running', 'partial', 'succeeded', 'failed', 'cancelled')"
        in checks(table)
    )
    assert (
        "(state IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL) "
        "OR (state IN ('created', 'running', 'partial') AND completed_at IS NULL)" in checks(table)
    )


def test_batch_items_have_bounded_source_and_state_metadata_only() -> None:
    _, operations = run_upgrade()
    table = operations.tables["document_batch_items"]

    assert set(table.c.keys()) == {
        "id",
        "batch_id",
        "document_id",
        "source_id",
        "source_key",
        "display_label",
        "state",
        "error_code",
        "attempts",
        "max_attempts",
        "available_at",
        "lease_owner",
        "lease_expires_at",
        "heartbeat_at",
        "created_at",
        "updated_at",
        "completed_at",
    }
    assert foreign_keys(table) == {
        "batch_id": ("document_batches.id", "CASCADE"),
        "document_id": ("documents.id", "RESTRICT"),
    }
    assert unique_columns(table) == {("batch_id", "source_id")}
    table_checks = checks(table)
    assert "source_id ~ '^[a-f0-9]{64}$'" in table_checks
    assert "btrim(source_key) <> ''" in table_checks
    assert "source_key !~ '(^/|(^|/)\\.\\.(/|$))'" in table_checks
    assert "btrim(display_label) <> ''" in table_checks
    assert (
        "state IN ('queued', 'running', 'succeeded', 'password_required', "
        "'retryable_failed', 'permanently_failed', 'cancelled')" in table_checks
    )
    assert "attempts >= 0" in table_checks
    assert "max_attempts >= 1 AND max_attempts <= 20" in table_checks
    assert "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'" in table_checks
    assert (
        "(state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
        "AND heartbeat_at IS NOT NULL) OR (state <> 'running' AND lease_owner IS NULL "
        "AND lease_expires_at IS NULL AND heartbeat_at IS NULL)" in table_checks
    )


def test_managed_archives_store_wrapped_key_metadata_only() -> None:
    _, operations = run_upgrade()
    table = operations.tables["managed_archives"]

    assert set(table.c.keys()) == {
        "id",
        "document_version_id",
        "object_key",
        "scheme",
        "key_version",
        "nonce",
        "wrapped_data_key",
        "ciphertext_size",
        "auth_tag",
        "created_at",
        "retired_at",
    }
    assert foreign_keys(table) == {
        "document_version_id": ("document_versions.id", "RESTRICT"),
    }
    assert unique_columns(table) == {("object_key",)}
    table_checks = checks(table)
    assert "scheme = 'aes-256-gcm+aes-kw-v1'" in table_checks
    assert "octet_length(nonce) = 12" in table_checks
    assert "octet_length(wrapped_data_key) = 40" in table_checks
    assert "octet_length(auth_tag) = 16" in table_checks
    assert "ciphertext_size >= 0" in table_checks
    assert "ciphertext_size <= 67108864" in table_checks
    active_index = operations.indexes["uq_managed_archives_active_document_version"]
    assert active_index["table_name"] == "managed_archives"
    assert active_index["columns"] == ["document_version_id"]
    assert active_index["unique"] is True
    assert str(active_index["postgresql_where"]) == "retired_at IS NULL"


def test_indexes_cover_scoped_batch_and_item_queries() -> None:
    _, operations = run_upgrade()

    assert operations.indexes["ix_document_batches_household_created"] == {
        "table_name": "document_batches",
        "columns": ["household_space_id", "created_at", "id"],
    }
    assert operations.indexes["ix_document_batch_items_batch_state"] == {
        "table_name": "document_batch_items",
        "columns": ["batch_id", "state", "created_at", "id"],
    }


@pytest.mark.integration
def test_live_database_rejects_raw_secret_archive_columns() -> None:
    database_url = os.getenv("FAMILYCARE_DATABASE_URL")
    if not database_url:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    connection_url = database_url.replace("postgresql+psycopg://", "postgresql://", 1)

    with psycopg.connect(connection_url) as connection:
        rows = connection.execute(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ANY(%s)
            ORDER BY table_name, ordinal_position
            """,
            (sorted(EXPECTED_TABLES),),
        ).fetchall()

    assert {table_name for table_name, _ in rows} == EXPECTED_TABLES
    persisted_column_names = {column_name for _, column_name in rows}
    assert all(
        forbidden not in column_name
        for forbidden in FORBIDDEN_COLUMN_PARTS
        for column_name in persisted_column_names
    )
