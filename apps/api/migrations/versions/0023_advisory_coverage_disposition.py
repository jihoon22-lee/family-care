"""Add advisory coverage disposition and decision-run catalog count."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0023_advisory_disposition"
down_revision: str | Sequence[str] | None = "0022_analysis_assistance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_IMMUTABLE_PUBLICATION_TABLES = (
    (
        "private_knowledge_coverage_execution_dispositions",
        "trg_private_knowledge_dispositions_immutable",
    ),
    (
        "private_knowledge_contract_status_intervals",
        "trg_private_knowledge_status_intervals_immutable",
    ),
    (
        "private_knowledge_fact_normalizer_publications",
        "trg_private_knowledge_fact_normalizers_immutable",
    ),
    (
        "private_knowledge_rule_publications",
        "trg_private_knowledge_rule_publications_immutable",
    ),
    (
        "private_knowledge_rule_citations",
        "trg_private_knowledge_rule_citations_immutable",
    ),
    (
        "private_knowledge_calculation_publications",
        "trg_private_knowledge_calculation_publications_immutable",
    ),
    (
        "private_knowledge_calculation_citations",
        "trg_private_knowledge_calculation_citations_immutable",
    ),
)


def _install_publication_mutation_guards() -> None:
    op.execute(
        """
        CREATE FUNCTION reject_private_knowledge_publication_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'private knowledge publication records are immutable'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    for table_name, trigger_name in _IMMUTABLE_PUBLICATION_TABLES:
        op.execute(
            f"""
            CREATE TRIGGER {trigger_name}
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_private_knowledge_publication_mutation()
            """
        )


def _remove_publication_mutation_guards() -> None:
    for table_name, trigger_name in _IMMUTABLE_PUBLICATION_TABLES:
        op.execute(f"DROP TRIGGER {trigger_name} ON {table_name}")
    op.execute("DROP FUNCTION reject_private_knowledge_publication_mutation()")


def _knowledge_counts_constraint() -> str:
    return (
        "knowledge_contract_count >= 0 AND knowledge_benefit_coverage_count >= 0 "
        "AND knowledge_published_coverage_count >= 0 "
        "AND knowledge_advisory_coverage_count >= 0 "
        "AND knowledge_blocked_coverage_count >= 0 "
        "AND knowledge_not_applicable_coverage_count >= 0 "
        "AND knowledge_published_coverage_count + knowledge_advisory_coverage_count "
        "+ knowledge_blocked_coverage_count + knowledge_not_applicable_coverage_count "
        "<= knowledge_benefit_coverage_count"
    )


def _calculation_status_constraint() -> str:
    return (
        "((calculation_status = 'CALCULATED' "
        "AND conditional_amount IS NOT NULL AND currency IS NOT NULL) OR "
        "(calculation_status = 'UNKNOWN' AND conditional_amount IS NULL) OR "
        "(calculation_status IN ('NOT_APPLICABLE', 'FAILED') "
        "AND currency IS NULL AND confirmed_amount IS NULL "
        "AND conditional_amount IS NULL AND excluded_amount IS NULL "
        "AND deductible_amount IS NULL AND applied_rate IS NULL "
        "AND applied_limit IS NULL)) AND "
        "(confirmed_amount IS NULL OR "
        "(calculation_status = 'CALCULATED' AND hold_reason_code IS NULL))"
    )


def _legacy_calculation_status_constraint() -> str:
    return (
        "((calculation_status = 'CALCULATED' "
        "AND conditional_amount IS NOT NULL AND currency IS NOT NULL) OR "
        "(calculation_status = 'UNKNOWN' AND conditional_amount IS NULL) OR "
        "(calculation_status IN ('NOT_APPLICABLE', 'FAILED') "
        "AND currency IS NULL AND confirmed_amount IS NULL "
        "AND conditional_amount IS NULL AND excluded_amount IS NULL "
        "AND deductible_amount IS NULL AND applied_rate IS NULL "
        "AND applied_limit IS NULL))"
    )


