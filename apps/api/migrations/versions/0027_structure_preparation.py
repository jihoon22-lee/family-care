"""Record local preparation separately from provider and knowledge completion."""

from collections.abc import Sequence

from alembic import op

revision: str = "0027_structure_preparation"
down_revision: str | Sequence[str] | None = "0026_document_structure"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE document_structure_preparations (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          batch_item_id uuid NOT NULL REFERENCES document_batch_items(id) ON DELETE RESTRICT,
          extraction_id uuid NOT NULL REFERENCES extractions(id) ON DELETE RESTRICT,
          ocr_layer_id uuid REFERENCES ocr_layers(id) ON DELETE RESTRICT,
          pipeline_revision varchar(64) NOT NULL,
          generation_id uuid REFERENCES document_structure_generations(id) ON DELETE RESTRICT,
          state varchar(32) NOT NULL
            CHECK (state IN ('PREPARED','PARTIAL','FAILED','RETRYABLE_FAILED')),
          attempts integer NOT NULL CHECK (attempts BETWEEN 1 AND 3),
          error_code varchar(64)
            CHECK (error_code IN ('STRUCTURE_SOURCE_INVALID','STRUCTURE_PREPARATION_RETRY')),
          available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE NULLS NOT DISTINCT (batch_item_id, extraction_id, ocr_layer_id, pipeline_revision),
          CHECK ((state IN ('PREPARED','PARTIAL') AND generation_id IS NOT NULL
                    AND error_code IS NULL)
              OR (state IN ('FAILED','RETRYABLE_FAILED') AND generation_id IS NULL
                    AND error_code IS NOT NULL))
        );
        CREATE FUNCTION protect_document_structure_preparation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'document preparation history is immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND (OLD.state <> 'RETRYABLE_FAILED' OR
             (to_jsonb(OLD) - ARRAY['state','attempts','error_code','generation_id',
                                   'available_at','updated_at']) IS DISTINCT FROM
             (to_jsonb(NEW) - ARRAY['state','attempts','error_code','generation_id',
                                   'available_at','updated_at'])) THEN
            RAISE EXCEPTION 'document preparation identity is immutable' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM document_batch_items item
            JOIN document_versions version ON version.document_id = item.document_id
            JOIN extractions extraction ON extraction.document_version_id = version.id
            WHERE item.id = NEW.batch_item_id AND extraction.id = NEW.extraction_id
              AND extraction.status = 'succeeded'
              AND (NEW.ocr_layer_id IS NULL OR EXISTS (
                SELECT 1 FROM ocr_layers layer WHERE layer.id = NEW.ocr_layer_id
                  AND layer.extraction_id = extraction.id AND layer.status = 'succeeded'))
              AND (NEW.generation_id IS NULL OR EXISTS (
                SELECT 1 FROM document_structure_generations generation
                WHERE generation.id = NEW.generation_id AND generation.batch_item_id = item.id
                  AND generation.extraction_id = extraction.id))
          ) THEN
            RAISE EXCEPTION 'document preparation scope invalid' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER document_structure_preparation_guard
          BEFORE INSERT OR UPDATE OR DELETE ON document_structure_preparations
          FOR EACH ROW EXECUTE FUNCTION protect_document_structure_preparation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM document_structure_preparations) THEN
            RAISE EXCEPTION 'document preparation history must be retained';
          END IF;
        END $$;
        DROP TABLE document_structure_preparations;
        DROP FUNCTION protect_document_structure_preparation();
        """
    )
