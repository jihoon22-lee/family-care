"""Retain source-bound program assessments of contract-to-edition applicability."""

from collections.abc import Sequence

from alembic import op

revision: str = "0042_terms_applicability"
down_revision: str | Sequence[str] | None = "0041_component_supersession"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
        CREATE FUNCTION metadata_match_key(value TEXT) RETURNS TEXT
        LANGUAGE sql IMMUTABLE STRICT AS $$
          SELECT btrim(regexp_replace(casefold(normalize(value,NFKC)
            COLLATE "pg_unicode_fast"),'[[:space:]]+',' ','g'))
        $$;
        CREATE FUNCTION metadata_terms_candidate(policy JSONB,terms JSONB,display_name TEXT)
        RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
          SELECT EXISTS(
            SELECT 1 FROM jsonb_array_elements(coalesce(policy->'facts','[]')) p
            CROSS JOIN jsonb_array_elements(coalesce(terms->'facts','[]')) t
            WHERE p->>'field' IN (
                'product_code','product_name','terms_reference','edition_reference')
              AND t->>'field' IN ('product_code','product_name','terms_code','edition_code')
              AND ((p->>'field'=t->>'field' AND p->>'field' IN ('product_code','product_name'))
              OR (p->>'field'='terms_reference' AND t->>'field'='terms_code')
              OR (p->>'field'='edition_reference' AND t->>'field'='edition_code'))
              AND metadata_match_key(p->>'value')<>''
              AND metadata_match_key(p->>'value')=metadata_match_key(t->>'value')
          ) OR EXISTS(
            SELECT 1 FROM jsonb_array_elements(coalesce(terms->'facts','[]')) t
            WHERE t->>'field'='product_name' AND metadata_match_key(display_name)<>''
              AND metadata_match_key(t->>'value')=metadata_match_key(display_name)
          )
        $$;
        CREATE VIEW terms_applicability_component_sources AS
          SELECT c.*,p.proof_json,proposal.generation_id,v.content_sha256
          FROM insurance_document_components c
          JOIN document_metadata_publications p ON p.id=c.metadata_publication_id
            AND p.component_id=c.id AND p.outcome='APPLIED'
          JOIN document_metadata_proposals proposal ON proposal.id=p.proposal_id
          JOIN document_versions v ON v.id=c.document_version_id
          JOIN documents d ON d.id=v.document_id
          JOIN family_members m ON m.id=c.family_member_id
            AND m.household_space_id=c.household_space_id
          WHERE c.deleted_at IS NULL AND c.superseded_by_component_id IS NULL
            AND c.review_state IN ('PROGRAM_VERIFIED','USER_CONFIRMED')
            AND d.deleted_at IS NULL AND m.deleted_at IS NULL
            AND c.page_end<=v.page_count
            AND c.role=p.proof_json->>'role'
            AND c.page_start=(p.proof_json->>'page_start')::int
            AND c.page_end=(p.proof_json->>'page_end')::int;

        CREATE FUNCTION policy_terms_input_context(policy_id UUID,member_id UUID,household_id UUID)
        RETURNS JSONB LANGUAGE sql STABLE AS $$
          WITH policy AS (
            SELECT p.*,e.physical_page,e.review_state AS evidence_review_state,
              v.content_sha256,e.extraction_id,e.x0 AS source_x0,e.y0 AS source_y0,
              e.x1 AS source_x1,e.y1 AS source_y1
            FROM policy_contracts p JOIN evidence e ON e.id=p.source_evidence_id
              AND e.household_space_id=p.household_space_id
              AND e.document_version_id=p.source_document_version_id
            JOIN document_versions v ON v.id=p.source_document_version_id
              AND v.content_sha256=e.content_sha256
            JOIN documents d ON d.id=v.document_id
            JOIN extractions x ON x.id=e.extraction_id AND x.document_version_id=v.id
            JOIN extraction_pages page ON page.extraction_id=x.id
              AND page.page_number=e.physical_page
            JOIN family_members m ON m.id=member_id AND m.household_space_id=p.household_space_id
            WHERE p.id=policy_id AND p.household_space_id=household_id AND p.deleted_at IS NULL
              AND d.deleted_at IS NULL AND m.deleted_at IS NULL AND x.status='succeeded'
              AND e.review_state IN ('AI_VERIFIED','USER_CONFIRMED')
              AND e.physical_page BETWEEN 1 AND v.page_count
              AND (e.x0 IS NULL OR (e.x0>=0 AND e.y0>=0 AND e.x1>e.x0 AND e.y1>e.y0
                AND e.x1<=page.width_points AND e.y1<=page.height_points))
              AND EXISTS(SELECT 1 FROM policy_parties party WHERE party.policy_contract_id=p.id
                AND party.household_space_id=household_id AND party.family_member_id=member_id
                AND party.role IN ('primary_insured','additional_insured') AND
                  party.deleted_at IS NULL)
          ), components AS (
            SELECT c.* FROM terms_applicability_component_sources c,policy p
            WHERE c.household_space_id=household_id AND c.family_member_id=member_id
              AND c.role='policy' AND c.document_version_id=p.source_document_version_id
              AND c.content_sha256=p.content_sha256
              AND p.physical_page BETWEEN c.page_start AND c.page_end
              AND NOT EXISTS(
                SELECT 1 FROM policy_contracts other JOIN evidence e ON
                  e.id=other.source_evidence_id
                JOIN document_versions v ON v.id=e.document_version_id
                WHERE other.id<>p.id AND other.household_space_id=household_id
                  AND other.deleted_at IS NULL AND v.content_sha256=c.content_sha256
                  AND e.physical_page BETWEEN c.page_start AND c.page_end)
          ), editions AS (
            SELECT e.id,e.version,c.id AS component_id,c.version AS component_version,
              c.review_state,c.metadata_publication_id
            FROM terms_editions e JOIN terms_applicability_component_sources c
              ON c.id=e.source_component_id AND c.role='terms',policy p
            WHERE e.household_space_id=household_id AND c.family_member_id=member_id
              AND terms_edition_allows_pages(e.id,household_id,e.source_page_start,
                e.source_page_end)
              AND EXISTS(SELECT 1 FROM components pc
                WHERE metadata_terms_candidate(pc.proof_json,c.proof_json,p.product_display))
          ) SELECT jsonb_build_object(
            'policy',jsonb_build_object('id',p.id,'source_document_version_id',p.source_document_version_id,
              'source_evidence_id',p.source_evidence_id,'insurer_display',p.insurer_display,
              'product_display',p.product_display,'contract_date',p.contract_date,
              'contract_date_origin',CASE WHEN p.contract_date IS NULL THEN 'MISSING'
                WHEN coalesce((SELECT publication.field_values->>'contract_start'
                  FROM range_enrollment_publications publication
                  WHERE publication.policy_contract_id=p.id AND publication.rider_id IS NULL
                    AND publication.household_space_id=household_id
                  ORDER BY publication.created_at DESC,publication.candidate_version_id
                    DESC LIMIT 1),
                  (SELECT field.value#>>'{}' FROM analysis_candidate_versions candidate
                    JOIN analysis_candidate_fields field ON field.candidate_version_id=candidate.id
                      AND field.field_id='contract_start'
                    WHERE candidate.aggregate_id=p.id AND candidate.household_space_id=household_id
                      AND candidate.candidate_kind='policy_contract'
                      AND candidate.published_at IS NOT NULL
                    ORDER BY candidate.published_at DESC,candidate.id DESC LIMIT 1))
                    =to_char(p.contract_date,'YYYY-MM-DD') THEN 'COVERAGE_START_DERIVED'
                ELSE 'EXPLICIT_LEDGER' END,
              'source_page',p.physical_page,'source_review_state',p.evidence_review_state,
              'source_bbox',jsonb_build_array(p.source_x0::text,p.source_y0::text,
                p.source_x1::text,p.source_y1::text),
              'content_sha256',p.content_sha256,'extraction_id',p.extraction_id),
            'policy_components',coalesce((SELECT jsonb_agg(jsonb_build_array(c.id,
              c.metadata_publication_id,c.version,c.review_state) ORDER BY c.id) FROM
                components c),'[]'),
            'editions',coalesce((SELECT jsonb_agg(jsonb_build_array(e.id,e.component_id,e.version,
              e.component_version,e.review_state,e.metadata_publication_id) ORDER BY e.id)
              FROM editions e),'[]'),
            'parties',coalesce((SELECT jsonb_agg(jsonb_build_array(party.id,
              party.version,party.role,
              party.evidence_id) ORDER BY party.id) FROM policy_parties party
              WHERE party.policy_contract_id=p.id AND party.household_space_id=household_id
                AND party.family_member_id=member_id AND party.deleted_at IS NULL),'[]'),
            'user_decisions',coalesce((SELECT jsonb_agg(jsonb_build_array(s.id,
              s.version,s.deleted_at,
              item.id,item.version,item.match_state,item.deleted_at,item.insurance_document_component_id)
              ORDER BY s.id,item.id) FROM insurance_document_sets s
              LEFT JOIN insurance_document_set_items item ON item.insurance_document_set_id=s.id
                AND item.role='terms'
              WHERE s.household_space_id=household_id AND s.family_member_id=member_id
                AND s.policy_contract_id=p.id AND (item.id IS NOT NULL OR s.deleted_at
                  IS NOT NULL)),'[]')
          ) FROM policy p
        $$;
        CREATE TABLE policy_terms_applicability (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
          family_member_id UUID NOT NULL REFERENCES family_members(id) ON DELETE RESTRICT,
          policy_contract_id UUID NOT NULL REFERENCES policy_contracts(id) ON DELETE RESTRICT,
          policy_component_id UUID NOT NULL REFERENCES
            insurance_document_components(id) ON DELETE RESTRICT,
          terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
          policy_publication_id UUID NOT NULL REFERENCES
            document_metadata_publications(id) ON DELETE RESTRICT,
          terms_publication_id UUID NOT NULL REFERENCES
            document_metadata_publications(id) ON DELETE RESTRICT,
          revision VARCHAR(64) NOT NULL CHECK(revision='terms-applicability-v1'),
          status VARCHAR(16) NOT NULL CHECK(status IN ('MATCH','NO_MATCH','UNKNOWN')),
          selection_state VARCHAR(16) NOT NULL CHECK(selection_state IN
            ('AUTOMATIC','USER_SELECTED','USER_OWNED','UNRESOLVED')),
          matched_by VARCHAR(64) CHECK(matched_by IN
            ('EXPLICIT_EDITION_REFERENCE','PRODUCT_CODE_PRINTED_PERIOD')),
          reason_codes JSONB NOT NULL CHECK(jsonb_typeof(reason_codes)='array'
            AND jsonb_array_length(reason_codes) BETWEEN 1 AND 32),
          evidence_fields JSONB NOT NULL CHECK(jsonb_typeof(evidence_fields)='array'
            AND jsonb_array_length(evidence_fields)<=32),
          input_context JSONB NOT NULL CHECK(jsonb_typeof(input_context)='object'
            AND octet_length(input_context::text)<=1048576),
          input_digest VARCHAR(64) NOT NULL CHECK(input_digest~'^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(policy_contract_id,family_member_id,terms_edition_id,revision,input_digest),
          CHECK((status='MATCH')=(matched_by IS NOT NULL)),
          CHECK(status<>'MATCH' OR selection_state IN ('AUTOMATIC','USER_SELECTED'))
        );
        CREATE INDEX ix_policy_terms_scope ON policy_terms_applicability
          (household_space_id,family_member_id,policy_contract_id,terms_edition_id);
        CREATE TABLE policy_terms_refresh_checks (
          policy_contract_id UUID NOT NULL REFERENCES policy_contracts(id) ON DELETE CASCADE,
          family_member_id UUID NOT NULL REFERENCES family_members(id) ON DELETE CASCADE,
          checked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          input_digest VARCHAR(64),
          retry_after TIMESTAMPTZ,
          PRIMARY KEY(policy_contract_id,family_member_id)
        );
        CREATE FUNCTION protect_policy_terms_applicability() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'terms applicability history is immutable' USING ERRCODE='23514';
          END IF;
          IF (NEW.selection_state='AUTOMATIC' AND NEW.input_context->'user_decisions'<>'[]'::jsonb)
            OR (NEW.selection_state='USER_SELECTED' AND NOT EXISTS(
              SELECT 1 FROM jsonb_array_elements(NEW.input_context->'user_decisions') decision
              JOIN terms_editions e ON e.id=NEW.terms_edition_id
              WHERE decision->>2 IS NULL AND decision->>5='USER_CONFIRMED'
                AND decision->>6 IS NULL AND decision->>7=e.source_component_id::text))
            OR NEW.input_context IS DISTINCT FROM policy_terms_input_context(
              NEW.policy_contract_id,NEW.family_member_id,NEW.household_space_id)
            OR NEW.input_digest<>encode(sha256(convert_to(NEW.input_context::text,'UTF8')),'hex')
            OR NOT EXISTS(SELECT 1 FROM terms_applicability_component_sources pc
              JOIN terms_editions e ON e.id=NEW.terms_edition_id
              JOIN terms_applicability_component_sources tc ON tc.id=e.source_component_id
              WHERE pc.id=NEW.policy_component_id AND pc.role='policy' AND tc.role='terms'
                AND pc.household_space_id=NEW.household_space_id
                AND tc.household_space_id=NEW.household_space_id
                AND pc.family_member_id=NEW.family_member_id AND
                  tc.family_member_id=NEW.family_member_id
                AND pc.metadata_publication_id=NEW.policy_publication_id
                AND tc.metadata_publication_id=NEW.terms_publication_id
                AND NEW.input_context->'policy_components' @>
                  jsonb_build_array(jsonb_build_array(pc.id,pc.metadata_publication_id,pc.version,pc.review_state))
                AND NEW.input_context->'editions' @> jsonb_build_array(jsonb_build_array(e.id,tc.id,
                  e.version,tc.version,tc.review_state,tc.metadata_publication_id))) THEN
            RAISE EXCEPTION 'terms applicability source mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER policy_terms_applicability_guard BEFORE INSERT OR UPDATE OR DELETE
          ON policy_terms_applicability FOR EACH ROW EXECUTE FUNCTION
            protect_policy_terms_applicability();
        CREATE VIEW current_policy_terms_applicability AS
          SELECT a.* FROM (
            SELECT DISTINCT policy_contract_id,family_member_id,household_space_id
            FROM policy_terms_applicability WHERE revision='terms-applicability-v1'
          ) scope
          CROSS JOIN LATERAL (
            SELECT policy_terms_input_context(scope.policy_contract_id,
              scope.family_member_id,scope.household_space_id) AS context OFFSET 0
          ) current_input
          JOIN policy_terms_applicability a ON a.policy_contract_id=scope.policy_contract_id
            AND a.family_member_id=scope.family_member_id
            AND a.household_space_id=scope.household_space_id
            AND a.revision='terms-applicability-v1'
            AND a.input_digest=encode(sha256(convert_to(current_input.context::text,'UTF8')),'hex')
            AND a.input_context=current_input.context;
        CREATE FUNCTION policy_terms_edition_applies(policy_id UUID,edition_id UUID,
          household_id UUID)
        RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
          SELECT EXISTS(SELECT 1 FROM current_policy_terms_applicability a
            WHERE a.policy_contract_id=policy_id AND a.terms_edition_id=edition_id
              AND a.household_space_id=household_id AND a.status='MATCH'
              AND a.selection_state IN ('AUTOMATIC','USER_SELECTED'))
        $$;
        CREATE FUNCTION policy_terms_applicability_gate(policy_id UUID,edition_id UUID,
          household_id UUID)
        RETURNS TEXT LANGUAGE sql STABLE AS $$
          WITH current_inputs AS MATERIALIZED (
            SELECT policy_terms_input_context(policy_id,party.family_member_id,household_id)
              AS context
            FROM (SELECT DISTINCT family_member_id FROM policy_parties
              WHERE policy_contract_id=policy_id AND household_space_id=household_id
                AND role IN ('primary_insured','additional_insured') AND deleted_at IS NULL) party
          ), current_assessments AS (
            SELECT a.* FROM policy_terms_applicability a JOIN current_inputs current_input
              ON a.input_digest=encode(sha256(convert_to(current_input.context::text,'UTF8')),'hex')
                AND a.input_context=current_input.context
            WHERE a.policy_contract_id=policy_id AND a.terms_edition_id=edition_id
              AND a.household_space_id=household_id AND a.revision='terms-applicability-v1'
          ) SELECT CASE
            WHEN EXISTS(SELECT 1 FROM current_assessments WHERE status='MATCH'
              AND selection_state IN ('AUTOMATIC','USER_SELECTED')) THEN 'MATCH'
            WHEN EXISTS(SELECT 1 FROM current_inputs
              WHERE context->'policy'->>'contract_date_origin'='COVERAGE_START_DERIVED')
              THEN 'BLOCKED'
            WHEN EXISTS(SELECT 1 FROM policy_terms_applicability a WHERE
              a.policy_contract_id=policy_id
                AND a.terms_edition_id=edition_id AND a.household_space_id=household_id)
              AND (NOT EXISTS(SELECT 1 FROM current_assessments)
                OR EXISTS(SELECT 1 FROM current_assessments WHERE status='NO_MATCH'
                  OR reason_codes ?| ARRAY['POLICY_SOURCE_CORRECTED',
                    'USER_DOCUMENT_DECISION_EXISTS',
                    'AMBIGUOUS_MATCHING_EDITIONS','IDENTITY_METADATA_UNCERTAIN',
                    'MULTIPLE_APPLICATION_REFERENCES','PERIOD_METADATA_UNCERTAIN',
                    'INVALID_CONTRACT_DATE','INVALID_PRINTED_PERIOD',
                    'LEDGER_CONTRACT_DATE_CONFLICT'])) THEN 'BLOCKED'
            ELSE 'LEGACY' END
        $$;
        CREATE FUNCTION policy_terms_link_applicability(policy_id UUID,edition_id UUID,
          household_id UUID)
        RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
          SELECT EXISTS(SELECT 1 FROM policy_contracts p JOIN terms_editions e
            ON e.id=edition_id AND e.household_space_id=p.household_space_id
            WHERE p.id=policy_id AND p.household_space_id=household_id AND p.deleted_at IS NULL
              AND e.deleted_at IS NULL AND (e.source_component_id IS NULL OR (
                terms_edition_allows_pages(e.id,household_id,e.source_page_start,e.source_page_end)
                AND CASE policy_terms_applicability_gate(p.id,e.id,household_id)
                  WHEN 'MATCH' THEN true WHEN 'BLOCKED' THEN false ELSE
                    terms_edition_has_printed_period(e.id) AND
                      p.contract_date>=e.applicability_start
                    AND (e.applicability_end IS NULL OR p.contract_date<=e.applicability_end)
                    AND p.insurer_key=e.insurer_key AND p.product_key=e.product_key END)))
        $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM policy_terms_applicability) THEN
            RAISE EXCEPTION 'terms applicability history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        DROP FUNCTION IF EXISTS policy_terms_link_applicability(UUID,UUID,UUID);
        DROP FUNCTION IF EXISTS policy_terms_applicability_gate(UUID,UUID,UUID);
        DROP FUNCTION policy_terms_edition_applies(UUID,UUID,UUID);
        DROP VIEW current_policy_terms_applicability;
        DROP TABLE policy_terms_refresh_checks;
        DROP TABLE policy_terms_applicability;
        DROP FUNCTION protect_policy_terms_applicability();
        DROP FUNCTION policy_terms_input_context(UUID,UUID,UUID);
        DROP VIEW terms_applicability_component_sources;
        DROP FUNCTION metadata_terms_candidate(JSONB,JSONB,TEXT);
        DROP FUNCTION metadata_match_key(TEXT);
    """)
