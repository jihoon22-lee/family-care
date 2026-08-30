"""Persist immutable private-knowledge decision and calculation results."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_private_knowledge_decisions"
down_revision: str | Sequence[str] | None = "0020_private_publications"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _nullable_uuid(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.UUID(as_uuid=True), nullable=True)


def _jsonb(name: str, *, default: str | None = None) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text(default) if default is not None else None,
    )


def _timestamp() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def _decision_run_foreign_key(prefix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["decision_run_id", "household_space_id"],
        ["decision_runs.id", "decision_runs.household_space_id"],
        name=f"fk_{prefix}_decision_household",
        ondelete="RESTRICT",
    )


def _rule_run_foreign_key(prefix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [
            "knowledge_rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
        ],
        [
            "private_knowledge_rule_import_runs.id",
            "private_knowledge_rule_import_runs.knowledge_import_run_id",
            "private_knowledge_rule_import_runs.household_space_id",
        ],
        name=f"fk_{prefix}_rule_run",
        ondelete="RESTRICT",
    )


def _array_check(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"jsonb_typeof({column}) = 'array'",
        name=name,
    )


def _reason_code_check(column: str, name: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} ~ '^[A-Z][A-Z0-9_]{{0,63}}$'",
        name=name,
    )


def upgrade() -> None:
    """Add knowledge snapshot identity and private result streams."""

    op.create_index(
        "uq_decision_runs_id_household",
        "decision_runs",
        ["id", "household_space_id"],
        unique=True,
    )
    op.create_index(
        "uq_pk_coverages_contract_scope",
        "private_knowledge_coverages",
        ["id", "knowledge_contract_id", "import_run_id"],
        unique=True,
    )
    op.create_index(
        "uq_pk_rule_publications_eval_scope",
        "private_knowledge_rule_publications",
        [
            "id",
            "rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
            "knowledge_coverage_id",
        ],
        unique=True,
    )
    op.create_index(
        "uq_pk_calc_publications_eval_scope",
        "private_knowledge_calculation_publications",
        [
            "id",
            "rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
            "knowledge_coverage_id",
        ],
        unique=True,
    )

    op.drop_constraint("ck_decision_runs_status", "decision_runs", type_="check")
    op.add_column(
        "decision_runs",
        sa.Column(
            "knowledge_import_run_id",
            sa.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "decision_runs",
        sa.Column(
            "knowledge_rule_import_run_id",
            sa.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "decision_runs",
        sa.Column(
            "knowledge_status_projection_digest",
            sa.String(length=64),
            nullable=True,
        ),
    )
    op.add_column(
        "decision_runs",
        sa.Column(
            "event_fact_schema_version",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'medical-event-facts.v2'"),
        ),
    )
    op.add_column(
        "decision_runs",
        sa.Column(
            "analysis_completeness",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNAVAILABLE'"),
        ),
    )
    op.add_column(
        "decision_runs",
        sa.Column(
            "source_failure_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_decision_runs_status",
        "decision_runs",
        "status IN ('running', 'succeeded', 'partial', 'failed')",
    )
    op.create_check_constraint(
        "ck_decision_runs_analysis_completeness",
        "decision_runs",
        "analysis_completeness IN ('COMPLETE', 'PARTIAL', 'UNAVAILABLE')",
    )
    op.create_check_constraint(
        "ck_decision_runs_fact_schema_nonempty",
        "decision_runs",
        "btrim(event_fact_schema_version) <> ''",
    )
    op.create_check_constraint(
        "ck_decision_runs_status_digest",
        "decision_runs",
        "knowledge_status_projection_digest IS NULL OR "
        "knowledge_status_projection_digest ~ '^[0-9a-f]{64}$'",
    )
    op.create_check_constraint(
        "ck_decision_runs_source_failures",
        "decision_runs",
        "jsonb_typeof(source_failure_codes_json) = 'array' "
        "AND jsonb_array_length(source_failure_codes_json) <= 32",
    )
    op.create_check_constraint(
        "ck_decision_runs_knowledge_lineage",
        "decision_runs",
        "knowledge_rule_import_run_id IS NULL OR knowledge_import_run_id IS NOT NULL",
    )
    op.create_foreign_key(
        "fk_decision_runs_knowledge_run",
        "decision_runs",
        "private_knowledge_import_runs",
        ["knowledge_import_run_id", "household_space_id"],
        ["id", "household_space_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_decision_runs_knowledge_rule_run",
        "decision_runs",
        "private_knowledge_rule_import_runs",
        [
            "knowledge_rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
        ],
        ["id", "knowledge_import_run_id", "household_space_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "private_knowledge_rule_evaluations",
        _uuid("id", primary_key=True),
        _uuid("household_space_id"),
        _uuid("decision_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("knowledge_rule_import_run_id"),
        _uuid("knowledge_coverage_id"),
        _uuid("rule_publication_id"),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        _jsonb("fact_paths_json", default="'[]'::jsonb"),
        _jsonb("missing_fields_json", default="'[]'::jsonb"),
        _jsonb("conflicting_fields_json", default="'[]'::jsonb"),
        _jsonb("citation_snapshot_json", default="'[]'::jsonb"),
        sa.Column("evaluator_version", sa.String(length=64), nullable=False),
        _timestamp(),
        _decision_run_foreign_key("pk_rule_evaluations"),
        sa.ForeignKeyConstraint(
            [
                "rule_publication_id",
                "knowledge_rule_import_run_id",
                "knowledge_import_run_id",
                "household_space_id",
                "knowledge_coverage_id",
            ],
            [
                "private_knowledge_rule_publications.id",
                "private_knowledge_rule_publications.rule_import_run_id",
                "private_knowledge_rule_publications.knowledge_import_run_id",
                "private_knowledge_rule_publications.household_space_id",
                "private_knowledge_rule_publications.knowledge_coverage_id",
            ],
            name="fk_pk_rule_evaluations_publication",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "result IN ('MATCH', 'NO_MATCH', 'UNKNOWN')",
            name="ck_pk_rule_evaluations_result",
        ),
        _reason_code_check("reason_code", "ck_pk_rule_evaluations_reason"),
        _array_check("fact_paths_json", "ck_pk_rule_evaluations_fact_paths"),
        _array_check("missing_fields_json", "ck_pk_rule_evaluations_missing"),
        _array_check("conflicting_fields_json", "ck_pk_rule_evaluations_conflicts"),
        _array_check("citation_snapshot_json", "ck_pk_rule_evaluations_citations"),
        sa.CheckConstraint(
            "btrim(evaluator_version) <> ''",
            name="ck_pk_rule_evaluations_evaluator",
        ),
        sa.UniqueConstraint(
            "decision_run_id",
            "rule_publication_id",
            name="uq_pk_rule_evaluations_run_rule",
        ),
    )
    op.create_index(
        "ix_pk_rule_evaluations_run_result",
        "private_knowledge_rule_evaluations",
        ["decision_run_id", "result", "id"],
        unique=False,
    )
    op.create_index(
        "ix_pk_rule_evaluations_coverage",
        "private_knowledge_rule_evaluations",
        ["knowledge_coverage_id", "decision_run_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_claim_candidates",
        _uuid("id", primary_key=True),
        _uuid("household_space_id"),
        _uuid("decision_run_id"),
        _uuid("knowledge_import_run_id"),
        _nullable_uuid("knowledge_rule_import_run_id"),
        _uuid("knowledge_contract_id"),
        _uuid("knowledge_coverage_id"),
        sa.Column("contract_label_snapshot", sa.String(length=240), nullable=False),
        sa.Column("coverage_label_snapshot", sa.String(length=800), nullable=False),
        sa.Column("benefit_type", sa.String(length=16), nullable=False),
        sa.Column("aggregate_result", sa.String(length=16), nullable=False),
        sa.Column("required_match_count", sa.Integer(), nullable=False),
        sa.Column("required_unknown_count", sa.Integer(), nullable=False),
        sa.Column("required_no_match_count", sa.Integer(), nullable=False),
        _jsonb("questions_json", default="'[]'::jsonb"),
        _jsonb("hold_reason_codes_json", default="'[]'::jsonb"),
        sa.Column(
            "claim_start_ready",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _timestamp(),
        sa.UniqueConstraint(
            "id",
            "decision_run_id",
            "household_space_id",
            "knowledge_import_run_id",
            "knowledge_coverage_id",
            name="uq_pk_candidates_identity",
        ),
        _decision_run_foreign_key("pk_candidates"),
        _rule_run_foreign_key("pk_candidates"),
        sa.ForeignKeyConstraint(
            [
                "knowledge_coverage_id",
                "knowledge_contract_id",
                "knowledge_import_run_id",
            ],
            [
                "private_knowledge_coverages.id",
                "private_knowledge_coverages.knowledge_contract_id",
                "private_knowledge_coverages.import_run_id",
            ],
            name="fk_pk_candidates_coverage_contract",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "benefit_type IN ('FIXED', 'INDEMNITY', 'UNKNOWN')",
            name="ck_pk_candidates_benefit_type",
        ),
        sa.CheckConstraint(
            "aggregate_result IN ('MATCH', 'NO_MATCH', 'UNKNOWN')",
            name="ck_pk_candidates_result",
        ),
        sa.CheckConstraint(
            "required_match_count >= 0 AND required_unknown_count >= 0 "
            "AND required_no_match_count >= 0",
            name="ck_pk_candidates_counts",
        ),
        _array_check("questions_json", "ck_pk_candidates_questions"),
        _array_check("hold_reason_codes_json", "ck_pk_candidates_holds"),
        sa.CheckConstraint(
            "btrim(contract_label_snapshot) <> '' AND btrim(coverage_label_snapshot) <> ''",
            name="ck_pk_candidates_labels",
        ),
        sa.CheckConstraint(
            "claim_start_ready = false",
            name="ck_pk_candidates_claim_ready",
        ),
    )
    op.create_index(
        "uq_pk_candidates_run_coverage",
        "private_knowledge_claim_candidates",
        ["decision_run_id", "knowledge_coverage_id"],
        unique=True,
    )
    op.create_index(
        "ix_pk_candidates_run_result",
        "private_knowledge_claim_candidates",
        ["decision_run_id", "aggregate_result", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_benefit_calculations",
        _uuid("id", primary_key=True),
        _uuid("household_space_id"),
        _uuid("decision_run_id"),
        _uuid("private_claim_candidate_id"),
        _uuid("knowledge_import_run_id"),
        _nullable_uuid("knowledge_rule_import_run_id"),
        _uuid("knowledge_coverage_id"),
        _nullable_uuid("calculation_publication_id"),
        sa.Column("calculation_kind", sa.String(length=16), nullable=False),
        sa.Column("calculation_status", sa.String(length=24), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("confirmed_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("conditional_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("excluded_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("deductible_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("applied_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("applied_limit", sa.Numeric(20, 4), nullable=True),
        sa.Column("rounding_rule", sa.String(length=32), nullable=True),
        sa.Column("hold_reason_code", sa.String(length=64), nullable=True),
        sa.Column("trace_digest_sha256", sa.String(length=64), nullable=False),
        _timestamp(),
        sa.ForeignKeyConstraint(
            [
                "private_claim_candidate_id",
                "decision_run_id",
                "household_space_id",
                "knowledge_import_run_id",
                "knowledge_coverage_id",
            ],
            [
                "private_knowledge_claim_candidates.id",
                "private_knowledge_claim_candidates.decision_run_id",
                "private_knowledge_claim_candidates.household_space_id",
                "private_knowledge_claim_candidates.knowledge_import_run_id",
                "private_knowledge_claim_candidates.knowledge_coverage_id",
            ],
            name="fk_pk_calculations_candidate",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "calculation_publication_id",
                "knowledge_rule_import_run_id",
                "knowledge_import_run_id",
                "household_space_id",
                "knowledge_coverage_id",
            ],
            [
                "private_knowledge_calculation_publications.id",
                "private_knowledge_calculation_publications.rule_import_run_id",
                "private_knowledge_calculation_publications.knowledge_import_run_id",
                "private_knowledge_calculation_publications.household_space_id",
                "private_knowledge_calculation_publications.knowledge_coverage_id",
            ],
            name="fk_pk_calculations_publication",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "calculation_kind IN ('FIXED', 'INDEMNITY', 'NONE', 'UNKNOWN')",
            name="ck_pk_calculations_kind",
        ),
        sa.CheckConstraint(
            "calculation_status IN ('CALCULATED', 'UNKNOWN', 'NOT_APPLICABLE', 'FAILED')",
            name="ck_pk_calculations_status",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_pk_calculations_currency",
        ),
        sa.CheckConstraint(
            "(confirmed_amount IS NULL OR confirmed_amount >= 0) "
            "AND (conditional_amount IS NULL OR conditional_amount >= 0) "
            "AND (excluded_amount IS NULL OR excluded_amount >= 0) "
            "AND (deductible_amount IS NULL OR deductible_amount >= 0) "
            "AND (applied_limit IS NULL OR applied_limit >= 0)",
            name="ck_pk_calculations_amounts",
        ),
        sa.CheckConstraint(
            "applied_rate IS NULL OR (applied_rate >= 0 AND applied_rate <= 1)",
            name="ck_pk_calculations_rate",
        ),
        sa.CheckConstraint(
            "((calculation_status = 'CALCULATED' "
            "AND conditional_amount IS NOT NULL AND currency IS NOT NULL) OR "
            "(calculation_status = 'UNKNOWN' AND conditional_amount IS NULL) OR "
            "(calculation_status IN ('NOT_APPLICABLE', 'FAILED') "
            "AND currency IS NULL AND confirmed_amount IS NULL "
            "AND conditional_amount IS NULL AND excluded_amount IS NULL "
            "AND deductible_amount IS NULL AND applied_rate IS NULL "
            "AND applied_limit IS NULL))",
            name="ck_pk_calculations_status_values",
        ),
        sa.CheckConstraint(
            "calculation_publication_id IS NULL OR knowledge_rule_import_run_id IS NOT NULL",
            name="ck_pk_calculations_publication_lineage",
        ),
        sa.CheckConstraint(
            "trace_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pk_calculations_trace_digest",
        ),
        sa.CheckConstraint(
            "hold_reason_code IS NULL OR hold_reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_pk_calculations_hold_reason",
        ),
        sa.UniqueConstraint(
            "private_claim_candidate_id",
            name="uq_pk_calculations_candidate",
        ),
    )
    op.create_index(
        "ix_pk_calculations_run_status",
        "private_knowledge_benefit_calculations",
        ["decision_run_id", "calculation_status", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_calculation_steps",
        _uuid("id", primary_key=True),
        _uuid("private_benefit_calculation_id"),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("input_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("input_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("output_amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("output_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("rounding_rule", sa.String(length=32), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        _timestamp(),
        sa.ForeignKeyConstraint(
            ["private_benefit_calculation_id"],
            ["private_knowledge_benefit_calculations.id"],
            name="fk_pk_calculation_steps_calculation",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "private_benefit_calculation_id",
            "step_number",
            name="uq_pk_calculation_steps_number",
        ),
        sa.CheckConstraint(
            "step_number >= 1",
            name="ck_pk_calculation_steps_number",
        ),
        sa.CheckConstraint(
            "btrim(operation) <> ''",
            name="ck_pk_calculation_steps_operation",
        ),
        sa.CheckConstraint(
            "input_amount IS NULL OR input_amount >= 0",
            name="ck_pk_calculation_steps_input_amount",
        ),
        sa.CheckConstraint(
            "output_amount IS NULL OR output_amount >= 0",
            name="ck_pk_calculation_steps_output_amount",
        ),
        sa.CheckConstraint(
            "input_currency IS NULL OR input_currency ~ '^[A-Z]{3}$'",
            name="ck_pk_calculation_steps_input_currency",
        ),
        sa.CheckConstraint(
            "output_currency IS NULL OR output_currency ~ '^[A-Z]{3}$'",
            name="ck_pk_calculation_steps_output_currency",
        ),
        _reason_code_check("reason_code", "ck_pk_calculation_steps_reason"),
    )
    op.create_index(
        "ix_pk_calculation_steps_calculation",
        "private_knowledge_calculation_steps",
        ["private_benefit_calculation_id", "step_number", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove private results and restore the v1 decision-run shape."""

    op.drop_index(
        "ix_pk_calculation_steps_calculation",
        table_name="private_knowledge_calculation_steps",
    )
    op.drop_table("private_knowledge_calculation_steps")
    op.drop_index(
        "ix_pk_calculations_run_status",
        table_name="private_knowledge_benefit_calculations",
    )
    op.drop_table("private_knowledge_benefit_calculations")
    op.drop_index(
        "ix_pk_candidates_run_result",
        table_name="private_knowledge_claim_candidates",
    )
    op.drop_index(
        "uq_pk_candidates_run_coverage",
        table_name="private_knowledge_claim_candidates",
    )
    op.drop_table("private_knowledge_claim_candidates")
    op.drop_index(
        "ix_pk_rule_evaluations_coverage",
        table_name="private_knowledge_rule_evaluations",
    )
    op.drop_index(
        "ix_pk_rule_evaluations_run_result",
        table_name="private_knowledge_rule_evaluations",
    )
    op.drop_table("private_knowledge_rule_evaluations")

    op.drop_constraint(
        "fk_decision_runs_knowledge_rule_run",
        "decision_runs",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_decision_runs_knowledge_run",
        "decision_runs",
        type_="foreignkey",
    )
    for constraint_name in (
        "ck_decision_runs_knowledge_lineage",
        "ck_decision_runs_source_failures",
        "ck_decision_runs_status_digest",
        "ck_decision_runs_fact_schema_nonempty",
        "ck_decision_runs_analysis_completeness",
        "ck_decision_runs_status",
    ):
        op.drop_constraint(constraint_name, "decision_runs", type_="check")
    op.execute("UPDATE decision_runs SET status = 'failed' WHERE status = 'partial'")
    op.create_check_constraint(
        "ck_decision_runs_status",
        "decision_runs",
        "status IN ('running', 'succeeded', 'failed')",
    )
    for column_name in (
        "source_failure_codes_json",
        "analysis_completeness",
        "event_fact_schema_version",
        "knowledge_status_projection_digest",
        "knowledge_rule_import_run_id",
        "knowledge_import_run_id",
    ):
        op.drop_column("decision_runs", column_name)

    op.drop_index(
        "uq_pk_calc_publications_eval_scope",
        table_name="private_knowledge_calculation_publications",
    )
    op.drop_index(
        "uq_pk_rule_publications_eval_scope",
        table_name="private_knowledge_rule_publications",
    )
    op.drop_index(
        "uq_pk_coverages_contract_scope",
        table_name="private_knowledge_coverages",
    )
    op.drop_index("uq_decision_runs_id_household", table_name="decision_runs")
