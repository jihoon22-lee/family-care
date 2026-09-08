"""Queue bounded terms proposals and share document-provider request accounting."""

from collections.abc import Sequence

from alembic import op

revision: str = "0054_terms_semantic_jobs"
down_revision: str | Sequence[str] | None = "0053_terms_semantic_processing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION terms_semantic_privacy_digest(household_id UUID)
      RETURNS TEXT LANGUAGE sql STABLE AS $$
        SELECT encode(sha256(convert_to(coalesce(jsonb_agg(jsonb_build_array(
          m.id,m.display_name,m.internal_alias,m.version) ORDER BY m.id),'[]'::jsonb)::text,
          'UTF8')),'hex') FROM family_members m
        WHERE m.household_space_id=household_id AND m.deleted_at IS NULL
      $$;
      CREATE FUNCTION lock_terms_semantic_work_source(edition_id UUID,household_id UUID)
      RETURNS BOOLEAN LANGUAGE plpgsql AS $$
      DECLARE found_id UUID;
      BEGIN
        PERFORM id FROM household_spaces WHERE id=household_id FOR UPDATE;
        IF NOT FOUND THEN RETURN false; END IF;
        -- Lock deleted rows too: restore changes the active privacy set without an INSERT.
        PERFORM id FROM family_members WHERE household_space_id=household_id ORDER BY id FOR SHARE;
        SELECT e.id INTO found_id FROM terms_editions e
          JOIN insurance_document_components c ON c.id=e.source_component_id
            AND c.household_space_id=e.household_space_id
          JOIN document_metadata_publications mp ON mp.id=c.metadata_publication_id
          JOIN document_metadata_proposals proposal ON proposal.id=mp.proposal_id
          JOIN document_structure_generations g ON g.id=proposal.generation_id
          JOIN document_versions v ON v.id=e.document_version_id
          JOIN documents d ON d.id=v.document_id
          JOIN family_members m ON m.id=c.family_member_id
            AND m.household_space_id=e.household_space_id
          WHERE e.id=edition_id AND e.household_space_id=household_id
          FOR SHARE OF e,c,d,m,g;
        RETURN found_id IS NOT NULL AND
          terms_semantic_input_context(edition_id,household_id) IS NOT NULL;
      END $$;
      CREATE TABLE terms_semantic_jobs(
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
        document_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE RESTRICT,
        input_context JSONB NOT NULL,
        input_digest TEXT NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
        privacy_digest TEXT NOT NULL CHECK(privacy_digest ~ '^[0-9a-f]{64}$'),
        work_key TEXT NOT NULL CHECK(work_key ~ '^[0-9a-f]{64}$'),
        pipeline_revision TEXT NOT NULL CHECK(pipeline_revision='terms-semantic-work-v1'),
        envelope_json JSONB NOT NULL CHECK(jsonb_typeof(envelope_json)='object'
          AND octet_length(envelope_json::text)<=8388608),
        state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN
          ('queued','running','retryable_failed','paused','succeeded','failed','cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 3),
        lease_owner TEXT CHECK(length(lease_owner) BETWEEN 1 AND 128),
        lease_token UUID,
        lease_expires_at TIMESTAMPTZ,
        heartbeat_at TIMESTAMPTZ,
        available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        candidate_id UUID REFERENCES terms_semantic_candidates(id) ON DELETE RESTRICT,
        error_code TEXT CHECK(error_code IN (
          'TERMS_STRUCTURING_DISABLED','TERMS_PROVIDER_UNCONFIGURED',
          'TERMS_PROVIDER_DOCUMENT_BUDGET','TERMS_PROVIDER_DAILY_BUDGET',
          'TERMS_SOURCE_CHANGED','TERMS_PRIVACY_CHANGED','TERMS_PRIVACY_UNAVAILABLE',
          'TERMS_STRUCTURING_INVALID','TERMS_PROVIDER_RETRYABLE',
          'TERMS_PROVIDER_FAILED','TERMS_LEASE_EXHAUSTED')),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(terms_edition_id,input_digest,privacy_digest,work_key,pipeline_revision),
        CHECK((state='running')=(lease_owner IS NOT NULL AND lease_token IS NOT NULL
          AND lease_expires_at IS NOT NULL AND heartbeat_at IS NOT NULL)),
        CHECK(state='running' OR (lease_owner IS NULL AND lease_token IS NULL
          AND lease_expires_at IS NULL AND heartbeat_at IS NULL)),
        CHECK((state='succeeded')=(candidate_id IS NOT NULL))
      );
      CREATE INDEX terms_semantic_jobs_due ON terms_semantic_jobs(available_at,created_at,id)
        WHERE state IN ('queued','running','retryable_failed','paused');
      CREATE TABLE terms_semantic_work_attempts(
        run_id UUID NOT NULL REFERENCES terms_semantic_processing_runs(id) ON DELETE RESTRICT,
        region_id TEXT NOT NULL CHECK(region_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        work_revision TEXT NOT NULL CHECK(work_revision='terms-semantic-work-v1'),
        privacy_digest TEXT NOT NULL CHECK(privacy_digest ~ '^[0-9a-f]{64}$'),
        outcome TEXT NOT NULL CHECK(outcome IN ('QUEUED','UNSUPPORTED')),
        reason_code TEXT NOT NULL CHECK(reason_code IN (
          'SEMANTIC_WORK_QUEUED','SEMANTIC_WORK_REGION_UNSUPPORTED',
          'SEMANTIC_WORK_REFERENCE_UNRESOLVED','SEMANTIC_WORK_LIMIT_EXCEEDED')),
        job_id UUID REFERENCES terms_semantic_jobs(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(run_id,region_id,work_revision,privacy_digest),
        CHECK((outcome='QUEUED')=(job_id IS NOT NULL))
      );
      CREATE TABLE terms_semantic_job_publications(
        job_id UUID NOT NULL REFERENCES terms_semantic_jobs(id) ON DELETE RESTRICT,
        projection_revision TEXT NOT NULL CHECK(length(projection_revision) BETWEEN 1 AND 128),
        outcome TEXT NOT NULL CHECK(outcome IN ('PUBLISHED','STALE','REJECTED')),
        publication_id UUID REFERENCES terms_semantic_publications(id) ON DELETE RESTRICT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(job_id,projection_revision),
        CHECK((outcome='PUBLISHED')=(publication_id IS NOT NULL))
      );
      CREATE FUNCTION protect_terms_semantic_work() RETURNS TRIGGER LANGUAGE plpgsql AS $$
      DECLARE parent terms_semantic_processing_runs; job terms_semantic_jobs;
      BEGIN
        IF TG_OP='DELETE' OR (TG_OP='UPDATE' AND TG_TABLE_NAME<>'terms_semantic_jobs') THEN
          RAISE EXCEPTION 'semantic work history is immutable' USING ERRCODE='23514';
        END IF;
        IF TG_TABLE_NAME='terms_semantic_jobs' THEN
          IF TG_OP='UPDATE' AND (
            OLD.state IN ('succeeded','cancelled') OR
            (to_jsonb(OLD)-ARRAY['state','attempts','lease_owner','lease_token','lease_expires_at',
              'heartbeat_at','available_at','candidate_id','error_code','updated_at'])
              IS DISTINCT FROM
            (to_jsonb(NEW)-ARRAY['state','attempts','lease_owner','lease_token','lease_expires_at',
              'heartbeat_at','available_at','candidate_id','error_code','updated_at'])) THEN
            RAISE EXCEPTION 'semantic work identity is immutable' USING ERRCODE='23514';
          END IF;
          IF (TG_OP='INSERT' OR NEW.state<>'cancelled') AND (
            NOT lock_terms_semantic_work_source(NEW.terms_edition_id,NEW.household_space_id) OR
            NEW.input_context IS DISTINCT FROM
              terms_semantic_input_context(NEW.terms_edition_id,NEW.household_space_id) OR
            NEW.privacy_digest
              IS DISTINCT FROM terms_semantic_privacy_digest(NEW.household_space_id))
          THEN RAISE EXCEPTION 'semantic work source changed' USING ERRCODE='23514'; END IF;
          IF NEW.input_digest<>encode(sha256(convert_to(NEW.input_context::text,'UTF8')),'hex')
            OR NEW.document_version_id::text
              IS DISTINCT FROM NEW.input_context->>'document_version_id'
            OR NEW.envelope_json->>'input_digest' IS DISTINCT FROM NEW.input_digest
            OR NEW.envelope_json->>'schema_revision' IS DISTINCT FROM NEW.pipeline_revision
            OR NEW.envelope_json->'source'->>'document_version_id' IS DISTINCT FROM
              NEW.document_version_id::text
            OR NEW.envelope_json->'source'->>'terms_edition_id'
              IS DISTINCT FROM NEW.terms_edition_id::text
            OR NEW.envelope_json->'source'->>'generation_id' IS DISTINCT FROM
              NEW.input_context->>'generation_id'
            OR NEW.envelope_json->'source'->>'content_sha256' IS DISTINCT FROM
              NEW.input_context->>'content_sha256'
            OR NEW.envelope_json->'source'->>'structure_identity_sha256' IS DISTINCT FROM
              NEW.input_context->>'structure_identity_sha256' THEN
            RAISE EXCEPTION 'semantic work envelope mismatch' USING ERRCODE='23514';
          END IF;
          IF NEW.candidate_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM terms_semantic_candidates c
            WHERE c.id=NEW.candidate_id AND c.household_space_id=NEW.household_space_id
              AND c.terms_edition_id=NEW.terms_edition_id AND c.input_digest=NEW.input_digest) THEN
            RAISE EXCEPTION 'semantic work candidate mismatch' USING ERRCODE='23514';
          END IF;
        ELSIF TG_TABLE_NAME='terms_semantic_work_attempts' THEN
          SELECT * INTO parent FROM terms_semantic_processing_runs WHERE id=NEW.run_id;
          IF NOT FOUND OR NOT (parent.manifest_json->'unresolved_regions' ? NEW.region_id)
            OR parent.input_context IS DISTINCT FROM terms_semantic_input_context(
              parent.terms_edition_id,parent.household_space_id)
            OR NEW.privacy_digest
              IS DISTINCT FROM terms_semantic_privacy_digest(parent.household_space_id)
          THEN RAISE EXCEPTION 'semantic work plan mismatch' USING ERRCODE='23514'; END IF;
          IF NEW.job_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM terms_semantic_jobs j
            WHERE j.id=NEW.job_id AND j.terms_edition_id=parent.terms_edition_id
              AND j.household_space_id=parent.household_space_id
              AND j.input_digest=parent.input_digest
              AND j.privacy_digest=NEW.privacy_digest
              AND j.envelope_json->'primary_region_ids' ? NEW.region_id) THEN
            RAISE EXCEPTION 'semantic work plan job mismatch' USING ERRCODE='23514';
          END IF;
        ELSE
          SELECT * INTO job FROM terms_semantic_jobs WHERE id=NEW.job_id AND state='succeeded';
          IF NOT FOUND THEN
            RAISE EXCEPTION 'semantic work publication state invalid' USING ERRCODE='23514';
          END IF;
          IF NEW.publication_id IS NOT NULL
              AND NOT EXISTS(SELECT 1 FROM terms_semantic_publications p
            WHERE p.id=NEW.publication_id AND p.candidate_id=job.candidate_id
              AND p.household_space_id=job.household_space_id
              AND p.terms_edition_id=job.terms_edition_id)
          THEN RAISE EXCEPTION 'semantic work publication mismatch' USING ERRCODE='23514'; END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER terms_semantic_job_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_jobs FOR EACH ROW EXECUTE FUNCTION protect_terms_semantic_work();
      CREATE TRIGGER terms_semantic_work_attempt_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_work_attempts FOR EACH ROW EXECUTE FUNCTION protect_terms_semantic_work();
      CREATE TRIGGER terms_semantic_job_publication_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_job_publications FOR EACH ROW
        EXECUTE FUNCTION protect_terms_semantic_work();

      ALTER TABLE policy_provider_requests ALTER COLUMN job_id DROP NOT NULL;
      ALTER TABLE policy_provider_requests ADD COLUMN terms_job_id UUID
        REFERENCES terms_semantic_jobs(id) ON DELETE RESTRICT;
      ALTER TABLE policy_provider_requests ADD CONSTRAINT ck_provider_request_owner
        CHECK((job_id IS NOT NULL)<>(terms_job_id IS NOT NULL));
      CREATE UNIQUE INDEX uq_terms_provider_active_request
        ON policy_provider_requests(terms_job_id,fingerprint) WHERE state='RESERVED';
      CREATE UNIQUE INDEX uq_terms_provider_cached_request
        ON policy_provider_requests(terms_job_id,fingerprint) WHERE state='SUCCEEDED';
      CREATE OR REPLACE FUNCTION protect_policy_provider_request()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'provider request accounting is immutable' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.state<>'RESERVED' OR NEW.state='RESERVED' OR
          (to_jsonb(OLD)-ARRAY['state','response_json','request_id']) IS DISTINCT FROM
          (to_jsonb(NEW)-ARRAY['state','response_json','request_id'])) THEN
          RAISE EXCEPTION 'provider request identity is immutable' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM document_versions v WHERE v.document_id=NEW.document_id AND (
          (NEW.job_id IS NOT NULL AND EXISTS(SELECT 1 FROM policy_structuring_jobs j
            WHERE j.id=NEW.job_id AND j.document_version_id=v.id)) OR
          (NEW.terms_job_id IS NOT NULL AND EXISTS(SELECT 1 FROM terms_semantic_jobs j
            WHERE j.id=NEW.terms_job_id AND j.document_version_id=v.id)))) THEN
          RAISE EXCEPTION 'provider request scope invalid' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM terms_semantic_jobs)
          OR EXISTS(SELECT 1 FROM terms_semantic_work_attempts) THEN
          RAISE EXCEPTION 'semantic work history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP INDEX uq_terms_provider_active_request;
      DROP INDEX uq_terms_provider_cached_request;
      ALTER TABLE policy_provider_requests DROP CONSTRAINT ck_provider_request_owner;
      ALTER TABLE policy_provider_requests DROP COLUMN terms_job_id;
      ALTER TABLE policy_provider_requests ALTER COLUMN job_id SET NOT NULL;
      CREATE OR REPLACE FUNCTION protect_policy_provider_request()
      RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='DELETE' THEN
          RAISE EXCEPTION 'provider request accounting is immutable' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND (OLD.state<>'RESERVED' OR NEW.state='RESERVED' OR
          (to_jsonb(OLD)-ARRAY['state','response_json','request_id']) IS DISTINCT FROM
          (to_jsonb(NEW)-ARRAY['state','response_json','request_id'])) THEN
          RAISE EXCEPTION 'provider request identity is immutable' USING ERRCODE='23514';
        END IF;
        IF NOT EXISTS(SELECT 1 FROM policy_structuring_jobs j
          JOIN document_versions v ON v.id=j.document_version_id
          WHERE j.id=NEW.job_id AND v.document_id=NEW.document_id) THEN
          RAISE EXCEPTION 'provider request scope invalid' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      DROP TABLE terms_semantic_job_publications;
      DROP TABLE terms_semantic_work_attempts;
      DROP TABLE terms_semantic_jobs;
      DROP FUNCTION protect_terms_semantic_work();
      DROP FUNCTION lock_terms_semantic_work_source(UUID,UUID);
      DROP FUNCTION terms_semantic_privacy_digest(UUID);
    """)
