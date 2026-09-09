"""Account for multi-document review calls without rewriting older reservations."""

from collections.abc import Sequence

from alembic import op

revision: str = "0059_guidance_review_budget"
down_revision: str | Sequence[str] | None = "0058_guidance_review_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE guidance_review_requests (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        review_job_id UUID NOT NULL REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT,
        phase VARCHAR(12) NOT NULL CHECK(phase IN ('discover','compare')),
        document_ids UUID[] NOT NULL,
        fingerprint CHAR(64) NOT NULL CHECK(fingerprint ~ '^[0-9a-f]{64}$'),
        input_token_bound INTEGER NOT NULL CHECK(input_token_bound BETWEEN 1 AND 32768),
        output_token_bound INTEGER NOT NULL CHECK(output_token_bound BETWEEN 1 AND 4000),
        state VARCHAR(12) NOT NULL DEFAULT 'RESERVED'
          CHECK(state IN ('RESERVED','SUCCEEDED','FAILED')),
        reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        settled_at TIMESTAMPTZ,
        usage_json JSONB,
        model VARCHAR(128) CHECK(model ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        service_tier VARCHAR(16) CHECK(service_tier IN
          ('auto','default','flex','scale','priority','fast','ultrafast')),
        UNIQUE(review_job_id,phase),
        CHECK((state='RESERVED')=(settled_at IS NULL)),
        CHECK(usage_json IS NULL OR (jsonb_typeof(usage_json)='object'
          AND octet_length(usage_json::text)<=1024)),
        CHECK(array_ndims(document_ids)=1 AND cardinality(document_ids) BETWEEN 1 AND 128
          AND array_position(document_ids,NULL) IS NULL)
      );
      CREATE INDEX ix_guidance_review_request_daily ON guidance_review_requests(reserved_at);
      CREATE INDEX ix_guidance_review_request_documents ON guidance_review_requests
        USING gin(document_ids);
      CREATE FUNCTION protect_guidance_review_request() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'REVIEW_ACCOUNTING_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.state<>'RESERVED' OR NEW.state='RESERVED' OR
          (to_jsonb(OLD)-ARRAY['state','settled_at','usage_json','model','service_tier'])
            IS DISTINCT FROM
          (to_jsonb(NEW)-ARRAY['state','settled_at','usage_json','model','service_tier'])) THEN
          RAISE EXCEPTION 'REVIEW_ACCOUNTING_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' AND (
          NEW.state<>'RESERVED' OR NEW.usage_json IS NOT NULL OR NEW.model IS NOT NULL
          OR NEW.service_tier IS NOT NULL OR
          NEW.document_ids IS DISTINCT FROM
            ARRAY(SELECT DISTINCT d FROM unnest(NEW.document_ids) d ORDER BY d) OR
          EXISTS(SELECT 1 FROM unnest(NEW.document_ids) x(id) WHERE NOT EXISTS(
            SELECT 1 FROM guidance_review_jobs j CROSS JOIN documents d
            JOIN document_versions v ON v.document_id=d.id
            WHERE j.id=NEW.review_job_id AND d.id=x.id AND d.deleted_at IS NULL
              AND (EXISTS(SELECT 1 FROM evidence e WHERE e.document_version_id=v.id
                AND e.household_space_id=j.household_space_id) OR
                EXISTS(SELECT 1 FROM terms_editions t WHERE t.document_version_id=v.id
                  AND t.household_space_id=j.household_space_id AND t.deleted_at IS NULL))))
        ) THEN
          RAISE EXCEPTION 'REVIEW_ACCOUNTING_SCOPE_INVALID' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guidance_review_request_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_requests FOR EACH ROW EXECUTE FUNCTION protect_guidance_review_request();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM guidance_review_requests) THEN
          RAISE EXCEPTION 'REVIEW_ACCOUNTING_REQUIRES_PRESERVATION';
        END IF;
      END $$;
      DROP TABLE guidance_review_requests;
      DROP FUNCTION protect_guidance_review_request();
    """)
