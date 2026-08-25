"""Create receipt-line and benefit-calculation trace persistence.

Revision ID: 0008_benefit_calculations
Revises: 0007_coverage_decision_engine
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0008_benefit_calculations"
down_revision: str | Sequence[str] | None = "0007_coverage_decision_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_RECEIPT_CATEGORIES = "'outpatient', 'inpatient', 'pharmacy'"
_COVERAGE_CATEGORIES = "'covered', 'possible_excluded', 'excluded', 'unknown'"
_CONFIRMATION_LEVELS = "'user', 'ai_structured', 'unconfirmed'"
_CALCULATION_KINDS = "'fixed', 'indemnity'"
_CALCULATION_STATUSES = "'computed', 'partial', 'unknown'"
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


def _foreign_uuid(name: str, target: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete="RESTRICT"),
        nullable=False,
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


def upgrade() -> None:
    """Create scoped receipt inputs and immutable calculation traces."""

    op.create_table(
        "receipt_lines",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("medical_event_id", "medical_events.id"),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("coverage_category", sa.String(length=32), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("confirmation_level", sa.String(length=32), nullable=False),
        sa.Column("note_code", sa.String(length=64), nullable=True),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"category IN ({_RECEIPT_CATEGORIES})",
            name="ck_receipt_lines_category",
        ),
        sa.CheckConstraint(
            f"coverage_category IN ({_COVERAGE_CATEGORIES})",
            name="ck_receipt_lines_coverage_category",
        ),
        sa.CheckConstraint(
            f"confirmation_level IN ({_CONFIRMATION_LEVELS})",
            name="ck_receipt_lines_confirmation_level",
        ),
        sa.CheckConstraint("amount >= 0", name="ck_receipt_lines_amount"),
        sa.CheckConstraint(
            f"currency ~ {_CURRENCY_PATTERN}",
            name="ck_receipt_lines_currency",
        ),
        sa.CheckConstraint("version >= 1", name="ck_receipt_lines_version"),
        sa.CheckConstraint(
            f"note_code IS NULL OR note_code ~ {_REASON_CODE_PATTERN}",
            name="ck_receipt_lines_note_code",
        ),
    )
    op.create_index(
        "ix_receipt_lines_household_active",
        "receipt_lines",
        ["household_space_id", "deleted_at", "id"],
    )
    op.create_index(
        "ix_receipt_lines_event_active",
        "receipt_lines",
        ["medical_event_id", "deleted_at", "id"],
    )

    op.create_table(
        "benefit_calculations",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("claim_candidate_id", "claim_candidates.id"),
        sa.Column("calculation_kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("confirmed_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("additional_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("excluded_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("deductible_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("applied_rate", sa.Numeric(9, 6), nullable=True),
        sa.Column("applied_limit", sa.Numeric(18, 2), nullable=True),
        sa.Column("rounding_rule", sa.String(length=32), nullable=True),
        sa.Column("hold_reason_code", sa.String(length=64), nullable=True),
        sa.Column(
            "excluded_reason_codes",
            sa.ARRAY(sa.String(length=64)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        _foreign_uuid("rule_version_id", "coverage_rule_versions.id"),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        _version(),
        _created_at(),
        sa.CheckConstraint(
            f"calculation_kind IN ({_CALCULATION_KINDS})",
            name="ck_benefit_calculations_kind",
        ),
        sa.CheckConstraint(
            f"status IN ({_CALCULATION_STATUSES})",
            name="ck_benefit_calculations_status",
        ),
        sa.CheckConstraint(
            f"currency IS NULL OR currency ~ {_CURRENCY_PATTERN}",
            name="ck_benefit_calculations_currency",
        ),
        sa.CheckConstraint(
            "confirmed_amount IS NULL OR confirmed_amount >= 0",
            name="ck_benefit_calculations_confirmed_amount",
        ),
        sa.CheckConstraint(
            "additional_amount IS NULL OR additional_amount >= 0",
            name="ck_benefit_calculations_additional_amount",
        ),
        sa.CheckConstraint(
            "excluded_amount IS NULL OR excluded_amount >= 0",
            name="ck_benefit_calculations_excluded_amount",
        ),
        sa.CheckConstraint(
            "deductible_amount IS NULL OR deductible_amount >= 0",
            name="ck_benefit_calculations_deductible_amount",
        ),
        sa.CheckConstraint(
            "applied_limit IS NULL OR applied_limit >= 0",
            name="ck_benefit_calculations_applied_limit",
        ),
        sa.CheckConstraint(
            "applied_rate IS NULL OR (applied_rate >= 0 AND applied_rate <= 1)",
            name="ck_benefit_calculations_applied_rate",
        ),
        sa.CheckConstraint(
            "rounding_rule IS NULL OR rounding_rule <> ''",
            name="ck_benefit_calculations_rounding_rule",
        ),
        sa.CheckConstraint(
            "hold_reason_code IS NULL OR hold_reason_code <> ''",
            name="ck_benefit_calculations_hold_reason_code",
        ),
        sa.CheckConstraint(
            "cardinality(excluded_reason_codes) <= 16",
            name="ck_benefit_calculations_excluded_reason_count",
        ),
        sa.CheckConstraint(
            "array_position(excluded_reason_codes, NULL) IS NULL AND "
            "(cardinality(excluded_reason_codes) = 0 OR "
            "array_to_string(excluded_reason_codes, ',') ~ "
            "'^[A-Z][A-Z0-9_]{0,63}(,[A-Z][A-Z0-9_]{0,63})*$')",
            name="ck_benefit_calculations_excluded_reason_format",
        ),
        sa.CheckConstraint(
            "engine_version <> ''",
            name="ck_benefit_calculations_engine_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_benefit_calculations_version"),
    )
    op.create_index(
        "ix_benefit_calculations_household_candidate",
        "benefit_calculations",
        ["household_space_id", "claim_candidate_id", "created_at", "id"],
    )
    op.create_index(
        "ix_benefit_calculations_candidate_created",
        "benefit_calculations",
        ["claim_candidate_id", "created_at", "id"],
    )

    op.create_table(
        "benefit_calculation_steps",
        _uuid("id", primary_key=True),
        _foreign_uuid("benefit_calculation_id", "benefit_calculations.id"),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("input_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("input_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("output_amount", sa.Numeric(18, 6), nullable=True),
        sa.Column("output_currency", sa.CHAR(length=3), nullable=True),
        sa.Column("rounding_rule", sa.String(length=32), nullable=True),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.UniqueConstraint(
            "benefit_calculation_id",
            "step_number",
            name="uq_benefit_calculation_steps_calculation_step",
        ),
        sa.CheckConstraint(
            "step_number >= 1",
            name="ck_benefit_calculation_steps_number",
        ),
        sa.CheckConstraint(
            "operation <> ''",
            name="ck_benefit_calculation_steps_operation",
        ),
        sa.CheckConstraint(
            "input_amount IS NULL OR input_amount >= 0",
            name="ck_benefit_calculation_steps_input_amount",
        ),
        sa.CheckConstraint(
            "output_amount IS NULL OR output_amount >= 0",
            name="ck_benefit_calculation_steps_output_amount",
        ),
        sa.CheckConstraint(
            f"input_currency IS NULL OR input_currency ~ {_CURRENCY_PATTERN}",
            name="ck_benefit_calculation_steps_input_currency",
        ),
        sa.CheckConstraint(
            f"output_currency IS NULL OR output_currency ~ {_CURRENCY_PATTERN}",
            name="ck_benefit_calculation_steps_output_currency",
        ),
        sa.CheckConstraint(
            "rounding_rule IS NULL OR rounding_rule <> ''",
            name="ck_benefit_calculation_steps_rounding_rule",
        ),
        sa.CheckConstraint(
            "reason_code <> ''",
            name="ck_benefit_calculation_steps_reason_code",
        ),
    )
    op.create_index(
        "ix_benefit_calculation_steps_calculation",
        "benefit_calculation_steps",
        ["benefit_calculation_id", "step_number"],
    )


def downgrade() -> None:
    """Drop calculation traces before their parent calculation and inputs."""

    op.drop_index(
        "ix_benefit_calculation_steps_calculation",
        table_name="benefit_calculation_steps",
    )
    op.drop_table("benefit_calculation_steps")
    op.drop_index(
        "ix_benefit_calculations_candidate_created",
        table_name="benefit_calculations",
    )
    op.drop_index(
        "ix_benefit_calculations_household_candidate",
        table_name="benefit_calculations",
    )
    op.drop_table("benefit_calculations")
    op.drop_index("ix_receipt_lines_event_active", table_name="receipt_lines")
    op.drop_index("ix_receipt_lines_household_active", table_name="receipt_lines")
    op.drop_table("receipt_lines")
