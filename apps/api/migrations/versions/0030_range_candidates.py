"""Bind review candidates to independently retained source ranges."""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_range_candidates"
down_revision: str | Sequence[str] | None = "0029_policy_ranges"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE document_policy_range_plans ADD COLUMN associations_json jsonb NOT NULL
          DEFAULT '{}' CHECK (jsonb_typeof(associations_json) = 'object'
                              AND octet_length(associations_json::text) <= 16777216);
        CREATE TABLE policy_range_candidate_sources (
          candidate_version_id uuid PRIMARY KEY
            REFERENCES analysis_candidate_versions(id) ON DELETE RESTRICT,
          job_id uuid NOT NULL,
          envelope_id char(64) NOT NULL,
          provider_candidate_id uuid NOT NULL,
          source_refs jsonb NOT NULL CHECK (jsonb_typeof(source_refs) = 'array'
            AND jsonb_array_length(source_refs) BETWEEN 1 AND 64
            AND octet_length(source_refs::text) <= 65536),
          association_json jsonb NOT NULL CHECK (jsonb_typeof(association_json) = 'object'
            AND octet_length(association_json::text) <= 16384),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          FOREIGN KEY(job_id, envelope_id)
            REFERENCES document_policy_ranges(job_id, envelope_id) ON DELETE RESTRICT,
          UNIQUE(job_id, envelope_id, provider_candidate_id)
        );
        CREATE FUNCTION protect_range_candidate_source() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'range candidate provenance is immutable' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM analysis_candidate_versions c
            JOIN policy_structuring_jobs j ON j.id = NEW.job_id
            JOIN document_policy_ranges r ON r.job_id = j.id AND r.envelope_id = NEW.envelope_id
            WHERE c.id = NEW.candidate_version_id AND c.structuring_job_id = j.id
              AND c.household_space_id = j.household_space_id
              AND r.state IN ('COMPLETE', 'REVIEW')
          ) THEN
            RAISE EXCEPTION 'range candidate scope invalid' USING ERRCODE = '23514';
          END IF;
          IF EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.source_refs) AS ref
            WHERE NOT EXISTS (
              SELECT 1 FROM document_policy_ranges r
              JOIN policy_structuring_jobs j ON j.id = r.job_id
              JOIN evidence e ON e.id::text = ref->>'evidence_id'
              CROSS JOIN LATERAL jsonb_array_elements(r.envelope_json->'evidence') AS item
              WHERE r.job_id = NEW.job_id AND r.envelope_id = NEW.envelope_id
                AND item->>'evidence_id' = ref->>'evidence_id'
                AND item->>'node_id' = ref->>'node_id'
                AND item->'start' = ref->'start' AND item->'end' = ref->'end'
                AND item->'source_role' = ref->'source_role'
                AND item->'primary' = ref->'primary'
                AND item->'page' = ref->'page'
                AND e.document_version_id = j.document_version_id
                AND e.extraction_id = j.extraction_id
                AND e.household_space_id = j.household_space_id
                AND e.physical_page::text = ref->>'page'
                AND e.document_version_id::text = ref->>'document_version_id'
                AND e.extraction_id::text = ref->>'extraction_id'
            )
          ) THEN
            RAISE EXCEPTION 'range candidate source invalid' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER range_candidate_source_guard BEFORE INSERT OR UPDATE OR DELETE
          ON policy_range_candidate_sources FOR EACH ROW
          EXECUTE FUNCTION protect_range_candidate_source();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM policy_range_candidate_sources) THEN
            RAISE EXCEPTION 'range candidate history must be retained';
          END IF;
        END $$;
        DROP TABLE policy_range_candidate_sources;
        DROP FUNCTION protect_range_candidate_source();
        ALTER TABLE document_policy_range_plans DROP COLUMN IF EXISTS associations_json;
    """)
