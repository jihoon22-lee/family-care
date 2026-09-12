"""Retain proven source-scoped enrollment when only the issuer remains unverified."""

from collections.abc import Sequence

from alembic import op

revision: str = "0077_source_scoped_identity"
down_revision: str | Sequence[str] | None = "0076_certificate_title_grounding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _admission(expanded: bool) -> None:
    previous = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4','retained-policy-association-v5',"
        "'retained-policy-association-v6','retained-policy-association-v7')"
    )
    current = previous[:-1] + ",'retained-policy-association-v8')"
    if not expanded:
        previous, current = current, previous
    for signature in ("policy_structuring_source_current(uuid)", "protect_retained_policy_job()"):
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'source scoped policy contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)
    revisions = "'policy-draft-normalization-v1','policy-draft-normalization-v2'"
    if expanded:
        revisions += ",'policy-draft-normalization-v3'"
    op.execute(f"""
      ALTER TABLE policy_range_replay_sources
        DROP CONSTRAINT policy_range_replay_sources_normalization_revision_check,
        ADD CONSTRAINT policy_range_replay_sources_normalization_revision_check
        CHECK(normalization_revision IN ({revisions}));
    """)


def upgrade() -> None:
    _admission(True)
    op.execute("""
      ALTER TABLE policy_contracts ALTER COLUMN insurer_display DROP NOT NULL,
        ALTER COLUMN insurer_key DROP NOT NULL,
        ADD CONSTRAINT ck_policy_contracts_insurer_pair
          CHECK((insurer_display IS NULL)=(insurer_key IS NULL));
      ALTER TABLE range_enrollment_publications ADD COLUMN source_identity_json JSONB,
        ADD CONSTRAINT ck_range_enrollment_source_identity CHECK(source_identity_json IS NULL OR
          COALESCE((jsonb_typeof(source_identity_json)='object'
           AND source_identity_json->>'revision'='source-scoped-policy-identity-v1'
           AND source_identity_json->>'insurer_state'='UNKNOWN'
           AND source_identity_json->>'reason_code'='INSURER_SOURCE_UNVERIFIED'
           AND source_identity_json->>'normalization_revision'='policy-draft-normalization-v3'
           AND source_identity_json->>'program_validation_version'='range-grounding-v4'
           AND rider_id IS NULL AND authority='PROGRAM_VERIFIED'),false));
      ALTER TABLE claim_cases DROP CONSTRAINT ck_claim_cases_insurer_source,
        ADD CONSTRAINT ck_claim_cases_insurer_source CHECK (
          rider_id IS NOT NULL OR (private_coverage_id IS NOT NULL AND insurer_key IS NULL
            AND insurer_display IS NOT NULL AND btrim(insurer_display)<>''));
      CREATE FUNCTION guard_source_scoped_claim_insurer() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NEW.rider_id IS NOT NULL AND NEW.insurer_key IS NULL AND NOT EXISTS(
          SELECT 1 FROM range_enrollment_publications p
          JOIN riders r ON r.policy_contract_id=p.policy_contract_id
            AND r.household_space_id=p.household_space_id
          WHERE p.policy_contract_id=NEW.policy_contract_id AND r.id=NEW.rider_id
            AND p.household_space_id=NEW.household_space_id AND p.rider_id IS NULL
            AND p.source_identity_json->>'reason_code'='INSURER_SOURCE_UNVERIFIED'
        ) THEN RAISE EXCEPTION 'missing source scoped claim proof' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      CREATE CONSTRAINT TRIGGER claim_source_scoped_insurer_guard
        AFTER INSERT OR UPDATE ON claim_cases DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION guard_source_scoped_claim_insurer();
      CREATE FUNCTION guard_source_scoped_policy_identity() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF NOT EXISTS(SELECT 1 FROM policy_contracts p WHERE p.id=NEW.id
                      AND p.insurer_display IS NULL AND p.deleted_at IS NULL)
          THEN RETURN NEW; END IF;
        IF NOT EXISTS(
          SELECT 1 FROM policy_contracts p
          JOIN range_enrollment_publications publication ON publication.policy_contract_id=p.id
            AND publication.household_space_id=p.household_space_id
            AND publication.rider_id IS NULL AND publication.authority='PROGRAM_VERIFIED'
          JOIN analysis_candidate_versions candidate
            ON candidate.id=publication.candidate_version_id
            AND candidate.id=publication.source_candidate_version_id
            AND candidate.candidate_kind='policy_contract' AND candidate.status='AI_VERIFIED'
            AND candidate.generator_version='policy-draft-normalization-v3'
          JOIN policy_range_candidate_sources source ON source.candidate_version_id=candidate.id
          JOIN policy_structuring_jobs job ON job.id=source.job_id
            AND job.pipeline_version='retained-policy-association-v8'
          JOIN document_policy_ranges r ON r.job_id=source.job_id
            AND r.envelope_id=source.envelope_id
          JOIN policy_range_replay_sources receipt ON receipt.job_id=r.job_id
            AND receipt.envelope_id=r.envelope_id
            AND receipt.normalization_revision='policy-draft-normalization-v3'
          WHERE p.id=NEW.id AND source.association_json->>'state'='RESOLVED'
            AND source.association_json->>'contract_scope_id'=p.id::text
            AND publication.source_identity_json->'contract_source'->>'family_member_id'
                =source.association_json->>'family_member_id'
            AND EXISTS(SELECT 1 FROM policy_parties party WHERE party.policy_contract_id=p.id
              AND party.household_space_id=p.household_space_id
              AND party.role='primary_insured' AND party.deleted_at IS NULL
              AND party.family_member_id::text=source.association_json->>'family_member_id')
            AND NOT EXISTS(SELECT 1 FROM range_enrollment_publications known
              WHERE known.policy_contract_id=p.id AND known.household_space_id=p.household_space_id
                AND known.rider_id IS NULL AND known.field_values ? 'insurer')
            AND publication.source_identity_json->>'revision'='source-scoped-policy-identity-v1'
            AND publication.source_identity_json->>'reason_code'='INSURER_SOURCE_UNVERIFIED'
            AND publication.source_identity_json->'contract_source'->>'content_sha256'=
                (SELECT content_sha256 FROM document_versions WHERE id=p.source_document_version_id)
            AND NOT(publication.field_values ? 'insurer')
            AND publication.field_values->>'product_name'=p.product_display
            AND r.result_json->>'program_validation_version'='range-grounding-v4'
        ) THEN RAISE EXCEPTION 'missing source scoped policy proof' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      CREATE CONSTRAINT TRIGGER policy_source_scoped_identity_guard
        AFTER INSERT OR UPDATE ON policy_contracts DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION guard_source_scoped_policy_identity();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE pipeline_version='retained-policy-association-v8')
          OR EXISTS(SELECT 1 FROM policy_range_replay_sources
            WHERE normalization_revision='policy-draft-normalization-v3')
          OR EXISTS(SELECT 1 FROM range_enrollment_publications
            WHERE source_identity_json IS NOT NULL)
          OR EXISTS(SELECT 1 FROM policy_contracts WHERE insurer_display IS NULL) THEN
          RAISE EXCEPTION 'source scoped policy history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP TRIGGER claim_source_scoped_insurer_guard ON claim_cases;
      DROP FUNCTION guard_source_scoped_claim_insurer();
      ALTER TABLE claim_cases DROP CONSTRAINT ck_claim_cases_insurer_source,
        ADD CONSTRAINT ck_claim_cases_insurer_source CHECK (
          (rider_id IS NOT NULL AND insurer_key IS NOT NULL)
          OR (private_coverage_id IS NOT NULL AND insurer_key IS NULL
            AND insurer_display IS NOT NULL AND btrim(insurer_display)<>''));
      DROP TRIGGER policy_source_scoped_identity_guard ON policy_contracts;
      DROP FUNCTION guard_source_scoped_policy_identity();
      ALTER TABLE range_enrollment_publications DROP COLUMN source_identity_json;
      ALTER TABLE policy_contracts DROP CONSTRAINT ck_policy_contracts_insurer_pair,
        ALTER COLUMN insurer_display SET NOT NULL, ALTER COLUMN insurer_key SET NOT NULL;
    """)
    _admission(False)
