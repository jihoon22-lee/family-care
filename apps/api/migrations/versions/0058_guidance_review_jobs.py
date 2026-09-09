"""Explicit review identities preserve the original local decision run."""

from collections.abc import Sequence

from alembic import op

revision: str = "0058_guidance_review_jobs"
down_revision: str | Sequence[str] | None = "0057_guidance_claim_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      CREATE TABLE guidance_review_jobs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        family_member_id UUID NOT NULL REFERENCES family_members(id) ON DELETE RESTRICT,
        medical_event_id UUID NOT NULL REFERENCES medical_events(id) ON DELETE RESTRICT,
        decision_run_id UUID NOT NULL REFERENCES decision_runs(id) ON DELETE RESTRICT,
        event_version INTEGER NOT NULL CHECK(event_version>0),
        input_digest CHAR(64) NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
        source_digest CHAR(64) NOT NULL CHECK(source_digest ~ '^[0-9a-f]{64}$'),
        model VARCHAR(128) NOT NULL CHECK(model ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        prompt_revision VARCHAR(64) NOT NULL,
        state VARCHAR(16) NOT NULL CHECK(state IN
          ('queued','running','partial','completed','disagreement','failed','cancelled')),
        http_attempts INTEGER NOT NULL DEFAULT 0 CHECK(http_attempts BETWEEN 0 AND 2),
        error_code VARCHAR(72) CHECK(error_code ~ '^REVIEW_[A-Z_]{1,64}$'),
        lease_token UUID,
        lease_expires_at TIMESTAMPTZ,
        deadline_at TIMESTAMPTZ,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        completed_at TIMESTAMPTZ,
        UNIQUE(household_space_id,medical_event_id,input_digest),
        CHECK((state='running')=(lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)),
        CHECK((state IN ('queued','running'))=(completed_at IS NULL))
      );
      CREATE INDEX ix_guidance_review_queue ON guidance_review_jobs(created_at,id)
        WHERE state='queued';
      CREATE FUNCTION protect_guidance_review_job() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'GUIDANCE_REVIEW_HISTORY_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (
          OLD.state NOT IN ('queued','running') OR
          (to_jsonb(OLD)-ARRAY['state','http_attempts','lease_token','lease_expires_at',
            'deadline_at','completed_at','error_code']) IS DISTINCT FROM
          (to_jsonb(NEW)-ARRAY['state','http_attempts','lease_token','lease_expires_at',
            'deadline_at','completed_at','error_code']) OR NEW.http_attempts<OLD.http_attempts
        ) THEN
          RAISE EXCEPTION 'GUIDANCE_REVIEW_IDENTITY_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' AND NOT EXISTS(SELECT 1 FROM decision_runs r
          JOIN medical_events e ON e.id=r.medical_event_id
          WHERE r.id=NEW.decision_run_id AND r.household_space_id=NEW.household_space_id
            AND r.medical_event_id=NEW.medical_event_id AND r.event_version=NEW.event_version
            AND e.household_space_id=NEW.household_space_id
            AND e.family_member_id=NEW.family_member_id) THEN
          RAISE EXCEPTION 'GUIDANCE_REVIEW_SCOPE_INVALID' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guidance_review_job_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_jobs FOR EACH ROW EXECUTE FUNCTION protect_guidance_review_job();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM guidance_review_jobs) THEN
          RAISE EXCEPTION 'GUIDANCE_REVIEW_HISTORY_REQUIRES_PRESERVATION';
        END IF;
      END $$;
      DROP TABLE guidance_review_jobs;
      DROP FUNCTION protect_guidance_review_job();
    """)
