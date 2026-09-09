"""Append explicit policy processing revisions over pinned retained generations."""

from collections.abc import Sequence

from alembic import op

revision: str = "0065_retained_policy_jobs"
down_revision: str | Sequence[str] | None = "0064_metadata_proven_prefix"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      ALTER TABLE policy_structuring_jobs
        ADD COLUMN processing_mode TEXT NOT NULL DEFAULT 'automatic'
          CHECK(processing_mode IN ('automatic','retained')),
        ADD COLUMN resubmission_of_job_id UUID REFERENCES policy_structuring_jobs(id)
          ON DELETE RESTRICT,
        ADD COLUMN source_generation_id UUID REFERENCES document_structure_generations(id)
          ON DELETE RESTRICT,
        ADD CONSTRAINT ck_policy_retained_source_pair CHECK(
          (processing_mode='automatic' AND resubmission_of_job_id IS NULL
            AND source_generation_id IS NULL) OR
          (processing_mode='retained' AND resubmission_of_job_id IS NOT NULL
            AND source_generation_id IS NOT NULL AND resubmission_of_job_id<>id)),
        DROP CONSTRAINT uq_policy_structuring_jobs_batch_item,
        DROP CONSTRAINT uq_policy_structuring_jobs_extraction;
      CREATE UNIQUE INDEX uq_policy_structuring_jobs_batch_item
        ON policy_structuring_jobs(batch_item_id) WHERE processing_mode='automatic';
      CREATE UNIQUE INDEX uq_policy_structuring_jobs_extraction
        ON policy_structuring_jobs(extraction_id) WHERE processing_mode='automatic';
      CREATE UNIQUE INDEX uq_policy_retained_revision
        ON policy_structuring_jobs(resubmission_of_job_id,source_generation_id,pipeline_version)
        WHERE processing_mode='retained';

      CREATE FUNCTION retained_policy_source_is_current(
        source_job UUID,source_generation UUID,household UUID)
      RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
        SELECT EXISTS(
          SELECT 1 FROM policy_structuring_jobs original
          JOIN document_batch_items item ON item.id=original.batch_item_id
            AND item.state='succeeded' AND item.document_kind='policy'
          JOIN document_batches batch ON batch.id=item.batch_id
            AND batch.household_space_id=original.household_space_id
            AND batch.family_member_id=original.family_member_id AND batch.state<>'cancelled'
          JOIN document_versions version ON version.id=original.document_version_id
            AND version.document_id=item.document_id
          JOIN documents document ON document.id=version.document_id
            AND document.deleted_at IS NULL AND document.document_kind='policy'
          JOIN extractions extraction ON extraction.id=original.extraction_id
            AND extraction.document_version_id=version.id AND extraction.status='succeeded'
          JOIN family_members member ON member.id=original.family_member_id
            AND member.household_space_id=original.household_space_id AND member.deleted_at IS NULL
          JOIN document_structure_generations generation ON generation.id=source_generation
            AND generation.household_space_id=original.household_space_id
            AND generation.family_member_id=original.family_member_id
            AND generation.batch_item_id=item.id AND generation.document_version_id=version.id
            AND generation.extraction_id=extraction.id
            AND generation.is_current AND NOT generation.cancelled
          WHERE original.id=source_job AND original.household_space_id=household
            AND original.processing_mode='automatic'
            AND original.state IN ('succeeded','permanently_failed','cancelled')
            AND (item.processed_document_version_id IS NULL
              OR item.processed_document_version_id=version.id))
      $$;

      CREATE FUNCTION policy_structuring_source_current(requested_job UUID)
      RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
        SELECT COALESCE((SELECT processing_mode='automatic' OR
          (pipeline_version='retained-policy-association-v1' AND
            retained_policy_source_is_current(resubmission_of_job_id,source_generation_id,
              household_space_id)) FROM policy_structuring_jobs WHERE id=requested_job),false)
      $$;

      CREATE FUNCTION protect_retained_policy_job() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' AND (
          OLD.processing_mode IS DISTINCT FROM NEW.processing_mode OR
          (OLD.processing_mode='retained' AND
            ROW(OLD.id,OLD.household_space_id,OLD.batch_item_id,OLD.family_member_id,
              OLD.document_version_id,OLD.extraction_id,OLD.policy_aggregate_id,
              OLD.pipeline_version,OLD.resubmission_of_job_id,OLD.source_generation_id)
            IS DISTINCT FROM
            ROW(NEW.id,NEW.household_space_id,NEW.batch_item_id,NEW.family_member_id,
              NEW.document_version_id,NEW.extraction_id,NEW.policy_aggregate_id,
              NEW.pipeline_version,NEW.resubmission_of_job_id,NEW.source_generation_id))) THEN
          RAISE EXCEPTION 'retained policy job identity is immutable' USING ERRCODE='23514';
        END IF;
        IF TG_OP='INSERT' AND NEW.processing_mode='retained' AND
          (NEW.pipeline_version<>'retained-policy-association-v1' OR NOT EXISTS(
          SELECT 1 FROM policy_structuring_jobs original
          WHERE original.id=NEW.resubmission_of_job_id
            AND original.household_space_id=NEW.household_space_id
            AND original.batch_item_id=NEW.batch_item_id
            AND original.family_member_id=NEW.family_member_id
            AND original.document_version_id=NEW.document_version_id
            AND original.extraction_id=NEW.extraction_id
            AND retained_policy_source_is_current(original.id,NEW.source_generation_id,
              NEW.household_space_id))) THEN
          RAISE EXCEPTION 'retained policy job source is invalid' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER retained_policy_job_guard BEFORE INSERT OR UPDATE
        ON policy_structuring_jobs FOR EACH ROW EXECUTE FUNCTION protect_retained_policy_job();

      CREATE FUNCTION pin_retained_policy_plan() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs j WHERE j.id=NEW.job_id
          AND j.processing_mode='retained'
          AND (j.source_generation_id<>NEW.generation_id
            OR NOT policy_structuring_source_current(j.id))) THEN
          RAISE EXCEPTION 'retained policy plan source changed' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER retained_policy_plan_guard BEFORE INSERT
        ON document_policy_range_plans FOR EACH ROW EXECUTE FUNCTION pin_retained_policy_plan();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs WHERE processing_mode='retained') THEN
          RAISE EXCEPTION 'retained policy processing history must be preserved';
        END IF;
      END $$;
      DROP TRIGGER retained_policy_plan_guard ON document_policy_range_plans;
      DROP FUNCTION pin_retained_policy_plan();
      DROP TRIGGER retained_policy_job_guard ON policy_structuring_jobs;
      DROP FUNCTION protect_retained_policy_job();
      DROP FUNCTION policy_structuring_source_current(UUID);
      DROP FUNCTION retained_policy_source_is_current(UUID,UUID,UUID);
      DROP INDEX uq_policy_retained_revision;
      DROP INDEX uq_policy_structuring_jobs_batch_item;
      DROP INDEX uq_policy_structuring_jobs_extraction;
      ALTER TABLE policy_structuring_jobs
        DROP CONSTRAINT ck_policy_retained_source_pair,
        DROP COLUMN source_generation_id,
        DROP COLUMN resubmission_of_job_id,
        DROP COLUMN processing_mode,
        ADD CONSTRAINT uq_policy_structuring_jobs_batch_item UNIQUE(batch_item_id),
        ADD CONSTRAINT uq_policy_structuring_jobs_extraction UNIQUE(extraction_id);
    """)