def _disposition_enrollment_authority_constraint() -> str:
    return (
        "((disposition = 'PUBLISHED' "
        "AND enrollment_decision_snapshot = 'MATCH' "
        "AND enrollment_authority IS NOT NULL "
        "AND enrollment_authority = 'CERTIFICATE_SNAPSHOT' "
        "AND enrollment_reason_code IS NULL "
        "AND enrollment_confirmed_by IS NULL) OR "
        "(disposition = 'ADVISORY' AND (("
        "enrollment_decision_snapshot = 'MATCH' "
        "AND enrollment_authority IS NOT NULL "
        "AND enrollment_authority = 'CERTIFICATE_SNAPSHOT' "
        "AND enrollment_reason_code IS NULL "
        "AND enrollment_confirmed_by IS NULL) OR ("
        "enrollment_decision_snapshot = 'UNKNOWN' "
        "AND enrollment_authority IS NOT NULL "
        "AND enrollment_authority = 'USER_CONFIRMED_COVERAGE_ENROLLMENT' "
        "AND enrollment_reason_code IS NOT NULL "
        "AND enrollment_reason_code = 'USER_CONFIRMED_COVERAGE_ENROLLMENT' "
        "AND enrollment_confirmed_by IS NOT NULL "
        "AND reason_codes_json @> "
        "'[\"USER_CONFIRMED_COVERAGE_ENROLLMENT\"]'::jsonb))) OR "
        "(disposition IN ('BLOCKED', 'NOT_APPLICABLE') "
        "AND (enrollment_decision_snapshot = 'MATCH' "
        "OR enrollment_decision_snapshot = 'UNKNOWN' "
        "OR enrollment_decision_snapshot = 'NO_MATCH') "
        "AND enrollment_authority IS NULL "
        "AND enrollment_reason_code IS NULL "
        "AND enrollment_confirmed_by IS NULL))"
    )


def _recommendation_enrollment_constraint() -> str:
    return (
        "((enrollment_decision_snapshot = 'MATCH' AND (("
        "coverage_execution_disposition_id IS NULL "
        "AND enrollment_authority_snapshot IS NULL) OR ("
        "coverage_execution_disposition_id IS NOT NULL "
        "AND enrollment_authority_snapshot IS NOT NULL "
        "AND enrollment_authority_snapshot = 'CERTIFICATE_SNAPSHOT'))) OR ("
        "enrollment_decision_snapshot = 'UNKNOWN' "
        "AND coverage_execution_disposition_id IS NOT NULL "
        "AND enrollment_authority_snapshot IS NOT NULL "
        "AND enrollment_authority_snapshot = "
        "'USER_CONFIRMED_COVERAGE_ENROLLMENT'))"
    )


