"""Create scoped ClaimCase, checklist, status, and history persistence."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_claim_workflow"
down_revision: str | Sequence[str] | None = "0009_event_structuring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLAIM_STATUSES = (
    "'preparing', 'submitted', 'supplementation_requested', 'paid', "
    "'partially_paid', 'denied', 'closed'"
)
_CLAIM_OUTCOMES = "'paid', 'partially_paid', 'denied'"
_CURRENCY_PATTERN = "'^[A-Z]{3}$'"
_REASON_CODE_PATTERN = "'^[A-Z][A-Z0-9_]{0,63}$'"


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


def _version() -> sa.Column[Any]:
    return sa.Column(
        "version",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
    )


def _jsonb(name: str, *, default: str = "'{}'::jsonb") -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text(default),
    )


def upgrade() -> None:
    """Create claim records without storing medical documents or raw notes."""

    op.create_table(
        "claim_cases",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("policy_contract_id", "policy_contracts.id"),
        sa.Column("insurer_key", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("receipt_number", sa.String(length=160), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("paid_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("outcome_reason_code", sa.String(length=64), nullable=True),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"status IN ({_CLAIM_STATUSES})",
            name="ck_claim_cases_status",
        ),
        sa.CheckConstraint(
            "btrim(insurer_key) <> ''",
            name="ck_claim_cases_insurer_key",
        ),
        sa.CheckConstraint(
            "receipt_number IS NULL OR btrim(receipt_number) <> ''",
            name="ck_claim_cases_receipt_number",
        ),
        sa.CheckConstraint(
            "claimed_amount IS NULL OR claimed_amount >= 0",
            name="ck_claim_cases_claimed_amount",
        ),
        sa.CheckConstraint(
            "paid_amount IS NULL OR paid_amount >= 0",
            name="ck_claim_cases_paid_amount",
        ),
        sa.CheckConstraint(
            f"currency IS NULL OR currency ~ {_CURRENCY_PATTERN}",
            name="ck_claim_cases_currency",
        ),
        sa.CheckConstraint(
            f"outcome_reason_code IS NULL OR outcome_reason_code ~ {_REASON_CODE_PATTERN}",
            name="ck_claim_cases_outcome_reason_code",
        ),
        sa.CheckConstraint("version >= 1", name="ck_claim_cases_version"),
    )
    op.create_index(
        "ix_claim_cases_household_active",
        "claim_cases",
        ["household_space_id", "deleted_at", "updated_at", "id"],
    )
    op.create_index(
        "ix_claim_cases_event_active",
        "claim_cases",
        ["medical_event_id", "deleted_at", "id"],
    )

    op.create_table(
        "claim_case_snapshots",
        _uuid("id", primary_key=True),
        _foreign_uuid("claim_case_id", "claim_cases.id"),
        sa.Column("snapshot_version", sa.Integer(), nullable=False),
        _jsonb("candidate_snapshot_json"),
        _jsonb("rule_snapshot_json"),
        _jsonb("policy_snapshot_json"),
        _jsonb("evidence_snapshot_json"),
        _jsonb("calculation_snapshot_json"),
        sa.Column("snapshot_sha256", sa.String(length=64), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "claim_case_id",
            "snapshot_version",
            name="uq_claim_case_snapshots_case_version",
        ),
        sa.CheckConstraint(
            "snapshot_version >= 1",
            name="ck_claim_case_snapshots_version",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(candidate_snapshot_json) = 'object'",
            name="ck_claim_case_snapshots_candidate_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(rule_snapshot_json) = 'object'",
            name="ck_claim_case_snapshots_rule_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(policy_snapshot_json) = 'object'",
            name="ck_claim_case_snapshots_policy_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_snapshot_json) = 'object'",
            name="ck_claim_case_snapshots_evidence_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(calculation_snapshot_json) = 'object'",
            name="ck_claim_case_snapshots_calculation_object",
        ),
        sa.CheckConstraint(
            "snapshot_sha256 ~ '^[a-f0-9]{64}$'",
            name="ck_claim_case_snapshots_sha256",
        ),
    )
    op.create_index(
        "ix_claim_case_snapshots_case_created",
        "claim_case_snapshots",
        ["claim_case_id", "snapshot_version", "created_at", "id"],
    )

    op.create_table(
        "claim_checklist_items",
        _uuid("id", primary_key=True),
        _foreign_uuid("claim_case_id", "claim_cases.id"),
        sa.Column("document_kind", sa.String(length=64), nullable=False),
        sa.Column("requirement_code", sa.String(length=64), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("conditional", sa.Boolean(), nullable=False),
        sa.Column("prepared", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("note_code", sa.String(length=64), nullable=True),
        _foreign_uuid(
            "source_rule_version_id",
            "coverage_rule_versions.id",
            nullable=True,
        ),
        _foreign_uuid("source_evidence_id", "evidence.id", nullable=True),
        _version(),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "btrim(document_kind) <> ''",
            name="ck_claim_checklist_items_document_kind",
        ),
        sa.CheckConstraint(
            "btrim(requirement_code) <> ''",
            name="ck_claim_checklist_items_requirement_code",
        ),
        sa.CheckConstraint(
            f"note_code IS NULL OR note_code ~ {_REASON_CODE_PATTERN}",
            name="ck_claim_checklist_items_note_code",
        ),
        sa.CheckConstraint("version >= 1", name="ck_claim_checklist_items_version"),
    )
    op.create_index(
        "ix_claim_checklist_items_case",
        "claim_checklist_items",
        ["claim_case_id", "version", "id"],
    )

    op.create_table(
        "claim_status_events",
        _uuid("id", primary_key=True),
        _foreign_uuid("claim_case_id", "claim_cases.id"),
        sa.Column("from_status", sa.String(length=32), nullable=True),
        sa.Column("to_status", sa.String(length=32), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        _jsonb("metadata_json"),
        _created_at(),
        sa.CheckConstraint(
            f"from_status IS NULL OR from_status IN ({_CLAIM_STATUSES})",
            name="ck_claim_status_events_from_status",
        ),
        sa.CheckConstraint(
            f"to_status IN ({_CLAIM_STATUSES})",
            name="ck_claim_status_events_to_status",
        ),
        sa.CheckConstraint(
            f"reason_code IS NULL OR reason_code ~ {_REASON_CODE_PATTERN}",
            name="ck_claim_status_events_reason_code",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_json) = 'object'",
            name="ck_claim_status_events_metadata_object",
        ),
    )
    op.create_index(
        "ix_claim_status_events_case_occurred",
        "claim_status_events",
        ["claim_case_id", "occurred_at", "id"],
    )

    op.create_table(
        "claim_history",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        _foreign_uuid("family_member_id", "family_members.id"),
        _foreign_uuid("policy_contract_id", "policy_contracts.id"),
        _foreign_uuid("rider_id", "riders.id", nullable=True),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("counted_occurrence", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            f"outcome IN ({_CLAIM_OUTCOMES})",
            name="ck_claim_history_outcome",
        ),
        sa.CheckConstraint(
            "amount IS NULL OR amount >= 0",
            name="ck_claim_history_amount",
        ),
        sa.CheckConstraint(
            f"currency IS NULL OR currency ~ {_CURRENCY_PATTERN}",
            name="ck_claim_history_currency",
        ),
        sa.CheckConstraint(
            f"reason_code IS NULL OR reason_code ~ {_REASON_CODE_PATTERN}",
            name="ck_claim_history_reason_code",
        ),
    )
    op.create_index(
        "ix_claim_history_household_member",
        "claim_history",
        ["household_space_id", "family_member_id", "created_at", "id"],
    )
    op.create_index(
        "ix_claim_history_rider",
        "claim_history",
        ["rider_id", "created_at", "id"],
    )


def downgrade() -> None:
    """Drop claim projections before their parent ClaimCase rows."""

    op.drop_index("ix_claim_history_rider", table_name="claim_history")
    op.drop_index("ix_claim_history_household_member", table_name="claim_history")
    op.drop_table("claim_history")
    op.drop_index("ix_claim_status_events_case_occurred", table_name="claim_status_events")
    op.drop_table("claim_status_events")
    op.drop_index("ix_claim_checklist_items_case", table_name="claim_checklist_items")
    op.drop_table("claim_checklist_items")
    op.drop_index(
        "ix_claim_case_snapshots_case_created",
        table_name="claim_case_snapshots",
    )
    op.drop_table("claim_case_snapshots")
    op.drop_index("ix_claim_cases_event_active", table_name="claim_cases")
    op.drop_index("ix_claim_cases_household_active", table_name="claim_cases")
    op.drop_table("claim_cases")
