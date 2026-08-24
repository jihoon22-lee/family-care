"""Create the household-scoped policy ledger.

Revision ID: 0003_policy_ledger
Revises: 0002_document_ingestion
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0003_policy_ledger"
down_revision: str | Sequence[str] | None = "0002_document_ingestion"
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


def _version() -> sa.Column[Any]:
    return sa.Column(
        "version",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )


def _household_foreign_key() -> sa.Column[Any]:
    return sa.Column(
        "household_space_id",
        sa.UUID(as_uuid=True),
        sa.ForeignKey("household_spaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _evidence_foreign_key(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
        nullable=nullable,
    )


def upgrade() -> None:
    """Create all and only the Phase 2 policy-ledger tables."""

    op.create_table(
        "household_spaces",
        _uuid("id", primary_key=True),
        sa.Column("space_key", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("space_key", name="uq_household_spaces_space_key"),
        sa.CheckConstraint("space_key <> ''", name="ck_household_spaces_space_key_nonempty"),
        sa.CheckConstraint(
            "display_name <> ''",
            name="ck_household_spaces_display_name_nonempty",
        ),
        sa.CheckConstraint("version >= 1", name="ck_household_spaces_version"),
    )

    op.create_table(
        "family_members",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("internal_alias", sa.String(length=80), nullable=False),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("display_name <> ''", name="ck_family_members_display_name_nonempty"),
        sa.CheckConstraint("internal_alias <> ''", name="ck_family_members_alias_nonempty"),
        sa.CheckConstraint("version >= 1", name="ck_family_members_version"),
    )
    op.create_index(
        "ix_family_members_household_space_id",
        "family_members",
        ["household_space_id", "id"],
        unique=False,
    )
    op.create_index(
        "uq_family_members_active_alias",
        "family_members",
        ["household_space_id", "internal_alias"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "evidence",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "document_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "extraction_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("extractions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("physical_page", sa.Integer(), nullable=False),
        sa.Column("x0", sa.Numeric(12, 4), nullable=True),
        sa.Column("y0", sa.Numeric(12, 4), nullable=True),
        sa.Column("x1", sa.Numeric(12, 4), nullable=True),
        sa.Column("y1", sa.Numeric(12, 4), nullable=True),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        _created_at(),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_evidence_content_sha256",
        ),
        sa.CheckConstraint("physical_page >= 1", name="ck_evidence_physical_page"),
        sa.CheckConstraint(
            "((x0 IS NULL AND y0 IS NULL AND x1 IS NULL AND y1 IS NULL) OR "
            "(x0 IS NOT NULL AND y0 IS NOT NULL AND x1 IS NOT NULL AND y1 IS NOT NULL "
            "AND x0 >= 0 AND y0 >= 0 AND x1 > x0 AND y1 > y0))",
            name="ck_evidence_bbox",
        ),
        sa.CheckConstraint(
            "review_state IN ('AI_VERIFIED', 'NEEDS_REVIEW', 'USER_CONFIRMED')",
            name="ck_evidence_review_state",
        ),
    )
    op.create_index(
        "ix_evidence_household_document",
        "evidence",
        ["household_space_id", "document_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_evidence_extraction_id",
        "evidence",
        ["extraction_id"],
        unique=False,
    )

    op.create_table(
        "policy_contracts",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "source_document_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _evidence_foreign_key("source_evidence_id"),
        sa.Column("insurer_display", sa.String(length=160), nullable=False),
        sa.Column("insurer_key", sa.String(length=160), nullable=False),
        sa.Column("product_display", sa.String(length=200), nullable=False),
        sa.Column("product_key", sa.String(length=200), nullable=False),
        sa.Column("contract_date", sa.Date(), nullable=True),
        sa.Column("coverage_start_date", sa.Date(), nullable=True),
        sa.Column("coverage_end_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        _evidence_foreign_key("status_evidence_id", nullable=True),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("insurer_display <> ''", name="ck_policy_contracts_insurer_display"),
        sa.CheckConstraint("insurer_key <> ''", name="ck_policy_contracts_insurer_key"),
        sa.CheckConstraint("product_display <> ''", name="ck_policy_contracts_product_display"),
        sa.CheckConstraint("product_key <> ''", name="ck_policy_contracts_product_key"),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'expired', 'cancelled', 'unknown')",
            name="ck_policy_contracts_status",
        ),
        sa.CheckConstraint(
            "coverage_start_date IS NULL OR "
            "(coverage_end_date IS NULL OR coverage_end_date >= coverage_start_date)",
            name="ck_policy_contracts_coverage_dates",
        ),
        sa.CheckConstraint("version >= 1", name="ck_policy_contracts_version"),
    )
    op.create_index(
        "ix_policy_contracts_household_active",
        "policy_contracts",
        ["household_space_id", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "policy_parties",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "policy_contract_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("policy_contracts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "family_member_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("family_members.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        _evidence_foreign_key("evidence_id"),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('policyholder', 'primary_insured', 'additional_insured', 'beneficiary')",
            name="ck_policy_parties_role",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from",
            name="ck_policy_parties_effective_dates",
        ),
        sa.CheckConstraint("version >= 1", name="ck_policy_parties_version"),
    )
    op.create_index(
        "ix_policy_parties_household_policy",
        "policy_parties",
        ["household_space_id", "policy_contract_id"],
        unique=False,
    )
    op.create_index(
        "ix_policy_parties_member_id",
        "policy_parties",
        ["family_member_id"],
        unique=False,
    )

    op.create_table(
        "riders",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "policy_contract_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("policy_contracts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _evidence_foreign_key("source_evidence_id"),
        sa.Column("display_name", sa.String(length=240), nullable=False),
        sa.Column("normalized_key", sa.String(length=240), nullable=False),
        sa.Column("benefit_type", sa.String(length=32), nullable=False),
        sa.Column("insured_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("payment_start_date", sa.Date(), nullable=True),
        sa.Column("payment_end_date", sa.Date(), nullable=True),
        sa.Column("coverage_start_date", sa.Date(), nullable=True),
        sa.Column("coverage_end_date", sa.Date(), nullable=True),
        sa.Column("renewable", sa.Boolean(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("status_checked_at", sa.DateTime(timezone=True), nullable=True),
        _evidence_foreign_key("status_evidence_id", nullable=True),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("display_name <> ''", name="ck_riders_display_name"),
        sa.CheckConstraint("normalized_key <> ''", name="ck_riders_normalized_key"),
        sa.CheckConstraint(
            "benefit_type IN ('fixed', 'indemnity')",
            name="ck_riders_benefit_type",
        ),
        sa.CheckConstraint(
            "insured_amount IS NULL OR insured_amount >= 0",
            name="ck_riders_insured_amount",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_riders_currency",
        ),
        sa.CheckConstraint(
            "payment_end_date IS NULL OR payment_start_date IS NULL OR "
            "payment_end_date >= payment_start_date",
            name="ck_riders_payment_dates",
        ),
        sa.CheckConstraint(
            "coverage_start_date IS NULL OR "
            "(coverage_end_date IS NULL OR coverage_end_date >= coverage_start_date)",
            name="ck_riders_coverage_dates",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'expired', 'cancelled', 'unknown')",
            name="ck_riders_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_riders_version"),
    )
    op.create_index(
        "ix_riders_household_policy_active",
        "riders",
        ["household_space_id", "policy_contract_id", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "policy_status_snapshots",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "policy_contract_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("policy_contracts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "rider_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("riders.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        _evidence_foreign_key("evidence_id"),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "((policy_contract_id IS NOT NULL AND rider_id IS NULL) OR "
            "(policy_contract_id IS NULL AND rider_id IS NOT NULL))",
            name="ck_policy_status_snapshots_target",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'expired', 'cancelled', 'unknown')",
            name="ck_policy_status_snapshots_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_policy_status_snapshots_version"),
    )
    op.create_index(
        "ix_policy_status_snapshots_household_policy",
        "policy_status_snapshots",
        ["household_space_id", "policy_contract_id", "rider_id", "effective_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the policy ledger in reverse dependency order."""

    for index_name in (
        "ix_policy_status_snapshots_household_policy",
        "ix_riders_household_policy_active",
        "ix_policy_parties_member_id",
        "ix_policy_parties_household_policy",
        "ix_policy_contracts_household_active",
        "ix_evidence_extraction_id",
        "ix_evidence_household_document",
        "uq_family_members_active_alias",
        "ix_family_members_household_space_id",
    ):
        op.drop_index(index_name)

    for table_name in (
        "policy_status_snapshots",
        "riders",
        "policy_parties",
        "policy_contracts",
        "evidence",
        "family_members",
        "household_spaces",
    ):
        op.drop_table(table_name)
