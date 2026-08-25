"""Create local administrators and hash-only server sessions."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0011_local_authentication"
down_revision: str | Sequence[str] | None = "0010_claim_workflow"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_USERNAME_PATTERN = "'^[a-z0-9][a-z0-9._-]{2,63}$'"
_SHA256_PATTERN = "'^[a-f0-9]{64}$'"


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _timestamp(name: str, *, default: bool = False, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("CURRENT_TIMESTAMP") if default else None,
    )


def upgrade() -> None:
    """Create identity rows without any raw password or session-token column."""

    op.create_table(
        "app_users",
        _uuid("id", primary_key=True),
        sa.Column(
            "household_space_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("household_spaces.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("password_hash", sa.String(length=512), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        _timestamp("created_at", default=True),
        _timestamp("updated_at", default=True),
        _timestamp("deactivated_at", nullable=True),
        sa.UniqueConstraint("username", name="uq_app_users_username"),
        sa.CheckConstraint(
            f"username ~ {_USERNAME_PATTERN}",
            name="ck_app_users_username",
        ),
        sa.CheckConstraint(
            "btrim(display_name) <> ''",
            name="ck_app_users_display_name",
        ),
        sa.CheckConstraint(
            "password_hash LIKE '$argon2id$%'",
            name="ck_app_users_password_hash",
        ),
        sa.CheckConstraint(
            "(is_active AND deactivated_at IS NULL) OR "
            "(NOT is_active AND deactivated_at IS NOT NULL)",
            name="ck_app_users_activation",
        ),
    )
    op.create_index(
        "ix_app_users_household_active",
        "app_users",
        ["household_space_id", "is_active", "id"],
    )

    op.create_table(
        "app_sessions",
        _uuid("id", primary_key=True),
        sa.Column(
            "app_user_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("app_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("csrf_token_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("device_label", sa.String(length=80), nullable=False),
        _timestamp("created_at", default=True),
        _timestamp("last_seen_at"),
        _timestamp("expires_at"),
        _timestamp("absolute_expires_at"),
        _timestamp("reauthenticated_at", nullable=True),
        _timestamp("revoked_at", nullable=True),
        sa.UniqueConstraint("token_hash", name="uq_app_sessions_token_hash"),
        sa.CheckConstraint(
            f"token_hash ~ {_SHA256_PATTERN}",
            name="ck_app_sessions_token_hash",
        ),
        sa.CheckConstraint(
            f"csrf_token_hash ~ {_SHA256_PATTERN}",
            name="ck_app_sessions_csrf_token_hash",
        ),
        sa.CheckConstraint(
            "btrim(device_label) <> ''",
            name="ck_app_sessions_device_label",
        ),
        sa.CheckConstraint(
            "last_seen_at >= created_at AND expires_at >= last_seen_at "
            "AND absolute_expires_at >= expires_at",
            name="ck_app_sessions_expiry_order",
        ),
        sa.CheckConstraint(
            "reauthenticated_at IS NULL OR "
            "(reauthenticated_at >= created_at AND reauthenticated_at <= expires_at)",
            name="ck_app_sessions_reauthenticated_at",
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= created_at",
            name="ck_app_sessions_revoked_at",
        ),
    )
    op.create_index(
        "ix_app_sessions_user_active",
        "app_sessions",
        ["app_user_id", "revoked_at", "expires_at", "id"],
    )


def downgrade() -> None:
    """Remove only the identity boundary."""

    op.drop_index("ix_app_sessions_user_active", table_name="app_sessions")
    op.drop_table("app_sessions")
    op.drop_index("ix_app_users_household_active", table_name="app_users")
    op.drop_table("app_users")
