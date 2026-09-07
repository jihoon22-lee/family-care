"""Retain minimized analysis envelopes and their independently completed results."""

from collections.abc import Sequence

from alembic import op

revision: str = "0029_policy_ranges"
down_revision: str | Sequence[str] | None = "0028_policy_request_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE document_policy_range_plans (
          job_id uuid PRIMARY KEY REFERENCES policy_structuring_jobs(id) ON DELETE RESTRICT,
          generation_id uuid NOT NULL
            REFERENCES document_structure_generations(id) ON DELETE RESTRICT,
          privacy_fingerprint char(64) NOT NULL CHECK (privacy_fingerprint ~ '^[0-9a-f]{64}$'),
          state varchar(16) NOT NULL CHECK (state IN ('PROCESSING','COMPLETE','PARTIAL')),
          unprocessed_json jsonb NOT NULL CHECK (jsonb_typeof(unprocessed_json) = 'array'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(job_id, generation_id)
        );
        CREATE TABLE document_policy_ranges (
          job_id uuid NOT NULL,
          generation_id uuid NOT NULL,
          envelope_id char(64) NOT NULL CHECK (envelope_id ~ '^[0-9a-f]{64}$'),
          position integer NOT NULL CHECK (position BETWEEN 0 AND 4095),
          envelope_json jsonb NOT NULL CHECK (jsonb_typeof(envelope_json) = 'object'
                                            AND octet_length(envelope_json::text) <= 131072),
          state varchar(16) NOT NULL CHECK (state IN ('PENDING','COMPLETE','REVIEW')),
          result_json jsonb CHECK (jsonb_typeof(result_json) = 'object'
                                  AND octet_length(result_json::text) <= 1048576),
          PRIMARY KEY(job_id, envelope_id),
          UNIQUE(job_id, position),
          FOREIGN KEY(job_id, generation_id)
            REFERENCES document_policy_range_plans(job_id, generation_id) ON DELETE RESTRICT,
          CHECK ((state = 'PENDING') = (result_json IS NULL))
        );
        CREATE FUNCTION protect_policy_range_plan() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'policy range plan is retained' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND (OLD.state <> 'PROCESSING' OR NEW.state = 'PROCESSING'
              OR (to_jsonb(OLD) - 'state') IS DISTINCT FROM (to_jsonb(NEW) - 'state')) THEN
            RAISE EXCEPTION 'policy range plan identity is immutable' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM policy_structuring_jobs j
            JOIN document_structure_generations g ON g.id = NEW.generation_id
            WHERE j.id = NEW.job_id AND j.household_space_id = g.household_space_id
              AND j.family_member_id = g.family_member_id AND j.batch_item_id = g.batch_item_id
              AND j.document_version_id = g.document_version_id
              AND j.extraction_id = g.extraction_id
          ) THEN
            RAISE EXCEPTION 'policy range plan scope invalid' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER policy_range_plan_guard BEFORE INSERT OR UPDATE OR DELETE
          ON document_policy_range_plans FOR EACH ROW EXECUTE FUNCTION protect_policy_range_plan();
        CREATE FUNCTION protect_policy_range() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'policy range is retained' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND (OLD.state <> 'PENDING' OR NEW.state = 'PENDING'
              OR (to_jsonb(OLD) - ARRAY['state','result_json']) IS DISTINCT FROM
                 (to_jsonb(NEW) - ARRAY['state','result_json'])) THEN
            RAISE EXCEPTION 'policy range identity is immutable' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER policy_range_guard BEFORE UPDATE OR DELETE
          ON document_policy_ranges FOR EACH ROW EXECUTE FUNCTION protect_policy_range();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM document_policy_range_plans) THEN
            RAISE EXCEPTION 'policy range history must be retained';
          END IF;
        END $$;
        DROP TABLE document_policy_ranges;
        DROP TABLE document_policy_range_plans;
        DROP FUNCTION protect_policy_range();
        DROP FUNCTION protect_policy_range_plan();
    """)
