"""Separate provider proposals, locally verified publications and review snapshots."""

from collections.abc import Sequence

from alembic import op

revision: str = "0061_guidance_review_results"
down_revision: str | Sequence[str] | None = "0060_guidance_review_inputs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE guidance_review_proposals (
        review_job_id UUID PRIMARY KEY REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT,
        lease_token UUID NOT NULL,
        proposal_json JSONB NOT NULL CHECK(jsonb_typeof(proposal_json)='object'
          AND proposal_json->>'schema_revision'='guidance-review-proposals-v1'
          AND octet_length(proposal_json::text)<=600000),
        received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
      );
      CREATE TABLE guidance_review_publications (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        review_job_id UUID NOT NULL REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT,
        packet_id VARCHAR(256) NOT NULL,
        source_digest CHAR(64) NOT NULL CHECK(source_digest ~ '^[0-9a-f]{64}$'),
        graph_json JSONB NOT NULL CHECK(jsonb_typeof(graph_json)='object'
          AND octet_length(graph_json::text)<=600000),
        proof_sha256 CHAR(64) NOT NULL CHECK(proof_sha256 ~ '^[0-9a-f]{64}$'),
        compiled_json JSONB NOT NULL CHECK(jsonb_typeof(compiled_json)='object'
          AND octet_length(compiled_json::text)<=1200000),
        verifier_revision VARCHAR(128) NOT NULL,
        compiler_revision VARCHAR(128) NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(review_job_id,packet_id)
      );
      CREATE TABLE guidance_review_results (
        review_job_id UUID PRIMARY KEY REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT,
        result_json JSONB NOT NULL CHECK(jsonb_typeof(result_json)='object'
          AND octet_length(result_json::text)<=4000000),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
      );
      CREATE FUNCTION protect_guidance_review_result() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE j guidance_review_jobs;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'REVIEW_RESULT_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        SELECT * INTO j FROM guidance_review_jobs WHERE id=NEW.review_job_id;
        IF j.id IS NULL OR j.state<>'running' OR j.deadline_at<=clock_timestamp()
          OR j.lease_expires_at<=clock_timestamp() THEN
          RAISE EXCEPTION 'REVIEW_RESULT_LEASE_INVALID' USING ERRCODE='23514';
        END IF;
        IF TG_TABLE_NAME='guidance_review_proposals' THEN
          IF NEW.lease_token<>j.lease_token OR NOT EXISTS(
            SELECT 1 FROM guidance_review_requests r WHERE r.review_job_id=j.id
              AND r.state='SUCCEEDED') THEN
            RAISE EXCEPTION 'REVIEW_PROPOSAL_SCOPE_INVALID' USING ERRCODE='23514';
          END IF;
        ELSIF TG_TABLE_NAME='guidance_review_publications' THEN
          IF NEW.source_digest<>j.source_digest OR NOT EXISTS(
            SELECT 1 FROM guidance_review_inputs i,
              LATERAL jsonb_array_elements(i.sources_json->'packets') p
            WHERE i.review_job_id=j.id AND p->>'packet_id'=NEW.packet_id) THEN
            RAISE EXCEPTION 'REVIEW_PUBLICATION_SCOPE_INVALID' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guidance_review_proposal_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_proposals FOR EACH ROW EXECUTE FUNCTION protect_guidance_review_result();
      CREATE TRIGGER guidance_review_publication_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_publications FOR EACH ROW EXECUTE FUNCTION
        protect_guidance_review_result();
      CREATE TRIGGER guidance_review_result_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_results FOR EACH ROW EXECUTE FUNCTION protect_guidance_review_result();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM guidance_review_proposals)
          OR EXISTS(SELECT 1 FROM guidance_review_publications)
          OR EXISTS(SELECT 1 FROM guidance_review_results) THEN
          RAISE EXCEPTION 'REVIEW_RESULTS_REQUIRE_PRESERVATION';
        END IF;
      END $$;
      DROP TABLE guidance_review_results,guidance_review_publications,guidance_review_proposals;
      DROP FUNCTION protect_guidance_review_result();
    """)
