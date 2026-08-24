"""Create versioned policy-candidate review persistence.

Revision ID: 0004_policy_candidate_review
Revises: 0003_policy_ledger
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_policy_candidate_review"
down_revision: str | Sequence[str] | None = "0003_policy_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'AI_VERIFIED', 'NEEDS_REVIEW', 'USER_CONFIRMED', 'rejected'"
_KINDS = "'policy_contract', 'policy_party', 'rider'"
_FIELD_IDS = (
    "'insurer', 'product_name', 'contract_start', 'contract_end', 'policy_status', "
    "'rider_name', 'rider_key', 'benefit_type', 'sum_assured', 'currency', "
    "'coverage_start', 'coverage_end', 'renewable', 'rider_status'"
)


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
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
    """Create immutable candidate versions and their bounded field Evidence."""

    op.create_table(
        "analysis_candidate_versions",
        _uuid("id", primary_key=True),
        _uuid("review_item_id"),
        sa.Column(
            "household_space_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("household_spaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_kind", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "parent_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("analysis_candidate_versions.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("generator_version", sa.String(length=128), nullable=False),
        sa.Column("verifier_version", sa.String(length=128), nullable=False),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("rejection_reason", sa.String(length=48), nullable=True),
        sa.Column(
            "issues",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("actor_id", sa.UUID(as_uuid=True), nullable=True),
        _created_at(),
        _updated_at(),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "review_item_id",
            "version",
            name="uq_candidate_versions_review_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_candidate_versions_version"),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})",
            name="ck_candidate_versions_status",
        ),
        sa.CheckConstraint(
            f"candidate_kind IN ({_KINDS})",
            name="ck_candidate_versions_kind",
        ),
        sa.CheckConstraint("schema_version <> ''", name="ck_candidate_versions_schema"),
        sa.CheckConstraint("generator_version <> ''", name="ck_candidate_versions_generator"),
        sa.CheckConstraint("verifier_version <> ''", name="ck_candidate_versions_verifier"),
        sa.CheckConstraint(
            "jsonb_typeof(issues) = 'array'",
            name="ck_candidate_versions_issues_array",
        ),
        sa.CheckConstraint(
            "rejection_reason IS NULL OR rejection_reason IN ("
            "'NOT_ENROLLED', 'TERMS_ONLY_RIDER', 'DUPLICATE_CANDIDATE', "
            "'INVALID_EVIDENCE', 'UNSUPPORTED_STRUCTURE')",
            name="ck_candidate_versions_rejection_reason",
        ),
    )
    op.create_index(
        "uq_candidate_versions_current_review_item",
        "analysis_candidate_versions",
        ["review_item_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_candidate_versions_household_status",
        "analysis_candidate_versions",
        ["household_space_id", "status", "created_at", "id"],
        unique=False,
        postgresql_where=sa.text("is_current AND deleted_at IS NULL"),
    )
    op.create_index(
        "ix_candidate_versions_household_aggregate",
        "analysis_candidate_versions",
        ["household_space_id", "aggregate_id", "candidate_kind", "version"],
        unique=False,
    )

    op.create_table(
        "analysis_candidate_fields",
        _uuid("id", primary_key=True),
        sa.Column(
            "candidate_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("analysis_candidate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_id", sa.String(length=48), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "candidate_version_id",
            "field_id",
            name="uq_candidate_fields_version_field",
        ),
        sa.UniqueConstraint(
            "candidate_version_id",
            "position",
            name="uq_candidate_fields_version_position",
        ),
        sa.CheckConstraint(
            f"field_id IN ({_FIELD_IDS})",
            name="ck_candidate_fields_field_id",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(value) IN ('string', 'number', 'boolean', 'null')",
            name="ck_candidate_fields_scalar_value",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 15",
            name="ck_candidate_fields_position",
        ),
    )
    op.create_index(
        "ix_candidate_fields_candidate_version_id",
        "analysis_candidate_fields",
        ["candidate_version_id", "field_id"],
        unique=False,
    )

    op.create_table(
        "analysis_candidate_evidence",
        _uuid("id", primary_key=True),
        sa.Column(
            "candidate_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("analysis_candidate_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("field_id", sa.String(length=48), nullable=False),
        sa.Column(
            "document_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("physical_page", sa.Integer(), nullable=False),
        sa.Column("bounded_excerpt", sa.String(length=240), nullable=False),
        sa.Column("x0", sa.Numeric(12, 4), nullable=True),
        sa.Column("y0", sa.Numeric(12, 4), nullable=True),
        sa.Column("x1", sa.Numeric(12, 4), nullable=True),
        sa.Column("y1", sa.Numeric(12, 4), nullable=True),
        sa.UniqueConstraint(
            "candidate_version_id",
            "field_id",
            "evidence_id",
            name="uq_candidate_evidence_version_field_evidence",
        ),
        sa.CheckConstraint(
            f"field_id IN ({_FIELD_IDS})",
            name="ck_candidate_evidence_field_id",
        ),
        sa.CheckConstraint(
            "physical_page >= 1 AND physical_page <= 500",
            name="ck_candidate_evidence_physical_page",
        ),
        sa.CheckConstraint(
            "bounded_excerpt <> '' AND char_length(bounded_excerpt) <= 240",
            name="ck_candidate_evidence_excerpt",
        ),
        sa.CheckConstraint(
            "((x0 IS NULL AND y0 IS NULL AND x1 IS NULL AND y1 IS NULL) OR "
            "(x0 IS NOT NULL AND y0 IS NOT NULL AND x1 IS NOT NULL AND y1 IS NOT NULL "
            "AND x0 >= 0 AND y0 >= 0 AND x1 > x0 AND y1 > y0))",
            name="ck_candidate_evidence_bbox",
        ),
    )
    op.create_index(
        "ix_candidate_evidence_candidate_version_id",
        "analysis_candidate_evidence",
        ["candidate_version_id", "field_id"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_evidence_evidence_id",
        "analysis_candidate_evidence",
        ["evidence_id", "document_version_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop candidate objects in reverse dependency order."""

    op.drop_index("ix_candidate_evidence_evidence_id", table_name="analysis_candidate_evidence")
    op.drop_index(
        "ix_candidate_evidence_candidate_version_id",
        table_name="analysis_candidate_evidence",
    )
    op.drop_table("analysis_candidate_evidence")
    op.drop_index(
        "ix_candidate_fields_candidate_version_id", table_name="analysis_candidate_fields"
    )
    op.drop_table("analysis_candidate_fields")
    op.drop_index(
        "ix_candidate_versions_household_aggregate",
        table_name="analysis_candidate_versions",
    )
    op.drop_index(
        "ix_candidate_versions_household_status",
        table_name="analysis_candidate_versions",
    )
    op.drop_index(
        "uq_candidate_versions_current_review_item",
        table_name="analysis_candidate_versions",
    )
    op.drop_table("analysis_candidate_versions")
