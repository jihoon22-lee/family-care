"""Add append-only insurance ledger reconciliation histories."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0024_insurance_reconciliation"
down_revision: str | Sequence[str] | None = "0023_advisory_disposition"
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


def _foreign_uuid(name: str, target: str, *, nullable: bool = False) -> sa.Column[Any]:
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


def _sha256(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.CHAR(length=64), nullable=False)


def _current_history_check(prefix: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "((is_current = true AND superseded_at IS NULL) OR "
        "(is_current = false AND superseded_at IS NOT NULL))",
        name=f"ck_{prefix}_current_state",
    )


def _install_history_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_insurance_reconciliation_history()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'insurance reconciliation history is append-only'
              USING ERRCODE = '55000';
          END IF;
          IF OLD.is_current = true AND NEW.is_current = false
             AND OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL
             AND (to_jsonb(OLD) - 'is_current' - 'superseded_at') =
                 (to_jsonb(NEW) - 'is_current' - 'superseded_at') THEN
            RETURN NEW;
          END IF;
          RAISE EXCEPTION 'insurance reconciliation history is append-only'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name, trigger_name in (
        (
            "private_knowledge_operational_links",
            "trg_pk_operational_links_history",
        ),
        (
            "document_batch_item_resolutions",
            "trg_document_resolutions_history",
        ),
    ):
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION enforce_insurance_reconciliation_history()
            """
        )


