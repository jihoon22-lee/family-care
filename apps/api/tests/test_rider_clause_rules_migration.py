"""Migration contract for Rider-Clause links and versioned CoverageRules."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0006_rider_clause_rules.py"

EXPECTED_TABLES = {
    "rider_clause_links",
    "rider_clause_link_evidence",
    "coverage_rules",
    "coverage_rule_versions",
    "coverage_rule_evidence",
}


class RecordingOperations:
    """Alembic spy that preserves table and index metadata."""

    def __init__(self) -> None:
        self.metadata = sa.MetaData()
        self.tables: dict[str, sa.Table] = {}
        self.indexes: dict[str, dict[str, Any]] = {}
        self.dropped_tables: list[str] = []
        self.dropped_indexes: list[str] = []
        self.dropped_constraints: list[dict[str, str]] = []
        self.created_checks: dict[str, dict[str, str]] = {}

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

    def drop_constraint(self, name: str, table_name: str, **kwargs: Any) -> None:
        self.dropped_constraints.append({"name": name, "table_name": table_name, **kwargs})

    def create_check_constraint(
        self, name: str, table_name: str, condition: str, **kwargs: Any
    ) -> None:
        self.created_checks[name] = {
            "table_name": table_name,
            "condition": condition,
            **kwargs,
        }


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("rider_clause_rules_migration", MIGRATION_PATH)
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


def foreign_keys(table: sa.Table) -> dict[str, tuple[str, str | None]]:
    return {
        column.name: (
            next(iter(column.foreign_keys)).target_fullname,
            next(iter(column.foreign_keys)).ondelete,
        )
        for column in table.columns
        if column.foreign_keys
    }


def unique_column_sets(table: sa.Table) -> set[frozenset[str]]:
    return {
        frozenset(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }


def test_revision_is_exactly_chained_from_clause_search() -> None:
    migration = load_migration()

    assert migration.revision == "0006_rider_clause_rules"
    assert migration.down_revision == "0005_clause_search"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_is_additive_and_does_not_duplicate_source_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert not {
        "household_spaces",
        "evidence",
        "policy_contracts",
        "riders",
        "terms_editions",
        "clauses",
        "analysis_candidate_versions",
    } & set(operations.tables)


def test_upgrade_extends_generic_review_domains_and_typed_fields() -> None:
    _, operations = run_upgrade()

    assert {(item["table_name"], item["name"]) for item in operations.dropped_constraints} >= {
        ("analysis_candidate_versions", "ck_candidate_versions_kind"),
        ("analysis_candidate_fields", "ck_candidate_fields_field_id"),
        ("analysis_candidate_evidence", "ck_candidate_evidence_field_id"),
    }
    kinds = operations.created_checks["ck_candidate_versions_kind"]
    assert kinds["table_name"] == "analysis_candidate_versions"
    assert all(
        kind in kinds["condition"]
        for kind in (
            "policy_contract",
            "policy_party",
            "rider",
            "rider_clause",
            "coverage_rule",
        )
    )
    fields = operations.created_checks["ck_candidate_fields_field_id"]
    assert fields["table_name"] == "analysis_candidate_fields"
    assert all(
        field in fields["condition"]
        for field in (
            "rider_id",
            "terms_edition_id",
            "clause_id",
            "rule_kind",
            "rule_operator",
            "fact_field",
            "unit",
            "decimal_boundary",
            "date_boundary",
            "required",
        )
    )
    evidence_fields = operations.created_checks["ck_candidate_evidence_field_id"]
    assert evidence_fields["table_name"] == "analysis_candidate_evidence"
    assert evidence_fields["condition"] == fields["condition"]


def test_rider_clause_links_preserve_scope_candidate_and_parent_lineage() -> None:
    _, operations = run_upgrade()
    links = operations.tables["rider_clause_links"]

    assert {
        "id",
        "household_space_id",
        "rider_id",
        "terms_edition_id",
        "clause_id",
        "candidate_version_id",
        "review_state",
        "applicability_reason_code",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    } == set(links.c.keys())
    assert foreign_keys(links) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "rider_id": ("riders.id", "RESTRICT"),
        "terms_edition_id": ("terms_editions.id", "RESTRICT"),
        "clause_id": ("clauses.id", "RESTRICT"),
        "candidate_version_id": ("analysis_candidate_versions.id", "RESTRICT"),
    }
    link_checks = checks(links)
    assert all(
        state in link_checks["ck_rider_clause_links_review_state"]
        for state in ("AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED", "rejected")
    )
    assert link_checks["ck_rider_clause_links_version"] == "version >= 1"
    assert (
        link_checks["ck_rider_clause_links_applicability_reason_code_nonempty"]
        == "applicability_reason_code <> ''"
    )


def test_link_evidence_is_unique_and_audit_preserving() -> None:
    _, operations = run_upgrade()
    link_evidence = operations.tables["rider_clause_link_evidence"]

    assert foreign_keys(link_evidence) == {
        "rider_clause_link_id": ("rider_clause_links.id", "RESTRICT"),
        "evidence_id": ("evidence.id", "RESTRICT"),
    }
    assert {column.name for column in link_evidence.primary_key.columns} == {
        "rider_clause_link_id",
        "evidence_id",
    }


def test_coverage_rules_have_scoped_status_and_version_boundaries() -> None:
    _, operations = run_upgrade()
    rules = operations.tables["coverage_rules"]

    assert foreign_keys(rules) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "rider_clause_link_id": ("rider_clause_links.id", "RESTRICT"),
    }
    rule_checks = checks(rules)
    assert all(
        status in rule_checks["ck_coverage_rules_current_status"]
        for status in ("generated", "published", "rejected")
    )
    assert rule_checks["ck_coverage_rules_rule_key_nonempty"] == "rule_key <> ''"
    assert rule_checks["ck_coverage_rules_version"] == "version >= 1"
    assert frozenset({"rider_clause_link_id", "rule_key"}) not in unique_column_sets(rules)


def test_rule_versions_are_immutable_typed_json_documents() -> None:
    _, operations = run_upgrade()
    versions = operations.tables["coverage_rule_versions"]

    assert foreign_keys(versions) == {
        "coverage_rule_id": ("coverage_rules.id", "RESTRICT"),
        "candidate_version_id": ("analysis_candidate_versions.id", "RESTRICT"),
    }
    assert isinstance(versions.c.input_field_paths.type, postgresql.JSONB)
    assert isinstance(versions.c.expression_json.type, postgresql.JSONB)
    assert frozenset({"coverage_rule_id", "version_number"}) in unique_column_sets(versions)
    version_checks = checks(versions)
    assert "coverage-rule-v1" in version_checks["ck_coverage_rule_versions_schema_version"]
    assert all(
        state in version_checks["ck_coverage_rule_versions_review_state"]
        for state in ("AI_VERIFIED", "NEEDS_REVIEW", "USER_CONFIRMED")
    )
    executable = version_checks["ck_coverage_rule_versions_executable_state"]
    assert "NOT executable" in executable
    assert "AI_VERIFIED" in executable
    assert "USER_CONFIRMED" in executable
    assert (
        "jsonb_typeof(input_field_paths) = 'array'"
        in version_checks["ck_coverage_rule_versions_input_fields_array"]
    )
    assert (
        "jsonb_typeof(expression_json) = 'object'"
        in version_checks["ck_coverage_rule_versions_expression_object"]
    )


def test_rule_evidence_is_unique_and_audit_preserving() -> None:
    _, operations = run_upgrade()
    rule_evidence = operations.tables["coverage_rule_evidence"]

    assert foreign_keys(rule_evidence) == {
        "coverage_rule_version_id": ("coverage_rule_versions.id", "RESTRICT"),
        "evidence_id": ("evidence.id", "RESTRICT"),
    }
    assert {column.name for column in rule_evidence.primary_key.columns} == {
        "coverage_rule_version_id",
        "evidence_id",
    }


def test_indexes_cover_scope_status_version_and_active_rows() -> None:
    _, operations = run_upgrade()

    assert operations.indexes["ix_rider_clause_links_household_active"]["columns"] == [
        "household_space_id",
        "deleted_at",
        "id",
    ]
    assert operations.indexes["ix_rider_clause_links_rider_state"]["columns"] == [
        "rider_id",
        "review_state",
        "version",
    ]
    assert operations.indexes["ix_coverage_rules_household_active"]["columns"] == [
        "household_space_id",
        "deleted_at",
        "id",
    ]
    active_rule_key = operations.indexes["uq_coverage_rules_active_link_key"]
    assert active_rule_key["columns"] == ["rider_clause_link_id", "rule_key"]
    assert active_rule_key["unique"] is True
    assert str(active_rule_key["postgresql_where"]) == "deleted_at IS NULL"
    assert operations.indexes["ix_coverage_rule_versions_rule_version"]["columns"] == [
        "coverage_rule_id",
        "version_number",
    ]


def test_migration_never_adds_raw_content_or_executable_code_columns() -> None:
    _, operations = run_upgrade()
    forbidden = {
        "source_path",
        "raw_text",
        "clause_text",
        "python_code",
        "sql_code",
        "javascript",
        "shell_command",
        "password",
        "provider_request_id",
    }

    assert not forbidden & {
        column.name for table in operations.tables.values() for column in table.columns
    }


def test_downgrade_reverses_tables_and_indexes_in_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    assert operations.dropped_tables == [
        "coverage_rule_evidence",
        "coverage_rule_versions",
        "coverage_rules",
        "rider_clause_link_evidence",
        "rider_clause_links",
    ]
