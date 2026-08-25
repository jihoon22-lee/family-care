"""Add the non-authoritative MedicalEvent structuring queue and fact history.

Revision ID: 0009_event_structuring
Revises: 0008_benefit_calculations
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_event_structuring"
down_revision: str | Sequence[str] | None = "0008_benefit_calculations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JOB_STATES = (
    "'queued', 'running', 'succeeded', 'retryable_failed', 'permanently_failed', 'cancelled'"
)
_JOB_ERROR_CODES = (
    "'STRUCTURING_AUTHENTICATION_FAILED', 'STRUCTURING_INVALID_RESPONSE', "
    "'STRUCTURING_PROVIDER_TIMEOUT', 'STRUCTURING_RATE_LIMITED', "
    "'STRUCTURING_UNAVAILABLE'"
)
_FACT_SOURCES = "'ai', 'user', 'system'"
_FACT_VERSION_STATES = "'candidate', 'applied', 'superseded'"
_AUDIT_ACTIONS = "'created', 'overridden', 'conflict_detected', 'superseded'"
_ACTOR_KINDS = "'ai', 'user', 'system'"
_REASON_CODE_PATTERN = "'^[A-Z][A-Z0-9_]{0,63}$'"
_PROVIDER_REQUEST_ID_PATTERN = "'^[A-Za-z0-9._:-]{1,128}$'"


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _foreign_uuid(
    name: str,
    target: str,
    *,
    nullable: bool = False,
    ondelete: str = "RESTRICT",
) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=nullable,
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


def _jsonb(name: str, *, default: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text(default),
    )


def upgrade() -> None:
    """Add bounded event situation metadata and an isolated structuring queue."""

    op.add_column(
        "medical_events",
        sa.Column("situation_text", sa.String(length=2000), nullable=True),
    )
    op.add_column(
        "medical_events",
        sa.Column(
            "situation_retention_until",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_medical_events_situation_text_nonempty",
        "medical_events",
        "situation_text IS NULL OR btrim(situation_text) <> ''",
    )
    op.add_column(
        "claim_candidates",
        sa.Column("rider_label_snapshot", sa.String(length=160), nullable=True),
    )

    op.create_table(
        "medical_event_structuring_jobs",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("structurer_version", sa.String(length=64), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("10")),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"state IN ({_JOB_STATES})",
            name="ck_medical_event_structuring_jobs_state",
        ),
        sa.CheckConstraint(
            "event_version >= 1",
            name="ck_medical_event_structuring_jobs_event_version",
        ),
        sa.CheckConstraint(
            "structurer_version <> ''",
            name="ck_medical_event_structuring_jobs_structurer_version",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_medical_event_structuring_jobs_attempts",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1",
            name="ck_medical_event_structuring_jobs_max_attempts",
        ),
        sa.CheckConstraint(
            "max_attempts <= 10",
            name="ck_medical_event_structuring_jobs_max_attempts_bound",
        ),
        sa.CheckConstraint(
            "attempts <= max_attempts",
            name="ck_medical_event_structuring_jobs_attempts_limit",
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_JOB_ERROR_CODES})",
            name="ck_medical_event_structuring_jobs_error_code",
        ),
        sa.CheckConstraint(
            "provider_request_id IS NULL OR provider_request_id <> ''",
            name="ck_medical_event_structuring_jobs_provider_request_id",
        ),
        sa.CheckConstraint(
            f"provider_request_id IS NULL OR provider_request_id ~ {_PROVIDER_REQUEST_ID_PATTERN}",
            name="ck_medical_event_structuring_jobs_provider_request_id_format",
        ),
        sa.CheckConstraint(
            "lease_owner IS NULL OR lease_owner <> ''",
            name="ck_medical_event_structuring_jobs_lease_owner",
        ),
        sa.CheckConstraint(
            "(state = 'succeeded' AND completed_at IS NOT NULL) OR "
            "(state <> 'succeeded' AND completed_at IS NULL)",
            name="ck_medical_event_structuring_jobs_completion",
        ),
    )
    op.create_index(
        "ix_medical_event_structuring_jobs_household_state",
        "medical_event_structuring_jobs",
        ["household_space_id", "state", "available_at", "id"],
    )
    op.create_index(
        "ix_medical_event_structuring_jobs_event_version",
        "medical_event_structuring_jobs",
        ["medical_event_id", "event_version", "created_at", "id"],
    )

    op.create_table(
        "medical_event_fact_versions",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        _foreign_uuid(
            "structuring_job_id",
            "medical_event_structuring_jobs.id",
            nullable=True,
        ),
        _foreign_uuid(
            "parent_version_id",
            "medical_event_fact_versions.id",
            nullable=True,
        ),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("version_state", sa.String(length=16), nullable=False),
        _jsonb("facts_json", default="'{}'::jsonb"),
        _jsonb("questions_json", default="'[]'::jsonb"),
        _jsonb("issue_codes_json", default="'[]'::jsonb"),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _created_at(),
        sa.CheckConstraint(
            "event_version >= 1",
            name="ck_medical_event_fact_versions_event_version",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_medical_event_fact_versions_version",
        ),
        sa.CheckConstraint(
            f"source IN ({_FACT_SOURCES})",
            name="ck_medical_event_fact_versions_source",
        ),
        sa.CheckConstraint(
            f"version_state IN ({_FACT_VERSION_STATES})",
            name="ck_medical_event_fact_versions_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(facts_json) = 'object'",
            name="ck_medical_event_fact_versions_facts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(questions_json) = 'array'",
            name="ck_medical_event_fact_versions_questions_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(issue_codes_json) = 'array'",
            name="ck_medical_event_fact_versions_issue_codes_array",
        ),
        sa.UniqueConstraint(
            "medical_event_id",
            "event_version",
            "version",
            name="uq_medical_event_fact_versions_event_version",
        ),
    )
    op.create_index(
        "ix_medical_event_fact_versions_household_event",
        "medical_event_fact_versions",
        ["household_space_id", "medical_event_id", "event_version", "version"],
    )
    op.create_index(
        "uq_medical_event_fact_versions_current",
        "medical_event_fact_versions",
        ["medical_event_id"],
        unique=True,
        postgresql_where=sa.text("is_current = true"),
    )

    op.create_table(
        "medical_event_fact_audit",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        _foreign_uuid("fact_version_id", "medical_event_fact_versions.id"),
        _foreign_uuid(
            "parent_version_id",
            "medical_event_fact_versions.id",
            nullable=True,
        ),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_kind", sa.String(length=16), nullable=False),
        _jsonb("changed_fields_json", default="'[]'::jsonb"),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "event_version >= 1",
            name="ck_medical_event_fact_audit_event_version",
        ),
        sa.CheckConstraint(
            f"action IN ({_AUDIT_ACTIONS})",
            name="ck_medical_event_fact_audit_action",
        ),
        sa.CheckConstraint(
            f"actor_kind IN ({_ACTOR_KINDS})",
            name="ck_medical_event_fact_audit_actor_kind",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(changed_fields_json) = 'array'",
            name="ck_medical_event_fact_audit_changed_fields_array",
        ),
        sa.CheckConstraint(
            "reason_code <> ''",
            name="ck_medical_event_fact_audit_reason_code",
        ),
        sa.CheckConstraint(
            f"reason_code ~ {_REASON_CODE_PATTERN}",
            name="ck_medical_event_fact_audit_reason_code_format",
        ),
    )
    op.create_index(
        "ix_medical_event_fact_audit_household_event",
        "medical_event_fact_audit",
        ["household_space_id", "medical_event_id", "created_at", "id"],
    )
    op.create_index(
        "ix_medical_event_fact_audit_fact_version",
        "medical_event_fact_audit",
        ["fact_version_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Remove event fact history and structuring metadata in reverse order."""

    op.drop_index(
        "ix_medical_event_fact_audit_fact_version",
        table_name="medical_event_fact_audit",
    )
    op.drop_index(
        "ix_medical_event_fact_audit_household_event",
        table_name="medical_event_fact_audit",
    )
    op.drop_table("medical_event_fact_audit")
    op.drop_index(
        "uq_medical_event_fact_versions_current",
        table_name="medical_event_fact_versions",
    )
    op.drop_index(
        "ix_medical_event_fact_versions_household_event",
        table_name="medical_event_fact_versions",
    )
    op.drop_table("medical_event_fact_versions")
    op.drop_index(
        "ix_medical_event_structuring_jobs_event_version",
        table_name="medical_event_structuring_jobs",
    )
    op.drop_index(
        "ix_medical_event_structuring_jobs_household_state",
        table_name="medical_event_structuring_jobs",
    )
    op.drop_table("medical_event_structuring_jobs")
    op.drop_column("claim_candidates", "rider_label_snapshot")
    op.drop_constraint(
        "ck_medical_events_situation_text_nonempty",
        table_name="medical_events",
        type_="check",
    )
    op.drop_column("medical_events", "situation_retention_until")
    op.drop_column("medical_events", "situation_text")
