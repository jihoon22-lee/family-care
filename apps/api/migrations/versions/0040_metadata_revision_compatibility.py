"""Keep old metadata proofs readable while revising cover recognition."""

from collections.abc import Sequence

from alembic import op

revision: str = "0040_metadata_revisions"
down_revision: str | Sequence[str] | None = "0039_component_terms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check CHECK(
            validator_revision IN ('document-metadata-api-v1','document-metadata-api-v2'));
        CREATE FUNCTION validate_metadata_revision_pair() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS(SELECT 1 FROM document_metadata_proposals p
            WHERE p.id=NEW.proposal_id AND NEW.validator_revision=
              replace(p.revision,'document-metadata-','document-metadata-api-')) THEN
            RAISE EXCEPTION 'metadata validator revision mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER metadata_revision_pair_guard BEFORE INSERT
          ON document_metadata_publications FOR EACH ROW
          EXECUTE FUNCTION validate_metadata_revision_pair();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_metadata_proposals WHERE revision='document-metadata-v2')
            OR EXISTS(SELECT 1 FROM document_metadata_publications
              WHERE validator_revision='document-metadata-api-v2') THEN
            RAISE EXCEPTION 'metadata revision history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        DROP TRIGGER metadata_revision_pair_guard ON document_metadata_publications;
        DROP FUNCTION validate_metadata_revision_pair();
        ALTER TABLE document_metadata_publications
          DROP CONSTRAINT document_metadata_publications_validator_revision_check,
          ADD CONSTRAINT document_metadata_publications_validator_revision_check
            CHECK(validator_revision='document-metadata-api-v1');
    """)
