"""Create immutable private insurance knowledge snapshots."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018_private_knowledge_catalog"
down_revision: str | Sequence[str] | None = "0017_insurance_inventory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRI_STATES = "'MATCH', 'NO_MATCH', 'UNKNOWN'"
_CURRENT_STATUSES = "'active', 'inactive', 'lapsed', 'terminated', 'unknown'"
_REVIEW_STATES = "'DIRECT_REVIEWED', 'NEEDS_REVIEW', 'USER_CONFIRMED', 'UNKNOWN'"


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


def _jsonb(name: str, *, default: str | None = None) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
        server_default=sa.text(default) if default is not None else None,
    )


def _sha256(name: str, *, nullable: bool = False) -> sa.Column[Any]:
    return sa.Column(name, sa.String(length=64), nullable=nullable)


def _source_record_columns() -> tuple[sa.Column[Any], sa.Column[Any]]:
    return (
        _jsonb("source_record_json"),
        _sha256("source_record_digest_sha256"),
    )


def _source_record_checks(prefix: str) -> tuple[sa.CheckConstraint, sa.CheckConstraint]:
    return (
        sa.CheckConstraint(
            "jsonb_typeof(source_record_json) = 'object'",
            name=f"ck_{prefix}_source_record_object",
        ),
        sa.CheckConstraint(
            "source_record_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name=f"ck_{prefix}_source_record_digest",
        ),
    )


def _tri_state(column: str, prefix: str) -> sa.CheckConstraint:
    return sa.CheckConstraint(
        f"{column} IN ({_TRI_STATES})",
        name=f"ck_{prefix}_{column}",
    )


def upgrade() -> None:
    """Add the private knowledge catalog without changing the operational ledger."""

    op.create_table(
        "private_knowledge_import_runs",
        _uuid("id", primary_key=True),
        _foreign_uuid("household_space_id", "household_spaces.id"),
        sa.Column("package_schema_version", sa.String(length=64), nullable=False),
        _sha256("package_digest_sha256"),
        _sha256("manifest_digest_sha256"),
        sa.Column("importer_version", sa.String(length=64), nullable=False),
        sa.Column("analysis_authority", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "is_current",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        _jsonb("manifest_counts_json", default="'{}'::jsonb"),
        _jsonb("reconciliation_counts_json", default="'{}'::jsonb"),
        _sha256("baseline_digest_sha256", nullable=True),
        _sha256("report_digest_sha256", nullable=True),
        _foreign_uuid("applied_by", "app_users.id", nullable=True),
        _timestamp("validated_at"),
        _timestamp("applied_at", nullable=True),
        _timestamp("superseded_at", nullable=True),
        _timestamp("created_at"),
        sa.CheckConstraint(
            "package_schema_version = 'private-analysis-package.sol-v2'",
            name="ck_private_knowledge_runs_schema_version",
        ),
        sa.CheckConstraint(
            "package_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_runs_package_digest",
        ),
        sa.CheckConstraint(
            "manifest_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_runs_manifest_digest",
        ),
        sa.CheckConstraint(
            "baseline_digest_sha256 IS NULL OR baseline_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_runs_baseline_digest",
        ),
        sa.CheckConstraint(
            "report_digest_sha256 IS NULL OR report_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_runs_report_digest",
        ),
        sa.CheckConstraint(
            "state IN ('VALIDATED', 'APPLIED', 'SUPERSEDED', 'REJECTED')",
            name="ck_private_knowledge_runs_state",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(manifest_counts_json) = 'object'",
            name="ck_private_knowledge_runs_manifest_counts_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reconciliation_counts_json) = 'object'",
            name="ck_private_knowledge_runs_reconciliation_counts_object",
        ),
        sa.CheckConstraint(
            "((is_current = true AND state = 'APPLIED' AND applied_by IS NOT NULL "
            "AND applied_at IS NOT NULL AND superseded_at IS NULL) OR is_current = false)",
            name="ck_private_knowledge_runs_current_state",
        ),
        sa.CheckConstraint(
            "((state = 'SUPERSEDED' AND applied_by IS NOT NULL AND applied_at IS NOT NULL "
            "AND superseded_at IS NOT NULL AND is_current = false) OR "
            "state <> 'SUPERSEDED')",
            name="ck_private_knowledge_runs_superseded_state",
        ),
        sa.CheckConstraint(
            "btrim(importer_version) <> '' AND btrim(analysis_authority) <> ''",
            name="ck_private_knowledge_runs_metadata_nonempty",
        ),
    )
    op.create_index(
        "uq_private_knowledge_runs_package",
        "private_knowledge_import_runs",
        ["household_space_id", "package_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_runs_current",
        "private_knowledge_import_runs",
        ["household_space_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_index(
        "ix_private_knowledge_runs_household_created",
        "private_knowledge_import_runs",
        ["household_space_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_subjects",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        sa.Column("source_subject_key", sa.String(length=160), nullable=False),
        sa.Column("family_alias", sa.String(length=160), nullable=False),
        _sha256("family_alias_digest_sha256"),
        _foreign_uuid("family_member_id", "family_members.id", nullable=True),
        sa.Column(
            "binding_decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column(
            "binding_conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("binding_reason_code", sa.String(length=64), nullable=False),
        _foreign_uuid("binding_confirmed_by", "app_users.id", nullable=True),
        _timestamp("binding_confirmed_at", nullable=True),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_subjects_id_run",
        ),
        _tri_state("binding_decision", "private_knowledge_subjects"),
        sa.CheckConstraint(
            "btrim(source_subject_key) <> '' AND btrim(family_alias) <> ''",
            name="ck_private_knowledge_subjects_identity_nonempty",
        ),
        sa.CheckConstraint(
            "family_alias_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_subjects_alias_digest",
        ),
        sa.CheckConstraint(
            "((binding_decision = 'MATCH' AND family_member_id IS NOT NULL) OR "
            "(binding_decision <> 'MATCH' AND family_member_id IS NULL))",
            name="ck_private_knowledge_subjects_member_binding",
        ),
        sa.CheckConstraint(
            "((binding_confirmed_by IS NULL AND binding_confirmed_at IS NULL) OR "
            "(binding_confirmed_by IS NOT NULL AND binding_confirmed_at IS NOT NULL))",
            name="ck_private_knowledge_subjects_confirmation_pair",
        ),
        *_source_record_checks("private_knowledge_subjects"),
    )
    op.create_index(
        "uq_private_knowledge_subjects_source",
        "private_knowledge_subjects",
        ["import_run_id", "source_subject_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_subjects_member",
        "private_knowledge_subjects",
        ["family_member_id", "import_run_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_contracts",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("subject_id", "private_knowledge_subjects.id"),
        sa.Column("source_contract_key", sa.String(length=200), nullable=False),
        sa.Column("insurer_display", sa.String(length=240), nullable=False),
        sa.Column("product_display", sa.String(length=320), nullable=False),
        sa.Column("contract_start", sa.Date(), nullable=True),
        sa.Column("contract_end", sa.Date(), nullable=True),
        sa.Column(
            "certificate_decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column(
            "current_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        _jsonb("status_candidates_json", default="'[]'::jsonb"),
        _jsonb("certificate_evidence_json", default="'[]'::jsonb"),
        _jsonb("review_issues_json", default="'[]'::jsonb"),
        _foreign_uuid("policy_contract_id", "policy_contracts.id", nullable=True),
        sa.Column(
            "operational_binding_decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column("operational_binding_reason_code", sa.String(length=64), nullable=False),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_contracts_id_run",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "import_run_id"],
            ["private_knowledge_subjects.id", "private_knowledge_subjects.import_run_id"],
            name="fk_private_knowledge_contracts_subject_run",
            ondelete="RESTRICT",
        ),
        _tri_state("certificate_decision", "private_knowledge_contracts"),
        _tri_state("operational_binding_decision", "private_knowledge_contracts"),
        sa.CheckConstraint(
            f"current_status IN ({_CURRENT_STATUSES})",
            name="ck_private_knowledge_contracts_current_status",
        ),
        sa.CheckConstraint(
            "btrim(source_contract_key) <> '' AND btrim(insurer_display) <> '' "
            "AND btrim(product_display) <> ''",
            name="ck_private_knowledge_contracts_identity_nonempty",
        ),
        sa.CheckConstraint(
            "contract_end IS NULL OR contract_start IS NULL OR contract_end >= contract_start",
            name="ck_private_knowledge_contracts_dates",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(status_candidates_json) = 'array'",
            name="ck_private_knowledge_contracts_status_candidates_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(certificate_evidence_json) = 'array'",
            name="ck_private_knowledge_contracts_evidence_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_issues_json) = 'array'",
            name="ck_private_knowledge_contracts_issues_array",
        ),
        sa.CheckConstraint(
            "((operational_binding_decision = 'MATCH' AND policy_contract_id IS NOT NULL) OR "
            "(operational_binding_decision <> 'MATCH' AND policy_contract_id IS NULL))",
            name="ck_private_knowledge_contracts_operational_binding",
        ),
        *_source_record_checks("private_knowledge_contracts"),
    )
    op.create_index(
        "uq_private_knowledge_contracts_source",
        "private_knowledge_contracts",
        ["import_run_id", "source_contract_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_contracts_subject",
        "private_knowledge_contracts",
        ["import_run_id", "subject_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_coverages",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("knowledge_contract_id", "private_knowledge_contracts.id"),
        sa.Column("source_coverage_key", sa.String(length=240), nullable=False),
        sa.Column("display_name", sa.String(length=500), nullable=False),
        sa.Column("component_role", sa.String(length=24), nullable=False),
        sa.Column("component_classification", sa.String(length=48), nullable=False),
        sa.Column("enrollment_decision", sa.String(length=16), nullable=False),
        sa.Column("benefit_type", sa.String(length=24), nullable=False),
        sa.Column("insured_amount", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("coverage_start", sa.Date(), nullable=True),
        sa.Column("coverage_end", sa.Date(), nullable=True),
        sa.Column("renewal_state", sa.String(length=16), nullable=False),
        sa.Column(
            "current_status",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'unknown'"),
        ),
        _jsonb("certificate_evidence_json", default="'[]'::jsonb"),
        _jsonb("review_issues_json", default="'[]'::jsonb"),
        _foreign_uuid("rider_id", "riders.id", nullable=True),
        sa.Column(
            "operational_binding_decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column("operational_binding_reason_code", sa.String(length=64), nullable=False),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_coverages_id_run",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_contract_id", "import_run_id"],
            ["private_knowledge_contracts.id", "private_knowledge_contracts.import_run_id"],
            name="fk_private_knowledge_coverages_contract_run",
            ondelete="RESTRICT",
        ),
        _tri_state("enrollment_decision", "private_knowledge_coverages"),
        _tri_state("operational_binding_decision", "private_knowledge_coverages"),
        sa.CheckConstraint(
            "component_role IN ('MAIN_CONTRACT', 'RIDER')",
            name="ck_private_knowledge_coverages_component_role",
        ),
        sa.CheckConstraint(
            "component_classification IN ('BENEFIT_COVERAGE', 'NON_BENEFIT_CONTRACT_COMPONENT')",
            name="ck_private_knowledge_coverages_classification",
        ),
        sa.CheckConstraint(
            "benefit_type IN ('FIXED', 'INDEMNITY', 'UNKNOWN', 'NOT_APPLICABLE')",
            name="ck_private_knowledge_coverages_benefit_type",
        ),
        sa.CheckConstraint(
            "((component_classification = 'NON_BENEFIT_CONTRACT_COMPONENT' "
            "AND benefit_type = 'NOT_APPLICABLE') OR "
            "(component_classification = 'BENEFIT_COVERAGE' "
            "AND benefit_type <> 'NOT_APPLICABLE'))",
            name="ck_private_knowledge_coverages_classification_benefit",
        ),
        sa.CheckConstraint(
            "renewal_state IN ('YES', 'NO', 'UNKNOWN', 'NOT_APPLICABLE')",
            name="ck_private_knowledge_coverages_renewal_state",
        ),
        sa.CheckConstraint(
            f"current_status IN ({_CURRENT_STATUSES})",
            name="ck_private_knowledge_coverages_current_status",
        ),
        sa.CheckConstraint(
            "btrim(source_coverage_key) <> '' AND btrim(display_name) <> ''",
            name="ck_private_knowledge_coverages_identity_nonempty",
        ),
        sa.CheckConstraint(
            "insured_amount IS NULL OR insured_amount >= 0",
            name="ck_private_knowledge_coverages_amount",
        ),
        sa.CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'",
            name="ck_private_knowledge_coverages_currency",
        ),
        sa.CheckConstraint(
            "coverage_end IS NULL OR coverage_start IS NULL OR coverage_end >= coverage_start",
            name="ck_private_knowledge_coverages_dates",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(certificate_evidence_json) = 'array'",
            name="ck_private_knowledge_coverages_evidence_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(review_issues_json) = 'array'",
            name="ck_private_knowledge_coverages_issues_array",
        ),
        sa.CheckConstraint(
            "((operational_binding_decision = 'MATCH' AND rider_id IS NOT NULL "
            "AND enrollment_decision = 'MATCH' "
            "AND component_classification = 'BENEFIT_COVERAGE') OR "
            "(operational_binding_decision <> 'MATCH' AND rider_id IS NULL))",
            name="ck_private_knowledge_coverages_operational_binding",
        ),
        *_source_record_checks("private_knowledge_coverages"),
    )
    op.create_index(
        "uq_private_knowledge_coverages_source",
        "private_knowledge_coverages",
        ["import_run_id", "source_coverage_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_coverages_contract",
        "private_knowledge_coverages",
        ["import_run_id", "knowledge_contract_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_terms_assignments",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("knowledge_contract_id", "private_knowledge_contracts.id"),
        sa.Column("source_assignment_key", sa.String(length=240), nullable=False),
        sa.Column("document_identity_decision", sa.String(length=16), nullable=False),
        sa.Column("edition_applicability_decision", sa.String(length=16), nullable=False),
        sa.Column("overall_decision", sa.String(length=16), nullable=False),
        _jsonb("reason_codes_json", default="'[]'::jsonb"),
        _foreign_uuid("terms_edition_id", "terms_editions.id", nullable=True),
        sa.Column(
            "operational_binding_decision",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'UNKNOWN'"),
        ),
        sa.Column("operational_binding_reason_code", sa.String(length=64), nullable=False),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_assignments_id_run",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_contract_id", "import_run_id"],
            ["private_knowledge_contracts.id", "private_knowledge_contracts.import_run_id"],
            name="fk_private_knowledge_assignments_contract_run",
            ondelete="RESTRICT",
        ),
        _tri_state("document_identity_decision", "private_knowledge_assignments"),
        _tri_state("edition_applicability_decision", "private_knowledge_assignments"),
        _tri_state("overall_decision", "private_knowledge_assignments"),
        _tri_state("operational_binding_decision", "private_knowledge_assignments"),
        sa.CheckConstraint(
            "btrim(source_assignment_key) <> ''",
            name="ck_private_knowledge_assignments_identity_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes_json) = 'array'",
            name="ck_private_knowledge_assignments_reason_codes_array",
        ),
        sa.CheckConstraint(
            "((operational_binding_decision = 'MATCH' AND terms_edition_id IS NOT NULL "
            "AND document_identity_decision = 'MATCH' "
            "AND edition_applicability_decision = 'MATCH') OR "
            "(operational_binding_decision <> 'MATCH' AND terms_edition_id IS NULL))",
            name="ck_private_knowledge_assignments_operational_binding",
        ),
        *_source_record_checks("private_knowledge_assignments"),
    )
    op.create_index(
        "uq_private_knowledge_assignments_source",
        "private_knowledge_terms_assignments",
        ["import_run_id", "source_assignment_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_assignments_contract",
        "private_knowledge_terms_assignments",
        ["import_run_id", "knowledge_contract_id", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_terms_assignment_sources",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("terms_assignment_id", "private_knowledge_terms_assignments.id"),
        sa.Column("source_alias", sa.String(length=500), nullable=False),
        _sha256("source_alias_digest_sha256"),
        sa.Column("selection_ordinal", sa.Integer(), nullable=False),
        _jsonb("selected_evidence_json", default="'{}'::jsonb"),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["terms_assignment_id", "import_run_id"],
            [
                "private_knowledge_terms_assignments.id",
                "private_knowledge_terms_assignments.import_run_id",
            ],
            name="fk_private_knowledge_assignment_sources_assignment_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "btrim(source_alias) <> ''",
            name="ck_private_knowledge_assignment_sources_alias_nonempty",
        ),
        sa.CheckConstraint(
            "source_alias_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_assignment_sources_alias_digest",
        ),
        sa.CheckConstraint(
            "selection_ordinal >= 1",
            name="ck_private_knowledge_assignment_sources_ordinal",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(selected_evidence_json) = 'object'",
            name="ck_private_knowledge_assignment_sources_evidence_object",
        ),
        *_source_record_checks("private_knowledge_assignment_sources"),
    )
    op.create_index(
        "uq_private_knowledge_assignment_sources_ordinal",
        "private_knowledge_terms_assignment_sources",
        ["import_run_id", "terms_assignment_id", "selection_ordinal"],
        unique=True,
    )
    op.create_index(
        "uq_private_knowledge_assignment_sources_alias",
        "private_knowledge_terms_assignment_sources",
        ["import_run_id", "terms_assignment_id", "source_alias_digest_sha256"],
        unique=True,
    )

    op.create_table(
        "private_knowledge_terms_sections",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        sa.Column("source_section_key", sa.String(length=240), nullable=False),
        sa.Column("terms_source_alias", sa.String(length=500), nullable=False),
        _sha256("terms_source_alias_digest_sha256"),
        sa.Column("section_kind", sa.String(length=64), nullable=False),
        sa.Column("heading", sa.String(length=800), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("review_state", sa.String(length=24), nullable=False),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_sections_id_run",
        ),
        sa.CheckConstraint(
            "btrim(source_section_key) <> '' AND btrim(terms_source_alias) <> '' "
            "AND btrim(section_kind) <> '' AND btrim(heading) <> ''",
            name="ck_private_knowledge_sections_identity_nonempty",
        ),
        sa.CheckConstraint(
            "terms_source_alias_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_sections_alias_digest",
        ),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_private_knowledge_sections_page_range",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES})",
            name="ck_private_knowledge_sections_review_state",
        ),
        *_source_record_checks("private_knowledge_sections"),
    )
    op.create_index(
        "uq_private_knowledge_sections_source",
        "private_knowledge_terms_sections",
        ["import_run_id", "source_section_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_sections_alias",
        "private_knowledge_terms_sections",
        ["import_run_id", "terms_source_alias_digest_sha256", "page_start", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_source_clauses",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("terms_section_id", "private_knowledge_terms_sections.id"),
        sa.Column("source_clause_key", sa.String(length=280), nullable=False),
        sa.Column("clause_label", sa.String(length=240), nullable=True),
        sa.Column("title", sa.String(length=800), nullable=True),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        _sha256("source_text_sha256"),
        sa.Column("review_state", sa.String(length=24), nullable=False),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_clauses_id_run",
        ),
        sa.ForeignKeyConstraint(
            ["terms_section_id", "import_run_id"],
            [
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ],
            name="fk_private_knowledge_clauses_section_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "btrim(source_clause_key) <> ''",
            name="ck_private_knowledge_clauses_key_nonempty",
        ),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_private_knowledge_clauses_page_range",
        ),
        sa.CheckConstraint(
            "source_text_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_clauses_text_digest",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES})",
            name="ck_private_knowledge_clauses_review_state",
        ),
        *_source_record_checks("private_knowledge_clauses"),
    )
    op.create_index(
        "uq_private_knowledge_clauses_source",
        "private_knowledge_source_clauses",
        ["import_run_id", "source_clause_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_clauses_section",
        "private_knowledge_source_clauses",
        ["import_run_id", "terms_section_id", "page_start", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_facts",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("terms_section_id", "private_knowledge_terms_sections.id"),
        sa.Column("source_fact_key", sa.String(length=280), nullable=False),
        sa.Column("fact_type", sa.String(length=40), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        _jsonb("conditions_json", default="'{}'::jsonb"),
        _jsonb("numeric_terms_json", default="'[]'::jsonb"),
        sa.Column("review_state", sa.String(length=24), nullable=False),
        sa.Column(
            "executable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "import_run_id",
            name="uq_private_knowledge_facts_id_run",
        ),
        sa.ForeignKeyConstraint(
            ["terms_section_id", "import_run_id"],
            [
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ],
            name="fk_private_knowledge_facts_section_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "btrim(source_fact_key) <> '' AND btrim(statement) <> '' "
            "AND char_length(statement) <= 8000",
            name="ck_private_knowledge_facts_content_bounded",
        ),
        sa.CheckConstraint(
            "fact_type IN ('PAYMENT_TRIGGER', 'DEFINITION', 'EXCLUSION', "
            "'WAITING_PERIOD', 'REDUCTION', 'FREQUENCY', 'AMOUNT', 'RENEWAL', "
            "'REQUIRED_DOCUMENT', 'TERMINATION', 'CROSS_REFERENCE', 'OTHER')",
            name="ck_private_knowledge_facts_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(conditions_json) = 'object'",
            name="ck_private_knowledge_facts_conditions_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(numeric_terms_json) = 'array'",
            name="ck_private_knowledge_facts_numeric_terms_array",
        ),
        sa.CheckConstraint(
            f"review_state IN ({_REVIEW_STATES})",
            name="ck_private_knowledge_facts_review_state",
        ),
        sa.CheckConstraint("executable = false", name="ck_private_knowledge_facts_not_executable"),
        *_source_record_checks("private_knowledge_facts"),
    )
    op.create_index(
        "uq_private_knowledge_facts_source",
        "private_knowledge_facts",
        ["import_run_id", "source_fact_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_facts_section",
        "private_knowledge_facts",
        ["import_run_id", "terms_section_id", "fact_type", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_fact_citations",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("fact_id", "private_knowledge_facts.id"),
        _foreign_uuid("source_clause_id", "private_knowledge_source_clauses.id"),
        sa.Column("citation_ordinal", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        _sha256("source_text_sha256"),
        _jsonb("locator_json", default="'{}'::jsonb"),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["fact_id", "import_run_id"],
            ["private_knowledge_facts.id", "private_knowledge_facts.import_run_id"],
            name="fk_private_knowledge_citations_fact_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_clause_id", "import_run_id"],
            [
                "private_knowledge_source_clauses.id",
                "private_knowledge_source_clauses.import_run_id",
            ],
            name="fk_private_knowledge_citations_clause_run",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "citation_ordinal >= 1",
            name="ck_private_knowledge_fact_citations_ordinal",
        ),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_private_knowledge_fact_citations_page_range",
        ),
        sa.CheckConstraint(
            "source_text_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_fact_citations_text_digest",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(locator_json) = 'object'",
            name="ck_private_knowledge_fact_citations_locator_object",
        ),
        *_source_record_checks("private_knowledge_fact_citations"),
    )
    op.create_index(
        "uq_private_knowledge_fact_citations_ordinal",
        "private_knowledge_fact_citations",
        ["import_run_id", "fact_id", "citation_ordinal"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_fact_citations_clause",
        "private_knowledge_fact_citations",
        ["import_run_id", "source_clause_id", "fact_id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_coverage_terms_mappings",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        _foreign_uuid("coverage_id", "private_knowledge_coverages.id"),
        _foreign_uuid(
            "terms_section_id",
            "private_knowledge_terms_sections.id",
            nullable=True,
        ),
        sa.Column("source_mapping_key", sa.String(length=300), nullable=False),
        sa.Column("mapping_applicability", sa.String(length=24), nullable=False),
        sa.Column("selected_terms_source_alias", sa.String(length=500), nullable=True),
        _sha256("selected_terms_source_alias_digest_sha256", nullable=True),
        sa.Column("enrollment_decision", sa.String(length=16), nullable=False),
        sa.Column("document_identity_decision", sa.String(length=16), nullable=False),
        sa.Column("edition_applicability_decision", sa.String(length=16), nullable=False),
        sa.Column("section_mapping_decision", sa.String(length=16), nullable=False),
        sa.Column("overall_decision", sa.String(length=16), nullable=False),
        _jsonb("reason_codes_json", default="'[]'::jsonb"),
        sa.Column(
            "executable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        *_source_record_columns(),
        _timestamp("created_at"),
        sa.ForeignKeyConstraint(
            ["coverage_id", "import_run_id"],
            [
                "private_knowledge_coverages.id",
                "private_knowledge_coverages.import_run_id",
            ],
            name="fk_private_knowledge_mappings_coverage_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["terms_section_id", "import_run_id"],
            [
                "private_knowledge_terms_sections.id",
                "private_knowledge_terms_sections.import_run_id",
            ],
            name="fk_private_knowledge_mappings_section_run",
            ondelete="RESTRICT",
        ),
        _tri_state("enrollment_decision", "private_knowledge_mappings"),
        _tri_state("document_identity_decision", "private_knowledge_mappings"),
        _tri_state("edition_applicability_decision", "private_knowledge_mappings"),
        _tri_state("section_mapping_decision", "private_knowledge_mappings"),
        _tri_state("overall_decision", "private_knowledge_mappings"),
        sa.CheckConstraint(
            "mapping_applicability IN ('APPLICABLE', 'NOT_APPLICABLE', 'UNKNOWN')",
            name="ck_private_knowledge_mappings_applicability",
        ),
        sa.CheckConstraint(
            "btrim(source_mapping_key) <> ''",
            name="ck_private_knowledge_mappings_key_nonempty",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes_json) = 'array'",
            name="ck_private_knowledge_mappings_reason_codes_array",
        ),
        sa.CheckConstraint(
            "((selected_terms_source_alias IS NULL AND "
            "selected_terms_source_alias_digest_sha256 IS NULL) OR "
            "(selected_terms_source_alias IS NOT NULL "
            "AND btrim(selected_terms_source_alias) <> '' "
            "AND selected_terms_source_alias_digest_sha256 ~ '^[0-9a-f]{64}$'))",
            name="ck_private_knowledge_mappings_selected_alias",
        ),
        sa.CheckConstraint(
            "(overall_decision <> 'MATCH' OR (terms_section_id IS NOT NULL "
            "AND mapping_applicability = 'APPLICABLE' "
            "AND selected_terms_source_alias IS NOT NULL "
            "AND enrollment_decision = 'MATCH' AND document_identity_decision = 'MATCH' "
            "AND edition_applicability_decision = 'MATCH' "
            "AND section_mapping_decision = 'MATCH'))",
            name="ck_private_knowledge_mappings_match_axes",
        ),
        sa.CheckConstraint(
            "(mapping_applicability <> 'NOT_APPLICABLE' OR (terms_section_id IS NULL "
            "AND selected_terms_source_alias IS NULL "
            "AND section_mapping_decision = 'UNKNOWN' AND overall_decision = 'UNKNOWN'))",
            name="ck_private_knowledge_mappings_not_applicable",
        ),
        sa.CheckConstraint(
            "executable = false",
            name="ck_private_knowledge_mappings_not_executable",
        ),
        *_source_record_checks("private_knowledge_mappings"),
    )
    op.create_index(
        "uq_private_knowledge_mappings_source",
        "private_knowledge_coverage_terms_mappings",
        ["import_run_id", "source_mapping_key"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_mappings_coverage",
        "private_knowledge_coverage_terms_mappings",
        ["import_run_id", "coverage_id", "overall_decision", "id"],
        unique=False,
    )

    op.create_table(
        "private_knowledge_document_bindings",
        _uuid("id", primary_key=True),
        _foreign_uuid("import_run_id", "private_knowledge_import_runs.id"),
        sa.Column("source_alias", sa.String(length=500), nullable=False),
        _sha256("source_alias_digest_sha256"),
        _foreign_uuid("document_version_id", "document_versions.id", nullable=True),
        _foreign_uuid("evidence_id", "evidence.id", nullable=True),
        sa.Column("binding_decision", sa.String(length=16), nullable=False),
        sa.Column(
            "binding_conflict",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("binding_reason_code", sa.String(length=64), nullable=False),
        _sha256("expected_content_sha256", nullable=True),
        sa.Column("expected_page_count", sa.Integer(), nullable=True),
        sa.Column("content_digest_decision", sa.String(length=16), nullable=False),
        sa.Column("page_count_decision", sa.String(length=16), nullable=False),
        sa.Column("document_kind_decision", sa.String(length=16), nullable=False),
        *_source_record_columns(),
        _timestamp("created_at"),
        _tri_state("binding_decision", "private_knowledge_document_bindings"),
        _tri_state("content_digest_decision", "private_knowledge_document_bindings"),
        _tri_state("page_count_decision", "private_knowledge_document_bindings"),
        _tri_state("document_kind_decision", "private_knowledge_document_bindings"),
        sa.CheckConstraint(
            "btrim(source_alias) <> ''",
            name="ck_private_knowledge_document_bindings_alias_nonempty",
        ),
        sa.CheckConstraint(
            "source_alias_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_document_bindings_alias_digest",
        ),
        sa.CheckConstraint(
            "expected_content_sha256 IS NULL OR expected_content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_private_knowledge_document_bindings_expected_digest",
        ),
        sa.CheckConstraint(
            "expected_page_count IS NULL OR expected_page_count >= 1",
            name="ck_private_knowledge_document_bindings_expected_pages",
        ),
        sa.CheckConstraint(
            "((binding_decision = 'MATCH' AND document_version_id IS NOT NULL "
            "AND content_digest_decision = 'MATCH' AND page_count_decision = 'MATCH' "
            "AND document_kind_decision = 'MATCH') OR binding_decision <> 'MATCH')",
            name="ck_private_knowledge_document_bindings_exact_match",
        ),
        sa.CheckConstraint(
            "evidence_id IS NULL OR document_version_id IS NOT NULL",
            name="ck_private_knowledge_document_bindings_evidence_version",
        ),
        *_source_record_checks("private_knowledge_document_bindings"),
    )
    op.create_index(
        "uq_private_knowledge_document_bindings_alias",
        "private_knowledge_document_bindings",
        ["import_run_id", "source_alias_digest_sha256"],
        unique=True,
    )
    op.create_index(
        "ix_private_knowledge_document_bindings_version",
        "private_knowledge_document_bindings",
        ["document_version_id", "import_run_id", "id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the additive catalog in reverse dependency order."""

    for table_name in reversed(
        [
            "private_knowledge_import_runs",
            "private_knowledge_subjects",
            "private_knowledge_contracts",
            "private_knowledge_coverages",
            "private_knowledge_terms_assignments",
            "private_knowledge_terms_assignment_sources",
            "private_knowledge_terms_sections",
            "private_knowledge_source_clauses",
            "private_knowledge_facts",
            "private_knowledge_fact_citations",
            "private_knowledge_coverage_terms_mappings",
            "private_knowledge_document_bindings",
        ]
    ):
        op.drop_table(table_name)
