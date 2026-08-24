"""Migration contracts for the policy-candidate review persistence boundary."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0004_policy_candidate_review.py"

EXPECTED_TABLES = {
    "analysis_candidate_versions",
    "analysis_candidate_fields",
    "analysis_candidate_evidence",
}
FORBIDDEN_COLUMNS = {
    "source_path",
    "absolute_path",
    "policy_number",
    "raw_pdf",
    "pdf_body",
    "password",
    "archive_key",
    "raw_provider_response",
    "provider_response",
    "document_text",
    "raw_extracted_text",
    "user_free_text",
}
CANDIDATE_STATUSES = {"AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED", "rejected"}
CANDIDATE_KINDS = {"policy_contract", "policy_party", "rider"}
FIELD_IDS = {
    "insurer",
    "product_name",
    "contract_start",
    "contract_end",
    "policy_status",
    "rider_name",
    "rider_key",
    "benefit_type",
    "sum_assured",
    "currency",
    "coverage_start",
    "coverage_end",
    "renewable",
    "rider_status",
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
    """Load the revision through Alembic-compatible file discovery."""

    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("policy_candidate_migration", MIGRATION_PATH)
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


def unique_constraints(table: sa.Table) -> set[tuple[str, ...]]:
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def foreign_keys(table: sa.Table) -> dict[str, str]:
    return {
        column.name: next(iter(column.foreign_keys)).target_fullname
        for column in table.columns
        if column.foreign_keys
    }


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    return value


def test_revision_is_exactly_chained_from_policy_ledger() -> None:
    migration = load_migration()

    assert migration.revision == "0004_policy_candidate_review"
    assert migration.down_revision == "0003_policy_ledger"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_creates_only_the_candidate_review_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert not {
        "household_spaces",
        "family_members",
        "evidence",
        "policy_contracts",
        "policy_parties",
        "riders",
        "document_versions",
    } & set(operations.tables)


def test_candidate_versions_preserve_review_identity_and_child_history() -> None:
    _, operations = run_upgrade()
    versions = operations.tables["analysis_candidate_versions"]

    assert {
        "id",
        "review_item_id",
        "household_space_id",
        "candidate_kind",
        "aggregate_id",
        "parent_version_id",
        "version",
        "is_current",
        "status",
        "schema_version",
        "generator_version",
        "verifier_version",
        "provider_request_id",
        "issues",
        "actor_id",
        "created_at",
        "updated_at",
        "published_at",
        "deleted_at",
    } <= set(versions.c.keys())
    assert versions.c.review_item_id.nullable is False
    assert versions.c.parent_version_id.nullable is True
    assert versions.c.version.nullable is False
    assert versions.c.is_current.nullable is False
    assert versions.c.is_current.server_default is not None
    assert versions.c.issues.nullable is False
    assert isinstance(versions.c.issues.type, postgresql.JSONB)
    assert foreign_keys(versions)["household_space_id"] == "household_spaces.id"
    assert foreign_keys(versions)["parent_version_id"] == "analysis_candidate_versions.id"

    assert {("review_item_id", "version")} <= unique_constraints(versions)
    assert any(
        index["columns"] == ["review_item_id"]
        and "is_current" in str(index.get("postgresql_where", ""))
        for index in operations.indexes.values()
    )


def test_candidate_versions_use_strict_status_kind_and_optimistic_version_checks() -> None:
    _, operations = run_upgrade()
    versions = operations.tables["analysis_candidate_versions"]
    checks = "\n".join(constraints(versions))

    assert "version >= 1" in checks
    assert versions.c.version.server_default is not None
    assert "status IN ('AI_VERIFIED', 'NEEDS_REVIEW', 'USER_CONFIRMED', 'rejected')" in checks
    assert "candidate_kind IN ('policy_contract', 'policy_party', 'rider')" in checks
    assert all(value in checks for value in (*CANDIDATE_STATUSES, *CANDIDATE_KINDS))


def test_candidate_fields_store_only_fixed_scalar_values() -> None:
    _, operations = run_upgrade()
    fields = operations.tables["analysis_candidate_fields"]
    checks = "\n".join(constraints(fields))

    assert {
        "candidate_version_id",
        "field_id",
        "value",
    } <= set(fields.c.keys())
    assert fields.c.candidate_version_id.nullable is False
    assert fields.c.field_id.nullable is False
    assert fields.c.value.nullable is False
    assert isinstance(fields.c.value.type, postgresql.JSONB)
    assert foreign_keys(fields) == {"candidate_version_id": "analysis_candidate_versions.id"}
    assert {("candidate_version_id", "field_id")} <= unique_constraints(fields)
    assert "field_id IN" in checks
    assert all(field_id in checks for field_id in FIELD_IDS)


def test_candidate_evidence_preserves_policy_or_terms_lineage_and_bounds_excerpt() -> None:
    _, operations = run_upgrade()
    evidence = operations.tables["analysis_candidate_evidence"]
    checks = "\n".join(constraints(evidence))

    assert {
        "candidate_version_id",
        "field_id",
        "document_version_id",
        "evidence_id",
        "physical_page",
        "bounded_excerpt",
    } <= set(evidence.c.keys())
    assert foreign_keys(evidence) == {
        "candidate_version_id": "analysis_candidate_versions.id",
        "document_version_id": "document_versions.id",
        "evidence_id": "evidence.id",
    }
    assert evidence.c.document_version_id.nullable is False
    assert evidence.c.evidence_id.nullable is False
    assert evidence.c.physical_page.nullable is False
    assert evidence.c.bounded_excerpt.nullable is False
    assert cast(Any, evidence.c.bounded_excerpt.type).length == 240
    assert "physical_page >= 1" in checks
    assert "bounded_excerpt <> ''" in checks
    assert {("candidate_version_id", "field_id", "evidence_id")} <= unique_constraints(evidence)

    if "bbox" in evidence.c:
        assert isinstance(evidence.c.bbox.type, postgresql.JSONB)
        assert evidence.c.bbox.nullable is True
    else:
        assert {"x0", "y0", "x1", "y1"} <= set(evidence.c.keys())
        assert all(evidence.c[name].nullable is True for name in ("x0", "y0", "x1", "y1"))


def test_candidate_tables_have_scope_and_review_lookup_indexes() -> None:
    _, operations = run_upgrade()

    assert any(
        index["table_name"] == "analysis_candidate_versions"
        and index["columns"][:2] == ["household_space_id", "status"]
        for index in operations.indexes.values()
    )
    assert any(
        index["table_name"] == "analysis_candidate_versions"
        and "review_item_id" in index["columns"]
        for index in operations.indexes.values()
    )
    assert any(
        index["table_name"] == "analysis_candidate_fields"
        and index["columns"][0] == "candidate_version_id"
        for index in operations.indexes.values()
    )
    assert any(
        index["table_name"] == "analysis_candidate_evidence"
        and index["columns"][0] == "candidate_version_id"
        for index in operations.indexes.values()
    )


def test_candidate_migration_has_no_forbidden_columns_or_raw_payload_storage() -> None:
    _, operations = run_upgrade()

    for table in operations.tables.values():
        assert not FORBIDDEN_COLUMNS & {column.name.lower() for column in table.columns}


def test_downgrade_drops_candidate_objects_in_reverse_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "analysis_candidate_evidence",
        "analysis_candidate_fields",
        "analysis_candidate_versions",
    ]
    assert operations.dropped_indexes


@pytest.mark.integration
def test_postgresql_candidate_constraints_are_present_after_head(database_url: str) -> None:
    """Scaffold for live PostgreSQL checks; default CI excludes integration tests."""

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        rows = connection.execute(
            """
            SELECT conrelid::regclass::text AS table_name,
                   conname,
                   pg_get_constraintdef(oid) AS definition
            FROM pg_constraint
            WHERE conrelid::regclass::text IN (
                'analysis_candidate_versions',
                'analysis_candidate_fields',
                'analysis_candidate_evidence'
            )
            ORDER BY table_name, conname
            """
        ).fetchall()

    constraints_by_table = {
        table_name: "\n".join(
            str(definition) for row_table, _, definition in rows if row_table == table_name
        )
        for table_name in EXPECTED_TABLES
    }
    assert "AI_VERIFIED" in constraints_by_table["analysis_candidate_versions"]
    assert "NEEDS_REVIEW" in constraints_by_table["analysis_candidate_versions"]
    assert "USER_CONFIRMED" in constraints_by_table["analysis_candidate_versions"]
    assert "rejected" in constraints_by_table["analysis_candidate_versions"]
    assert "physical_page >= 1" in constraints_by_table["analysis_candidate_evidence"]
    assert "field_id" in constraints_by_table["analysis_candidate_fields"]
