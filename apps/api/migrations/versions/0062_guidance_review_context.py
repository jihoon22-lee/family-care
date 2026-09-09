"""Recheck the requested enrollment, rule and source revisions before transmission."""

from collections.abc import Sequence

from alembic import op

revision: str = "0062_guidance_review_context"
down_revision: str | Sequence[str] | None = "0061_guidance_review_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
      CREATE FUNCTION guidance_review_scope_digest(job UUID) RETURNS TEXT
      LANGUAGE sql STABLE AS $$
        WITH requested AS (
          SELECT j.*,i.sources_json FROM guidance_review_jobs j
          JOIN guidance_review_inputs i ON i.review_job_id=j.id WHERE j.id=job
        ), native AS (
          SELECT DISTINCT coalesce(x->'native_ref',x->'ref')->>'coverage_id' AS id
          FROM requested j,LATERAL jsonb_array_elements(j.sources_json->'index') x
          WHERE coalesce(x->'native_ref',x->'ref')->>'kind'='OPERATIONAL_RIDER'
        ), private AS (
          SELECT x->'ref'->>'coverage_id' AS id
          FROM requested j,LATERAL jsonb_array_elements(j.sources_json->'index') x
          WHERE x->'ref'->>'kind'='PRIVATE_KNOWLEDGE_COVERAGE'
        ), owned_riders AS (
          SELECT r.* FROM riders r,requested j WHERE r.household_space_id=j.household_space_id
            AND r.id::text IN (SELECT id FROM native)
        ), links AS (
          SELECT l.* FROM rider_clause_links l,requested j
          WHERE l.household_space_id=j.household_space_id
            AND l.rider_id IN (SELECT id FROM owned_riders)
        ), editions AS (
          SELECT DISTINCT terms_edition_id AS id FROM links
        ), source_versions AS (
          SELECT DISTINCT v.version_id::UUID AS id FROM requested j,
            LATERAL jsonb_array_elements(j.sources_json->'index') x,
            LATERAL jsonb_array_elements_text(x->'source_document_version_ids') v(version_id)
          UNION SELECT (p->'envelope'->'source'->>'document_version_id')::UUID
            FROM requested j,LATERAL jsonb_array_elements(j.sources_json->'packets') p
        )
        SELECT encode(sha256(convert_to(jsonb_build_object(
          'event',(SELECT to_jsonb(e) FROM medical_events e WHERE e.id=j.medical_event_id
            AND e.household_space_id=j.household_space_id),
          'privacy',terms_semantic_privacy_digest(j.household_space_id),
          'enrolled_inventory',(SELECT jsonb_agg(jsonb_build_array(r.id,r.version,p.id,p.version)
            ORDER BY r.id) FROM riders r JOIN policy_contracts p ON p.id=r.policy_contract_id
            WHERE r.household_space_id=j.household_space_id AND r.deleted_at IS NULL
              AND p.deleted_at IS NULL AND EXISTS(SELECT 1 FROM policy_parties party
                WHERE party.policy_contract_id=p.id AND
                party.household_space_id=j.household_space_id
                  AND party.family_member_id=j.family_member_id AND party.deleted_at IS NULL
                  AND party.role IN ('primary_insured','additional_insured'))),
          'private_inventory',(SELECT
          jsonb_agg(jsonb_build_array(c.id,c.source_record_digest_sha256,
            run.id,run.is_current,run.state) ORDER BY c.id)
            FROM private_knowledge_coverages c
            JOIN private_knowledge_contracts k ON k.id=c.knowledge_contract_id
            JOIN private_knowledge_subjects s ON s.id=k.subject_id
            JOIN private_knowledge_import_runs run ON run.id=c.import_run_id
            WHERE c.household_space_id=j.household_space_id AND
            s.family_member_id=j.family_member_id
              AND run.is_current),
          'riders',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id) FROM owned_riders r),
          'policies',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id) FROM policy_contracts p
            WHERE p.id IN (SELECT policy_contract_id FROM owned_riders)),
          'parties',(SELECT jsonb_agg(to_jsonb(p) ORDER BY p.id) FROM policy_parties p
            WHERE p.household_space_id=j.household_space_id
              AND p.family_member_id=j.family_member_id
              AND p.policy_contract_id IN (SELECT policy_contract_id FROM owned_riders)),
          'status',(SELECT jsonb_agg(to_jsonb(s) ORDER BY s.id) FROM policy_status_snapshots s
            WHERE s.household_space_id=j.household_space_id
              AND (s.policy_contract_id IN (SELECT policy_contract_id FROM owned_riders)
                OR s.rider_id IN (SELECT id FROM owned_riders))),
          'evidence',(SELECT jsonb_agg(to_jsonb(e) ORDER BY e.id) FROM evidence e
            WHERE e.household_space_id=j.household_space_id
              AND e.document_version_id IN (SELECT id FROM source_versions)),
          'receipt',(SELECT jsonb_agg(to_jsonb(r) ORDER BY r.id) FROM receipt_lines r
            WHERE r.household_space_id=j.household_space_id AND
            r.medical_event_id=j.medical_event_id),
          'facts',(SELECT jsonb_agg(to_jsonb(f) ORDER BY f.id) FROM medical_event_fact_versions f
            WHERE f.household_space_id=j.household_space_id AND
            f.medical_event_id=j.medical_event_id),
          'claims',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id) FROM claim_history c
            WHERE c.household_space_id=j.household_space_id AND
            c.family_member_id=j.family_member_id
              AND c.medical_event_id<>j.medical_event_id),
          'links',(SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id) FROM links l),
          'clauses',(SELECT jsonb_agg(to_jsonb(c) ORDER BY c.id) FROM clauses c
            WHERE c.household_space_id=j.household_space_id AND c.id IN (SELECT clause_id FROM
            links)),
          'documents',(SELECT jsonb_agg(jsonb_build_array(v.id,v.content_sha256,d.deleted_at)
            ORDER BY v.id) FROM document_versions v JOIN documents d ON d.id=v.document_id
            WHERE v.id IN (SELECT id FROM source_versions)),
          'rules',(SELECT jsonb_agg(jsonb_build_array(r,v) ORDER BY r.id,v.id)
            FROM coverage_rules r LEFT JOIN coverage_rule_versions v ON v.coverage_rule_id=r.id
            WHERE r.rider_clause_link_id IN (SELECT id FROM links)),
          'sources',(SELECT jsonb_agg(jsonb_build_array(e.id,
            terms_semantic_input_context(e.id,j.household_space_id)) ORDER BY e.id) FROM editions
            e),
          'terms_selection',(SELECT jsonb_agg(jsonb_build_array(p.id,
            policy_terms_input_context(p.id,j.family_member_id,j.household_space_id))
            ORDER BY p.id) FROM policy_contracts p
            WHERE p.id IN (SELECT policy_contract_id FROM owned_riders)),
          'terms_applicability',(SELECT jsonb_agg(jsonb_build_array(a.id,a.input_digest,
            a.status,a.selection_state) ORDER BY a.id) FROM current_policy_terms_applicability a
            WHERE a.household_space_id=j.household_space_id
              AND a.family_member_id=j.family_member_id
              AND a.policy_contract_id IN (SELECT policy_contract_id FROM owned_riders)),
          'terms_changes',(SELECT jsonb_agg(jsonb_build_array(a,
            terms_change_input_context(a.source_component_id,a.household_space_id,a.scope_kind))
            ORDER BY a.id) FROM effective_policy_terms_changes a
            WHERE a.household_space_id=j.household_space_id
              AND a.family_member_id=j.family_member_id
              AND a.policy_contract_id IN (SELECT policy_contract_id FROM owned_riders)),
          'semantic',(SELECT jsonb_agg(jsonb_build_array(p.id,p.candidate_id,p.proof_sha256)
            ORDER BY p.id) FROM terms_semantic_publications p
            WHERE p.household_space_id=j.household_space_id
              AND p.terms_edition_id IN (SELECT id FROM editions)),
          'private',(SELECT jsonb_agg(jsonb_build_array(c,s,run.id,run.is_current,run.state,
            run.package_digest_sha256) ORDER BY c.id)
            FROM private_knowledge_coverages c
            JOIN private_knowledge_contracts k ON k.id=c.knowledge_contract_id
            JOIN private_knowledge_subjects s ON s.id=k.subject_id
            JOIN private_knowledge_import_runs run ON run.id=c.import_run_id
            WHERE c.household_space_id=j.household_space_id AND c.id::text IN (SELECT id FROM
            private)),
          'private_rules',(SELECT jsonb_agg(jsonb_build_array(r.id,r.is_current,r.state,
            r.package_digest_sha256) ORDER BY r.id) FROM private_knowledge_rule_import_runs r
            WHERE r.household_space_id=j.household_space_id AND r.knowledge_import_run_id IN (
              SELECT import_run_id FROM private_knowledge_coverages WHERE id::text IN
                (SELECT id FROM private))),
          'canonical',(SELECT jsonb_agg(jsonb_build_array(l.id,l.fingerprint) ORDER BY l.id)
            FROM private_knowledge_canonical_links l WHERE l.household_space_id=j.household_space_id
              AND l.family_member_id=j.family_member_id AND l.knowledge_coverage_id::text IN
                (SELECT id FROM private))
        )::text,'UTF8')),'hex') FROM requested j
      $$;
      CREATE TABLE guidance_review_contexts (
        review_job_id UUID PRIMARY KEY REFERENCES guidance_review_jobs(id) ON DELETE RESTRICT,
        scope_digest CHAR(64) NOT NULL CHECK(scope_digest ~ '^[0-9a-f]{64}$')
      );
      CREATE FUNCTION protect_guidance_review_context() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'REVIEW_CONTEXT_IMMUTABLE' USING ERRCODE='23514';
        END IF;
        IF NEW.scope_digest IS DISTINCT FROM guidance_review_scope_digest(NEW.review_job_id)
          OR NOT EXISTS(SELECT 1 FROM guidance_review_jobs j WHERE j.id=NEW.review_job_id
            AND j.state='queued' AND j.http_attempts=0) THEN
          RAISE EXCEPTION 'REVIEW_CONTEXT_INVALID' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER guidance_review_context_guard BEFORE INSERT OR UPDATE OR DELETE
        ON guidance_review_contexts FOR EACH ROW EXECUTE FUNCTION protect_guidance_review_context();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM guidance_review_contexts) THEN
          RAISE EXCEPTION 'REVIEW_CONTEXT_REQUIRES_PRESERVATION';
        END IF;
      END $$;
      DROP TABLE guidance_review_contexts;
      DROP FUNCTION protect_guidance_review_context();
      DROP FUNCTION guidance_review_scope_digest(UUID);
    """)
