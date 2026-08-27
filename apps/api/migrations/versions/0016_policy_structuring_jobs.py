"""Create the leased private policy structuring queue."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0016_policy_structuring_jobs"
down_revision: str | Sequence[str] | None = "0015_private_batch_document_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = "'queued', 'running', 'succeeded', 'retryable_failed', 'permanently_failed', 'cancelled'"
_TERMINAL_STATES = "'succeeded', 'permanently_failed', 'cancelled'"
_ERROR_CODES = (
    "'POLICY_STRUCTURING_AUTHENTICATION_FAILED', "
    "'POLICY_STRUCTURING_INVALID_RESPONSE', "
    "'POLICY_STRUCTURING_NO_EVIDENCE', "
    "'POLICY_STRUCTURING_PROVIDER_TIMEOUT', "
    "'POLICY_STRUCTURING_RATE_LIMITED', "
    "'POLICY_STRUCTURING_UNAVAILABLE'"
)


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


def _timestamp(name: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("CURRENT_TIMESTAMP"),
    )


def upgrade() -> None:
    """Create a metadata-only, household-scoped policy structuring queue."""

    op.create_table(
        "policy_structuring_jobs",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("batch_item_id", "document_batch_items.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("document_version_id", "document_versions.id"),
        _foreign_uuid("extraction_id", "extractions.id"),
        sa.Column(
            "policy_aggregate_id",
            sa.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("pipeline_version", sa.String(length=64), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("5")),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "batch_item_id",
            name="uq_policy_structuring_jobs_batch_item",
        ),
        sa.UniqueConstraint(
            "extraction_id",
            name="uq_policy_structuring_jobs_extraction",
        ),
        sa.UniqueConstraint(
            "policy_aggregate_id",
            name="uq_policy_structuring_jobs_policy_aggregate",
        ),
        sa.CheckConstraint(
            f"state IN ({_STATES})",
            name="ck_policy_structuring_jobs_state",
        ),
        sa.CheckConstraint(
            "btrim(pipeline_version) <> ''",
            name="ck_policy_structuring_jobs_pipeline_version",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= max_attempts AND max_attempts >= 1 "
            "AND max_attempts <= 5",
            name="ck_policy_structuring_jobs_attempts",
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_ERROR_CODES})",
            name="ck_policy_structuring_jobs_error_code",
        ),
        sa.CheckConstraint(
            "((state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR (state <> 'running' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL))",
            name="ck_policy_structuring_jobs_lease",
        ),
        sa.CheckConstraint(
            f"((state IN ({_TERMINAL_STATES}) AND completed_at IS NOT NULL) OR "
            f"(state NOT IN ({_TERMINAL_STATES}) AND completed_at IS NULL))",
            name="ck_policy_structuring_jobs_completed",
        ),
    )
    op.create_index(
        "ix_policy_structuring_jobs_queue",
        "policy_structuring_jobs",
        ["state", "available_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_policy_structuring_jobs_household_document",
        "policy_structuring_jobs",
        ["household_space_id", "document_version_id", "id"],
        unique=False,
    )

    op.add_column(
        "analysis_candidate_versions",
        sa.Column(
            "structuring_job_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("policy_structuring_jobs.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )
    op.add_column(
        "analysis_candidate_versions",
        sa.Column("source_candidate_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.create_check_constraint(
        "ck_candidate_versions_structuring_source_pair",
        "analysis_candidate_versions",
        "((structuring_job_id IS NULL AND source_candidate_id IS NULL) OR "
        "(structuring_job_id IS NOT NULL AND source_candidate_id IS NOT NULL))",
    )
    op.create_unique_constraint(
        "uq_candidate_versions_structuring_source",
        "analysis_candidate_versions",
        ["structuring_job_id", "source_candidate_id"],
    )
    op.create_index(
        "ix_candidate_versions_structuring_job_id",
        "analysis_candidate_versions",
        ["structuring_job_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove candidate links before dropping their referenced job table."""

    op.drop_index(
        "ix_candidate_versions_structuring_job_id",
        table_name="analysis_candidate_versions",
    )
    op.drop_constraint(
        "uq_candidate_versions_structuring_source",
        "analysis_candidate_versions",
        type_="unique",
    )
    op.drop_constraint(
        "ck_candidate_versions_structuring_source_pair",
        "analysis_candidate_versions",
        type_="check",
    )
    op.drop_column("analysis_candidate_versions", "source_candidate_id")
    op.drop_column("analysis_candidate_versions", "structuring_job_id")
    op.drop_index(
        "ix_policy_structuring_jobs_household_document",
        table_name="policy_structuring_jobs",
    )
    op.drop_index(
        "ix_policy_structuring_jobs_queue",
        table_name="policy_structuring_jobs",
    )
    op.drop_table("policy_structuring_jobs")
