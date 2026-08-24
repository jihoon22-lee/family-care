"""Create the Phase 1 document-ingestion physical model.

Revision ID: 0002_document_ingestion
Revises: 0001_foundation
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_document_ingestion"
down_revision: str | Sequence[str] | None = "0001_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DOCUMENT_KINDS = (
    "policy",
    "terms",
    "application",
    "amendment",
    "claim",
    "supporting",
)
_ANALYSIS_JOB_STATES = (
    "queued",
    "running",
    "succeeded",
    "retryable_failed",
    "permanently_failed",
    "cancelled",
)
_PAGE_CLASSIFICATIONS = ("TEXT_SUFFICIENT", "OCR_REQUIRED")
_DOCUMENT_STATUSES = ("pending", "ready", "failed")
_EXTRACTION_STATUSES = ("running", "succeeded", "failed")
_REVIEW_STATES = ("candidate", "confirmed", "rejected")


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
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


def _jsonb(name: str, *, nullable: bool = False, default: str | None = None) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(),
        nullable=nullable,
        server_default=sa.text(default) if default is not None else None,
    )


def upgrade() -> None:
    """Create all and only the Phase 1 ingestion tables."""

    op.create_table(
        "documents",
        _uuid("id", primary_key=True),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        sa.Column("document_kind", sa.String(length=32), nullable=False),
        sa.Column("media_type", sa.String(length=255), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "document_kind IN ("
            "'policy', 'terms', 'application', 'amendment', 'claim', 'supporting'"
            ")",
            name="ck_documents_document_kind",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ready', 'failed')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint("source_key <> ''", name="ck_documents_source_key_nonempty"),
        sa.CheckConstraint("byte_size IS NULL OR byte_size >= 0", name="ck_documents_byte_size"),
        sa.CheckConstraint("page_count IS NULL OR page_count >= 0", name="ck_documents_page_count"),
    )
    op.create_index(
        "uq_documents_active_source_key",
        "documents",
        ["source_key"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "document_versions",
        _uuid("id", primary_key=True),
        sa.Column(
            "document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.BigInteger(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        _created_at(),
        sa.UniqueConstraint(
            "document_id",
            "version_number",
            name="uq_document_versions_document_version_number",
        ),
        sa.UniqueConstraint(
            "document_id",
            "content_sha256",
            name="uq_document_versions_document_content_sha256",
        ),
        sa.CheckConstraint("version_number >= 1", name="ck_document_versions_version_number"),
        sa.CheckConstraint("byte_size >= 0", name="ck_document_versions_byte_size"),
        sa.CheckConstraint("page_count >= 1", name="ck_document_versions_page_count"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_document_versions_content_sha256",
        ),
    )
    op.create_index(
        "ix_document_versions_document_id",
        "document_versions",
        ["document_id"],
        unique=False,
    )

    op.create_table(
        "extractions",
        _uuid("id", primary_key=True),
        sa.Column(
            "document_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("extractor_name", sa.String(length=128), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("extractor_config_hash", sa.String(length=64), nullable=False),
        sa.Column("quality_rule_version", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'running'"),
        ),
        sa.Column("succeeded_at", sa.DateTime(timezone=True), nullable=True),
        _created_at(),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="ck_extractions_status",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND succeeded_at IS NOT NULL) OR "
            "(status <> 'succeeded' AND succeeded_at IS NULL)",
            name="ck_extractions_succeeded_at",
        ),
        sa.CheckConstraint(
            "extractor_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_extractions_extractor_config_hash",
        ),
        sa.CheckConstraint(
            "quality_rule_version <> ''",
            name="ck_extractions_quality_rule_version",
        ),
    )
    op.create_index(
        "ix_extractions_document_version_id",
        "extractions",
        ["document_version_id"],
        unique=False,
    )
    op.create_index(
        "uq_extractions_succeeded_config",
        "extractions",
        ["document_version_id", "extractor_config_hash"],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "extraction_pages",
        _uuid("id", primary_key=True),
        sa.Column(
            "extraction_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("extractions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("width_points", sa.Numeric(10, 3), nullable=False),
        sa.Column("height_points", sa.Numeric(10, 3), nullable=False),
        sa.Column("non_whitespace_chars", sa.Integer(), nullable=False),
        sa.Column("alphanumeric_ratio", sa.Numeric(6, 5), nullable=False),
        sa.Column("replacement_character_ratio", sa.Numeric(6, 5), nullable=False),
        sa.Column("maximum_repeated_character_run", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=32), nullable=False),
        _jsonb("warning_codes", default="'[]'::jsonb"),
        sa.UniqueConstraint(
            "extraction_id",
            "page_number",
            name="uq_extraction_pages_extraction_page_number",
        ),
        sa.CheckConstraint("page_number >= 1", name="ck_extraction_pages_page_number"),
        sa.CheckConstraint("width_points > 0", name="ck_extraction_pages_width_points"),
        sa.CheckConstraint("height_points > 0", name="ck_extraction_pages_height_points"),
        sa.CheckConstraint(
            "non_whitespace_chars >= 0",
            name="ck_extraction_pages_non_whitespace_chars",
        ),
        sa.CheckConstraint(
            "alphanumeric_ratio >= 0 AND alphanumeric_ratio <= 1",
            name="ck_extraction_pages_alphanumeric_ratio",
        ),
        sa.CheckConstraint(
            "replacement_character_ratio >= 0 AND replacement_character_ratio <= 1",
            name="ck_extraction_pages_replacement_character_ratio",
        ),
        sa.CheckConstraint(
            "maximum_repeated_character_run >= 0",
            name="ck_extraction_pages_maximum_repeated_character_run",
        ),
        sa.CheckConstraint(
            "classification IN ('TEXT_SUFFICIENT', 'OCR_REQUIRED')",
            name="ck_extraction_pages_classification",
        ),
    )
    op.create_index(
        "ix_extraction_pages_extraction_id",
        "extraction_pages",
        ["extraction_id"],
        unique=False,
    )

    op.create_table(
        "extraction_blocks",
        _uuid("id", primary_key=True),
        sa.Column(
            "page_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("extraction_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("text", sa.Text(), nullable=False),
        _jsonb("bbox"),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "page_id",
            "reading_order",
            name="uq_extraction_blocks_page_reading_order",
        ),
        sa.CheckConstraint("reading_order >= 0", name="ck_extraction_blocks_reading_order"),
    )
    op.create_index(
        "ix_extraction_blocks_page_id",
        "extraction_blocks",
        ["page_id"],
        unique=False,
    )

    op.create_table(
        "extraction_tables",
        _uuid("id", primary_key=True),
        sa.Column(
            "page_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("extraction_pages.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _jsonb("bbox"),
        _jsonb("metadata_json"),
        sa.Column(
            "review_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'candidate'"),
        ),
        sa.CheckConstraint(
            "review_state IN ('candidate', 'confirmed', 'rejected')",
            name="ck_extraction_tables_review_state",
        ),
    )
    op.create_index(
        "ix_extraction_tables_page_id",
        "extraction_tables",
        ["page_id"],
        unique=False,
    )

    op.create_table(
        "extraction_cells",
        _uuid("id", primary_key=True),
        sa.Column(
            "table_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("extraction_tables.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("column_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        _jsonb("bbox"),
        sa.Column(
            "review_state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'candidate'"),
        ),
        sa.UniqueConstraint(
            "table_id",
            "row_index",
            "column_index",
            name="uq_extraction_cells_table_coordinates",
        ),
        sa.CheckConstraint("row_index >= 0", name="ck_extraction_cells_row_index"),
        sa.CheckConstraint("column_index >= 0", name="ck_extraction_cells_column_index"),
        sa.CheckConstraint(
            "review_state IN ('candidate', 'confirmed', 'rejected')",
            name="ck_extraction_cells_review_state",
        ),
    )
    op.create_index(
        "ix_extraction_cells_table_id",
        "extraction_cells",
        ["table_id"],
        unique=False,
    )

    op.create_table(
        "analysis_jobs",
        _uuid("id", primary_key=True),
        sa.Column(
            "document_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_key", sa.String(length=512), nullable=False),
        _jsonb("settings_json"),
        sa.Column("extractor_config_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "state",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'queued'"),
        ),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default=sa.text("3")),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        _created_at(),
        _updated_at(),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'retryable_failed', "
            "'permanently_failed', 'cancelled')",
            name="ck_analysis_jobs_state",
        ),
        sa.CheckConstraint("source_key <> ''", name="ck_analysis_jobs_source_key_nonempty"),
        sa.CheckConstraint("attempts >= 0", name="ck_analysis_jobs_attempts"),
        sa.CheckConstraint("max_attempts > 0", name="ck_analysis_jobs_max_attempts"),
        sa.CheckConstraint(
            "attempts <= max_attempts",
            name="ck_analysis_jobs_attempt_limit",
        ),
        sa.CheckConstraint(
            "error_code IS NULL OR error_code IN ("
            "'ANALYSIS_JOB_NOT_FOUND', 'DOCUMENT_NOT_FOUND', 'DOCUMENT_PATH_ESCAPE', "
            "'DOCUMENT_TOO_LARGE', 'EXTRACTION_TIMEOUT', 'INVALID_REQUEST', "
            "'PAGE_LIMIT_EXCEEDED', 'PASSWORD_INVALID', 'PASSWORD_REQUIRED', "
            "'PDF_CORRUPT', 'RESOURCE_LIMIT_EXCEEDED', 'TEMP_CLEANUP_FAILED', "
            "'UNSUPPORTED_FILE_TYPE')",
            name="ck_analysis_jobs_error_code",
        ),
        sa.CheckConstraint(
            "extractor_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_analysis_jobs_extractor_config_hash",
        ),
    )
    op.create_index(
        "ix_analysis_jobs_document_id",
        "analysis_jobs",
        ["document_id"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_jobs_claim",
        "analysis_jobs",
        ["state", "available_at"],
        unique=False,
    )
    op.create_index(
        "ix_analysis_jobs_lease_expiry",
        "analysis_jobs",
        ["state", "lease_expires_at"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the Phase 1 ingestion model in reverse dependency order."""

    op.drop_index("ix_analysis_jobs_lease_expiry", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_claim", table_name="analysis_jobs")
    op.drop_index("ix_analysis_jobs_document_id", table_name="analysis_jobs")
    op.drop_index("ix_extraction_cells_table_id", table_name="extraction_cells")
    op.drop_index("ix_extraction_tables_page_id", table_name="extraction_tables")
    op.drop_index("ix_extraction_blocks_page_id", table_name="extraction_blocks")
    op.drop_index("ix_extraction_pages_extraction_id", table_name="extraction_pages")
    op.drop_index("uq_extractions_succeeded_config", table_name="extractions")
    op.drop_index("ix_extractions_document_version_id", table_name="extractions")
    op.drop_index("ix_document_versions_document_id", table_name="document_versions")
    op.drop_index("uq_documents_active_source_key", table_name="documents")
    op.drop_table("analysis_jobs")
    op.drop_table("extraction_cells")
    op.drop_table("extraction_blocks")
    op.drop_table("extraction_tables")
    op.drop_table("extraction_pages")
    op.drop_table("extractions")
    op.drop_table("document_versions")
    op.drop_table("documents")
