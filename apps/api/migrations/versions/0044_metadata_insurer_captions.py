"""Preserve v3 history while allowing independently verified issuer captions."""

from collections.abc import Sequence

from alembic import op

revision: str = "0044_metadata_insurer_captions"
down_revision: str | Sequence[str] | None = "0043_metadata_body_revision"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2',
              'document-metadata-api-v3','document-metadata-api-v4'));
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_metadata_proposals WHERE revision='document-metadata-v4')
            OR EXISTS(SELECT 1 FROM document_metadata_publications
              WHERE validator_revision='document-metadata-api-v4') THEN
            RAISE EXCEPTION 'metadata revision history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2',
              'document-metadata-api-v3'));
    """)
