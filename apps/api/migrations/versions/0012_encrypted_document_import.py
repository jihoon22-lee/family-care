"""Create encrypted document batch and managed archive metadata."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0012_encrypted_document_import"
down_revision: str | Sequence[str] | None = "0011_local_authentication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BATCH_STATES = "'created', 'running', 'partial', 'succeeded', 'failed', 'cancelled'"
_ITEM_STATES = (
    "'queued', 'running', 'succeeded', 'password_required', "
    "'retryable_failed', 'permanently_failed', 'cancelled'"
)
_TERMINAL_ITEM_STATES = "'succeeded', 'permanently_failed', 'cancelled'"


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


def _timestamp(
    name: str,
    *,
    default: bool = False,
    nullable: bool = False,
) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("CURRENT_TIMESTAMP") if default else None,
    )


def upgrade() -> None:
    """Create metadata-only batch and archive tables."""

    op.create_table(
        "document_batches",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("created_by", "app_users.id"),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'created'"),
        ),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        _timestamp("completed_at", nullable=True),
        sa.CheckConstraint(
            f"state IN ({_BATCH_STATES})",
            name="ck_document_batches_state",
        ),
        sa.CheckConstraint(
            "(state IN ('succeeded', 'failed', 'cancelled') AND completed_at IS NOT NULL) "
            "OR (state IN ('created', 'running', 'partial') AND completed_at IS NULL)",
            name="ck_document_batches_completion",
        ),
    )
    op.create_index(
        "ix_document_batches_household_created",
        "document_batches",
        ["household_space_id", "created_at", "id"],
    )
    op.create_index(
        "ix_document_batches_member_created",
        "document_batches",
        ["family_member_id", "created_at", "id"],
    )

    op.create_table(
        "document_batch_items",
        _uuid("id", primary_key=True),
        _foreign_uuid("batch_id", "document_batches.id", ondelete="CASCADE"),
        _foreign_uuid("document_id", "documents.id", nullable=True),
        sa.Column("source_id", sa.CHAR(length=64), nullable=False),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("display_label", sa.String(length=160), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        _timestamp("available_at", default=True),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        _timestamp("lease_expires_at", nullable=True),
        _timestamp("heartbeat_at", nullable=True),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        _timestamp("completed_at", nullable=True),
        sa.UniqueConstraint(
            "batch_id",
            "source_id",
            name="uq_document_batch_items_batch_source",
        ),
        sa.CheckConstraint(
            "source_id ~ '^[a-f0-9]{64}$'",
            name="ck_document_batch_items_source_id",
        ),
        sa.CheckConstraint(
            "btrim(source_key) <> ''",
            name="ck_document_batch_items_source_key_nonempty",
        ),
        sa.CheckConstraint(
            "source_key !~ '(^/|(^|/)\\.\\.(/|$))'",
            name="ck_document_batch_items_source_key_relative",
        ),
        sa.CheckConstraint(
            "btrim(display_label) <> ''",
            name="ck_document_batch_items_display_label",
        ),
        sa.CheckConstraint(
            f"state IN ({_ITEM_STATES})",
            name="ck_document_batch_items_state",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_document_batch_items_error_code",
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name="ck_document_batch_items_attempts",
        ),
        sa.CheckConstraint(
            "max_attempts >= 1 AND max_attempts <= 20",
            name="ck_document_batch_items_max_attempts",
        ),
        sa.CheckConstraint(
            "(state = 'running' AND lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL "
            "AND heartbeat_at IS NOT NULL) OR (state <> 'running' AND lease_owner IS NULL "
            "AND lease_expires_at IS NULL AND heartbeat_at IS NULL)",
            name="ck_document_batch_items_lease",
        ),
        sa.CheckConstraint(
            f"(state IN ({_TERMINAL_ITEM_STATES}) AND completed_at IS NOT NULL) OR "
            f"(state NOT IN ({_TERMINAL_ITEM_STATES}) AND completed_at IS NULL)",
            name="ck_document_batch_items_completion",
        ),
    )
    op.create_index(
        "ix_document_batch_items_batch_state",
        "document_batch_items",
        ["batch_id", "state", "created_at", "id"],
    )
    op.create_index(
        "ix_document_batch_items_claim",
        "document_batch_items",
        ["state", "available_at", "created_at", "id"],
    )
    op.create_index(
        "ix_document_batch_items_lease_expiry",
        "document_batch_items",
        ["state", "lease_expires_at", "id"],
    )
    op.create_index(
        "ix_document_batch_items_document",
        "document_batch_items",
        ["document_id", "id"],
    )

    op.create_table(
        "managed_archives",
        _uuid("id", primary_key=True),
        _foreign_uuid("document_version_id", "document_versions.id"),
        sa.Column("object_key", sa.String(length=64), nullable=False),
        sa.Column("scheme", sa.String(length=64), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.LargeBinary(length=12), nullable=False),
        sa.Column("wrapped_data_key", sa.LargeBinary(length=40), nullable=False),
        sa.Column("ciphertext_size", sa.BigInteger(), nullable=False),
        sa.Column("auth_tag", sa.LargeBinary(length=16), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("retired_at", nullable=True),
        sa.UniqueConstraint("object_key", name="uq_managed_archives_object_key"),
        sa.CheckConstraint(
            "object_key ~ '^[a-f0-9]{32}$'",
            name="ck_managed_archives_object_key",
        ),
        sa.CheckConstraint(
            "scheme = 'aes-256-gcm+aes-kw-v1'",
            name="ck_managed_archives_scheme",
        ),
        sa.CheckConstraint(
            "key_version ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_managed_archives_key_version",
        ),
        sa.CheckConstraint(
            "octet_length(nonce) = 12",
            name="ck_managed_archives_nonce",
        ),
        sa.CheckConstraint(
            "octet_length(wrapped_data_key) = 40",
            name="ck_managed_archives_wrapped_data_key",
        ),
        sa.CheckConstraint(
            "ciphertext_size >= 0",
            name="ck_managed_archives_ciphertext_size",
        ),
        sa.CheckConstraint(
            "ciphertext_size <= 67108864",
            name="ck_managed_archives_ciphertext_size_limit",
        ),
        sa.CheckConstraint(
            "octet_length(auth_tag) = 16",
            name="ck_managed_archives_auth_tag",
        ),
        sa.CheckConstraint(
            "retired_at IS NULL OR retired_at >= created_at",
            name="ck_managed_archives_retired_at",
        ),
    )
    op.create_index(
        "uq_managed_archives_active_document_version",
        "managed_archives",
        ["document_version_id"],
        unique=True,
        postgresql_where=sa.text("retired_at IS NULL"),
    )


def downgrade() -> None:
    """Remove only encrypted import and managed archive metadata."""

    op.drop_index(
        "uq_managed_archives_active_document_version",
        table_name="managed_archives",
    )
    op.drop_table("managed_archives")
    op.drop_index("ix_document_batch_items_document", table_name="document_batch_items")
    op.drop_index("ix_document_batch_items_lease_expiry", table_name="document_batch_items")
    op.drop_index("ix_document_batch_items_claim", table_name="document_batch_items")
    op.drop_index("ix_document_batch_items_batch_state", table_name="document_batch_items")
    op.drop_table("document_batch_items")
    op.drop_index("ix_document_batches_member_created", table_name="document_batches")
    op.drop_index("ix_document_batches_household_created", table_name="document_batches")
    op.drop_table("document_batches")