def upgrade() -> None:
    op.drop_constraint(
        "ck_private_knowledge_rule_runs_schema",
        "private_knowledge_rule_import_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_private_knowledge_rule_runs_schema",
        "private_knowledge_rule_import_runs",
        "package_schema_version IN ("
        "'private-knowledge-rule-publication.sol-v1', "
        "'private-knowledge-rule-publication.sol-v2')",
    )
    op.create_index(
        "uq_private_knowledge_rule_runs_confirmation_actor",
        "private_knowledge_rule_import_runs",
        [
            "id",
            "knowledge_import_run_id",
            "household_space_id",
            "reviewed_by",
        ],
        unique=True,
    )
    op.drop_constraint(
        "ck_private_knowledge_dispositions_value",
        "private_knowledge_coverage_execution_dispositions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_private_knowledge_dispositions_value",
        "private_knowledge_coverage_execution_dispositions",
        "disposition IN ('PUBLISHED', 'ADVISORY', 'BLOCKED', 'NOT_APPLICABLE')",
    )
    for column in (
        sa.Column("enrollment_decision_snapshot", sa.String(length=16), nullable=True),
        sa.Column("enrollment_authority", sa.String(length=64), nullable=True),
        sa.Column("enrollment_reason_code", sa.String(length=64), nullable=True),
        sa.Column("enrollment_confirmed_by", sa.UUID(as_uuid=True), nullable=True),
    ):
        op.add_column(
            "private_knowledge_coverage_execution_dispositions",
            column,
        )
    op.execute(
        """
        UPDATE private_knowledge_coverage_execution_dispositions AS disposition
        SET enrollment_decision_snapshot = coverage.enrollment_decision,
            enrollment_authority = CASE
              WHEN disposition.disposition = 'PUBLISHED'
               AND coverage.enrollment_decision = 'MATCH'
              THEN 'CERTIFICATE_SNAPSHOT'
              ELSE NULL
            END
        FROM private_knowledge_coverages AS coverage
        WHERE coverage.id = disposition.knowledge_coverage_id
          AND coverage.import_run_id = disposition.knowledge_import_run_id
        """
    )
    op.alter_column(
        "private_knowledge_coverage_execution_dispositions",
        "enrollment_decision_snapshot",
        existing_type=sa.String(length=16),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_private_knowledge_dispositions_enrollment_snapshot",
        "private_knowledge_coverage_execution_dispositions",
        "private_knowledge_coverages",
        [
            "knowledge_coverage_id",
            "knowledge_import_run_id",
            "enrollment_decision_snapshot",
        ],
        ["id", "import_run_id", "enrollment_decision"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_private_knowledge_dispositions_enrollment_confirmer",
        "private_knowledge_coverage_execution_dispositions",
        "app_users",
        ["enrollment_confirmed_by", "household_space_id"],
        ["id", "household_space_id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_private_knowledge_dispositions_confirmation_run_actor",
        "private_knowledge_coverage_execution_dispositions",
        "private_knowledge_rule_import_runs",
        [
            "rule_import_run_id",
            "knowledge_import_run_id",
            "household_space_id",
            "enrollment_confirmed_by",
        ],
        [
            "id",
            "knowledge_import_run_id",
            "household_space_id",
            "reviewed_by",
        ],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_private_knowledge_dispositions_enrollment_snapshot",
        "private_knowledge_coverage_execution_dispositions",
        [
            "knowledge_coverage_id",
            "knowledge_import_run_id",
            "enrollment_decision_snapshot",
        ],
        unique=False,
    )
    op.create_index(
        "ix_private_knowledge_dispositions_enrollment_confirmer",
        "private_knowledge_coverage_execution_dispositions",
        ["enrollment_confirmed_by", "household_space_id"],
        unique=False,
    )
    op.create_check_constraint(
        "ck_private_knowledge_dispositions_enrollment_authority",
        "private_knowledge_coverage_execution_dispositions",
        _disposition_enrollment_authority_constraint(),
    )
    op.create_index(
        "uq_private_knowledge_dispositions_authority_scope",
        "private_knowledge_coverage_execution_dispositions",
        [
            "id",
            "knowledge_import_run_id",
            "household_space_id",
            "knowledge_coverage_id",
            "enrollment_decision_snapshot",
            "enrollment_authority",
        ],
        unique=True,
    )
    op.add_column(
        "analysis_recommendations",
        sa.Column(
            "coverage_execution_disposition_id",
            sa.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.add_column(
        "analysis_recommendations",
        sa.Column("enrollment_authority_snapshot", sa.String(length=64), nullable=True),
    )
    op.execute("DROP TRIGGER trg_analysis_recommendations_immutable ON analysis_recommendations")
    op.execute(
        """
        UPDATE analysis_recommendations AS recommendation
        SET coverage_execution_disposition_id = disposition.id,
            enrollment_authority_snapshot = disposition.enrollment_authority
        FROM private_knowledge_claim_candidates AS candidate
        JOIN private_knowledge_coverage_execution_dispositions AS disposition
          ON disposition.rule_import_run_id = candidate.knowledge_rule_import_run_id
         AND disposition.knowledge_import_run_id = candidate.knowledge_import_run_id
         AND disposition.household_space_id = candidate.household_space_id
         AND disposition.knowledge_coverage_id = candidate.knowledge_coverage_id
        WHERE candidate.id = recommendation.private_claim_candidate_id
          AND candidate.decision_run_id = recommendation.decision_run_id
          AND candidate.household_space_id = recommendation.household_space_id
          AND candidate.knowledge_import_run_id = recommendation.knowledge_import_run_id
          AND candidate.knowledge_coverage_id = recommendation.knowledge_coverage_id
          AND disposition.enrollment_decision_snapshot =
              recommendation.enrollment_decision_snapshot
          AND disposition.enrollment_authority IS NOT NULL
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_analysis_recommendations_immutable
        BEFORE UPDATE OR DELETE ON analysis_recommendations
        FOR EACH ROW EXECUTE FUNCTION reject_analysis_assistance_result_mutation()
        """
    )
    op.drop_constraint(
        "ck_analysis_recommendations_enrollment",
        "analysis_recommendations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_analysis_recommendations_enrollment",
        "analysis_recommendations",
        _recommendation_enrollment_constraint(),
    )
    op.create_index(
        "ix_analysis_recommendations_disposition_lineage",
        "analysis_recommendations",
        ["coverage_execution_disposition_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_analysis_recommendations_disposition_authority",
        "analysis_recommendations",
        "private_knowledge_coverage_execution_dispositions",
        [
            "coverage_execution_disposition_id",
            "knowledge_import_run_id",
            "household_space_id",
            "knowledge_coverage_id",
            "enrollment_decision_snapshot",
            "enrollment_authority_snapshot",
        ],
        [
            "id",
            "knowledge_import_run_id",
            "household_space_id",
            "knowledge_coverage_id",
            "enrollment_decision_snapshot",
            "enrollment_authority",
        ],
        ondelete="RESTRICT",
    )
    op.add_column(
        "decision_runs",
        sa.Column(
            "knowledge_advisory_coverage_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.drop_constraint("ck_decision_runs_knowledge_counts", "decision_runs", type_="check")
    op.create_check_constraint(
        "ck_decision_runs_knowledge_counts",
        "decision_runs",
        _knowledge_counts_constraint(),
    )
    op.drop_constraint(
        "ck_pk_calculations_status_values",
        "private_knowledge_benefit_calculations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pk_calculations_status_values",
        "private_knowledge_benefit_calculations",
        _calculation_status_constraint(),
    )
    _install_publication_mutation_guards()


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM private_knowledge_rule_import_runs
            WHERE package_schema_version = 'private-knowledge-rule-publication.sol-v2'
          ) OR EXISTS (
            SELECT 1
            FROM private_knowledge_coverage_execution_dispositions
            WHERE disposition = 'ADVISORY'
          ) OR EXISTS (
            SELECT 1
            FROM decision_runs
            WHERE knowledge_advisory_coverage_count <> 0
          ) OR EXISTS (
            SELECT 1
            FROM private_knowledge_coverage_execution_dispositions
            WHERE enrollment_authority = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'
          ) OR EXISTS (
            SELECT 1
            FROM analysis_recommendations
            WHERE enrollment_authority_snapshot = 'USER_CONFIRMED_COVERAGE_ENROLLMENT'
          ) THEN
            RAISE EXCEPTION
              'cannot downgrade advisory disposition migration with v2 history';
          END IF;
        END;
        $$
        """
    )
    _remove_publication_mutation_guards()
    op.drop_constraint(
        "fk_analysis_recommendations_disposition_authority",
        "analysis_recommendations",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_analysis_recommendations_disposition_lineage",
        table_name="analysis_recommendations",
    )
    op.drop_constraint(
        "ck_analysis_recommendations_enrollment",
        "analysis_recommendations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_analysis_recommendations_enrollment",
        "analysis_recommendations",
        "enrollment_decision_snapshot = 'MATCH'",
    )
    op.drop_column("analysis_recommendations", "enrollment_authority_snapshot")
    op.drop_column("analysis_recommendations", "coverage_execution_disposition_id")
    op.drop_index(
        "uq_private_knowledge_dispositions_authority_scope",
        table_name="private_knowledge_coverage_execution_dispositions",
    )
    op.drop_constraint(
        "ck_private_knowledge_dispositions_enrollment_authority",
        "private_knowledge_coverage_execution_dispositions",
        type_="check",
    )
    op.drop_constraint(
        "fk_private_knowledge_dispositions_confirmation_run_actor",
        "private_knowledge_coverage_execution_dispositions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_private_knowledge_dispositions_enrollment_confirmer",
        "private_knowledge_coverage_execution_dispositions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_private_knowledge_dispositions_enrollment_snapshot",
        "private_knowledge_coverage_execution_dispositions",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_private_knowledge_dispositions_enrollment_confirmer",
        table_name="private_knowledge_coverage_execution_dispositions",
    )
    op.drop_index(
        "ix_private_knowledge_dispositions_enrollment_snapshot",
        table_name="private_knowledge_coverage_execution_dispositions",
    )
    for column_name in (
        "enrollment_confirmed_by",
        "enrollment_reason_code",
        "enrollment_authority",
        "enrollment_decision_snapshot",
    ):
        op.drop_column(
            "private_knowledge_coverage_execution_dispositions",
            column_name,
        )
    op.drop_index(
        "uq_private_knowledge_rule_runs_confirmation_actor",
        table_name="private_knowledge_rule_import_runs",
    )
    op.drop_constraint(
        "ck_pk_calculations_status_values",
        "private_knowledge_benefit_calculations",
        type_="check",
    )
    op.create_check_constraint(
        "ck_pk_calculations_status_values",
        "private_knowledge_benefit_calculations",
        _legacy_calculation_status_constraint(),
    )
    op.drop_constraint("ck_decision_runs_knowledge_counts", "decision_runs", type_="check")
    op.drop_column("decision_runs", "knowledge_advisory_coverage_count")
    op.create_check_constraint(
        "ck_decision_runs_knowledge_counts",
        "decision_runs",
        "knowledge_contract_count >= 0 AND knowledge_benefit_coverage_count >= 0 "
        "AND knowledge_published_coverage_count >= 0 "
        "AND knowledge_blocked_coverage_count >= 0 "
        "AND knowledge_not_applicable_coverage_count >= 0 "
        "AND knowledge_published_coverage_count + knowledge_blocked_coverage_count "
        "+ knowledge_not_applicable_coverage_count <= knowledge_benefit_coverage_count",
    )
    op.drop_constraint(
        "ck_private_knowledge_dispositions_value",
        "private_knowledge_coverage_execution_dispositions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_private_knowledge_dispositions_value",
        "private_knowledge_coverage_execution_dispositions",
        "disposition IN ('PUBLISHED', 'BLOCKED', 'NOT_APPLICABLE')",
    )
    op.drop_constraint(
        "ck_private_knowledge_rule_runs_schema",
        "private_knowledge_rule_import_runs",
        type_="check",
    )
    op.create_check_constraint(
        "ck_private_knowledge_rule_runs_schema",
        "private_knowledge_rule_import_runs",
        "package_schema_version = 'private-knowledge-rule-publication.sol-v1'",
    )
