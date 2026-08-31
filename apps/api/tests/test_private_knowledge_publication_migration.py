"""Migration contract for verified private-knowledge rule publications."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0020_private_knowledge_publications.py"

EXPECTED_TABLES = [
    "private_knowledge_contract_status_intervals",
    "private_knowledge_rule_import_runs",
    "private_knowledge_coverage_execution_dispositions",
    "private_knowledge_fact_normalizer_publications",
    "private_knowledge_rule_publications",
    "private_knowledge_rule_citations",
    "private_knowledge_calculation_publications",
    "private_knowledge_calculation_citations",
]


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

    def create_foreign_key(
        self,
        name: str,
        source_table: str,
        referent_table: str,
        local_cols: list[str],
        remote_cols: list[str],
        **kwargs: Any,
    ) -> None:
        table = self.tables[source_table]
        table.append_constraint(
            sa.ForeignKeyConstraint(
                local_cols,
                [f"{referent_table}.{column}" for column in remote_cols],
                name=name,
                **kwargs,
            )
        )
        self.operations.append(("create_foreign_key", name, source_table))

    def drop_index(self, name: str, **kwargs: Any) -> None:
        self.operations.append(("drop_index", name, cast(str | None, kwargs.get("table_name"))))

    def drop_constraint(self, name: str, table_name: str, **kwargs: Any) -> None:
        self.operations.append(("drop_constraint", name, table_name))

    def drop_table(self, name: str) -> None:
        self.operations.append(("drop_table", name, None))


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location(
        "private_knowledge_publications",
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


def test_revision_adds_publication_tables_in_dependency_order() -> None:
    migration, operations = run_upgrade()

    assert migration.revision == "0020_private_publications"
    assert migration.down_revision == "0019_private_confirmations"
    assert list(operations.tables) == EXPECTED_TABLES


def test_status_intervals_require_exact_contract_household_and_user_authority() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_contract_status_intervals"]

    assert {
        "id",
        "rule_import_run_id",
        "import_run_id",
        "household_space_id",
        "knowledge_contract_id",
        "decision",
        "confirmed_status",
        "effective_from",
        "effective_through",
        "authority",
        "reason_code",
        "review_state",
        "confirmed_by",
        "confirmed_at",
        "interval_digest_sha256",
        "created_at",
    } == set(table.columns.keys())
    assert composite_foreign_keys(table) >= {
        (
            ("rule_import_run_id", "import_run_id", "household_space_id"),
            (
                "private_knowledge_rule_import_runs.id",
                "private_knowledge_rule_import_runs.knowledge_import_run_id",
                "private_knowledge_rule_import_runs.household_space_id",
            ),
            "RESTRICT",
        ),
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
    authority_checks = " ".join(table_checks)
    assert "USER_CONFIRMED_EVENT_DATE" in authority_checks
    assert "REVIEWED_STATUS_DOCUMENT" in authority_checks
    assert "decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')" in table_checks
    assert "review_state = 'USER_CONFIRMED'" in table_checks
    assert any("effective_through" in value and "effective_from" in value for value in table_checks)
    digest_index = operations.indexes["uq_private_knowledge_status_intervals_digest"]
    assert digest_index["columns"] == ["rule_import_run_id", "interval_digest_sha256"]


def test_publication_run_is_user_reviewed_digest_bound_and_append_only() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_rule_import_runs"]
    table_checks = checks(table)

    assert {
        "package_digest_sha256",
        "manifest_digest_sha256",
        "baseline_digest_sha256",
        "report_digest_sha256",
        "projection_digest_sha256",
        "entity_counts_json",
        "disposition_counts_json",
        "reviewed_by",
        "reviewed_at",
        "is_current",
        "superseded_at",
    } <= set(table.columns.keys())
    assert "review_state = 'USER_CONFIRMED'" in table_checks
    assert any("jsonb_typeof(entity_counts_json) = 'object'" in value for value in table_checks)
    assert any(
        "package_digest_sha256" in value and "[0-9a-f]{64}" in value for value in table_checks
    )
    assert composite_foreign_keys(table) >= {
        (
            ("knowledge_import_run_id", "household_space_id"),
            (
                "private_knowledge_import_runs.id",
                "private_knowledge_import_runs.household_space_id",
            ),
            "RESTRICT",
        ),
        (
            ("reviewed_by", "household_space_id"),
            ("app_users.id", "app_users.household_space_id"),
            "RESTRICT",
        ),
    }
    current = operations.indexes["uq_private_knowledge_rule_runs_current"]
    assert current["unique"] is True
    assert current["columns"] == ["household_space_id"]
    assert str(current["postgresql_where"]) == "is_current"


def test_dispositions_close_each_coverage_once_per_publication_run() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_coverage_execution_dispositions"]

    assert "disposition IN ('PUBLISHED', 'BLOCKED', 'NOT_APPLICABLE')" in checks(table)
    assert any("jsonb_typeof(reason_codes_json) = 'array'" in value for value in checks(table))
    assert composite_foreign_keys(table) >= {
        (
            ("knowledge_coverage_id", "knowledge_import_run_id"),
            (
                "private_knowledge_coverages.id",
                "private_knowledge_coverages.import_run_id",
            ),
            "RESTRICT",
        ),
        (
            ("rule_import_run_id", "knowledge_import_run_id", "household_space_id"),
            (
                "private_knowledge_rule_import_runs.id",
                "private_knowledge_rule_import_runs.knowledge_import_run_id",
                "private_knowledge_rule_import_runs.household_space_id",
            ),
            "RESTRICT",
        ),
    }
    unique = operations.indexes["uq_private_knowledge_dispositions_coverage"]
    assert unique["unique"] is True
    assert unique["columns"] == ["rule_import_run_id", "knowledge_coverage_id"]


def test_normalizers_rules_and_calculations_are_strict_user_reviewed_data() -> None:
    _, operations = run_upgrade()
    normalizers = operations.tables["private_knowledge_fact_normalizer_publications"]
    rules = operations.tables["private_knowledge_rule_publications"]
    calculations = operations.tables["private_knowledge_calculation_publications"]

    normalizer_checks = checks(normalizers)
    assert "match_kind = 'EXACT_TOKEN_SEQUENCE'" in normalizer_checks
    assert "review_state = 'USER_CONFIRMED'" in normalizer_checks
    assert any(
        "jsonb_typeof(normalized_tokens_json) = 'array'" in value for value in normalizer_checks
    )
    assert any("jsonb_typeof(normalized_value_json)" in value for value in normalizer_checks)

    rule_checks = checks(rules)
    assert "review_state = 'USER_CONFIRMED'" in rule_checks
    assert any("jsonb_typeof(rule_json) = 'object'" in value for value in rule_checks)
    assert any("rule_kind IN" in value and "eligibility" in value for value in rule_checks)

    calculation_checks = checks(calculations)
    assert "calculation_kind IN ('FIXED', 'INDEMNITY', 'NONE', 'UNKNOWN')" in calculation_checks
    assert "review_state = 'USER_CONFIRMED'" in calculation_checks
    assert any("jsonb_typeof(calculation_json) = 'object'" in value for value in calculation_checks)


def test_rule_and_calculation_citations_keep_exact_run_page_and_clause_lineage() -> None:
    _, operations = run_upgrade()

    for table_name, parent_table, parent_column in (
        (
            "private_knowledge_rule_citations",
            "private_knowledge_rule_publications",
            "rule_publication_id",
        ),
        (
            "private_knowledge_calculation_citations",
            "private_knowledge_calculation_publications",
            "calculation_publication_id",
        ),
    ):
        table = operations.tables[table_name]
        table_checks = checks(table)
        assert any(
            "page_start >= 1" in value and "page_end >= page_start" in value
            for value in table_checks
        )
        assert any(
            "citation_digest_sha256" in value and "[0-9a-f]{64}" in value for value in table_checks
        )
        assert any(
            "source_text_sha256" in value and "[0-9a-f]{64}" in value for value in table_checks
        )
        assert any(
            "evidence_purpose IN" in value and "ELIGIBILITY" in value for value in table_checks
        )
        assert "citation_key" in table.columns
        assert (
            (
                parent_column,
                "rule_import_run_id",
                "knowledge_import_run_id",
                "household_space_id",
            ),
            (
                f"{parent_table}.id",
                f"{parent_table}.rule_import_run_id",
                f"{parent_table}.knowledge_import_run_id",
                f"{parent_table}.household_space_id",
            ),
            "RESTRICT",
        ) in composite_foreign_keys(table)
        assert (
            ("terms_section_id", "knowledge_import_run_id"),
            (
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ),
            "RESTRICT",
        ) in composite_foreign_keys(table)
        assert (
            ("fact_id", "knowledge_import_run_id"),
            (
                "private_knowledge_facts.id",
                "private_knowledge_facts.import_run_id",
            ),
            "RESTRICT",
        ) in composite_foreign_keys(table)


def test_downgrade_drops_indexes_and_tables_in_reverse_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    dropped_tables = [item[1] for item in operations.operations if item[0] == "drop_table"]
    assert dropped_tables == list(reversed(EXPECTED_TABLES))
    assert any(item[0] == "drop_index" for item in operations.operations)
