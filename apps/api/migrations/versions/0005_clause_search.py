"""Create household-scoped Terms and Clause search persistence.

Revision ID: 0005_clause_search
Revises: 0004_policy_candidate_review
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_clause_search"
down_revision: str | Sequence[str] | None = "0004_policy_candidate_review"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NORMALIZATION_VERSION = "unicode-nfc-v1"
_CLAUSE_TYPES = (
    "'chapter', 'section', 'article', 'paragraph', 'item', "
    "'special_terms', 'definition', 'appendix', 'table'"
)


def _uuid(name: str, *, primary_key: bool = False) -> sa.Column[Any]:
    return sa.Column(
        name,
        sa.UUID(as_uuid=True),
        primary_key=primary_key,
        nullable=False,
        server_default=sa.text("gen_random_uuid()") if primary_key else None,
    )


def _household_foreign_key() -> sa.Column[Any]:
    return sa.Column(
        "household_space_id",
        sa.UUID(as_uuid=True),
        sa.ForeignKey("household_spaces.id", ondelete="CASCADE"),
        nullable=False,
    )


def _version() -> sa.Column[Any]:
    return sa.Column(
        "version",
        sa.Integer(),
        nullable=False,
        server_default=sa.text("1"),
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
    """Create the additive Terms hierarchy and PostgreSQL search indexes."""

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    op.create_table(
        "terms_editions",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "document_version_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("document_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("insurer_display", sa.String(length=160), nullable=False),
        sa.Column("insurer_key", sa.String(length=160), nullable=False),
        sa.Column("product_display", sa.String(length=200), nullable=False),
        sa.Column("product_key", sa.String(length=200), nullable=False),
        sa.Column("applicability_start", sa.Date(), nullable=True),
        sa.Column("applicability_end", sa.Date(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint(
            "household_space_id",
            "document_version_id",
            "content_sha256",
            name="uq_terms_editions_household_document_content",
        ),
        sa.CheckConstraint(
            "insurer_display <> ''",
            name="ck_terms_editions_insurer_display_nonempty",
        ),
        sa.CheckConstraint(
            "insurer_key <> ''",
            name="ck_terms_editions_insurer_key_nonempty",
        ),
        sa.CheckConstraint(
            "product_display <> ''",
            name="ck_terms_editions_product_display_nonempty",
        ),
        sa.CheckConstraint(
            "product_key <> ''",
            name="ck_terms_editions_product_key_nonempty",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_terms_editions_content_sha256",
        ),
        sa.CheckConstraint(
            f"normalization_version = '{_NORMALIZATION_VERSION}'",
            name="ck_terms_editions_normalization_version",
        ),
        sa.CheckConstraint(
            "applicability_start IS NULL OR applicability_end IS NULL "
            "OR applicability_end >= applicability_start",
            name="ck_terms_editions_applicability_dates",
        ),
        sa.CheckConstraint("version >= 1", name="ck_terms_editions_version"),
    )
    op.create_index(
        "ix_terms_editions_household_applicability",
        "terms_editions",
        ["household_space_id", "applicability_start", "applicability_end", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_terms_editions_household_keys",
        "terms_editions",
        ["household_space_id", "insurer_key", "product_key", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "clauses",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column(
            "terms_edition_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("terms_editions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "parent_clause_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("clauses.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("clause_type", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("normalized_title", sa.Text(), nullable=False),
        sa.Column("normalized_text", sa.Text(), nullable=False),
        sa.Column(
            "search_vector",
            postgresql.TSVECTOR(),
            sa.Computed(
                "to_tsvector('simple', normalized_title || ' ' || normalized_text)",
                persisted=True,
            ),
            nullable=False,
        ),
        sa.Column("physical_page_start", sa.Integer(), nullable=False),
        sa.Column("physical_page_end", sa.Integer(), nullable=False),
        sa.Column("normalization_version", sa.String(length=32), nullable=False),
        _version(),
        _created_at(),
        _updated_at(),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"clause_type IN ({_CLAUSE_TYPES})",
            name="ck_clauses_type",
        ),
        sa.CheckConstraint("label <> ''", name="ck_clauses_label_nonempty"),
        sa.CheckConstraint(
            "normalized_title <> ''",
            name="ck_clauses_normalized_title_nonempty",
        ),
        sa.CheckConstraint(
            "normalized_text <> ''",
            name="ck_clauses_normalized_text_nonempty",
        ),
        sa.CheckConstraint(
            "physical_page_start >= 1",
            name="ck_clauses_physical_page_start",
        ),
        sa.CheckConstraint(
            "physical_page_end >= physical_page_start",
            name="ck_clauses_physical_page_range",
        ),
        sa.CheckConstraint(
            f"normalization_version = '{_NORMALIZATION_VERSION}'",
            name="ck_clauses_normalization_version",
        ),
        sa.CheckConstraint("version >= 1", name="ck_clauses_version"),
    )
    op.create_index(
        "ix_clauses_search_vector",
        "clauses",
        ["search_vector"],
        unique=False,
        postgresql_using="gin",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_clauses_normalized_title_trgm",
        "clauses",
        ["normalized_title"],
        unique=False,
        postgresql_using="gin",
        postgresql_ops={"normalized_title": "gin_trgm_ops"},
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_clauses_household_edition_page",
        "clauses",
        ["household_space_id", "terms_edition_id", "physical_page_start", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_clauses_edition_parent",
        "clauses",
        ["terms_edition_id", "parent_clause_id", "physical_page_start", "id"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "clause_evidence",
        sa.Column(
            "clause_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("clauses.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "evidence_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
    )
    op.create_index(
        "ix_clause_evidence_evidence_id",
        "clause_evidence",
        ["evidence_id", "clause_id"],
        unique=False,
    )

    op.create_table(
        "clause_search_synonyms",
        _uuid("id", primary_key=True),
        _household_foreign_key(),
        sa.Column("synonym_key", sa.String(length=160), nullable=False),
        sa.Column("replacement_text", sa.String(length=320), nullable=False),
        sa.Column("dictionary_version", sa.String(length=32), nullable=False),
        _created_at(),
        sa.Column("created_by", sa.String(length=32), nullable=False),
        sa.UniqueConstraint(
            "household_space_id",
            "synonym_key",
            "dictionary_version",
            name="uq_clause_search_synonyms_household_key_version",
        ),
        sa.CheckConstraint(
            "synonym_key <> ''",
            name="ck_clause_search_synonyms_key_nonempty",
        ),
        sa.CheckConstraint(
            "replacement_text <> ''",
            name="ck_clause_search_synonyms_replacement_nonempty",
        ),
        sa.CheckConstraint(
            "dictionary_version <> ''",
            name="ck_clause_search_synonyms_dictionary_version_nonempty",
        ),
        sa.CheckConstraint(
            "created_by IN ('system', 'admin')",
            name="ck_clause_search_synonyms_created_by",
        ),
    )
    op.create_index(
        "ix_clause_search_synonyms_household_dictionary",
        "clause_search_synonyms",
        ["household_space_id", "dictionary_version", "synonym_key"],
        unique=False,
    )


def downgrade() -> None:
    """Drop only Clause search-owned objects; pg_trgm may be shared."""

    op.drop_index(
        "ix_clause_evidence_evidence_id",
        table_name="clause_evidence",
    )
    op.drop_table("clause_evidence")

    op.drop_index(
        "ix_clause_search_synonyms_household_dictionary",
        table_name="clause_search_synonyms",
    )
    op.drop_table("clause_search_synonyms")

    op.drop_index("ix_clauses_edition_parent", table_name="clauses")
    op.drop_index("ix_clauses_household_edition_page", table_name="clauses")
    op.drop_index("ix_clauses_normalized_title_trgm", table_name="clauses")
    op.drop_index("ix_clauses_search_vector", table_name="clauses")
    op.drop_table("clauses")

    op.drop_index("ix_terms_editions_household_keys", table_name="terms_editions")
    op.drop_index(
        "ix_terms_editions_household_applicability",
        table_name="terms_editions",
    )
    op.drop_table("terms_editions")
