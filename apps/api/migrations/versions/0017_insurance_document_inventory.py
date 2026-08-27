"""Create reviewed insurance document components, sets, and inventory links."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0017_insurance_inventory"
down_revision: str | Sequence[str] | None = "0016_policy_structuring_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLES = "'policy', 'terms', 'product_explanation', 'application', 'supporting'"
_REVIEW_STATES = "'SUGGESTED', 'USER_CONFIRMED', 'CONFLICT', 'REJECTED'"
_DOCUMENT_KINDS = (
    "'policy', 'terms', 'product_explanation', 'application', 'amendment', 'claim', 'supporting'"
)


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
) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete="RESTRICT"),
        nullable=nullable,
    )


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.text("CURRENT_TIMESTAMP"),
    )


def _version() -> sa.Column[Any]:
    return sa.Column(
        "version",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )


def upgrade() -> None:
    """Add bounded source kinds and metadata-only reviewed inventory records."""

    op.drop_constraint("ck_documents_document_kind", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_document_kind",
        "documents",
        f"document_kind IN ({_DOCUMENT_KINDS})",
    )
    op.drop_constraint(
        "ck_document_batch_items_document_kind",
        "document_batch_items",
        type_="check",
    )
    op.alter_column(
        "document_batch_items",
        "document_kind",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_document_batch_items_document_kind",
        "document_batch_items",
        f"document_kind IN ({_ROLES})",
    )
    op.add_column(
        "document_batch_items",
        _foreign_uuid(
            "processed_document_version_id",
            "document_versions.id",
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE document_batch_items AS item
        SET processed_document_version_id = job.document_version_id
        FROM policy_structuring_jobs AS job
        WHERE job.batch_item_id = item.id
          AND item.processed_document_version_id IS NULL
        """
    )
    op.execute(
        """
        UPDATE document_batch_items AS item
        SET processed_document_version_id = (
            SELECT version.id
            FROM document_versions AS version
            JOIN managed_archives AS archive
              ON archive.document_version_id = version.id
            WHERE version.document_id = item.document_id
              AND archive.created_at <= item.completed_at
              AND (
                  archive.retired_at IS NULL
                  OR archive.retired_at >= item.completed_at
              )
            ORDER BY archive.created_at DESC, version.version_number DESC, version.id DESC
            LIMIT 1
        )
        WHERE item.state = 'succeeded'
          AND item.processed_document_version_id IS NULL
          AND item.document_id IS NOT NULL
        """
    )

    op.create_table(
        "insurance_document_components",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("document_batch_item_id", "document_batch_items.id"),
        _foreign_uuid("document_version_id", "document_versions.id"),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        _foreign_uuid("evidence_id", "evidence.id", nullable=True),
        sa.Column(
            "review_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'SUGGESTED'"),
        ),
        _foreign_uuid("created_by", "app_users.id"),
        _version(),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("deleted_at", nullable=True),
        sa.CheckConstraint(f"role IN ({_ROLES})", name="ck_insurance_components_role"),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_insurance_components_page_range",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES})",
            name="ck_insurance_components_review_state",
        ),
        sa.CheckConstraint("version >= 1", name="ck_insurance_components_version"),
    )
    op.create_index(
        "ix_insurance_document_components_member",
        "insurance_document_components",
        ["household_space_id", "family_member_id", "id"],
        unique=False,
    )
    op.create_index(
        "ix_insurance_document_components_batch_item",
        "insurance_document_components",
        ["document_batch_item_id", "id"],
        unique=False,
    )
    op.create_index(
        "uq_insurance_document_components_active_identity",
        "insurance_document_components",
        [
            "household_space_id",
            "family_member_id",
            "document_version_id",
            "page_start",
            "page_end",
            "role",
        ],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "insurance_document_sets",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("policy_contract_id", "policy_contracts.id", nullable=True),
        sa.Column("insurer_display", sa.String(length=160), nullable=True),
        sa.Column("product_display", sa.String(length=200), nullable=True),
        sa.Column("display_label", sa.String(length=200), nullable=False),
        _foreign_uuid("created_by", "app_users.id"),
        _version(),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("deleted_at", nullable=True),
        sa.CheckConstraint(
            "insurer_display IS NULL OR btrim(insurer_display) <> ''",
            name="ck_insurance_document_sets_insurer_display",
        ),
        sa.CheckConstraint(
            "product_display IS NULL OR btrim(product_display) <> ''",
            name="ck_insurance_document_sets_product_display",
        ),
        sa.CheckConstraint(
            "btrim(display_label) <> ''",
            name="ck_insurance_document_sets_display_label",
        ),
        sa.CheckConstraint("version >= 1", name="ck_insurance_document_sets_version"),
    )
    op.create_index(
        "ix_insurance_document_sets_member",
        "insurance_document_sets",
        ["household_space_id", "family_member_id", "id"],
        unique=False,
    )
    op.create_index(
        "uq_insurance_document_sets_active_policy",
        "insurance_document_sets",
        ["household_space_id", "family_member_id", "policy_contract_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL AND policy_contract_id IS NOT NULL"),
    )

    op.create_table(
        "insurance_document_set_items",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("insurance_document_set_id", "insurance_document_sets.id"),
        _foreign_uuid("policy_contract_id", "policy_contracts.id", nullable=True),
        _foreign_uuid(
            "insurance_document_component_id",
            "insurance_document_components.id",
        ),
        _foreign_uuid("document_batch_item_id", "document_batch_items.id"),
        _foreign_uuid("document_version_id", "document_versions.id"),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "match_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'SUGGESTED'"),
        ),
        _foreign_uuid("evidence_id", "evidence.id", nullable=True),
        _foreign_uuid("confirmed_by", "app_users.id", nullable=True),
        _timestamp("confirmed_at", nullable=True),
        _version(),
        _timestamp("created_at"),
        _timestamp("updated_at"),
        _timestamp("deleted_at", nullable=True),
        sa.CheckConstraint(f"role IN ({_ROLES})", name="ck_insurance_set_items_role"),
        sa.CheckConstraint(
            f"match_state IN ({_REVIEW_STATES})",
            name="ck_insurance_set_items_match_state",
        ),
        sa.CheckConstraint(
            "((match_state = 'USER_CONFIRMED' AND confirmed_by IS NOT NULL "
            "AND confirmed_at IS NOT NULL) OR (match_state <> 'USER_CONFIRMED' "
            "AND ((confirmed_by IS NULL AND confirmed_at IS NULL) OR "
            "(confirmed_by IS NOT NULL AND confirmed_at IS NOT NULL))))",
            name="ck_insurance_set_items_confirmation",
        ),
        sa.CheckConstraint("version >= 1", name="ck_insurance_set_items_version"),
    )
    op.create_index(
        "ix_insurance_document_set_items_set",
        "insurance_document_set_items",
        ["household_space_id", "insurance_document_set_id", "id"],
        unique=False,
    )
    op.create_index(
        "uq_insurance_document_set_items_active_link",
        "insurance_document_set_items",
        ["insurance_document_set_id", "insurance_document_component_id"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    """Remove inventory metadata before restoring the previous kind bounds."""

    op.drop_index(
        "uq_insurance_document_set_items_active_link",
        table_name="insurance_document_set_items",
    )
    op.drop_index(
        "ix_insurance_document_set_items_set",
        table_name="insurance_document_set_items",
    )
    op.drop_table("insurance_document_set_items")
    op.drop_index(
        "uq_insurance_document_sets_active_policy",
        table_name="insurance_document_sets",
    )
    op.drop_index(
        "ix_insurance_document_sets_member",
        table_name="insurance_document_sets",
    )
    op.drop_table("insurance_document_sets")
    op.drop_index(
        "uq_insurance_document_components_active_identity",
        table_name="insurance_document_components",
    )
    op.drop_index(
        "ix_insurance_document_components_batch_item",
        table_name="insurance_document_components",
    )
    op.drop_index(
        "ix_insurance_document_components_member",
        table_name="insurance_document_components",
    )
    op.drop_table("insurance_document_components")
    op.drop_column("document_batch_items", "processed_document_version_id")

    op.execute(
        "UPDATE document_batch_items SET document_kind = 'supporting' "
        "WHERE document_kind IN ('product_explanation', 'application')"
    )
    op.execute(
        "UPDATE documents SET document_kind = 'supporting' "
        "WHERE document_kind = 'product_explanation'"
    )
    op.drop_constraint(
        "ck_document_batch_items_document_kind",
        "document_batch_items",
        type_="check",
    )
    op.alter_column(
        "document_batch_items",
        "document_kind",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.create_check_constraint(
        "ck_document_batch_items_document_kind",
        "document_batch_items",
        "document_kind IN ('policy', 'terms', 'supporting')",
    )
    op.drop_constraint("ck_documents_document_kind", "documents", type_="check")
    op.create_check_constraint(
        "ck_documents_document_kind",
        "documents",
        "document_kind IN ('policy', 'terms', 'application', 'amendment', 'claim', 'supporting')",
    )
