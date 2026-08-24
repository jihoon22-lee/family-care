"""Migration contract for the household-scoped Clause search boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0005_clause_search.py"

EXPECTED_TABLES = {
    "terms_editions",
    "clauses",
    "clause_evidence",
    "clause_search_synonyms",
}
CLAUSE_TYPES = {
    "chapter",
    "section",
    "article",
    "paragraph",
    "item",
    "special_terms",
    "definition",
    "appendix",
    "table",
}


class RecordingOperations:
    """Small Alembic spy that materializes migration metadata."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.executed: list[str] = []
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []

    def execute(self, statement: str) -> None:
        self.executed.append(statement)

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
    spec = importlib.util.spec_from_file_location("clause_search_migration", MIGRATION_PATH)
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
        constraint.name or "": str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }


def foreign_keys(table: sa.Table) -> dict[str, str]:
    return {
        column.name: next(iter(column.foreign_keys)).target_fullname
        for column in table.columns
        if column.foreign_keys
    }


def test_revision_follows_policy_candidate_review() -> None:
    migration = load_migration()

    assert migration.revision == "0005_clause_search"
    assert migration.down_revision == "0004_policy_candidate_review"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_is_additive_and_enables_trigram_search() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert operations.executed == ["CREATE EXTENSION IF NOT EXISTS pg_trgm"]
    assert not {
        "documents",
        "document_versions",
        "extractions",
        "evidence",
        "policy_contracts",
        "riders",
        "analysis_candidate_versions",
    } & set(operations.tables)


def test_terms_editions_preserve_document_lineage_and_applicability() -> None:
    _, operations = run_upgrade()
    editions = operations.tables["terms_editions"]

    assert {
        "id",
        "household_space_id",
        "document_version_id",
        "insurer_display",
        "insurer_key",
        "product_display",
        "product_key",
        "applicability_start",
        "applicability_end",
        "content_sha256",
        "normalization_version",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    } == set(editions.c.keys())
    assert foreign_keys(editions) == {
        "household_space_id": "household_spaces.id",
        "document_version_id": "document_versions.id",
    }
    edition_checks = checks(editions)
    assert "^[0-9a-f]{64}$" in edition_checks["ck_terms_editions_content_sha256"]
    assert "unicode-nfc-v1" in edition_checks["ck_terms_editions_normalization_version"]
    assert (
        "applicability_end >= applicability_start"
        in edition_checks["ck_terms_editions_applicability_dates"]
    )
    assert edition_checks["ck_terms_editions_version"] == "version >= 1"


def test_clauses_have_strict_hierarchy_pages_and_search_vector() -> None:
    _, operations = run_upgrade()
    clauses = operations.tables["clauses"]

    assert foreign_keys(clauses) == {
        "household_space_id": "household_spaces.id",
        "terms_edition_id": "terms_editions.id",
        "parent_clause_id": "clauses.id",
    }
    assert isinstance(clauses.c.search_vector.type, postgresql.TSVECTOR)
    assert clauses.c.search_vector.nullable is False
    clause_checks = checks(clauses)
    type_check = clause_checks["ck_clauses_type"]
    assert all(clause_type in type_check for clause_type in CLAUSE_TYPES)
    assert clause_checks["ck_clauses_physical_page_start"] == "physical_page_start >= 1"
    assert (
        "physical_page_end >= physical_page_start"
        in clause_checks["ck_clauses_physical_page_range"]
    )
    assert "unicode-nfc-v1" in clause_checks["ck_clauses_normalization_version"]
    assert clause_checks["ck_clauses_label_nonempty"] == "label <> ''"


def test_evidence_join_and_synonyms_are_household_scoped() -> None:
    _, operations = run_upgrade()
    clause_evidence = operations.tables["clause_evidence"]
    synonyms = operations.tables["clause_search_synonyms"]

    assert foreign_keys(clause_evidence) == {
        "clause_id": "clauses.id",
        "evidence_id": "evidence.id",
    }
    assert {column.name for column in clause_evidence.primary_key.columns} == {
        "clause_id",
        "evidence_id",
    }
    assert foreign_keys(synonyms) == {"household_space_id": "household_spaces.id"}
    synonym_checks = checks(synonyms)
    assert synonym_checks["ck_clause_search_synonyms_key_nonempty"] == "synonym_key <> ''"
    assert (
        synonym_checks["ck_clause_search_synonyms_replacement_nonempty"] == "replacement_text <> ''"
    )


def test_search_indexes_cover_fts_trigram_scope_and_dates() -> None:
    _, operations = run_upgrade()

    search_index = operations.indexes["ix_clauses_search_vector"]
    assert search_index["table_name"] == "clauses"
    assert search_index["columns"] == ["search_vector"]
    assert search_index["postgresql_using"] == "gin"

    title_index = operations.indexes["ix_clauses_normalized_title_trgm"]
    assert title_index["postgresql_using"] == "gin"
    assert title_index["postgresql_ops"] == {"normalized_title": "gin_trgm_ops"}

    assert operations.indexes["ix_clauses_household_edition_page"]["columns"] == [
        "household_space_id",
        "terms_edition_id",
        "physical_page_start",
        "id",
    ]
    assert operations.indexes["ix_terms_editions_household_applicability"]["columns"] == [
        "household_space_id",
        "applicability_start",
        "applicability_end",
        "id",
    ]


def test_migration_never_adds_private_content_or_location_columns() -> None:
    _, operations = run_upgrade()
    forbidden = {
        "source_path",
        "absolute_path",
        "password",
        "archive_key",
        "raw_pdf",
        "page_image",
        "full_text",
        "raw_query",
    }

    assert not forbidden & {
        column.name for table in operations.tables.values() for column in table.columns
    }


def test_downgrade_reverses_dependencies_without_dropping_pg_trgm() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "clause_evidence",
        "clause_search_synonyms",
        "clauses",
        "terms_editions",
    ]
    assert not any("DROP EXTENSION" in statement.upper() for statement in operations.executed)
