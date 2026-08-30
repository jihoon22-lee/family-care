"""Persist scoped structured-search and optional LLM assistance projections."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0022_analysis_assistance"
down_revision: str | Sequence[str] | None = "0021_private_knowledge_decisions"
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


def _reason_code(column: str, name: str, *, nullable: bool = False) -> sa.CheckConstraint:
    prefix = f"{column} IS NULL OR " if nullable else ""
    return sa.CheckConstraint(
        f"{prefix}{column} ~ '^[A-Z][A-Z0-9_]{{0,63}}$'",
        name=name,
    )


def upgrade() -> None:
    """Add append-only assistance runs without changing decision authority."""

    op.create_index(
        "uq_analysis_medical_events_scope",
        "medical_events",
        ["id", "household_space_id"],
        unique=True,
    )
    op.create_index(
        "uq_analysis_decision_runs_scope",
        "decision_runs",
        ["id", "household_space_id", "medical_event_id", "event_version"],
        unique=True,
    )
    op.create_index(
        "uq_analysis_citations_scope",
        "private_knowledge_fact_citations",
        ["id", "import_run_id", "fact_id", "source_clause_id"],
        unique=True,
    )
    op.create_index(
        "uq_analysis_coverages_enrollment_scope",
        "private_knowledge_coverages",
        ["id", "import_run_id", "enrollment_decision"],
        unique=True,
    )
    op.create_index(
        "uq_analysis_facts_section_scope",
        "private_knowledge_facts",
        ["id", "import_run_id", "terms_section_id"],
        unique=True,
    )
    op.create_index(
        "uq_analysis_clauses_section_scope",
        "private_knowledge_source_clauses",
        ["id", "import_run_id", "terms_section_id"],
        unique=True,
    )

    op.create_table(
        "analysis_assistance_jobs",
        _uuid("id", primary_key=True),
        _uuid("household_space_id"),
        _uuid("medical_event_id"),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("candidate_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("attempts", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("outcome_code", sa.String(length=64), nullable=True),
        _timestamp("claimed_at", nullable=True),
        _timestamp("completed_at", nullable=True),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "household_space_id",
            "medical_event_id",
            "event_version",
            "candidate_digest_sha256",
            name="uq_analysis_assistance_jobs_scope",
        ),
        sa.UniqueConstraint(
            "household_space_id",
            "medical_event_id",
            "event_version",
            "candidate_digest_sha256",
            name="uq_analysis_assistance_jobs_dedupe",
        ),
        sa.ForeignKeyConstraint(
            ["medical_event_id", "household_space_id"],
            ["medical_events.id", "medical_events.household_space_id"],
            name="fk_analysis_assistance_jobs_event_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "event_version >= 1",
            name="ck_analysis_assistance_jobs_event_version",
        ),
        sa.CheckConstraint(
            "candidate_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_analysis_assistance_jobs_digest",
        ),
        sa.CheckConstraint(
            "state IN ('QUEUED', 'RUNNING', 'SUCCEEDED')",
            name="ck_analysis_assistance_jobs_state",
        ),
        sa.CheckConstraint(
            "attempts >= 0 AND attempts <= 1",
            name="ck_analysis_assistance_jobs_attempts",
        ),
        _reason_code(
            "outcome_code",
            "ck_analysis_assistance_jobs_outcome",
            nullable=True,
        ),
        sa.CheckConstraint(
            "((state = 'QUEUED' AND attempts = 0 AND claimed_at IS NULL "
            "AND completed_at IS NULL) OR "
            "(state = 'RUNNING' AND attempts = 1 AND claimed_at IS NOT NULL "
            "AND completed_at IS NULL) OR "
            "(state = 'SUCCEEDED' AND completed_at IS NOT NULL AND "
            "((attempts = 0 AND claimed_at IS NULL) OR "
            "(attempts = 1 AND claimed_at IS NOT NULL))))",
            name="ck_analysis_assistance_jobs_lifecycle",
        ),
    )
    op.create_index(
        "ix_analysis_assistance_jobs_claim",
        "analysis_assistance_jobs",
        ["state", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "analysis_assistance_runs",
        _uuid("id", primary_key=True),
        _uuid("analysis_job_id"),
        _uuid("household_space_id"),
        _uuid("medical_event_id"),
        _uuid("decision_run_id"),
        sa.Column("event_version", sa.Integer(), nullable=False),
        sa.Column("candidate_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=24), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("provider_label", sa.String(length=64), nullable=True),
        sa.Column("provider_request_id", sa.String(length=200), nullable=True),
        sa.Column("model_label", sa.String(length=120), nullable=True),
        sa.Column("config_version", sa.String(length=64), nullable=True),
        sa.Column("outcome_code", sa.String(length=64), nullable=False),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "id",
            "decision_run_id",
            "household_space_id",
            "candidate_digest_sha256",
            name="uq_analysis_assistance_runs_scope",
        ),
        sa.UniqueConstraint(
            "decision_run_id",
            "mode",
            "state",
            name="uq_analysis_assistance_runs_decision_mode_state",
        ),
        sa.ForeignKeyConstraint(
            [
                "analysis_job_id",
                "household_space_id",
                "medical_event_id",
                "event_version",
                "candidate_digest_sha256",
            ],
            [
                "analysis_assistance_jobs.id",
                "analysis_assistance_jobs.household_space_id",
                "analysis_assistance_jobs.medical_event_id",
                "analysis_assistance_jobs.event_version",
                "analysis_assistance_jobs.candidate_digest_sha256",
            ],
            name="fk_analysis_assistance_runs_job_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "decision_run_id",
                "household_space_id",
                "medical_event_id",
                "event_version",
            ],
            [
                "decision_runs.id",
                "decision_runs.household_space_id",
                "decision_runs.medical_event_id",
                "decision_runs.event_version",
            ],
            name="fk_analysis_assistance_runs_decision_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "candidate_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_analysis_assistance_runs_digest",
        ),
        sa.CheckConstraint(
            "mode IN ('STRUCTURED_SEARCH', 'LLM_ASSISTED', 'NONE')",
            name="ck_analysis_assistance_runs_mode",
        ),
        sa.CheckConstraint(
            "state IN ('SEARCH_READY', 'LLM_PENDING', 'LLM_READY')",
            name="ck_analysis_assistance_runs_state",
        ),
        _reason_code("outcome_code", "ck_analysis_assistance_runs_outcome"),
        sa.CheckConstraint(
            "((mode = 'LLM_ASSISTED' AND provider_label IS NOT NULL "
            "AND model_label IS NOT NULL AND config_version IS NOT NULL "
            "AND state = 'LLM_READY') OR "
            "(mode IN ('STRUCTURED_SEARCH', 'NONE') AND provider_label IS NULL "
            "AND provider_request_id IS NULL AND model_label IS NULL "
            "AND config_version IS NULL AND state <> 'LLM_READY'))",
            name="ck_analysis_assistance_runs_provider_lineage",
        ),
        sa.CheckConstraint(
            "provider_label IS NULL OR (btrim(provider_label) <> '' "
            "AND char_length(provider_label) <= 64)",
            name="ck_analysis_assistance_runs_provider_label",
        ),
        sa.CheckConstraint(
            "provider_request_id IS NULL OR (btrim(provider_request_id) <> '' "
            "AND char_length(provider_request_id) <= 200)",
            name="ck_analysis_assistance_runs_request_id",
        ),
        sa.CheckConstraint(
            "model_label IS NULL OR (btrim(model_label) <> '' AND char_length(model_label) <= 120)",
            name="ck_analysis_assistance_runs_model_label",
        ),
        sa.CheckConstraint(
            "config_version IS NULL OR (btrim(config_version) <> '' "
            "AND char_length(config_version) <= 64)",
            name="ck_analysis_assistance_runs_config_version",
        ),
    )
    op.create_index(
        "ix_analysis_assistance_runs_latest",
        "analysis_assistance_runs",
        ["decision_run_id", "created_at", "id"],
        unique=False,
    )

    op.create_table(
        "analysis_recommendations",
        _uuid("id", primary_key=True),
        _uuid("analysis_assistance_run_id"),
        _uuid("household_space_id"),
        _uuid("decision_run_id"),
        _uuid("private_claim_candidate_id"),
        _uuid("knowledge_import_run_id"),
        _uuid("knowledge_coverage_id"),
        sa.Column("enrollment_decision_snapshot", sa.String(length=16), nullable=False),
        _uuid("terms_section_id"),
        _uuid("knowledge_fact_id"),
        _uuid("source_clause_id"),
        _uuid("fact_citation_id"),
        sa.Column("candidate_digest_sha256", sa.String(length=64), nullable=False),
        sa.Column("rank", sa.SmallInteger(), nullable=False),
        sa.Column("score", sa.Numeric(precision=12, scale=6), nullable=False),
        sa.Column("contract_label_snapshot", sa.String(length=240), nullable=False),
        sa.Column("coverage_label_snapshot", sa.String(length=800), nullable=False),
        sa.Column("clause_label_snapshot", sa.String(length=800), nullable=False),
        sa.Column("excerpt", sa.String(length=240), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("citation_kind", sa.String(length=40), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("explanation_code", sa.String(length=64), nullable=True),
        sa.Column("question_code", sa.String(length=64), nullable=True),
        _timestamp("created_at"),
        sa.UniqueConstraint(
            "analysis_assistance_run_id",
            "rank",
            name="uq_analysis_recommendations_run_rank",
        ),
        sa.UniqueConstraint(
            "analysis_assistance_run_id",
            "knowledge_coverage_id",
            "fact_citation_id",
            name="uq_analysis_recommendations_run_citation",
        ),
        sa.ForeignKeyConstraint(
            [
                "analysis_assistance_run_id",
                "decision_run_id",
                "household_space_id",
                "candidate_digest_sha256",
            ],
            [
                "analysis_assistance_runs.id",
                "analysis_assistance_runs.decision_run_id",
                "analysis_assistance_runs.household_space_id",
                "analysis_assistance_runs.candidate_digest_sha256",
            ],
            name="fk_analysis_recommendations_run_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "private_claim_candidate_id",
                "decision_run_id",
                "household_space_id",
                "knowledge_import_run_id",
                "knowledge_coverage_id",
            ],
            [
                "private_knowledge_claim_candidates.id",
                "private_knowledge_claim_candidates.decision_run_id",
                "private_knowledge_claim_candidates.household_space_id",
                "private_knowledge_claim_candidates.knowledge_import_run_id",
                "private_knowledge_claim_candidates.knowledge_coverage_id",
            ],
            name="fk_analysis_recommendations_enrolled_coverage",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "knowledge_coverage_id",
                "knowledge_import_run_id",
                "enrollment_decision_snapshot",
            ],
            [
                "private_knowledge_coverages.id",
                "private_knowledge_coverages.import_run_id",
                "private_knowledge_coverages.enrollment_decision",
            ],
            name="fk_analysis_recommendations_enrolled_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["knowledge_fact_id", "knowledge_import_run_id", "terms_section_id"],
            [
                "private_knowledge_facts.id",
                "private_knowledge_facts.import_run_id",
                "private_knowledge_facts.terms_section_id",
            ],
            name="fk_analysis_recommendations_fact_section",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_clause_id", "knowledge_import_run_id", "terms_section_id"],
            [
                "private_knowledge_source_clauses.id",
                "private_knowledge_source_clauses.import_run_id",
                "private_knowledge_source_clauses.terms_section_id",
            ],
            name="fk_analysis_recommendations_clause_section",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "fact_citation_id",
                "knowledge_import_run_id",
                "knowledge_fact_id",
                "source_clause_id",
            ],
            [
                "private_knowledge_fact_citations.id",
                "private_knowledge_fact_citations.import_run_id",
                "private_knowledge_fact_citations.fact_id",
                "private_knowledge_fact_citations.source_clause_id",
            ],
            name="fk_analysis_recommendations_citation_scope",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "enrollment_decision_snapshot = 'MATCH'",
            name="ck_analysis_recommendations_enrollment",
        ),
        sa.CheckConstraint(
            "candidate_digest_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_analysis_recommendations_digest",
        ),
        sa.CheckConstraint(
            "rank >= 1 AND rank <= 12",
            name="ck_analysis_recommendations_rank",
        ),
        sa.CheckConstraint(
            "score >= 0",
            name="ck_analysis_recommendations_score",
        ),
        sa.CheckConstraint(
            "btrim(contract_label_snapshot) <> '' "
            "AND btrim(coverage_label_snapshot) <> '' "
            "AND btrim(clause_label_snapshot) <> ''",
            name="ck_analysis_recommendations_labels",
        ),
        sa.CheckConstraint(
            "btrim(excerpt) <> '' AND char_length(excerpt) <= 240",
            name="ck_analysis_recommendations_excerpt",
        ),
        sa.CheckConstraint(
            "page_start >= 1 AND page_end >= page_start",
            name="ck_analysis_recommendations_pages",
        ),
        sa.CheckConstraint(
            "page_end - page_start <= 20",
            name="ck_analysis_recommendations_page_span",
        ),
        sa.CheckConstraint(
            "citation_kind IN ('FACT_CITATION')",
            name="ck_analysis_recommendations_citation_kind",
        ),
        _reason_code("reason_code", "ck_analysis_recommendations_reason"),
        _reason_code(
            "explanation_code",
            "ck_analysis_recommendations_explanation",
            nullable=True,
        ),
        _reason_code(
            "question_code",
            "ck_analysis_recommendations_question",
            nullable=True,
        ),
    )
    op.create_index(
        "ix_analysis_recommendations_run",
        "analysis_recommendations",
        ["analysis_assistance_run_id", "rank", "id"],
        unique=False,
    )

    op.execute(
        """
        CREATE FUNCTION reject_analysis_assistance_result_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'analysis assistance results are immutable'
            USING ERRCODE = '55000';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_analysis_assistance_runs_immutable
        BEFORE UPDATE OR DELETE ON analysis_assistance_runs
        FOR EACH ROW EXECUTE FUNCTION reject_analysis_assistance_result_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_analysis_recommendations_immutable
        BEFORE UPDATE OR DELETE ON analysis_recommendations
        FOR EACH ROW EXECUTE FUNCTION reject_analysis_assistance_result_mutation()
        """
    )


def downgrade() -> None:
    """Remove assistance projections and their supporting indexes."""

    op.drop_index(
        "ix_analysis_recommendations_run",
        table_name="analysis_recommendations",
    )
    op.drop_table("analysis_recommendations")
    op.drop_index(
        "ix_analysis_assistance_runs_latest",
        table_name="analysis_assistance_runs",
    )
    op.drop_table("analysis_assistance_runs")
    op.drop_index(
        "ix_analysis_assistance_jobs_claim",
        table_name="analysis_assistance_jobs",
    )
    op.drop_table("analysis_assistance_jobs")
    op.execute("DROP FUNCTION reject_analysis_assistance_result_mutation()")

    op.drop_index(
        "uq_analysis_clauses_section_scope",
        table_name="private_knowledge_source_clauses",
    )
    op.drop_index(
        "uq_analysis_facts_section_scope",
        table_name="private_knowledge_facts",
    )
    op.drop_index(
        "uq_analysis_coverages_enrollment_scope",
        table_name="private_knowledge_coverages",
    )
    op.drop_index(
        "uq_analysis_citations_scope",
        table_name="private_knowledge_fact_citations",
    )
    op.drop_index(
        "uq_analysis_decision_runs_scope",
        table_name="decision_runs",
    )
    op.drop_index(
        "uq_analysis_medical_events_scope",
        table_name="medical_events",
    )
