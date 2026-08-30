"""Add verified private-knowledge rule publication authority."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_private_publications"
down_revision: str | Sequence[str] | None = "0019_private_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIGEST_CHECK = "~ '^[0-9a-f]{64}$'"


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _nullable_uuid(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.UUID(as_uuid=True), nullable=True)


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.text("CURRENT_TIMESTAMP"),
    )


def _jsonb(name: str, *, default: str | None = None) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text(default) if default is not None else None,
    )


def _sha256(name: str) -> sa.Column[Any]:
    return sa.Column(name, sa.String(length=64), nullable=False)


def _digest_check(column: str, prefix: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} {_DIGEST_CHECK}",
        name=f"ck_{prefix}_{column}",
    )


def _run_foreign_key(prefix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["rule_import_run_id", "knowledge_import_run_id", "household_space_id"],
        [
            "private_knowledge_rule_import_runs.id",
            "private_knowledge_rule_import_runs.knowledge_import_run_id",
            "private_knowledge_rule_import_runs.household_space_id",
        ],
        name=f"fk_{prefix}_rule_run",
        ondelete="RESTRICT",
    )


def _coverage_foreign_key(prefix: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["knowledge_coverage_id", "knowledge_import_run_id"],
        [
            "private_knowledge_coverages.id",
            "private_knowledge_coverages.import_run_id",
        ],
        name=f"fk_{prefix}_coverage_run",
        ondelete="RESTRICT",
    )


def _actor_foreign_key(prefix: str, column: str = "reviewed_by") -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column, "household_space_id"],
        ["app_users.id", "app_users.household_space_id"],
        name=f"fk_{prefix}_actor_household",
        ondelete="RESTRICT",
    )


def _review_check(prefix: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        "review_state = 'USER_CONFIRMED'",
        name=f"ck_{prefix}_review_state",
    )


def _nonempty(column: str, prefix: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"btrim({column}) <> ''",
        name=f"ck_{prefix}_{column}_nonempty",
    )


def upgrade() -> None:
    """Create append-only status, rule, calculation, and citation publications."""

    op.create_table(
        "private_knowledge_contract_status_intervals",
        _uuid("id", primary_key=True),
        _uuid("rule_import_run_id"),
        _uuid("import_run_id"),
        _uuid("household_space_id"),
        _uuid("knowledge_contract_id"),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("confirmed_status", sa.String(length=16), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("effective_through", sa.Date(), nullable=False),
        sa.Column("authority", sa.String(length=64), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        _uuid("confirmed_by"),
        _timestamp("confirmed_at"),
        _sha256("interval_digest_sha256"),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["knowledge_contract_id", "import_run_id"],
            [
                "private_knowledge_contracts.id",
                "private_knowledge_contracts.import_run_id",
            ],
            name="fk_private_knowledge_status_intervals_contract_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_run_id", "household_space_id"],
            [
                "private_knowledge_import_runs.id",
                "private_knowledge_import_runs.household_space_id",
            ],
            name="fk_private_knowledge_status_intervals_run_household",
            ondelete="RESTRICT",
        ),
        _actor_foreign_key(
            "private_knowledge_status_intervals",
            column="confirmed_by",
        ),
        sa.CheckConstraint(
            "decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')",
            name="ck_private_knowledge_status_intervals_decision",
        ),
        sa.CheckConstraint(
            "confirmed_status IN ('active', 'inactive', 'lapsed', 'terminated', 'unknown')",
            name="ck_private_knowledge_status_intervals_status",
        ),
        sa.CheckConstraint(
            "((decision = 'MATCH' AND confirmed_status <> 'unknown') OR "
            "(decision <> 'MATCH' AND confirmed_status = 'unknown'))",
            name="ck_private_knowledge_status_intervals_decision_status",
        ),
        sa.CheckConstraint(
            "effective_through >= effective_from",
            name="ck_private_knowledge_status_intervals_dates",
        ),
        sa.CheckConstraint(
            "authority IN ('USER_CONFIRMED_EVENT_DATE', 'REVIEWED_STATUS_DOCUMENT')",
            name="ck_private_knowledge_status_intervals_authority",
        ),
        _review_check("private_knowledge_status_intervals"),
        _nonempty("reason_code", "private_knowledge_status_intervals"),
        _digest_check(
            "interval_digest_sha256",
            "private_knowledge_status_intervals",
        ),
    )
    op.create_index(
        "uq_private_knowledge_status_intervals_digest",
        "private_knowledge_contract_status_intervals",
        ["rule_import_run_id", "interval_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_status_intervals_contract_dates",
        "private_knowledge_contract_status_intervals",
        ["knowledge_contract_id", "effective_from", "effective_through", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_rule_import_runs",
        _uuid("id", primary_key=True),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        sa.Column("package_schema_version", sa.String(length=80), nullable=False),
        _sha256("package_digest_sha256"),
        _sha256("manifest_digest_sha256"),
        _sha256("baseline_digest_sha256"),
        _sha256("report_digest_sha256"),
        _sha256("projection_digest_sha256"),
        sa.Column("publisher_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        _jsonb("entity_counts_json", default="'{}'::jsonb"),
        _jsonb("disposition_counts_json", default="'{}'::jsonb"),
        _uuid("reviewed_by"),
        _timestamp("reviewed_at"),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _timestamp("superseded_at", nullable=True),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "knowledge_import_run_id",
            "household_space_id",
            name="uq_private_knowledge_rule_runs_identity",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_import_run_id", "household_space_id"],
            [
                "private_knowledge_import_runs.id",
                "private_knowledge_import_runs.household_space_id",
            ],
            name="fk_private_knowledge_rule_runs_knowledge_household",
            ondelete="RESTRICT",
        ),
        _actor_foreign_key("private_knowledge_rule_runs"),
        sa.CheckConstraint(
            "package_schema_version = 'private-knowledge-rule-publication.sol-v1'",
            name="ck_private_knowledge_rule_runs_schema",
        ),
        sa.CheckConstraint(
            "state IN ('VALIDATED', 'APPLIED', 'SUPERSEDED', 'REJECTED')",
            name="ck_private_knowledge_rule_runs_state",
        ),
        _review_check("private_knowledge_rule_runs"),
        sa.CheckConstraint(
            "jsonb_typeof(entity_counts_json) = 'object'",
            name="ck_private_knowledge_rule_runs_entity_counts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(disposition_counts_json) = 'object'",
            name="ck_private_knowledge_rule_runs_disposition_counts_object",
        ),
        sa.CheckConstraint(
            "((is_current = true AND state = 'APPLIED' AND superseded_at IS NULL) OR "
            "(is_current = false AND state <> 'APPLIED') OR "
            "(is_current = false AND state = 'APPLIED'))",
            name="ck_private_knowledge_rule_runs_current_state",
        ),
        sa.CheckConstraint(
            "((state = 'SUPERSEDED' AND is_current = false "
            "AND superseded_at IS NOT NULL) OR state <> 'SUPERSEDED')",
            name="ck_private_knowledge_rule_runs_superseded_state",
        ),
        _nonempty("publisher_version", "private_knowledge_rule_runs"),
        *(
            _digest_check(column, "private_knowledge_rule_runs")
            for column in (
                "package_digest_sha256",
                "manifest_digest_sha256",
                "baseline_digest_sha256",
                "report_digest_sha256",
                "projection_digest_sha256",
            )
        ),
    )
    op.create_index(
        "uq_private_knowledge_rule_runs_package",
        "private_knowledge_rule_import_runs",
        ["household_space_id", "package_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_rule_runs_current",
        "private_knowledge_rule_import_runs",
        ["household_space_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_private_knowledge_rule_runs_household_created",
        "private_knowledge_rule_import_runs",
        ["household_space_id", "created_at", "id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_private_knowledge_status_intervals_rule_run",
        "private_knowledge_contract_status_intervals",
        "private_knowledge_rule_import_runs",
        ["rule_import_run_id", "import_run_id", "household_space_id"],
        ["id", "knowledge_import_run_id", "household_space_id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "private_knowledge_coverage_execution_dispositions",
        _uuid("id", primary_key=True),
        _uuid("rule_import_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        _uuid("knowledge_coverage_id"),
        sa.Column("disposition", sa.String(length=24), nullable=False),
        _jsonb("reason_codes_json", default="'[]'::jsonb"),
        _timestamp("created_at"),
        _run_foreign_key("private_knowledge_dispositions"),
        _coverage_foreign_key("private_knowledge_dispositions"),
        sa.CheckConstraint(
            "disposition IN ('PUBLISHED', 'BLOCKED', 'NOT_APPLICABLE')",
            name="ck_private_knowledge_dispositions_value",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes_json) = 'array'",
            name="ck_private_knowledge_dispositions_reasons_array",
        ),
    )
    op.create_index(
        "uq_private_knowledge_dispositions_coverage",
        "private_knowledge_coverage_execution_dispositions",
        ["rule_import_run_id", "knowledge_coverage_id"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_dispositions_run_value",
        "private_knowledge_coverage_execution_dispositions",
        ["rule_import_run_id", "disposition", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_fact_normalizer_publications",
        _uuid("id", primary_key=True),
        _uuid("rule_import_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        sa.Column("field_path", sa.String(length=160), nullable=False),
        _jsonb("normalized_tokens_json"),
        _jsonb("normalized_value_json"),
        sa.Column("match_kind", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        _uuid("reviewed_by"),
        _timestamp("reviewed_at"),
        _sha256("normalizer_digest_sha256"),
        _timestamp("created_at"),
        _run_foreign_key("private_knowledge_fact_normalizers"),
        _actor_foreign_key("private_knowledge_fact_normalizers"),
        sa.CheckConstraint(
            "match_kind = 'EXACT_TOKEN_SEQUENCE'",
            name="ck_private_knowledge_fact_normalizers_match_kind",
        ),
        _review_check("private_knowledge_fact_normalizers"),
        sa.CheckConstraint(
            "jsonb_typeof(normalized_tokens_json) = 'array' "
            "AND jsonb_array_length(normalized_tokens_json) BETWEEN 1 AND 32",
            name="ck_private_knowledge_fact_normalizers_tokens_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(normalized_value_json) IN ('string', 'boolean', 'number')",
            name="ck_private_knowledge_fact_normalizers_value_scalar",
        ),
        sa.CheckConstraint(
            "priority BETWEEN 0 AND 1000",
            name="ck_private_knowledge_fact_normalizers_priority",
        ),
        _nonempty("field_path", "private_knowledge_fact_normalizers"),
        _digest_check(
            "normalizer_digest_sha256",
            "private_knowledge_fact_normalizers",
        ),
    )
    op.create_index(
        "uq_private_knowledge_fact_normalizers_key",
        "private_knowledge_fact_normalizer_publications",
        ["rule_import_run_id", "field_path", "normalizer_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_fact_normalizers_run_field",
        "private_knowledge_fact_normalizer_publications",
        ["rule_import_run_id", "field_path", "priority", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_rule_publications",
        _uuid("id", primary_key=True),
        _uuid("rule_import_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        _uuid("knowledge_coverage_id"),
        sa.Column("rule_key", sa.String(length=160), nullable=False),
        sa.Column("rule_kind", sa.String(length=32), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        _jsonb("rule_json"),
        sa.Column("result_reason_code", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        _uuid("reviewed_by"),
        _timestamp("reviewed_at"),
        _sha256("rule_digest_sha256"),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
            name="uq_private_knowledge_rule_publications_identity",
        ),
        _run_foreign_key("private_knowledge_rule_publications"),
        _coverage_foreign_key("private_knowledge_rule_publications"),
        _actor_foreign_key("private_knowledge_rule_publications"),
        sa.CheckConstraint(
            "rule_kind IN ('eligibility', 'classification', 'temporal', 'exclusion', "
            "'frequency', 'fixed_amount', 'rate_amount', 'indemnity_eligibility', "
            "'deductible', 'limit', 'required_document')",
            name="ck_private_knowledge_rule_publications_kind",
        ),
        _review_check("private_knowledge_rule_publications"),
        sa.CheckConstraint(
            "jsonb_typeof(rule_json) = 'object'",
            name="ck_private_knowledge_rule_publications_rule_object",
        ),
        _nonempty("rule_key", "private_knowledge_rule_publications"),
        _nonempty("schema_version", "private_knowledge_rule_publications"),
        _nonempty("result_reason_code", "pk_rule_publications"),
        _digest_check("rule_digest_sha256", "private_knowledge_rule_publications"),
    )
    op.create_index(
        "uq_private_knowledge_rule_publications_key",
        "private_knowledge_rule_publications",
        ["rule_import_run_id", "knowledge_coverage_id", "rule_key"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_rule_publications_digest",
        "private_knowledge_rule_publications",
        ["rule_import_run_id", "rule_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_rule_publications_coverage",
        "private_knowledge_rule_publications",
        ["knowledge_coverage_id", "rule_import_run_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_rule_citations",
        _uuid("id", primary_key=True),
        _uuid("rule_publication_id"),
        _uuid("rule_import_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        _uuid("terms_section_id"),
        _nullable_uuid("source_clause_id"),
        _nullable_uuid("fact_id"),
        sa.Column("citation_key", sa.String(length=160), nullable=False),
        sa.Column("evidence_purpose", sa.String(length=24), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        _sha256("source_text_sha256"),
        _sha256("citation_digest_sha256"),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            [
                "rule_publication_id",
                "rule_import_run_id",
                "knowledge_import_run_id",
                "household_space_id",
            ],
            [
                "private_knowledge_rule_publications.id",
                "private_knowledge_rule_publications.rule_import_run_id",
                "private_knowledge_rule_publications.knowledge_import_run_id",
                "private_knowledge_rule_publications.household_space_id",
            ],
            name="fk_private_knowledge_rule_citations_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terms_section_id", "knowledge_import_run_id"],
            [
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ],
            name="fk_private_knowledge_rule_citations_section_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_clause_id", "knowledge_import_run_id"],
            [
                "private_knowledge_source_clauses.id",
                "private_knowledge_source_clauses.import_run_id",
            ],
            name="fk_private_knowledge_rule_citations_clause_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fact_id", "knowledge_import_run_id"],
            [
                "private_knowledge_facts.id",
                "private_knowledge_facts.import_run_id",
            ],
            name="fk_private_knowledge_rule_citations_fact_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "evidence_purpose IN ('ELIGIBILITY', 'DEFINITION', 'EXCLUSION', "
            "'WAITING', 'REDUCTION', 'FREQUENCY', 'AMOUNT', 'RENEWAL', "
            "'INDEMNITY', 'LIMIT', 'DEDUCTIBLE')",
            name="ck_private_knowledge_rule_citations_purpose",
        ),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_private_knowledge_rule_citations_pages",
        ),
        _digest_check(
            "source_text_sha256",
            "private_knowledge_rule_citations",
        ),
        _nonempty("citation_key", "private_knowledge_rule_citations"),
        _digest_check("citation_digest_sha256", "private_knowledge_rule_citations"),
    )
    op.create_index(
        "uq_private_knowledge_rule_citations_key",
        "private_knowledge_rule_citations",
        ["rule_import_run_id", "citation_key"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_rule_citations_digest",
        "private_knowledge_rule_citations",
        ["rule_publication_id", "citation_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_rule_citations_publication",
        "private_knowledge_rule_citations",
        ["rule_publication_id", "page_start", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_calculation_publications",
        _uuid("id", primary_key=True),
        _uuid("rule_import_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        _uuid("knowledge_coverage_id"),
        sa.Column("calculation_key", sa.String(length=160), nullable=False),
        sa.Column("calculation_kind", sa.String(length=24), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        _jsonb("calculation_json"),
        sa.Column("result_reason_code", sa.String(length=64), nullable=False),
        sa.Column("review_state", sa.String(length=32), nullable=False),
        _uuid("reviewed_by"),
        _timestamp("reviewed_at"),
        _sha256("calculation_digest_sha256"),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
            name="uq_private_knowledge_calculation_publications_identity",
        ),
        _run_foreign_key("private_knowledge_calculation_publications"),
        _coverage_foreign_key("private_knowledge_calculation_publications"),
        _actor_foreign_key("private_knowledge_calculation_publications"),
        sa.CheckConstraint(
            "calculation_kind IN ('FIXED', 'INDEMNITY', 'NONE', 'UNKNOWN')",
            name="ck_private_knowledge_calculation_publications_kind",
        ),
        _review_check("private_knowledge_calculation_publications"),
        sa.CheckConstraint(
            "jsonb_typeof(calculation_json) = 'object'",
            name="ck_private_knowledge_calculation_publications_object",
        ),
        _nonempty("calculation_key", "pk_calc_publications"),
        _nonempty("schema_version", "pk_calc_publications"),
        _nonempty("result_reason_code", "pk_calc_publications"),
        _digest_check(
            "calculation_digest_sha256",
            "pk_calc_publications",
        ),
    )
    op.create_index(
        "uq_private_knowledge_calculation_publications_key",
        "private_knowledge_calculation_publications",
        ["rule_import_run_id", "knowledge_coverage_id", "calculation_key"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_calculation_publications_digest",
        "private_knowledge_calculation_publications",
        ["rule_import_run_id", "calculation_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_calculation_publications_coverage",
        "private_knowledge_calculation_publications",
        ["knowledge_coverage_id", "rule_import_run_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_calculation_citations",
        _uuid("id", primary_key=True),
        _uuid("calculation_publication_id"),
        _uuid("rule_import_run_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("household_space_id"),
        _uuid("terms_section_id"),
        _nullable_uuid("source_clause_id"),
        _nullable_uuid("fact_id"),
        sa.Column("citation_key", sa.String(length=160), nullable=False),
        sa.Column("evidence_purpose", sa.String(length=24), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        _sha256("source_text_sha256"),
        _sha256("citation_digest_sha256"),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            [
                "calculation_publication_id",
                "rule_import_run_id",
                "knowledge_import_run_id",
                "household_space_id",
            ],
            [
                "private_knowledge_calculation_publications.id",
                "private_knowledge_calculation_publications.rule_import_run_id",
                "private_knowledge_calculation_publications.knowledge_import_run_id",
                "private_knowledge_calculation_publications.household_space_id",
            ],
            name="fk_private_knowledge_calculation_citations_publication",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terms_section_id", "knowledge_import_run_id"],
            [
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ],
            name="fk_private_knowledge_calculation_citations_section_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_clause_id", "knowledge_import_run_id"],
            [
                "private_knowledge_source_clauses.id",
                "private_knowledge_source_clauses.import_run_id",
            ],
            name="fk_private_knowledge_calculation_citations_clause_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fact_id", "knowledge_import_run_id"],
            [
                "private_knowledge_facts.id",
                "private_knowledge_facts.import_run_id",
            ],
            name="fk_private_knowledge_calculation_citations_fact_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "evidence_purpose IN ('ELIGIBILITY', 'DEFINITION', 'EXCLUSION', "
            "'WAITING', 'REDUCTION', 'FREQUENCY', 'AMOUNT', 'RENEWAL', "
            "'INDEMNITY', 'LIMIT', 'DEDUCTIBLE')",
            name="ck_private_knowledge_calculation_citations_purpose",
        ),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_private_knowledge_calculation_citations_pages",
        ),
        _digest_check(
            "source_text_sha256",
            "pk_calc_citations",
        ),
        _nonempty("citation_key", "pk_calc_citations"),
        _digest_check(
            "citation_digest_sha256",
            "pk_calc_citations",
        ),
    )
    op.create_index(
        "uq_private_knowledge_calculation_citations_key",
        "private_knowledge_calculation_citations",
        ["rule_import_run_id", "citation_key"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_calculation_citations_digest",
        "private_knowledge_calculation_citations",
        ["calculation_publication_id", "citation_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_calculation_citations_publication",
        "private_knowledge_calculation_citations",
        ["calculation_publication_id", "page_start", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove only the additive private publication layer."""

    op.drop_constraint(
        "fk_private_knowledge_status_intervals_rule_run",
        "private_knowledge_contract_status_intervals",
        type_="foreignkey",
    )
    indexes = (
        (
            "ix_private_knowledge_calculation_citations_publication",
            "private_knowledge_calculation_citations",
        ),
        (
            "uq_private_knowledge_calculation_citations_key",
            "private_knowledge_calculation_citations",
        ),
        (
            "uq_private_knowledge_calculation_citations_digest",
            "private_knowledge_calculation_citations",
        ),
        (
            "ix_private_knowledge_calculation_publications_coverage",
            "private_knowledge_calculation_publications",
        ),
        (
            "uq_private_knowledge_calculation_publications_digest",
            "private_knowledge_calculation_publications",
        ),
        (
            "uq_private_knowledge_calculation_publications_key",
            "private_knowledge_calculation_publications",
        ),
        (
            "ix_private_knowledge_rule_citations_publication",
            "private_knowledge_rule_citations",
        ),
        (
            "uq_private_knowledge_rule_citations_key",
            "private_knowledge_rule_citations",
        ),
        (
            "uq_private_knowledge_rule_citations_digest",
            "private_knowledge_rule_citations",
        ),
        (
            "ix_private_knowledge_rule_publications_coverage",
            "private_knowledge_rule_publications",
        ),
        (
            "uq_private_knowledge_rule_publications_digest",
            "private_knowledge_rule_publications",
        ),
        (
            "uq_private_knowledge_rule_publications_key",
            "private_knowledge_rule_publications",
        ),
        (
            "ix_private_knowledge_fact_normalizers_run_field",
            "private_knowledge_fact_normalizer_publications",
        ),
        (
            "uq_private_knowledge_fact_normalizers_key",
            "private_knowledge_fact_normalizer_publications",
        ),
        (
            "ix_private_knowledge_dispositions_run_value",
            "private_knowledge_coverage_execution_dispositions",
        ),
        (
            "uq_private_knowledge_dispositions_coverage",
            "private_knowledge_coverage_execution_dispositions",
        ),
        (
            "ix_private_knowledge_rule_runs_household_created",
            "private_knowledge_rule_import_runs",
        ),
        ("uq_private_knowledge_rule_runs_current", "private_knowledge_rule_import_runs"),
        ("uq_private_knowledge_rule_runs_package", "private_knowledge_rule_import_runs"),
        (
            "ix_private_knowledge_status_intervals_contract_dates",
            "private_knowledge_contract_status_intervals",
        ),
        (
            "uq_private_knowledge_status_intervals_digest",
            "private_knowledge_contract_status_intervals",
        ),
    )
    for index_name, table_name in indexes:
        op.drop_index(index_name, table_name=table_name)
    for table_name in (
        "private_knowledge_calculation_citations",
        "private_knowledge_calculation_publications",
        "private_knowledge_rule_citations",
        "private_knowledge_rule_publications",
        "private_knowledge_fact_normalizer_publications",
        "private_knowledge_coverage_execution_dispositions",
        "private_knowledge_rule_import_runs",
        "private_knowledge_contract_status_intervals",
    ):
        op.drop_table(table_name)
