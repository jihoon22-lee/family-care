"""Keep the exact requested local event and source inventory immutable."""

from collections.abc import Sequence

from alembic import op

revision: str = "0060_guidance_review_inputs"
down_revision: str | Sequence[str] | None = "0059_guidance_review_budget"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE guidance_review_inputs (
        review_job_id UUID PRIMARY KEY REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT,
        sources_json JSONB NOT NULL CHECK(jsonb_typeof(sources_json)='object'
          AND octet_length(sources_json::text)<=600000),
        event_json JSONB NOT NULL CHECK(jsonb_typeof(event_json)='object'
          AND octet_length(event_json::text)<=65536),
        privacy_digest CHAR(64) NOT NULL CHECK(privacy_digest ~ '^[0-9a-f]{64}$')
      );
      CREATE FUNCTION protect_guidance_review_input() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'REVIEW_INPUT_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM guidance_review_jobs j WHERE j.id=NEW.review_job_id
          AND j.state='queued' AND j.http_attempts=0
          AND NEW.sources_json->>'schema_revision'='guidance-review-sources-v1'
          AND NEW.sources_json->>'household_space_id'=j.household_space_id::text
          AND NEW.sources_json->>'family_member_id'=j.family_member_id::text
          AND NEW.sources_json->>'medical_event_id'=j.medical_event_id::text
          AND NEW.sources_json->>'event_version'=j.event_version::text
          AND NEW.sources_json->>'digest_sha256'=j.source_digest
          AND NEW.event_json->>'id'=j.medical_event_id::text
          AND NEW.event_json->>'household_space_id'=j.household_space_id::text
          AND NEW.event_json->>'family_member_id'=j.family_member_id::text
          AND NEW.event_json->>'version'=j.event_version::text
          AND NEW.privacy_digest=terms_semantic_privacy_digest(j.household_space_id)) THEN
          RAISE EXCEPTION 'REVIEW_INPUT_SCOPE_INVALID' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guidance_review_input_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_inputs FOR EACH ROW EXECUTE FUNCTION protect_guidance_review_input();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM guidance_review_inputs) THEN
          RAISE EXCEPTION 'REVIEW_INPUT_REQUIRES_PRESERVATION';
        END IF;
      END $$;
      DROP TABLE guidance_review_inputs;
      DROP FUNCTION protect_guidance_review_input();
    """)
