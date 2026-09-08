"""Preserve metadata history while distinguishing complete reference instructions."""

from collections.abc import Sequence

from alembic import op

revision: str = "0050_metadata_instructions"
down_revision: str | Sequence[str] | None = "0049_metadata_navigation_pages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2',
              'document-metadata-api-v3','document-metadata-api-v4','document-metadata-api-v5','document-metadata-api-v6'));
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_metadata_proposals WHERE revision='document-metadata-v6')
            OR EXISTS(SELECT 1 FROM document_metadata_publications
              WHERE validator_revision='document-metadata-api-v6') THEN
            RAISE EXCEPTION 'metadata revision history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2',
              'document-metadata-api-v3','document-metadata-api-v4','document-metadata-api-v5'));
    """)
