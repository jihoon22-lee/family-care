"""Create a separate selective OCR provenance layer."""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_selective_ocr"
down_revision: str | Sequence[str] | None = "0012_encrypted_document_import"
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


def _foreign_uuid(name: str, target: str, *, ondelete: str) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        sa.ForeignKey(target, ondelete=ondelete),
        nullable=False,
    )


def _jsonb(name: str, *, default: str | None = None) -> sa.Column[Any]:
    return sa.Column(
        name,
        postgresql.JSONB(),
        nullable=False,
        server_default=sa.text(default) if default is not None else None,
    )


def upgrade() -> None:
    """Add metadata-only OCR results without changing native extraction rows."""

    op.create_table(
        "ocr_layers",
        _uuid("id", primary_key=True),
        _foreign_uuid("extraction_id", "extractions.id", ondelete="CASCADE"),
        sa.Column("source_layer", sa.String(length=16), nullable=False),
        sa.Column("engine_name", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("language_config_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("quality_rule_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        _jsonb("warning_codes", default="'[]'::jsonb"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "succeeded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.CheckConstraint("source_layer = 'ocr'", name="ck_ocr_layers_source_layer"),
        sa.CheckConstraint("engine_name = 'tesseract'", name="ck_ocr_layers_engine_name"),
        sa.CheckConstraint("btrim(engine_version) <> ''", name="ck_ocr_layers_engine_version"),
        sa.CheckConstraint(
            "language_config_hash ~ '^[0-9a-f]{64}$'",
            name="ck_ocr_layers_language_config_hash",
        ),
        sa.CheckConstraint(
            "quality_rule_version = 'quality-v1'",
            name="ck_ocr_layers_quality_rule_version",
        ),
        sa.CheckConstraint("status = 'succeeded'", name="ck_ocr_layers_status"),
    )
    op.create_index(
        "ix_ocr_layers_extraction",
        "ocr_layers",
        ["extraction_id", "created_at", "id"],
    )
    op.create_index(
        "uq_ocr_layers_succeeded_config",
        "ocr_layers",
        [
            "extraction_id",
            "engine_name",
            "engine_version",
            "language_config_hash",
            "quality_rule_version",
        ],
        unique=True,
        postgresql_where=sa.text("status = 'succeeded'"),
    )

    op.create_table(
        "ocr_pages",
        _uuid("id", primary_key=True),
        _foreign_uuid("ocr_layer_id", "ocr_layers.id", ondelete="CASCADE"),
        _foreign_uuid("document_version_id", "document_versions.id", ondelete="CASCADE"),
        sa.Column("content_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("rendered_dpi", sa.Integer(), nullable=False),
        sa.Column("image_width_pixels", sa.Integer(), nullable=False),
        sa.Column("image_height_pixels", sa.Integer(), nullable=False),
        sa.Column("selected_classification", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        _jsonb("warning_codes", default="'[]'::jsonb"),
        sa.UniqueConstraint("ocr_layer_id", "page_number", name="uq_ocr_pages_layer_page"),
        sa.CheckConstraint(
            "page_number >= 1 AND page_number <= 500", name="ck_ocr_pages_page_number"
        ),
        sa.CheckConstraint("rendered_dpi = 300", name="ck_ocr_pages_rendered_dpi"),
        sa.CheckConstraint(
            "image_width_pixels >= 1 AND image_width_pixels <= 20000",
            name="ck_ocr_pages_image_width",
        ),
        sa.CheckConstraint(
            "image_height_pixels >= 1 AND image_height_pixels <= 20000",
            name="ck_ocr_pages_image_height",
        ),
        sa.CheckConstraint(
            "selected_classification = 'OCR_REQUIRED'",
            name="ck_ocr_pages_selected_classification",
        ),
        sa.CheckConstraint("status IN ('completed', 'warning')", name="ck_ocr_pages_status"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_ocr_pages_content_sha256",
        ),
    )
    op.create_index("ix_ocr_pages_layer", "ocr_pages", ["ocr_layer_id", "page_number"])
    op.create_index(
        "ix_ocr_pages_evidence",
        "ocr_pages",
        ["document_version_id", "page_number"],
    )

    op.create_table(
        "ocr_blocks",
        _uuid("id", primary_key=True),
        _foreign_uuid("ocr_page_id", "ocr_pages.id", ondelete="CASCADE"),
        sa.Column("text", sa.Text(), nullable=False),
        _jsonb("bbox"),
        sa.Column("reading_order", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Numeric(6, 3), nullable=False),
        sa.Column("source_layer", sa.String(length=16), nullable=False),
        sa.Column("review_state", sa.String(length=16), nullable=False),
        sa.UniqueConstraint("ocr_page_id", "reading_order", name="uq_ocr_blocks_page_order"),
        sa.CheckConstraint("char_length(text) BETWEEN 1 AND 8192", name="ck_ocr_blocks_text"),
        sa.CheckConstraint(
            "reading_order >= 0 AND reading_order <= 9999",
            name="ck_ocr_blocks_reading_order",
        ),
        sa.CheckConstraint(
            "confidence >= 0 AND confidence <= 100",
            name="ck_ocr_blocks_confidence",
        ),
        sa.CheckConstraint("source_layer = 'ocr'", name="ck_ocr_blocks_source_layer"),
        sa.CheckConstraint("review_state = 'candidate'", name="ck_ocr_blocks_review_state"),
    )
    op.create_index("ix_ocr_blocks_page", "ocr_blocks", ["ocr_page_id", "reading_order"])

    op.add_column(
        "document_batch_items",
        sa.Column(
            "ocr_state",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'pending'"),
        ),
    )
    op.add_column(
        "document_batch_items",
        sa.Column(
            "ocr_pages_processed",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "document_batch_items",
        sa.Column(
            "ocr_warning_codes",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_check_constraint(
        "ck_document_batch_items_ocr_state",
        "document_batch_items",
        "ocr_state IN ('pending', 'native_only', 'running', 'completed', 'warning', 'failed')",
    )
    op.create_check_constraint(
        "ck_document_batch_items_ocr_pages_processed",
        "document_batch_items",
        "ocr_pages_processed >= 0 AND ocr_pages_processed <= 500",
    )


def downgrade() -> None:
    """Remove only the additive OCR layer and batch progress metadata."""

    op.drop_constraint(
        "ck_document_batch_items_ocr_pages_processed",
        "document_batch_items",
        type_="check",
    )
    op.drop_constraint(
        "ck_document_batch_items_ocr_state",
        "document_batch_items",
        type_="check",
    )
    op.drop_column("document_batch_items", "ocr_warning_codes")
    op.drop_column("document_batch_items", "ocr_pages_processed")
    op.drop_column("document_batch_items", "ocr_state")
    op.drop_index("ix_ocr_blocks_page", table_name="ocr_blocks")
    op.drop_table("ocr_blocks")
    op.drop_index("ix_ocr_pages_evidence", table_name="ocr_pages")
    op.drop_index("ix_ocr_pages_layer", table_name="ocr_pages")
    op.drop_table("ocr_pages")
    op.drop_index("uq_ocr_layers_succeeded_config", table_name="ocr_layers")
    op.drop_index("ix_ocr_layers_extraction", table_name="ocr_layers")
    op.drop_table("ocr_layers")
