"""Retain provider stages and account for every reserved network attempt."""

from collections.abc import Sequence

from alembic import op

revision: str = "0028_policy_request_budget"
down_revision: str | Sequence[str] | None = "0027_structure_preparation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE policy_provider_requests (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          job_id uuid NOT NULL REFERENCES policy_structuring_jobs(id) ON DELETE RESTRICT,
          document_id uuid NOT NULL REFERENCES documents(id) ON DELETE RESTRICT,
          fingerprint char(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
          state varchar(16) NOT NULL CHECK (state IN ('RESERVED','SUCCEEDED','FAILED')),
          reserved_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          expires_at timestamptz NOT NULL DEFAULT clock_timestamp() + interval '180 seconds',
          response_json jsonb,
          request_id varchar(128),
          CHECK ((state = 'SUCCEEDED' AND response_json IS NOT NULL AND request_id IS NOT NULL)
             OR (state <> 'SUCCEEDED' AND response_json IS NULL AND request_id IS NULL)),
          CHECK (response_json IS NULL OR (jsonb_typeof(response_json) = 'object'
                  AND octet_length(response_json::text) <= 524288))
        );
        CREATE UNIQUE INDEX uq_policy_provider_active_request
          ON policy_provider_requests(job_id, fingerprint) WHERE state = 'RESERVED';
        CREATE UNIQUE INDEX uq_policy_provider_cached_request
          ON policy_provider_requests(job_id, fingerprint) WHERE state = 'SUCCEEDED';
        CREATE INDEX ix_policy_provider_daily_budget ON policy_provider_requests(reserved_at);
        CREATE INDEX ix_policy_provider_document_budget ON policy_provider_requests(document_id);
        CREATE FUNCTION protect_policy_provider_request() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'provider request accounting is immutable' USING ERRCODE = '23514';
          END IF;
          IF TG_OP = 'UPDATE' AND (OLD.state <> 'RESERVED' OR NEW.state = 'RESERVED' OR
              (to_jsonb(OLD) - ARRAY['state','response_json','request_id']) IS DISTINCT FROM
              (to_jsonb(NEW) - ARRAY['state','response_json','request_id'])) THEN
            RAISE EXCEPTION 'provider request identity is immutable' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM policy_structuring_jobs j
            JOIN document_versions v ON v.id = j.document_version_id
            WHERE j.id = NEW.job_id AND v.document_id = NEW.document_id
          ) THEN
            RAISE EXCEPTION 'provider request scope invalid' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER policy_provider_request_guard BEFORE INSERT OR UPDATE OR DELETE
          ON policy_provider_requests FOR EACH ROW
          EXECUTE FUNCTION protect_policy_provider_request();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM policy_provider_requests) THEN
            RAISE EXCEPTION 'provider request accounting must be retained';
          END IF;
        END $$;
        DROP TABLE policy_provider_requests;
        DROP FUNCTION protect_policy_provider_request();
        """
    )
