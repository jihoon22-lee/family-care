"""Retain independent local component metadata without changing source IR."""

from collections.abc import Sequence

from alembic import op

revision: str = "0037_document_metadata_proposals"
down_revision: str | Sequence[str] | None = "0036_structure_page_storage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE document_metadata_proposals (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          generation_id UUID NOT NULL
            REFERENCES document_structure_generations(id) ON DELETE RESTRICT,
          revision VARCHAR(64) NOT NULL,
          state VARCHAR(32) NOT NULL CHECK (state IN ('PREPARED','RETRYABLE_FAILED','FAILED')),
          attempts INTEGER NOT NULL CHECK (attempts BETWEEN 1 AND 3),
          proposal_json JSONB,
          error_code VARCHAR(64),
          available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(generation_id,revision),
          CHECK (revision ~ '^[a-z][a-z0-9-]{0,63}$'),
          CHECK (error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
          CHECK ((state='PREPARED' AND proposal_json IS NOT NULL AND error_code IS NULL)
            OR (state<>'PREPARED' AND proposal_json IS NULL AND error_code IS NOT NULL)),
          CHECK (state<>'RETRYABLE_FAILED' OR attempts<3),
          CHECK (proposal_json IS NULL OR octet_length(proposal_json::text)<=8388608)
        );
        CREATE FUNCTION protect_document_metadata_proposal() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source_identity TEXT;
        BEGIN
          IF TG_OP='DELETE' THEN
            RAISE EXCEPTION 'document metadata history is immutable' USING ERRCODE='23514';
          END IF;
          IF TG_OP='UPDATE' AND (
            OLD.state<>'RETRYABLE_FAILED' OR NEW.id<>OLD.id
            OR NEW.generation_id<>OLD.generation_id OR NEW.revision<>OLD.revision
            OR NEW.created_at<>OLD.created_at
            OR NEW.attempts NOT IN (OLD.attempts,OLD.attempts+1)
            OR (NEW.attempts=OLD.attempts AND (
              NEW.state<>OLD.state OR NEW.proposal_json IS DISTINCT FROM OLD.proposal_json
              OR NEW.error_code IS DISTINCT FROM OLD.error_code))
          ) THEN
            RAISE EXCEPTION 'document metadata history is immutable' USING ERRCODE='23514';
          END IF;
          SELECT identity_sha256 INTO source_identity FROM document_structure_generations
            WHERE id=NEW.generation_id;
          IF NEW.state='PREPARED' AND (
            source_identity IS NULL
            OR NEW.proposal_json->>'schema_version' IS DISTINCT FROM '1'
            OR NEW.proposal_json->>'revision' IS DISTINCT FROM NEW.revision
            OR NEW.proposal_json->>'generation_id' IS DISTINCT FROM NEW.generation_id::text
            OR NEW.proposal_json->>'structure_identity_sha256' IS DISTINCT FROM source_identity
            OR jsonb_typeof(NEW.proposal_json->'components') IS DISTINCT FROM 'array'
            OR jsonb_typeof(NEW.proposal_json->'unresolved_pages') IS DISTINCT FROM 'array'
          ) THEN
            RAISE EXCEPTION 'document metadata source mismatch' USING ERRCODE='23514';
          END IF;
          IF NEW.state='PREPARED' THEN
            IF jsonb_array_length(NEW.proposal_json->'components')>500 OR
              jsonb_array_length(NEW.proposal_json->'unresolved_pages')>500 OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(NEW.proposal_json->'components') component
                WHERE (jsonb_typeof(component)='object' AND
                  jsonb_typeof(component->'identity')='string' AND
                  component->>'identity' ~ '^[0-9a-f]{64}$') IS NOT TRUE
              ) OR (SELECT count(DISTINCT component->>'identity')
                FROM jsonb_array_elements(NEW.proposal_json->'components') component)
                <>jsonb_array_length(NEW.proposal_json->'components') THEN
              RAISE EXCEPTION 'document metadata identity invalid' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER document_metadata_proposal_guard BEFORE INSERT OR UPDATE OR DELETE
          ON document_metadata_proposals FOR EACH ROW
          EXECUTE FUNCTION protect_document_metadata_proposal();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_metadata_proposals) THEN
            RAISE EXCEPTION 'document metadata history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        DROP TABLE document_metadata_proposals;
        DROP FUNCTION protect_document_metadata_proposal();
    """)
