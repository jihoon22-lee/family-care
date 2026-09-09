"""Preserve the exact reviewed result used to prepare a claim."""

from collections.abc import Sequence

from alembic import op

revision: str = "0063_review_claim_sources"
down_revision: str | Sequence[str] | None = "0062_guidance_review_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE claim_case_snapshots ADD COLUMN review_job_id UUID
        REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT;
      CREATE FUNCTION validate_review_claim_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
      DECLARE c claim_cases; j guidance_review_jobs; result JSONB; local JSONB;
        original JSONB; selected_run JSONB; selected_candidate JSONB;
      BEGIN
        local := NEW.candidate_snapshot_json->'local_guidance';
        IF NEW.review_job_id IS NULL THEN
          IF jsonb_path_exists(NEW.candidate_snapshot_json,
            '$.**.review_job_id ? (@ != null)') THEN
            RAISE EXCEPTION 'REVIEW_CLAIM_SOURCE_REQUIRED' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END IF;
        SELECT * INTO c FROM claim_cases WHERE id=NEW.claim_case_id;
        SELECT * INTO j FROM guidance_review_jobs WHERE id=NEW.review_job_id;
        SELECT result_json INTO result FROM guidance_review_results WHERE review_job_id=j.id;
        IF j.id IS NULL OR result IS NULL OR j.household_space_id<>c.household_space_id
          OR j.medical_event_id<>c.medical_event_id OR j.family_member_id<>c.family_member_id
          OR j.state NOT IN ('partial','completed','disagreement')
          OR NOT EXISTS(SELECT 1 FROM guidance_review_contexts context
            WHERE context.review_job_id=j.id
              AND context.scope_digest=guidance_review_scope_digest(j.id)) THEN
          RAISE EXCEPTION 'REVIEW_CLAIM_SCOPE_INVALID' USING ERRCODE='23514';
        END IF;
        SELECT local_guidance_json INTO original FROM decision_runs WHERE id=j.decision_run_id;
        SELECT local_guidance_json INTO selected_run FROM decision_runs
          WHERE id::text=local->>'run_id' AND household_space_id=c.household_space_id
            AND medical_event_id=c.medical_event_id AND event_version=j.event_version;
        SELECT candidate INTO selected_candidate
          FROM jsonb_array_elements(result->'guidance'->'candidates') candidate
          WHERE candidate->'ref'=local->'candidate'->'ref';
        IF local IS NULL OR selected_run IS NULL OR original IS DISTINCT FROM selected_run
          OR selected_candidate IS NULL OR selected_candidate IS DISTINCT FROM local->'candidate'
          OR (local->>'medical_event_id'=c.medical_event_id::text
            AND local->>'family_member_id'=c.family_member_id::text
            AND local->>'event_version'=j.event_version::text
            AND local->'versions'=result->'guidance'->'versions'
            AND local->'expenses'=result->'guidance'->'expenses'
            AND local->'event_date'=result->'guidance'->'event_date'
            AND local->'review'->>'review_job_id'=j.id::text
            AND local->'review'->>'original_decision_run_id'=j.decision_run_id::text
            AND local->'review'->>'source_digest'=j.source_digest
            AND local->'review'->>'result_digest'=
              encode(sha256(convert_to(result::text,'UTF8')),'hex')
            AND result->>'source_digest'=j.source_digest
            AND ((c.rider_id IS NOT NULL
              AND local->'candidate'->'ref'->>'kind'='OPERATIONAL_RIDER'
              AND local->'candidate'->'ref'->>'coverage_id'=c.rider_id::text
              AND local->'candidate'->'ref'->>'contract_id'=c.policy_contract_id::text)
            OR (c.private_coverage_id IS NOT NULL
              AND local->'candidate'->'ref'->>'kind'='PRIVATE_KNOWLEDGE_COVERAGE'
              AND local->'candidate'->'ref'->>'coverage_id'=c.private_coverage_id::text
              AND local->'candidate'->'ref'->>'contract_id'=c.private_contract_id::text)))
            IS NOT TRUE THEN
          RAISE EXCEPTION 'REVIEW_CLAIM_SNAPSHOT_INVALID' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER claim_review_snapshot_guard BEFORE INSERT ON claim_case_snapshots
        FOR EACH ROW EXECUTE FUNCTION validate_review_claim_snapshot();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM claim_case_snapshots WHERE review_job_id IS NOT NULL) THEN
          RAISE EXCEPTION 'REVIEW_CLAIM_HISTORY_REQUIRES_PRESERVATION';
        END IF;
      END $$;
      DROP TRIGGER claim_review_snapshot_guard ON claim_case_snapshots;
      DROP FUNCTION validate_review_claim_snapshot();
      ALTER TABLE claim_case_snapshots DROP COLUMN review_job_id;
    """)