def upgrade() -> None:
    """Create additive operational-link and unreadable-resolution histories."""

    op.create_table(
        "private_knowledge_operational_links",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("knowledge_contract_id", "private_knowledge_contracts.id"),
        _foreign_uuid("policy_contract_id", "policy_contracts.id", nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "link_conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("authority", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        _foreign_uuid("confirmed_by", "app_users.id"),
        _timestamp("confirmed_at"),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        _timestamp("superseded_at", nullable=True),
        _sha256("link_digest_sha256"),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["knowledge_contract_id", "import_run_id"],
            ["private_knowledge_contracts.id", "private_knowledge_contracts.import_run_id"],
            name="fk_pk_operational_links_contract_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id", "household_space_id"],
            [
                "private_knowledge_import_runs.id",
                "private_knowledge_import_runs.household_space_id",
            ],
            name="fk_pk_operational_links_run_household",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["family_member_id", "household_space_id"],
            ["family_members.id", "family_members.household_space_id"],
            name="fk_pk_operational_links_member_household",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["policy_contract_id", "household_space_id"],
            ["policy_contracts.id", "policy_contracts.household_space_id"],
            name="fk_pk_operational_links_policy_household",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by", "household_space_id"],
            ["app_users.id", "app_users.household_space_id"],
            name="fk_pk_operational_links_actor_household",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')",
            name="ck_pk_operational_links_decision",
        ),
        sa.CheckConstraint(
            "authority = 'USER_CONFIRMED_OPERATIONAL_IDENTITY'",
            name="ck_pk_operational_links_authority",
        ),
        sa.CheckConstraint(
            "((decision = 'MATCH' AND policy_contract_id IS NOT NULL "
            "AND link_conflict = false) OR "
            "(decision <> 'MATCH' AND policy_contract_id IS NULL))",
            name="ck_pk_operational_links_policy_shape",
        ),
        sa.CheckConstraint(
            "(link_conflict = false OR (link_conflict = true "
            "AND decision = 'UNKNOWN' AND policy_contract_id IS NULL))",
            name="ck_pk_operational_links_conflict_shape",
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_pk_operational_links_reason",
        ),
        _current_history_check("pk_operational_links"),
        sa.CheckConstraint(
            "link_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_pk_operational_links_digest",
        ),
    )
    op.create_index(
        "uq_pk_operational_links_digest",
        "private_knowledge_operational_links",
        ["import_run_id", "link_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_pk_operational_links_current_contract",
        "private_knowledge_operational_links",
        ["knowledge_contract_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "uq_pk_operational_links_current_policy",
        "private_knowledge_operational_links",
        ["policy_contract_id"],
        unique=True,
        postgresql_where=sa.text("is_current AND decision = 'MATCH'"),
    )
    op.create_index(
        "ix_pk_operational_links_member",
        "private_knowledge_operational_links",
        ["household_space_id", "family_member_id", "import_run_id", "knowledge_contract_id"],
    )

    op.create_table(
        "document_batch_item_resolutions",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("failed_item_id", "document_batch_items.id"),
        _foreign_uuid("replacement_item_id", "document_batch_items.id", nullable=True),
        sa.Column("resolution", sa.String(length=16), nullable=False),
        sa.Column("authority", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        _foreign_uuid("confirmed_by", "app_users.id"),
        _timestamp("confirmed_at"),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        _timestamp("superseded_at", nullable=True),
        _sha256("resolution_digest_sha256"),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["family_member_id", "household_space_id"],
            ["family_members.id", "family_members.household_space_id"],
            name="fk_document_resolutions_member_household",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by", "household_space_id"],
            ["app_users.id", "app_users.household_space_id"],
            name="fk_document_resolutions_actor_household",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "resolution IN ('REPLACED', 'DISMISSED', 'REOPENED')",
            name="ck_document_resolutions_value",
        ),
        sa.CheckConstraint(
            "authority = 'USER_CONFIRMED_DOCUMENT_RESOLUTION'",
            name="ck_document_resolutions_authority",
        ),
        sa.CheckConstraint(
            "((resolution = 'REPLACED' AND replacement_item_id IS NOT NULL) OR "
            "(resolution IN ('DISMISSED', 'REOPENED') AND replacement_item_id IS NULL))",
            name="ck_document_resolutions_replacement_shape",
        ),
        sa.CheckConstraint(
            "failed_item_id <> replacement_item_id",
            name="ck_document_resolutions_distinct_items",
        ),
        sa.CheckConstraint(
            "reason_code ~ '^[A-Z][A-Z0-9_]{0,63}$'",
            name="ck_document_resolutions_reason",
        ),
        _current_history_check("document_resolutions"),
        sa.CheckConstraint(
            "resolution_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_resolutions_digest",
        ),
    )
    op.create_index(
        "uq_document_resolutions_digest",
        "document_batch_item_resolutions",
        ["failed_item_id", "resolution_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_document_resolutions_current",
        "document_batch_item_resolutions",
        ["failed_item_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_document_resolutions_member",
        "document_batch_item_resolutions",
        ["household_space_id", "family_member_id", "failed_item_id"],
    )
    _install_history_guards()


def downgrade() -> None:
    """Remove only the additive reconciliation histories."""

    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM private_knowledge_operational_links)
             OR EXISTS (SELECT 1 FROM document_batch_item_resolutions) THEN
            RAISE EXCEPTION 'cannot downgrade insurance reconciliation with history';
          END IF;
        END;
        $$
        """
    )
    op.execute("DROP TRIGGER trg_document_resolutions_history ON document_batch_item_resolutions")
    op.execute(
        "DROP TRIGGER trg_pk_operational_links_history ON private_knowledge_operational_links"
    )
    op.execute("DROP FUNCTION enforce_insurance_reconciliation_history()")
    op.drop_index(
        "ix_document_resolutions_member",
        table_name="document_batch_item_resolutions",
    )
    op.drop_index(
        "uq_document_resolutions_current",
        table_name="document_batch_item_resolutions",
    )
    op.drop_index(
        "uq_document_resolutions_digest",
        table_name="document_batch_item_resolutions",
    )
    op.drop_table("document_batch_item_resolutions")
    op.drop_index(
        "ix_pk_operational_links_member",
        table_name="private_knowledge_operational_links",
    )
    op.drop_index(
        "uq_pk_operational_links_current_policy",
        table_name="private_knowledge_operational_links",
    )
    op.drop_index(
        "uq_pk_operational_links_current_contract",
        table_name="private_knowledge_operational_links",
    )
    op.drop_index(
        "uq_pk_operational_links_digest",
        table_name="private_knowledge_operational_links",
    )
    op.drop_table("private_knowledge_operational_links")
