"""Structural tests for the advisory coverage migration."""

from __future__ import annotations

import importlib
from typing import Any


class RecordingOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def __getattr__(self, name: str):
        def record(*args: Any, **kwargs: Any) -> None:
            self.calls.append((name, args, kwargs))

        return record


def _migration():
    return importlib.import_module(
        "apps.api.migrations.versions.0023_advisory_coverage_disposition"
    )


def test_upgrade_adds_advisory_disposition_and_closed_decision_count(monkeypatch) -> None:
    migration = _migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    assert migration.down_revision == "0022_analysis_assistance"
    assert migration.revision == "0023_advisory_disposition"
    assert len(migration.revision) <= 32
    advisory_constraints = [
        args[2]
        for name, args, _ in operations.calls
        if name == "create_check_constraint"
        and args[0] == "ck_private_knowledge_dispositions_value"
    ]
    assert advisory_constraints == [
        "disposition IN ('PUBLISHED', 'ADVISORY', 'BLOCKED', 'NOT_APPLICABLE')"
    ]
    publication_schema_constraints = [
        args[2]
        for name, args, _ in operations.calls
        if name == "create_check_constraint" and args[0] == "ck_private_knowledge_rule_runs_schema"
    ]
    assert publication_schema_constraints == [
        "package_schema_version IN ("
        "'private-knowledge-rule-publication.sol-v1', "
        "'private-knowledge-rule-publication.sol-v2')"
    ]
    added_columns = [
        args[1]
        for name, args, _ in operations.calls
        if name == "add_column" and args[0] == "decision_runs"
    ]
    assert [column.name for column in added_columns] == ["knowledge_advisory_coverage_count"]
    closure = [
        args[2]
        for name, args, _ in operations.calls
        if name == "create_check_constraint" and args[0] == "ck_decision_runs_knowledge_counts"
    ]
    assert len(closure) == 1
    assert "knowledge_advisory_coverage_count >= 0" in closure[0]
    assert (
        "knowledge_published_coverage_count + knowledge_advisory_coverage_count "
        "+ knowledge_blocked_coverage_count + knowledge_not_applicable_coverage_count "
        "<= knowledge_benefit_coverage_count"
    ) in closure[0]
    calculation_constraints = [
        args[2]
        for name, args, _ in operations.calls
        if name == "create_check_constraint" and args[0] == "ck_pk_calculations_status_values"
    ]
    assert len(calculation_constraints) == 1
    assert "confirmed_amount IS NULL OR" in calculation_constraints[0]
    assert "calculation_status = 'CALCULATED'" in calculation_constraints[0]
    assert "hold_reason_code IS NULL" in calculation_constraints[0]


