"""Allow independently verified body/range metadata without changing old proofs."""

from collections.abc import Sequence

from alembic import op

revision: str = "0043_metadata_body_revision"
down_revision: str | Sequence[str] | None = "0042_terms_applicability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2',
              'document-metadata-api-v3'));
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_metadata_proposals WHERE revision='document-metadata-v3')
            OR EXISTS(SELECT 1 FROM document_metadata_publications
              WHERE validator_revision='document-metadata-api-v3') THEN
            RAISE EXCEPTION 'metadata revision history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2'));
    """)
