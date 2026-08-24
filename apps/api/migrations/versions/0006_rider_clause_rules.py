"""Create Rider-Clause links and versioned data-only CoverageRules.

Revision ID: 0006_rider_clause_rules
Revises: 0005_clause_search
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_rider_clause_rules"
down_revision: str | Sequence[str] | None = "0005_clause_search"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_LINK_REVIEW_STATES = "'AI_VERIFIED', 'NEEDS_REVIEW', 'USER_CONFIRMED', 'rejected'"
_RULE_STATUSES = "'generated', 'published', 'rejected'"
_RULE_REVIEW_STATES = "'AI_VERIFIED', 'NEEDS_REVIEW', 'USER_CONFIRMED'"
_CANDIDATE_KINDS_V1 = "'policy_contract', 'policy_party', 'rider'"
_CANDIDATE_KINDS_V2 = "'policy_contract', 'policy_party', 'rider', 'rider_clause', 'coverage_rule'"
_CANDIDATE_FIELD_IDS_V1 = (
    "'insurer', 'product_name', 'contract_start', 'contract_end', 'policy_status', "
    "'rider_name', 'rider_key', 'benefit_type', 'sum_assured', 'currency', "
    "'coverage_start', 'coverage_end', 'renewable', 'rider_status'"
)
_CANDIDATE_FIELD_IDS_V2 = (
    _CANDIDATE_FIELD_IDS_V1 + ", 'rider_id', 'terms_edition_id', 'clause_id', 'link_review_state', "
    "'rule_kind', 'rule_operator', 'fact_field', 'unit', 'decimal_boundary', "
    "'date_boundary', 'required'"
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
    """Add the reviewable link graph and immutable rule-version boundary."""

    op.drop_constraint(
        "ck_candidate_versions_kind",
        "analysis_candidate_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_candidate_versions_kind",
        "analysis_candidate_versions",
        f"candidate_kind IN ({_CANDIDATE_KINDS_V2})",
    )
    op.drop_constraint(
        "ck_candidate_fields_field_id",
        "analysis_candidate_fields",
        type_="check",
    )
    op.create_check_constraint(
        "ck_candidate_fields_field_id",
        "analysis_candidate_fields",
        f"field_id IN ({_CANDIDATE_FIELD_IDS_V2})",
    )

    op.create_table(
        "rider_clause_links",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("rider_id", "riders.id"),
        _foreign_uuid("terms_edition_id", "terms_editions.id"),
        _foreign_uuid("clause_id", "clauses.id"),
        _foreign_uuid("candidate_version_id", "analysis_candidate_versions.id"),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column("applicability_reason_code", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"review_state IN ({_LINK_REVIEW_STATES})",
            name="ck_rider_clause_links_review_state",
        ),
        sa.CheckConstraint(
            "applicability_reason_code <> ''",
            name="ck_rider_clause_links_applicability_reason_code_nonempty",
        ),
        sa.CheckConstraint("version >= 1", name="ck_rider_clause_links_version"),
    )
    op.create_index(
        "ix_rider_clause_links_household_active",
        "rider_clause_links",
        ["household_space_id", "deleted_at", "id"],
    )
    op.create_index(
        "ix_rider_clause_links_rider_state",
        "rider_clause_links",
        ["rider_id", "review_state", "version"],
    )
    op.create_index(
        "ix_rider_clause_links_terms_clause",
        "rider_clause_links",
        ["terms_edition_id", "clause_id", "id"],
    )
    op.create_index(
        "ix_rider_clause_links_candidate",
        "rider_clause_links",
        ["candidate_version_id", "id"],
    )

    op.create_table(
        "rider_clause_link_evidence",
        _foreign_uuid("rider_clause_link_id", "rider_clause_links.id"),
        _foreign_uuid("evidence_id", "evidence.id"),
        sa.PrimaryKeyConstraint(
            "rider_clause_link_id",
            "evidence_id",
            name="pk_rider_clause_link_evidence",
        ),
    )
    op.create_index(
        "ix_rider_clause_link_evidence_evidence",
        "rider_clause_link_evidence",
        ["evidence_id", "rider_clause_link_id"],
    )

    op.create_table(
        "coverage_rules",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        _foreign_uuid("rider_clause_link_id", "rider_clause_links.id"),
        sa.Column("rule_key", sa.String(length=160), nullable=False),
        sa.Column(
            "current_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'generated'"),
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("rule_key <> ''", name="ck_coverage_rules_rule_key_nonempty"),
        sa.CheckConstraint(
            f"current_status IN ({_RULE_STATUSES})",
            name="ck_coverage_rules_current_status",
        ),
        sa.CheckConstraint("version >= 1", name="ck_coverage_rules_version"),
    )
    op.create_index(
        "ix_coverage_rules_household_active",
        "coverage_rules",
        ["household_space_id", "deleted_at", "id"],
    )
    op.create_index(
        "ix_coverage_rules_link_status",
        "coverage_rules",
        ["rider_clause_link_id", "current_status", "version"],
    )
    op.create_index(
        "uq_coverage_rules_active_link_key",
        "coverage_rules",
        ["rider_clause_link_id", "rule_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "coverage_rule_versions",
        _uuid("id", primary_key=True),
        _foreign_uuid("coverage_rule_id", "coverage_rules.id"),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("schema_version", sa.String(length=32), nullable=False),
        sa.Column("rule_kind", sa.String(length=48), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column(
            "input_field_paths",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "expression_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("result_reason_code", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        sa.Column(
            "executable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("generator_version", sa.String(length=64), nullable=False),
        sa.Column("verifier_version", sa.String(length=64), nullable=False),
        _created_at(),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "coverage_rule_id",
            "version_number",
            name="uq_coverage_rule_versions_rule_version",
        ),
        sa.CheckConstraint(
            "version_number >= 1",
            name="ck_coverage_rule_versions_version_number",
        ),
        sa.CheckConstraint(
            "schema_version = 'coverage-rule-v1'",
            name="ck_coverage_rule_versions_schema_version",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_RULE_REVIEW_STATES})",
            name="ck_coverage_rule_versions_review_state",
        ),
        sa.CheckConstraint(
            "NOT executable OR review_state IN ('AI_VERIFIED', 'USER_CONFIRMED')",
            name="ck_coverage_rule_versions_executable_state",
        ),
        sa.CheckConstraint(
            "NOT executable OR published_at IS NOT NULL",
            name="ck_coverage_rule_versions_executable_published",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_field_paths) = 'array'",
            name="ck_coverage_rule_versions_input_fields_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(expression_json) = 'object'",
            name="ck_coverage_rule_versions_expression_object",
        ),
        sa.CheckConstraint(
            "result_reason_code <> ''",
            name="ck_coverage_rule_versions_reason_code_nonempty",
        ),
        sa.CheckConstraint(
            "generator_version <> ''",
            name="ck_coverage_rule_versions_generator_version_nonempty",
        ),
        sa.CheckConstraint(
            "verifier_version <> ''",
            name="ck_coverage_rule_versions_verifier_version_nonempty",
        ),
    )
    op.create_index(
        "ix_coverage_rule_versions_rule_version",
        "coverage_rule_versions",
        ["coverage_rule_id", "version_number"],
    )
    op.create_index(
        "ix_coverage_rule_versions_review_executable",
        "coverage_rule_versions",
        ["review_state", "executable", "published_at", "id"],
    )

    op.create_table(
        "coverage_rule_evidence",
        _foreign_uuid("coverage_rule_version_id", "coverage_rule_versions.id"),
        _foreign_uuid("evidence_id", "evidence.id"),
        sa.PrimaryKeyConstraint(
            "coverage_rule_version_id",
            "evidence_id",
            name="pk_coverage_rule_evidence",
        ),
    )
    op.create_index(
        "ix_coverage_rule_evidence_evidence",
        "coverage_rule_evidence",
        ["evidence_id", "coverage_rule_version_id"],
    )


def downgrade() -> None:
    """Remove rule persistence without changing the source ledgers."""

    op.drop_index("ix_coverage_rule_evidence_evidence", table_name="coverage_rule_evidence")
    op.drop_table("coverage_rule_evidence")
    op.drop_index(
        "ix_coverage_rule_versions_review_executable",
        table_name="coverage_rule_versions",
    )
    op.drop_index(
        "ix_coverage_rule_versions_rule_version",
        table_name="coverage_rule_versions",
    )
    op.drop_table("coverage_rule_versions")
    op.drop_index("uq_coverage_rules_active_link_key", table_name="coverage_rules")
    op.drop_index("ix_coverage_rules_link_status", table_name="coverage_rules")
    op.drop_index("ix_coverage_rules_household_active", table_name="coverage_rules")
    op.drop_table("coverage_rules")
    op.drop_index(
        "ix_rider_clause_link_evidence_evidence",
        table_name="rider_clause_link_evidence",
    )
    op.drop_table("rider_clause_link_evidence")
    op.drop_index("ix_rider_clause_links_candidate", table_name="rider_clause_links")
    op.drop_index("ix_rider_clause_links_terms_clause", table_name="rider_clause_links")
    op.drop_index("ix_rider_clause_links_rider_state", table_name="rider_clause_links")
    op.drop_index("ix_rider_clause_links_household_active", table_name="rider_clause_links")
    op.drop_table("rider_clause_links")
    op.drop_constraint(
        "ck_candidate_fields_field_id",
        "analysis_candidate_fields",
        type_="check",
    )
    op.create_check_constraint(
        "ck_candidate_fields_field_id",
        "analysis_candidate_fields",
        f"field_id IN ({_CANDIDATE_FIELD_IDS_V1})",
    )
    op.drop_constraint(
        "ck_candidate_versions_kind",
        "analysis_candidate_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_candidate_versions_kind",
        "analysis_candidate_versions",
        f"candidate_kind IN ({_CANDIDATE_KINDS_V1})",
    )
