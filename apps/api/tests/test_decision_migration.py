"""Migration contract for the deterministic coverage decision boundary."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = ROOT / "apps/api/migrations/versions/0007_coverage_decision_engine.py"

EXPECTED_TABLES = {
    "medical_events",
    "decision_runs",
    "rule_evaluations",
    "rule_evaluation_evidence",
    "claim_candidates",
}


class RecordingOperations:
    """Alembic spy that preserves table, index, and check metadata."""

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
    spec = importlib.util.spec_from_file_location("decision_migration", MIGRATION_PATH)
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


def test_revision_is_exactly_chained_from_rider_clause_rules() -> None:
    migration = load_migration()

    assert migration.revision == "0007_coverage_decision_engine"
    assert migration.down_revision == "0006_rider_clause_rules"
    assert migration.branch_labels is None
    assert migration.depends_on is None


def test_upgrade_creates_only_the_five_decision_tables() -> None:
    _, operations = run_upgrade()

    assert set(operations.tables) == EXPECTED_TABLES
    assert not {
        "household_spaces",
        "family_members",
        "policy_contracts",
        "riders",
        "coverage_rules",
        "coverage_rule_versions",
        "claim_cases",
    } & set(operations.tables)


def test_medical_events_are_scoped_structured_and_soft_deletable() -> None:
    _, operations = run_upgrade()
    events = operations.tables["medical_events"]

    assert {
        "id",
        "household_space_id",
        "family_member_id",
        "mode",
        "event_date",
        "visit_date",
        "facts_json",
        "confirmation_json",
        "version",
        "created_at",
        "updated_at",
        "deleted_at",
    } == set(events.c.keys())
    assert foreign_keys(events) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "family_member_id": ("family_members.id", "RESTRICT"),
    }
    assert isinstance(events.c.facts_json.type, postgresql.JSONB)
    assert isinstance(events.c.confirmation_json.type, postgresql.JSONB)
    event_checks = checks(events)
    assert "pre_visit" in event_checks["ck_medical_events_mode"]
    assert "post_treatment" in event_checks["ck_medical_events_mode"]
    assert event_checks["ck_medical_events_version"] == "version >= 1"
    assert "event_date IS NULL" in event_checks["ck_medical_events_event_date"]
    assert "visit_date IS NULL" in event_checks["ck_medical_events_visit_date"]
    assert event_checks["ck_medical_events_facts_object"] == ("jsonb_typeof(facts_json) = 'object'")
    assert (
        event_checks["ck_medical_events_confirmation_object"]
        == "jsonb_typeof(confirmation_json) = 'object'"
    )


def test_decision_runs_preserve_event_rule_and_policy_snapshot_versions() -> None:
    _, operations = run_upgrade()
    runs = operations.tables["decision_runs"]

    assert {
        "id",
        "household_space_id",
        "medical_event_id",
        "engine_version",
        "rule_set_version",
        "event_version",
        "policy_snapshot_at",
        "status",
        "stale",
        "created_at",
    } == set(runs.c.keys())
    assert foreign_keys(runs) == {
        "household_space_id": ("household_spaces.id", "RESTRICT"),
        "medical_event_id": ("medical_events.id", "RESTRICT"),
    }
    run_checks = checks(runs)
    assert all(
        status in run_checks["ck_decision_runs_status"]
        for status in ("running", "succeeded", "failed")
    )
    assert run_checks["ck_decision_runs_event_version"] == "event_version >= 1"
    assert run_checks["ck_decision_runs_engine_version_nonempty"] == "engine_version <> ''"
    assert run_checks["ck_decision_runs_rule_set_version_nonempty"] == "rule_set_version <> ''"


def test_rule_evaluations_store_one_tri_state_and_versioned_inputs() -> None:
    _, operations = run_upgrade()
    evaluations = operations.tables["rule_evaluations"]

    assert {
        "id",
        "decision_run_id",
        "rider_id",
        "coverage_rule_version_id",
        "result",
        "required",
        "reason_code",
        "facts_json",
        "evidence_snapshot_json",
        "missing_fields_json",
        "conflicting_fields_json",
        "evaluator_version",
        "created_at",
    } == set(evaluations.c.keys())
    assert foreign_keys(evaluations) == {
        "decision_run_id": ("decision_runs.id", "RESTRICT"),
        "rider_id": ("riders.id", "RESTRICT"),
        "coverage_rule_version_id": ("coverage_rule_versions.id", "RESTRICT"),
    }
    assert isinstance(evaluations.c.facts_json.type, postgresql.JSONB)
    assert isinstance(evaluations.c.evidence_snapshot_json.type, postgresql.JSONB)
    assert isinstance(evaluations.c.missing_fields_json.type, postgresql.JSONB)
    assert isinstance(evaluations.c.conflicting_fields_json.type, postgresql.JSONB)
    evaluation_checks = checks(evaluations)
    assert all(
        result in evaluation_checks["ck_rule_evaluations_result"]
        for result in ("MATCH", "NO_MATCH", "UNKNOWN")
    )
    assert evaluation_checks["ck_rule_evaluations_reason_code_nonempty"] == "reason_code <> ''"
    assert evaluation_checks["ck_rule_evaluations_evaluator_version_nonempty"] == (
        "evaluator_version <> ''"
    )
    assert evaluation_checks["ck_rule_evaluations_facts_object"] == (
        "jsonb_typeof(facts_json) = 'object'"
    )
    assert evaluation_checks["ck_rule_evaluations_evidence_snapshot_array"] == (
        "jsonb_typeof(evidence_snapshot_json) = 'array'"
    )
    assert evaluation_checks["ck_rule_evaluations_missing_fields_array"] == (
        "jsonb_typeof(missing_fields_json) = 'array'"
    )
    assert evaluation_checks["ck_rule_evaluations_conflicting_fields_array"] == (
        "jsonb_typeof(conflicting_fields_json) = 'array'"
    )
    assert (
        "decision_run_id",
        "rider_id",
        "coverage_rule_version_id",
    ) in unique_columns(evaluations)


def test_evaluation_evidence_is_a_unique_audit_preserving_join() -> None:
    _, operations = run_upgrade()
    join = operations.tables["rule_evaluation_evidence"]

    assert foreign_keys(join) == {
        "rule_evaluation_id": ("rule_evaluations.id", "RESTRICT"),
        "evidence_id": ("evidence.id", "RESTRICT"),
    }
    assert {column.name for column in join.primary_key.columns} == {
        "rule_evaluation_id",
        "evidence_id",
    }


def test_claim_candidates_aggregate_only_decision_results() -> None:
    _, operations = run_upgrade()
    candidates = operations.tables["claim_candidates"]

    assert {
        "id",
        "decision_run_id",
        "rider_id",
        "rider_type",
        "aggregate_result",
        "required_match_count",
        "required_unknown_count",
        "required_no_match_count",
        "questions_json",
        "hold_reason_codes_json",
        "version",
        "created_at",
    } == set(candidates.c.keys())
    assert foreign_keys(candidates) == {
        "decision_run_id": ("decision_runs.id", "RESTRICT"),
        "rider_id": ("riders.id", "RESTRICT"),
    }
    assert isinstance(candidates.c.questions_json.type, postgresql.JSONB)
    assert isinstance(candidates.c.hold_reason_codes_json.type, postgresql.JSONB)
    candidate_checks = checks(candidates)
    assert all(
        result in candidate_checks["ck_claim_candidates_aggregate_result"]
        for result in ("MATCH", "NO_MATCH", "UNKNOWN")
    )
    assert candidate_checks["ck_claim_candidates_match_count"] == "required_match_count >= 0"
    assert candidate_checks["ck_claim_candidates_unknown_count"] == "required_unknown_count >= 0"
    assert candidate_checks["ck_claim_candidates_no_match_count"] == "required_no_match_count >= 0"
    assert candidate_checks["ck_claim_candidates_version"] == "version >= 1"
    assert "'fixed'" in candidate_checks["ck_claim_candidates_rider_type"]
    assert "'indemnity'" in candidate_checks["ck_claim_candidates_rider_type"]
    assert candidate_checks["ck_claim_candidates_questions_array"] == (
        "jsonb_typeof(questions_json) = 'array'"
    )
    assert candidate_checks["ck_claim_candidates_hold_reasons_array"] == (
        "jsonb_typeof(hold_reason_codes_json) = 'array'"
    )
    assert ("decision_run_id", "rider_id") in unique_columns(candidates)


def test_scope_and_result_indexes_cover_reads_without_private_payload_indexes() -> None:
    _, operations = run_upgrade()

    assert operations.indexes["ix_medical_events_household_active"]["columns"] == [
        "household_space_id",
        "deleted_at",
        "id",
    ]
    assert operations.indexes["ix_decision_runs_household_event"]["columns"] == [
        "household_space_id",
        "medical_event_id",
        "created_at",
        "id",
    ]
    assert operations.indexes["ix_rule_evaluations_run"]["columns"] == [
        "decision_run_id",
        "id",
    ]
    assert operations.indexes["ix_claim_candidates_run_result"]["columns"] == [
        "decision_run_id",
        "aggregate_result",
        "id",
    ]


def test_migration_never_adds_medical_files_paths_or_raw_text() -> None:
    _, operations = run_upgrade()
    forbidden = {
        "source_path",
        "absolute_path",
        "file_path",
        "file_id",
        "raw_text",
        "document_text",
        "ocr_text",
        "pdf_bytes",
        "image_bytes",
        "password",
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
        "claim_candidates",
        "rule_evaluation_evidence",
        "rule_evaluations",
        "decision_runs",
        "medical_events",
    ]
