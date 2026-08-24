"""Create the deterministic MedicalEvent and coverage decision boundary.

Revision ID: 0007_coverage_decision_engine
Revises: 0006_rider_clause_rules
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_coverage_decision_engine"
down_revision: str | Sequence[str] | None = "0006_rider_clause_rules"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EVENT_MODES = "'pre_visit', 'post_treatment'"
_RUN_STATUSES = "'running', 'succeeded', 'failed'"
_TRI_STATES = "'MATCH', 'NO_MATCH', 'UNKNOWN'"


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _foreign_uuid(name: str, target: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete="RESTRICT"),
        nullable=False,
    )


def _created_at() -> sa.Column[Any]:
    return sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def _updated_at() -> sa.Column[Any]:
    return sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def upgrade() -> None:
    """Add structured events and immutable decision-result persistence."""

    op.create_table(
        "medical_events",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=True),
        sa.Column(
            "facts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "confirmation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"mode IN ({_EVENT_MODES})",
            name="ck_medical_events_mode",
        ),
        sa.CheckConstraint(
            "event_date IS NULL OR event_date >= DATE '0001-01-01'",
            name="ck_medical_events_event_date",
        ),
        sa.CheckConstraint(
            "visit_date IS NULL OR visit_date >= DATE '0001-01-01'",
            name="ck_medical_events_visit_date",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(facts_json) = 'object'",
            name="ck_medical_events_facts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(confirmation_json) = 'object'",
            name="ck_medical_events_confirmation_object",
        ),
        sa.CheckConstraint("version >= 1", name="ck_medical_events_version"),
    )
    op.create_index(
        "ix_medical_events_household_active",
        "medical_events",
        ["household_space_id", "deleted_at", "id"],
    )
    op.create_index(
        "ix_medical_events_member_date",
        "medical_events",
        ["family_member_id", "event_date", "id"],
    )

    op.create_table(
        "decision_runs",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("rule_set_version", sa.String(length=64), nullable=False),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("policy_snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        _created_at(),
        sa.CheckConstraint(
            f"status IN ({_RUN_STATUSES})",
            name="ck_decision_runs_status",
        ),
        sa.CheckConstraint("event_version >= 1", name="ck_decision_runs_event_version"),
        sa.CheckConstraint(
            "engine_version <> ''",
            name="ck_decision_runs_engine_version_nonempty",
        ),
        sa.CheckConstraint(
            "rule_set_version <> ''",
            name="ck_decision_runs_rule_set_version_nonempty",
        ),
    )
    op.create_index(
        "ix_decision_runs_household_event",
        "decision_runs",
        ["household_space_id", "medical_event_id", "created_at", "id"],
    )
    op.create_index(
        "ix_decision_runs_event_version",
        "decision_runs",
        ["medical_event_id", "event_version", "created_at", "id"],
    )

    op.create_table(
        "rule_evaluations",
        _uuid("id", primary_key=True),
        _foreign_uuid("decision_run_id", "decision_runs.id"),
        _foreign_uuid("rider_id", "riders.id"),
        _foreign_uuid("coverage_rule_version_id", "coverage_rule_versions.id"),
        sa.Column("result", sa.String(length=16), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column(
            "facts_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "evidence_snapshot_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "missing_fields_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "conflicting_fields_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("evaluator_version", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            f"result IN ({_TRI_STATES})",
            name="ck_rule_evaluations_result",
        ),
        sa.CheckConstraint(
            "reason_code <> ''",
            name="ck_rule_evaluations_reason_code_nonempty",
        ),
        sa.CheckConstraint(
            "evaluator_version <> ''",
            name="ck_rule_evaluations_evaluator_version_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(facts_json) = 'object'",
            name="ck_rule_evaluations_facts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_snapshot_json) = 'array'",
            name="ck_rule_evaluations_evidence_snapshot_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(missing_fields_json) = 'array'",
            name="ck_rule_evaluations_missing_fields_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(conflicting_fields_json) = 'array'",
            name="ck_rule_evaluations_conflicting_fields_array",
        ),
        sa.UniqueConstraint(
            "decision_run_id",
            "rider_id",
            "coverage_rule_version_id",
            name="uq_rule_evaluations_run_rider_rule",
        ),
    )
    op.create_index(
        "ix_rule_evaluations_run",
        "rule_evaluations",
        ["decision_run_id", "id"],
    )
    op.create_index(
        "ix_rule_evaluations_rider_result",
        "rule_evaluations",
        ["rider_id", "result", "id"],
    )

    op.create_table(
        "rule_evaluation_evidence",
        _foreign_uuid("rule_evaluation_id", "rule_evaluations.id"),
        _foreign_uuid("evidence_id", "evidence.id"),
        sa.PrimaryKeyConstraint(
            "rule_evaluation_id",
            "evidence_id",
            name="pk_rule_evaluation_evidence",
        ),
    )
    op.create_index(
        "ix_rule_evaluation_evidence_evidence",
        "rule_evaluation_evidence",
        ["evidence_id", "rule_evaluation_id"],
    )

    op.create_table(
        "claim_candidates",
        _uuid("id", primary_key=True),
        _foreign_uuid("decision_run_id", "decision_runs.id"),
        _foreign_uuid("rider_id", "riders.id"),
        sa.Column("rider_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_result", sa.String(length=16), nullable=False),
        sa.Column("required_match_count", sa.Integer(), nullable=False),
        sa.Column("required_unknown_count", sa.Integer(), nullable=False),
        sa.Column("required_no_match_count", sa.Integer(), nullable=False),
        sa.Column(
            "questions_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "hold_reason_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        _created_at(),
        sa.CheckConstraint(
            f"aggregate_result IN ({_TRI_STATES})",
            name="ck_claim_candidates_aggregate_result",
        ),
        sa.CheckConstraint(
            "required_match_count >= 0",
            name="ck_claim_candidates_match_count",
        ),
        sa.CheckConstraint(
            "required_unknown_count >= 0",
            name="ck_claim_candidates_unknown_count",
        ),
        sa.CheckConstraint(
            "required_no_match_count >= 0",
            name="ck_claim_candidates_no_match_count",
        ),
        sa.CheckConstraint(
            "rider_type IN ('fixed', 'indemnity')",
            name="ck_claim_candidates_rider_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(questions_json) = 'array'",
            name="ck_claim_candidates_questions_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(hold_reason_codes_json) = 'array'",
            name="ck_claim_candidates_hold_reasons_array",
        ),
        sa.CheckConstraint("version >= 1", name="ck_claim_candidates_version"),
        sa.UniqueConstraint(
            "decision_run_id",
            "rider_id",
            name="uq_claim_candidates_run_rider",
        ),
    )
    op.create_index(
        "ix_claim_candidates_run_result",
        "claim_candidates",
        ["decision_run_id", "aggregate_result", "id"],
    )
    op.create_index(
        "ix_claim_candidates_rider",
        "claim_candidates",
        ["rider_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove decision persistence in reverse dependency order."""

    op.drop_index("ix_claim_candidates_rider", table_name="claim_candidates")
    op.drop_index("ix_claim_candidates_run_result", table_name="claim_candidates")
    op.drop_table("claim_candidates")
    op.drop_index(
        "ix_rule_evaluation_evidence_evidence",
        table_name="rule_evaluation_evidence",
    )
    op.drop_table("rule_evaluation_evidence")
    op.drop_index("ix_rule_evaluations_rider_result", table_name="rule_evaluations")
    op.drop_index("ix_rule_evaluations_run", table_name="rule_evaluations")
    op.drop_table("rule_evaluations")
    op.drop_index("ix_decision_runs_event_version", table_name="decision_runs")
    op.drop_index("ix_decision_runs_household_event", table_name="decision_runs")
    op.drop_table("decision_runs")
    op.drop_index("ix_medical_events_member_date", table_name="medical_events")
    op.drop_index("ix_medical_events_household_active", table_name="medical_events")
    op.drop_table("medical_events")
