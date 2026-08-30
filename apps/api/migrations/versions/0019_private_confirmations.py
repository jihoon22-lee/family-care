"""Add append-only current-enrollment confirmations to private knowledge."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0019_private_confirmations"
down_revision: str | Sequence[str] | None = "0018_private_knowledge_catalog"
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


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.text("CURRENT_TIMESTAMP"),
    )


def upgrade() -> None:
    """Store user confirmations without changing certificate evidence."""

    op.create_table(
        "private_knowledge_contract_confirmations",
        _uuid("id", primary_key=True),
        _uuid("import_run_id"),
        _uuid("household_space_id"),
        _uuid("knowledge_contract_id"),
        sa.Column(
            "decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column(
            "confirmed_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        sa.Column("status_as_of", sa.Date(), nullable=False),
        sa.Column("authority", sa.String(length=80), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        _uuid("confirmed_by"),
        _timestamp("confirmed_at"),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        _timestamp("superseded_at", nullable=True),
        sa.Column(
            "confirmation_digest_sha256",
            sa.String(length=64),
            nullable=False,
        ),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["knowledge_contract_id", "import_run_id"],
            [
                "private_knowledge_contracts.id",
                "private_knowledge_contracts.import_run_id",
            ],
            name="fk_private_knowledge_confirmations_contract_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id", "household_space_id"],
            [
                "private_knowledge_import_runs.id",
                "private_knowledge_import_runs.household_space_id",
            ],
            name="fk_private_knowledge_confirmations_run_household",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by", "household_space_id"],
            ["app_users.id", "app_users.household_space_id"],
            name="fk_private_knowledge_confirmations_actor_household",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')",
            name="ck_private_knowledge_confirmations_decision",
        ),
        sa.CheckConstraint(
            "confirmed_status IN ('active', 'inactive', 'lapsed', 'terminated', 'unknown')",
            name="ck_private_knowledge_confirmations_status",
        ),
        sa.CheckConstraint(
            "((decision = 'MATCH' AND confirmed_status <> 'unknown') OR "
            "(decision <> 'MATCH' AND confirmed_status = 'unknown'))",
            name="ck_private_knowledge_confirmations_decision_status",
        ),
        sa.CheckConstraint(
            "authority = 'USER_CONFIRMED_CURRENT_ENROLLMENT'",
            name="ck_private_knowledge_confirmations_authority",
        ),
        sa.CheckConstraint(
            "btrim(reason_code) <> ''",
            name="ck_private_knowledge_confirmations_reason_nonempty",
        ),
        sa.CheckConstraint(
            "((is_current = true AND superseded_at IS NULL) OR "
            "(is_current = false AND superseded_at IS NOT NULL))",
            name="ck_private_knowledge_confirmations_current_state",
        ),
        sa.CheckConstraint(
            "confirmation_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_confirmations_digest",
        ),
        sa.CheckConstraint(
            "status_as_of <= confirmed_at::date",
            name="ck_private_knowledge_confirmations_status_date",
        ),
    )
    op.create_index(
        "uq_private_knowledge_confirmation_digest",
        "private_knowledge_contract_confirmations",
        ["import_run_id", "confirmation_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_confirmation_current",
        "private_knowledge_contract_confirmations",
        ["knowledge_contract_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_private_knowledge_confirmation_household",
        "private_knowledge_contract_confirmations",
        ["household_space_id", "import_run_id", "knowledge_contract_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove the additive confirmation layer."""

    op.drop_index(
        "ix_private_knowledge_confirmation_household",
        table_name="private_knowledge_contract_confirmations",
    )
    op.drop_index(
        "uq_private_knowledge_confirmation_current",
        table_name="private_knowledge_contract_confirmations",
    )
    op.drop_index(
        "uq_private_knowledge_confirmation_digest",
        table_name="private_knowledge_contract_confirmations",
    )
    op.drop_table("private_knowledge_contract_confirmations")
