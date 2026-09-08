"""Persist immutable local document structures and resumable source ranges."""

from collections.abc import Sequence

from alembic import op

revision: str = "0026_document_structure"
down_revision: str | Sequence[str] | None = "0025_local_guidance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_structure_generations (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          household_space_id uuid NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
          family_member_id uuid NOT NULL REFERENCES family_members(id) ON DELETE RESTRICT,
          batch_item_id uuid NOT NULL REFERENCES document_batch_items(id) ON DELETE RESTRICT,
          document_version_id uuid NOT NULL REFERENCES document_versions(id) ON DELETE RESTRICT,
          extraction_id uuid NOT NULL REFERENCES extractions(id) ON DELETE RESTRICT,
          identity_sha256 char(64) NOT NULL CHECK (identity_sha256 ~ '^[0-9a-f]{64}$'),
          structure_version varchar(64) NOT NULL,
          structure_json jsonb NOT NULL,
          plan_json jsonb NOT NULL,
          range_plan_complete boolean NOT NULL,
          is_current boolean NOT NULL DEFAULT false,
          cancelled boolean NOT NULL DEFAULT false,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (household_space_id, family_member_id, batch_item_id, identity_sha256),
          CHECK (jsonb_typeof(structure_json) = 'object' AND
                 jsonb_typeof(plan_json) = 'object' AND
                 octet_length(structure_json::text) <= 67108864 AND
                 octet_length(plan_json::text) <= 67108864),
          CHECK ((structure_json#>>'{lineage,document_version_id}' = document_version_id::text
              AND structure_json#>>'{lineage,extraction_id}' = extraction_id::text
              AND structure_json#>>'{lineage,structure_version}' = structure_version
              AND plan_json->'lineage' = structure_json->'lineage'
              AND plan_json->>'complete' = range_plan_complete::text) IS TRUE)
        );
        CREATE UNIQUE INDEX uq_current_document_structure
          ON document_structure_generations (household_space_id, family_member_id, batch_item_id)
          WHERE is_current;

        CREATE TABLE document_structure_chunks (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          generation_id uuid NOT NULL REFERENCES document_structure_generations(id)
            ON DELETE RESTRICT,
          chunk_key char(64) NOT NULL CHECK (chunk_key ~ '^[0-9a-f]{64}$'),
          position integer NOT NULL CHECK (position >= 0),
          chunk_json jsonb NOT NULL,
          state varchar(32) NOT NULL DEFAULT 'PENDING'
            CHECK (state IN ('PENDING','RUNNING','SUCCEEDED',
                            'RETRYABLE_FAILED','FAILED','CANCELLED')),
          attempts integer NOT NULL DEFAULT 0,
          max_attempts integer NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 5),
          available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          lease_owner varchar(128),
          lease_token uuid,
          lease_expires_at timestamptz,
          result_json jsonb,
          error_code varchar(64),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          completed_at timestamptz,
          UNIQUE (generation_id, chunk_key),
          UNIQUE (generation_id, position),
          CHECK (attempts BETWEEN 0 AND max_attempts),
          CHECK (jsonb_typeof(chunk_json) = 'object' AND octet_length(chunk_json::text) <= 32768),
          CHECK ((chunk_json->>'chunk_id' = chunk_key) IS TRUE),
          CHECK (error_code IS NULL OR error_code ~ '^[A-Z][A-Z0-9_]{0,63}$'),
          CHECK ((state = 'RUNNING' AND lease_owner IS NOT NULL AND lease_token IS NOT NULL
                                    AND lease_expires_at IS NOT NULL) OR
                 (state <> 'RUNNING' AND lease_owner IS NULL AND lease_token IS NULL
                                     AND lease_expires_at IS NULL)),
          CHECK ((state = 'SUCCEEDED' AND result_json IS NOT NULL AND completed_at IS NOT NULL)
              OR (state <> 'SUCCEEDED' AND result_json IS NULL)),
          CHECK (result_json IS NULL OR (jsonb_typeof(result_json) = 'object'
                                         AND octet_length(result_json::text) <= 65536))
        );
        CREATE INDEX ix_document_structure_chunk_queue
          ON document_structure_chunks (available_at, generation_id, position)
          WHERE state IN ('PENDING','RUNNING','RETRYABLE_FAILED');

        CREATE FUNCTION protect_document_structure_generation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'document structure history is immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND
             (to_jsonb(OLD) - ARRAY['is_current','cancelled','updated_at']) IS DISTINCT FROM
             (to_jsonb(NEW) - ARRAY['is_current','cancelled','updated_at']) THEN
            RAISE EXCEPTION 'document structure source is immutable' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM document_batch_items item
            JOIN document_batches batch ON batch.id = item.batch_id
            JOIN family_members member ON member.id = batch.family_member_id
            JOIN document_versions version ON version.document_id = item.document_id
            JOIN extractions extraction ON extraction.document_version_id = version.id
            WHERE item.id = NEW.batch_item_id
              AND batch.household_space_id = NEW.household_space_id
              AND batch.family_member_id = NEW.family_member_id
              AND member.household_space_id = NEW.household_space_id
              AND version.id = NEW.document_version_id AND extraction.id = NEW.extraction_id
              AND extraction.status = 'succeeded'
              AND (item.processed_document_version_id IS NULL
                   OR item.processed_document_version_id = NEW.document_version_id)
              AND version.content_sha256 = NEW.structure_json#>>'{lineage,content_sha256}'
          ) THEN
            RAISE EXCEPTION 'document structure scope mismatch' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_document_structure_generation
          BEFORE INSERT OR UPDATE OR DELETE ON document_structure_generations
          FOR EACH ROW EXECUTE FUNCTION protect_document_structure_generation();

        CREATE FUNCTION protect_document_structure_chunk() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'document range history is immutable' USING ERRCODE = '23514';
          END IF;
          IF OLD.generation_id IS DISTINCT FROM NEW.generation_id
             OR OLD.chunk_key IS DISTINCT FROM NEW.chunk_key
             OR OLD.position IS DISTINCT FROM NEW.position
             OR OLD.chunk_json IS DISTINCT FROM NEW.chunk_json
             OR OLD.id IS DISTINCT FROM NEW.id
             OR OLD.created_at IS DISTINCT FROM NEW.created_at
             OR (OLD.state = 'SUCCEEDED' AND to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW)) THEN
            RAISE EXCEPTION 'document range source and success are immutable'
              USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER trg_document_structure_chunk
          BEFORE UPDATE OR DELETE ON document_structure_chunks
          FOR EACH ROW EXECUTE FUNCTION protect_document_structure_chunk();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM document_structure_generations) THEN
            RAISE EXCEPTION 'cannot downgrade document structure with history';
          END IF;
        END $$;
        DROP TABLE document_structure_chunks;
        DROP TABLE document_structure_generations;
        DROP FUNCTION protect_document_structure_chunk();
        DROP FUNCTION protect_document_structure_generation();
        """
    )
