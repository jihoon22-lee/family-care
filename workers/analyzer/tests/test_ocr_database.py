"""Physical-model tests for OCR provenance and safe batch progress metadata."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0013_selective_ocr.py"


class RecordingOperations:
    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.added_columns: dict[str, dict[str, sa.Column[Any]]] = {}
        self.constraints: list[tuple[str, str, str]] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []
        self.dropped_columns: list[tuple[str, str]] = []

    def create_table(self, name: str, *elements: Any, **kwargs: Any) -> sa.Table:
        table = sa.Table(name, self.metadata, *elements, **kwargs)
        self.tables[name] = table
        return table

    def create_index(self, name: str, table_name: str, columns: list[str], **kwargs: Any) -> None:
        self.indexes[name] = {"table_name": table_name, "columns": columns, **kwargs}

    def add_column(self, table_name: str, column: sa.Column[Any]) -> None:
        self.added_columns.setdefault(table_name, {})[column.name] = column

    def create_check_constraint(self, name: str, table_name: str, condition: str) -> None:
        self.constraints.append((name, table_name, condition))

    def drop_constraint(self, name: str, table_name: str, **_: Any) -> None:
        del name, table_name

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped_columns.append((table_name, column_name))

    def drop_index(self, name: str, **_: Any) -> None:
        self.dropped_indexes.append(name)

    def drop_table(self, name: str) -> None:
        self.dropped_tables.append(name)


def _migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("selective_ocr", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade() -> tuple[ModuleType, RecordingOperations]:
    migration = cast(Any, _migration())
    operations = RecordingOperations()
    migration.op = operations
    migration.upgrade()
    return migration, operations


def _foreign_keys(table: sa.Table) -> dict[str, tuple[str, str | None]]:
    return {
        column.name: (
            next(iter(column.foreign_keys)).target_fullname,
            next(iter(column.foreign_keys)).ondelete,
        )
        for column in table.columns
        if column.foreign_keys
    }


def test_migration_chains_from_encrypted_import() -> None:
    migration = _migration()

    assert migration.revision == "0013_selective_ocr"
    assert migration.down_revision == "0012_encrypted_document_import"


def test_ocr_tables_preserve_native_extraction_and_document_evidence() -> None:
    _, operations = _upgrade()

    assert set(operations.tables) == {"ocr_layers", "ocr_pages", "ocr_blocks"}
    assert _foreign_keys(operations.tables["ocr_layers"]) == {
        "extraction_id": ("extractions.id", "CASCADE")
    }
    assert _foreign_keys(operations.tables["ocr_pages"]) == {
        "ocr_layer_id": ("ocr_layers.id", "CASCADE"),
        "document_version_id": ("document_versions.id", "CASCADE"),
    }
    assert _foreign_keys(operations.tables["ocr_blocks"]) == {
        "ocr_page_id": ("ocr_pages.id", "CASCADE")
    }
    all_columns = {column.name for table in operations.tables.values() for column in table.columns}
    assert {
        "absolute_path",
        "image_path",
        "password",
        "pdf_bytes",
        "source_key",
        "tsv_path",
    }.isdisjoint(all_columns)


def test_batch_progress_additions_are_metadata_only_and_bounded() -> None:
    _, operations = _upgrade()

    columns = operations.added_columns["document_batch_items"]
    assert set(columns) == {"ocr_state", "ocr_pages_processed", "ocr_warning_codes"}
    assert str(columns["ocr_state"].server_default.arg) == "'pending'"
    assert str(columns["ocr_pages_processed"].server_default.arg) == "0"
    conditions = {
        condition
        for _, table, condition in operations.constraints
        if table == "document_batch_items"
    }
    assert (
        "ocr_state IN ('pending', 'native_only', 'running', 'completed', 'warning', 'failed')"
        in conditions
    )
    assert "ocr_pages_processed >= 0 AND ocr_pages_processed <= 500" in conditions


def test_migration_never_rewrites_native_extraction_rows() -> None:
    source = MIGRATION_PATH.read_text(encoding="utf-8")

    assert "UPDATE extraction_blocks" not in source
    assert "UPDATE extraction_pages" not in source
    assert 'op.drop_table("extractions")' not in source
