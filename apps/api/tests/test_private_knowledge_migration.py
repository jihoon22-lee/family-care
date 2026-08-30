"""Migration contract for immutable private insurance knowledge snapshots."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0018_private_knowledge_catalog.py"


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


def load_migration() -> ModuleType:
    assert MIGRATION_PATH.is_file(), f"missing migration: {MIGRATION_PATH}"
    spec = importlib.util.spec_from_file_location("private_knowledge_catalog", MIGRATION_PATH)
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


def foreign_keys(table: sa.Table) -> dict[str, tuple[str, str | None]]:
    result: dict[str, tuple[str, str | None]] = {}
    for column in table.columns:
        if not column.foreign_keys:
            continue
        foreign_key = next(
            (
                candidate
                for candidate in column.foreign_keys
                if candidate.target_fullname.endswith(".id")
            ),
            next(iter(column.foreign_keys)),
        )
        result[column.name] = (foreign_key.target_fullname, foreign_key.ondelete)
    return result


def composite_foreign_keys(
    table: sa.Table,
) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {
        (
            tuple(column.name for column in constraint.columns),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, sa.ForeignKeyConstraint) and len(constraint.columns) > 1
    }


def test_revision_and_table_dependency_order_are_additive() -> None:
    migration, operations = run_upgrade()

    assert migration.revision == "0018_private_knowledge_catalog"
    assert migration.down_revision == "0017_insurance_inventory"
    expected = [
        "private_knowledge_import_runs",
        "private_knowledge_subjects",
        "private_knowledge_contracts",
        "private_knowledge_coverages",
        "private_knowledge_terms_assignments",
        "private_knowledge_terms_assignment_sources",
        "private_knowledge_terms_sections",
        "private_knowledge_source_clauses",
        "private_knowledge_semantic_reviews",
        "private_knowledge_facts",
        "private_knowledge_fact_citations",
        "private_knowledge_coverage_terms_mappings",
        "private_knowledge_document_bindings",
    ]
    assert list(operations.tables) == expected
    assert [item[1] for item in operations.operations if item[0] == "create_table"] == expected


def test_import_run_is_household_scoped_idempotent_and_count_only() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_import_runs"]

    assert foreign_keys(table) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "applied_by": ("app_users.id", "RESTRICT"),
    }
    assert {
        "package_schema_version",
        "package_digest_sha256",
        "manifest_digest_sha256",
        "importer_version",
        "analysis_authority",
        "state",
        "is_current",
        "manifest_counts_json",
        "manifest_json",
        "reconciliation_counts_json",
        "baseline_digest_sha256",
        "report_digest_sha256",
        "validated_at",
        "applied_at",
        "superseded_at",
        "created_at",
    } <= set(table.columns.keys())
    assert operations.indexes["uq_private_knowledge_runs_package"]["unique"] is True
    assert operations.indexes["uq_private_knowledge_runs_package"]["columns"] == [
        "household_space_id",
        "package_digest_sha256",
    ]
    current = operations.indexes["uq_private_knowledge_runs_current"]
    assert current["unique"] is True
    assert current["columns"] == ["household_space_id"]
    assert str(current["postgresql_where"]) == "is_current"
    run_checks = checks(table)
    assert "state IN ('VALIDATED', 'APPLIED', 'SUPERSEDED', 'REJECTED')" in run_checks
    assert "package_schema_version = 'private-analysis-package.sol-v2'" in run_checks
    assert "jsonb_typeof(manifest_counts_json) = 'object'" in run_checks
    assert "jsonb_typeof(reconciliation_counts_json) = 'object'" in run_checks
    assert any("is_current" in value and "APPLIED" in value for value in run_checks)


def test_subject_contract_and_coverage_keep_authorities_separate() -> None:
    _, operations = run_upgrade()
    subjects = operations.tables["private_knowledge_subjects"]
    contracts = operations.tables["private_knowledge_contracts"]
    coverages = operations.tables["private_knowledge_coverages"]

    assert foreign_keys(subjects) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
        "binding_confirmed_by": ("app_users.id", "RESTRICT"),
    }
    assert foreign_keys(contracts) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "subject_id": ("private_knowledge_subjects.id", "RESTRICT"),
        "policy_contract_id": ("policy_contracts.id", "RESTRICT"),
    }
    assert foreign_keys(coverages) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "knowledge_contract_id": ("private_knowledge_contracts.id", "RESTRICT"),
        "rider_id": ("riders.id", "RESTRICT"),
    }
    assert {
        "source_subject_key",
        "family_alias",
        "family_alias_digest_sha256",
        "binding_decision",
        "binding_conflict",
        "binding_reason_code",
        "source_record_json",
        "source_record_digest_sha256",
    } <= set(subjects.columns.keys())
    assert {
        "source_contract_key",
        "insurer_display",
        "product_display",
        "certificate_decision",
        "current_status",
        "status_candidates_json",
        "certificate_evidence_json",
        "review_issues_json",
        "operational_binding_decision",
        "operational_binding_reason_code",
        "source_record_json",
        "source_record_digest_sha256",
    } <= set(contracts.columns.keys())
    assert {
        "source_coverage_key",
        "display_name",
        "component_role",
        "component_classification",
        "enrollment_decision",
        "benefit_type",
        "insured_amount",
        "currency",
        "renewal_state",
        "current_status",
        "certificate_evidence_json",
        "review_issues_json",
        "operational_binding_decision",
        "operational_binding_reason_code",
        "source_record_json",
        "source_record_digest_sha256",
    } <= set(coverages.columns.keys())

    tri_state = "binding_decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')"
    assert tri_state in checks(subjects)
    assert "certificate_decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')" in checks(contracts)
    assert "enrollment_decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')" in checks(coverages)
    assert "component_role IN ('MAIN_CONTRACT', 'RIDER')" in checks(coverages)
    assert (
        "component_classification IN ('BENEFIT_COVERAGE', 'NON_BENEFIT_CONTRACT_COMPONENT')"
    ) in checks(coverages)
    assert "benefit_type IN ('FIXED', 'INDEMNITY', 'UNKNOWN', 'NOT_APPLICABLE')" in checks(
        coverages
    )
    assert any("NON_BENEFIT_CONTRACT_COMPONENT" in value for value in checks(coverages))
    assert any(
        "operational_binding_decision" in value and "rider_id" in value
        for value in checks(coverages)
    )


def test_terms_clause_fact_and_mapping_lineage_is_normalized() -> None:
    _, operations = run_upgrade()
    assignments = operations.tables["private_knowledge_terms_assignments"]
    sections = operations.tables["private_knowledge_terms_sections"]
    clauses = operations.tables["private_knowledge_source_clauses"]
    semantic_reviews = operations.tables["private_knowledge_semantic_reviews"]
    facts = operations.tables["private_knowledge_facts"]
    citations = operations.tables["private_knowledge_fact_citations"]
    mappings = operations.tables["private_knowledge_coverage_terms_mappings"]
    assignment_sources = operations.tables["private_knowledge_terms_assignment_sources"]

    assert foreign_keys(assignments) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "knowledge_contract_id": ("private_knowledge_contracts.id", "RESTRICT"),
        "terms_edition_id": ("terms_editions.id", "RESTRICT"),
    }
    assert foreign_keys(assignment_sources) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "terms_assignment_id": ("private_knowledge_terms_assignments.id", "RESTRICT"),
    }
    assert foreign_keys(sections) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
    }
    assert foreign_keys(clauses) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "terms_section_id": ("private_knowledge_terms_sections.id", "RESTRICT"),
    }
    assert foreign_keys(semantic_reviews) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "terms_section_id": ("private_knowledge_terms_sections.id", "RESTRICT"),
    }
    assert foreign_keys(facts) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "terms_section_id": ("private_knowledge_terms_sections.id", "RESTRICT"),
        "semantic_review_id": ("private_knowledge_semantic_reviews.id", "RESTRICT"),
    }
    assert foreign_keys(citations) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "fact_id": ("private_knowledge_facts.id", "RESTRICT"),
        "source_clause_id": ("private_knowledge_source_clauses.id", "RESTRICT"),
    }
    assert foreign_keys(mappings) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "coverage_id": ("private_knowledge_coverages.id", "RESTRICT"),
        "terms_section_id": ("private_knowledge_terms_sections.id", "RESTRICT"),
    }

    for decision in (
        "document_identity_decision",
        "edition_applicability_decision",
        "overall_decision",
    ):
        assert decision in assignments.columns
    for decision in (
        "enrollment_decision",
        "document_identity_decision",
        "edition_applicability_decision",
        "section_mapping_decision",
        "overall_decision",
    ):
        assert decision in mappings.columns
    assert {
        "source_alias",
        "source_alias_digest_sha256",
        "selection_ordinal",
        "selected_evidence_json",
    } <= set(assignment_sources.columns.keys())
    assert {
        "mapping_applicability",
        "selected_terms_source_alias",
        "selected_terms_source_alias_digest_sha256",
    } <= set(mappings.columns.keys())
    assert "mapping_applicability IN ('APPLICABLE', 'NOT_APPLICABLE', 'UNKNOWN')" in checks(
        mappings
    )
    assert {"source_clause_key", "page_start", "page_end", "source_text_sha256"} <= set(
        clauses.columns.keys()
    )
    assert {
        "source_review_key",
        "section_summary",
        "analysis_status",
        "confidence",
        "review_state",
        "found_categories_json",
        "missing_categories_json",
        "warnings_json",
    } <= set(semantic_reviews.columns.keys())
    assert {
        "source_fact_key",
        "fact_type",
        "statement",
        "conditions_json",
        "numeric_terms_json",
        "review_state",
        "executable",
    } <= set(facts.columns.keys())
    assert {"citation_ordinal", "page_start", "page_end", "source_text_sha256"} <= set(
        citations.columns.keys()
    )
    assert "executable = false" in checks(facts)
    assert "executable = false" in checks(mappings)
    assert "page_start >= 1 AND page_end >= page_start" in checks(clauses)
    assert "page_start >= 1 AND page_end >= page_start" in checks(citations)


def test_cross_entity_references_cannot_cross_import_runs() -> None:
    _, operations = run_upgrade()

    assert composite_foreign_keys(operations.tables["private_knowledge_contracts"]) == {
        (
            ("subject_id", "import_run_id"),
            (
                "private_knowledge_subjects.id",
                "private_knowledge_subjects.import_run_id",
            ),
        )
    }
    assert composite_foreign_keys(operations.tables["private_knowledge_coverages"]) == {
        (
            ("knowledge_contract_id", "import_run_id"),
            (
                "private_knowledge_contracts.id",
                "private_knowledge_contracts.import_run_id",
            ),
        )
    }
    assert composite_foreign_keys(operations.tables["private_knowledge_terms_assignments"]) == {
        (
            ("knowledge_contract_id", "import_run_id"),
            (
                "private_knowledge_contracts.id",
                "private_knowledge_contracts.import_run_id",
            ),
        )
    }
    assert composite_foreign_keys(
        operations.tables["private_knowledge_terms_assignment_sources"]
    ) == {
        (
            ("terms_assignment_id", "import_run_id"),
            (
                "private_knowledge_terms_assignments.id",
                "private_knowledge_terms_assignments.import_run_id",
            ),
        )
    }
    assert composite_foreign_keys(operations.tables["private_knowledge_source_clauses"]) == {
        (
            ("terms_section_id", "import_run_id"),
            (
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ),
        )
    }
    assert composite_foreign_keys(operations.tables["private_knowledge_semantic_reviews"]) == {
        (
            ("terms_section_id", "import_run_id"),
            (
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ),
        )
    }
    assert composite_foreign_keys(operations.tables["private_knowledge_facts"]) == {
        (
            ("terms_section_id", "import_run_id"),
            (
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ),
        ),
        (
            ("semantic_review_id", "import_run_id"),
            (
                "private_knowledge_semantic_reviews.id",
                "private_knowledge_semantic_reviews.import_run_id",
            ),
        ),
    }
    assert composite_foreign_keys(operations.tables["private_knowledge_fact_citations"]) == {
        (
            ("fact_id", "import_run_id"),
            ("private_knowledge_facts.id", "private_knowledge_facts.import_run_id"),
        ),
        (
            ("source_clause_id", "import_run_id"),
            (
                "private_knowledge_source_clauses.id",
                "private_knowledge_source_clauses.import_run_id",
            ),
        ),
    }
    assert composite_foreign_keys(
        operations.tables["private_knowledge_coverage_terms_mappings"]
    ) == {
        (
            ("coverage_id", "import_run_id"),
            (
                "private_knowledge_coverages.id",
                "private_knowledge_coverages.import_run_id",
            ),
        ),
        (
            ("terms_section_id", "import_run_id"),
            (
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ),
        ),
    }


def test_document_binding_requires_exact_axes_for_match() -> None:
    _, operations = run_upgrade()
    table = operations.tables["private_knowledge_document_bindings"]

    assert foreign_keys(table) == {
        "import_run_id": ("private_knowledge_import_runs.id", "RESTRICT"),
        "document_version_id": ("document_versions.id", "RESTRICT"),
        "evidence_id": ("evidence.id", "RESTRICT"),
    }
    assert {
        "source_alias",
        "source_alias_digest_sha256",
        "binding_decision",
        "binding_conflict",
        "binding_reason_code",
        "expected_content_sha256",
        "expected_page_count",
        "content_digest_decision",
        "page_count_decision",
        "document_kind_decision",
        "source_record_json",
        "source_record_digest_sha256",
    } <= set(table.columns.keys())
    table_checks = checks(table)
    assert "binding_decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')" in table_checks
    assert any(
        "binding_decision <> 'MATCH'" in value
        and "content_digest_decision = 'MATCH'" in value
        and "page_count_decision = 'MATCH'" in value
        and "document_kind_decision = 'MATCH'" in value
        for value in table_checks
    )


def test_source_records_are_bounded_json_objects_without_path_columns() -> None:
    _, operations = run_upgrade()
    forbidden = {
        "absolute_path",
        "archive_key",
        "database_url",
        "dsn",
        "file_path",
        "password",
        "policy_number",
        "provider_payload",
        "raw_pdf",
        "source_path",
    }
    all_columns = {column.name for table in operations.tables.values() for column in table.columns}
    assert forbidden.isdisjoint(all_columns)
    assert "deleted_at" not in all_columns

    for name, table in operations.tables.items():
        if "source_record_json" not in table.columns:
            continue
        assert "source_record_digest_sha256" in table.columns
        assert "jsonb_typeof(source_record_json) = 'object'" in checks(table), name


def test_unique_source_identity_and_query_indexes_cover_every_entity() -> None:
    _, operations = run_upgrade()
    expected_unique_indexes = {
        "uq_private_knowledge_subjects_source",
        "uq_private_knowledge_contracts_source",
        "uq_private_knowledge_coverages_source",
        "uq_private_knowledge_assignments_source",
        "uq_private_knowledge_assignment_sources_ordinal",
        "uq_private_knowledge_sections_source",
        "uq_private_knowledge_clauses_source",
        "uq_private_knowledge_semantic_reviews_source",
        "uq_private_knowledge_facts_source",
        "uq_private_knowledge_fact_citations_ordinal",
        "uq_private_knowledge_mappings_source",
        "uq_private_knowledge_document_bindings_alias",
    }
    assert expected_unique_indexes <= set(operations.indexes)
    assert all(operations.indexes[name]["unique"] is True for name in expected_unique_indexes)
    for name in expected_unique_indexes:
        assert operations.indexes[name]["columns"][0] == "import_run_id"


def test_downgrade_drops_tables_in_reverse_dependency_order() -> None:
    migration = cast(Any, load_migration())
    operations = RecordingOperations()
    migration.op = operations

    migration.downgrade()

    dropped = [item[1] for item in operations.operations if item[0] == "drop_table"]
    assert dropped == list(
        reversed(
            [
                "private_knowledge_import_runs",
                "private_knowledge_subjects",
                "private_knowledge_contracts",
                "private_knowledge_coverages",
                "private_knowledge_terms_assignments",
                "private_knowledge_terms_assignment_sources",
                "private_knowledge_terms_sections",
                "private_knowledge_source_clauses",
                "private_knowledge_semantic_reviews",
                "private_knowledge_facts",
                "private_knowledge_fact_citations",
                "private_knowledge_coverage_terms_mappings",
                "private_knowledge_document_bindings",
            ]
        )
    )