def test_upgrade_adds_append_only_enrollment_authority_and_recommendation_lineage(
    monkeypatch,
) -> None:
    """Catch loss of the raw certificate decision or exact disposition authority."""

    migration = _migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.upgrade()

    added_columns = {
        (args[0], args[1].name): args[1]
        for name, args, _ in operations.calls
        if name == "add_column"
    }
    assert {
        name
        for table, name in added_columns
        if table == "private_knowledge_coverage_execution_dispositions"
    } == {
        "enrollment_decision_snapshot",
        "enrollment_authority",
        "enrollment_reason_code",
        "enrollment_confirmed_by",
    }
    assert {name for table, name in added_columns if table == "analysis_recommendations"} == {
        "coverage_execution_disposition_id",
        "enrollment_authority_snapshot",
    }

    constraints = {
        args[0]: args[2] for name, args, _ in operations.calls if name == "create_check_constraint"
    }
    authority = constraints["ck_private_knowledge_dispositions_enrollment_authority"]
    assert "enrollment_decision_snapshot = 'NO_MATCH'" in authority
    assert "enrollment_authority IS NULL" in authority
    assert "enrollment_decision_snapshot = 'UNKNOWN'" in authority
    assert "enrollment_authority = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'" in authority
    assert "enrollment_reason_code = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'" in authority
    assert "enrollment_confirmed_by IS NOT NULL" in authority
    assert ("reason_codes_json @> '[\"USER_CONFIRMED_COVERAGE_ENROLLMENT\"]'::jsonb") in authority
    assert not authority.startswith("((enrollment_decision_snapshot IS NULL")
    snapshot_not_null = [
        (args, kwargs)
        for name, args, kwargs in operations.calls
        if name == "alter_column"
        and args[:2]
        == (
            "private_knowledge_coverage_execution_dispositions",
            "enrollment_decision_snapshot",
        )
    ]
    assert snapshot_not_null
    assert snapshot_not_null[0][1]["nullable"] is False
    recommendation = constraints["ck_analysis_recommendations_enrollment"]
    assert "enrollment_decision_snapshot = 'MATCH'" in recommendation
    assert "enrollment_authority_snapshot = 'CERTIFICATE_SNAPSHOT'" in recommendation
    assert "enrollment_decision_snapshot = 'UNKNOWN'" in recommendation
    assert "enrollment_authority_snapshot = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'" in recommendation
    assert "enrollment_decision_snapshot = 'NO_MATCH'" not in recommendation

    foreign_keys = {
        args[0]: (args, kwargs)
        for name, args, kwargs in operations.calls
        if name == "create_foreign_key"
    }
    assert "fk_private_knowledge_dispositions_enrollment_snapshot" in foreign_keys
    assert "fk_private_knowledge_dispositions_enrollment_confirmer" in foreign_keys
    assert "fk_private_knowledge_dispositions_confirmation_run_actor" in foreign_keys
    lineage_args, _ = foreign_keys["fk_analysis_recommendations_disposition_authority"]
    assert lineage_args[1] == "analysis_recommendations"
    assert lineage_args[2] == "private_knowledge_coverage_execution_dispositions"
    assert "coverage_execution_disposition_id" in lineage_args[3]
    assert "enrollment_authority_snapshot" in lineage_args[3]
    indexes = {args[0] for name, args, _ in operations.calls if name == "create_index"}
    assert "ix_private_knowledge_dispositions_enrollment_snapshot" in indexes
    assert "ix_private_knowledge_dispositions_enrollment_confirmer" in indexes

    mutation_sql = "\n".join(
        str(args[0]) for name, args, _ in operations.calls if name == "execute"
    )
    assert "UPDATE private_knowledge_coverage_execution_dispositions" in mutation_sql
    assert "coverage.enrollment_decision" in mutation_sql
    assert "UPDATE analysis_recommendations" in mutation_sql
    assert "coverage_execution_disposition_id" in mutation_sql


def test_downgrade_fails_closed_before_rewriting_v2_history(monkeypatch) -> None:
    migration = _migration()
    operations = RecordingOperations()
    monkeypatch.setattr(migration, "op", operations)

    migration.downgrade()

    sql = "\n".join(str(args[0]) for name, args, _ in operations.calls if name == "execute")
    assert "RAISE EXCEPTION" in sql
    assert "private-knowledge-rule-publication.sol-v2" in sql
    assert "disposition = 'ADVISORY'" in sql
    assert "knowledge_advisory_coverage_count <> 0" in sql
    assert "enrollment_authority = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'" in sql
    assert "enrollment_authority_snapshot = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'" in sql
    assert "END;\n        $$" in sql
    assert "UPDATE" not in sql.upper()
    assert "DELETE" not in sql.upper()
    restored_calculation_constraints = [
        args[2]
        for name, args, _ in operations.calls
        if name == "create_check_constraint" and args[0] == "ck_pk_calculations_status_values"
    ]
    assert len(restored_calculation_constraints) == 1
    assert "confirmed_amount IS NULL OR" not in restored_calculation_constraints[0]
    restored_schema_constraints = [
        args[2]
        for name, args, _ in operations.calls
        if name == "create_check_constraint" and args[0] == "ck_private_knowledge_rule_runs_schema"
    ]
    assert restored_schema_constraints == [
        "package_schema_version = 'private-knowledge-rule-publication.sol-v1'"
    ]
