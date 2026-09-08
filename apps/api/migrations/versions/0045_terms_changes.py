"""Retain scoped amendment assessments and their original source dependencies."""

from collections.abc import Sequence

from alembic import op

revision: str = "0045_terms_changes"
down_revision: str | Sequence[str] | None = "0044_metadata_insurer_captions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION terms_change_input_context(component_id UUID,household_id UUID)
      RETURNS JSONB LANGUAGE sql STABLE AS $$
        SELECT jsonb_build_object(
          'revision','terms-change-v1',
          'source',jsonb_build_array(c.id,c.version,c.review_state,c.metadata_publication_id,
            c.generation_id,c.family_member_id,c.content_sha256),
          'members',coalesce((SELECT jsonb_agg(jsonb_build_array(m.id,m.version,m.display_name,
            m.internal_alias) ORDER BY m.id) FROM family_members m WHERE
            m.household_space_id=household_id AND m.deleted_at IS NULL),'[]'),
          'contracts',coalesce((SELECT jsonb_agg(jsonb_build_array(p.id,p.version,
            p.source_document_version_id,p.source_evidence_id,p.insurer_display,p.product_display,
            p.contract_date) ORDER BY p.id) FROM policy_contracts p JOIN documents d ON d.id=(
              SELECT document_id FROM document_versions WHERE id=p.source_document_version_id)
            WHERE p.household_space_id=household_id AND p.deleted_at IS NULL
              AND d.deleted_at IS NULL),'[]'),
          'parties',coalesce((SELECT jsonb_agg(jsonb_build_array(p.id,p.policy_contract_id,
            p.family_member_id,p.role,p.version,p.evidence_id) ORDER BY p.id)
            FROM policy_parties p WHERE p.household_space_id=household_id
              AND p.deleted_at IS NULL),'[]'),
          'riders',coalesce((SELECT jsonb_agg(jsonb_build_array(r.id,r.policy_contract_id,
            r.source_evidence_id) ORDER BY r.id)
            FROM riders r WHERE r.household_space_id=household_id AND r.deleted_at IS NULL),'[]'),
          'ledger_evidence',coalesce((SELECT jsonb_agg(jsonb_build_array(e.id,e.document_version_id,
            e.extraction_id,e.content_sha256,e.physical_page,e.x0::text,e.y0::text,
            e.x1::text,e.y1::text,e.review_state,
            d.document_kind,d.deleted_at,x.status) ORDER BY e.id)
            FROM evidence e JOIN document_versions v ON v.id=e.document_version_id
            JOIN documents d ON d.id=v.document_id JOIN extractions x ON x.id=e.extraction_id
            WHERE e.household_space_id=household_id AND (EXISTS(SELECT 1 FROM policy_contracts p
              WHERE p.household_space_id=household_id AND p.source_evidence_id=e.id)
              OR EXISTS(SELECT 1 FROM riders r WHERE r.household_space_id=household_id
                AND r.source_evidence_id=e.id))),'[]'),
          'enrollment_sources',coalesce((SELECT jsonb_agg(jsonb_build_array(p.candidate_version_id,
            p.source_candidate_version_id,p.policy_contract_id,p.rider_id,p.authority,
            plan.generation_id) ORDER BY p.candidate_version_id)
            FROM range_enrollment_publications p JOIN policy_range_candidate_sources source
              ON source.candidate_version_id=p.source_candidate_version_id
            JOIN document_policy_range_plans plan ON plan.job_id=source.job_id
            JOIN document_structure_generations g ON g.id=plan.generation_id
            JOIN document_versions v ON v.id=g.document_version_id
            JOIN documents d ON d.id=v.document_id AND d.deleted_at IS NULL
              AND d.document_kind='policy'
            WHERE p.household_space_id=household_id),'[]'),
          'editions',coalesce((SELECT jsonb_agg(jsonb_build_array(e.id,e.version,source.id,
            source.version,source.review_state,source.metadata_publication_id) ORDER BY e.id)
            FROM terms_editions e JOIN terms_applicability_component_sources source
              ON source.id=e.source_component_id AND source.role='terms'
            WHERE e.household_space_id=household_id AND source.family_member_id=c.family_member_id
              AND terms_edition_allows_pages(e.id,household_id,
                e.source_page_start,e.source_page_end)),
              '[]'),
          'user_decisions',coalesce((SELECT jsonb_agg(jsonb_build_array(s.id,s.version,s.deleted_at,
            s.policy_contract_id,i.id,i.version,i.role,i.match_state,i.deleted_at,
            i.insurance_document_component_id) ORDER BY s.id,i.id)
            FROM insurance_document_sets s LEFT JOIN insurance_document_set_items i
              ON i.insurance_document_set_id=s.id AND i.role IN ('terms','amendment')
            WHERE s.household_space_id=household_id AND s.family_member_id=c.family_member_id
              AND (i.id IS NOT NULL OR s.deleted_at IS NOT NULL)),'[]')
        ) FROM terms_applicability_component_sources c JOIN household_spaces h
          ON h.id=c.household_space_id AND h.deleted_at IS NULL
        WHERE c.id=component_id AND c.household_space_id=household_id AND c.role='amendment'
      $$;

      CREATE TABLE policy_terms_changes (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        family_member_id UUID NOT NULL REFERENCES family_members(id) ON DELETE RESTRICT,
        source_component_id UUID NOT NULL
          REFERENCES insurance_document_components(id) ON DELETE RESTRICT,
        source_publication_id UUID NOT NULL
          REFERENCES document_metadata_publications(id) ON DELETE RESTRICT,
        source_generation_id UUID NOT NULL
          REFERENCES document_structure_generations(id) ON DELETE RESTRICT,
        source_content_sha256 VARCHAR(64) NOT NULL CHECK(source_content_sha256~'^[0-9a-f]{64}$'),
        policy_contract_id UUID REFERENCES policy_contracts(id) ON DELETE RESTRICT,
        rider_id UUID REFERENCES riders(id) ON DELETE RESTRICT,
        clause_id UUID REFERENCES clauses(id) ON DELETE RESTRICT,
        scope_kind VARCHAR(16) CHECK(scope_kind IN ('CONTRACT','RIDER','CLAUSE')),
        scope_resolved BOOLEAN NOT NULL,
        operation VARCHAR(16) CHECK(operation IN ('ADD','REPLACE')),
        change_kind VARCHAR(16) CHECK(change_kind IN ('AMENDMENT','RENEWAL')),
        previous_edition_id UUID REFERENCES terms_editions(id) ON DELETE RESTRICT,
        new_edition_id UUID REFERENCES terms_editions(id) ON DELETE RESTRICT,
        effective_from DATE,
        effective_through DATE,
        status VARCHAR(16) NOT NULL CHECK(status IN ('MATCH','NO_MATCH','UNKNOWN')),
        revision VARCHAR(64) NOT NULL CHECK(revision='terms-change-v1'),
        reason_codes JSONB NOT NULL CHECK(jsonb_typeof(reason_codes)='array'
          AND jsonb_array_length(reason_codes)<=32 AND octet_length(reason_codes::text)<=4096),
        source_fields JSONB NOT NULL CHECK(jsonb_typeof(source_fields)='array'
          AND jsonb_array_length(source_fields)<=128 AND octet_length(source_fields::text)<=524288),
        input_context JSONB NOT NULL CHECK(jsonb_typeof(input_context)='object'
          AND octet_length(input_context::text)<=1048576),
        input_digest VARCHAR(64) NOT NULL CHECK(input_digest~'^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(source_component_id,revision,input_digest),
        CHECK(NOT scope_resolved OR (policy_contract_id IS NOT NULL AND scope_kind IS NOT NULL
          AND CASE scope_kind WHEN 'CONTRACT' THEN rider_id IS NULL AND clause_id IS NULL
            WHEN 'RIDER' THEN rider_id IS NOT NULL AND clause_id IS NULL
            WHEN 'CLAUSE' THEN clause_id IS NOT NULL ELSE false END)),
        CHECK(status<>'MATCH' OR (scope_resolved AND new_edition_id IS NOT NULL
          AND effective_from IS NOT NULL AND operation IS NOT NULL AND change_kind IS NOT NULL
          AND (effective_through IS NULL OR effective_through>=effective_from)
          AND CASE operation WHEN 'ADD' THEN previous_edition_id IS NULL
            WHEN 'REPLACE' THEN previous_edition_id IS NOT NULL
              AND previous_edition_id<>new_edition_id ELSE false END))
      );
      CREATE INDEX ix_policy_terms_changes_scope ON policy_terms_changes
        (household_space_id,family_member_id,policy_contract_id,rider_id,clause_id);
      CREATE TABLE terms_change_refresh_checks (
        source_component_id UUID PRIMARY KEY
          REFERENCES insurance_document_components(id) ON DELETE CASCADE,
        input_digest VARCHAR(64),
        checked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        retry_after TIMESTAMPTZ
      );

      CREATE FUNCTION terms_change_fields_valid(generation_id UUID,household_id UUID,
        component_id UUID,fields JSONB)
      RETURNS BOOLEAN LANGUAGE plpgsql STABLE AS $$
      DECLARE f JSONB; s JSONB; nodes JSONB; source JSONB; component RECORD; key TEXT;
        cached_page INT;
      BEGIN
        SELECT c.page_start,c.page_end INTO component FROM insurance_document_components c
          WHERE c.id=component_id AND c.household_space_id=household_id;
        IF NOT FOUND OR jsonb_typeof(fields) IS DISTINCT FROM 'array'
          OR jsonb_array_length(fields)>128 THEN RETURN false; END IF;
        FOR f IN SELECT value FROM jsonb_array_elements(fields) LOOP
          IF jsonb_typeof(f) IS DISTINCT FROM 'object'
            OR NOT f ?& ARRAY['name','value','spans']
            OR f-ARRAY['name','value','spans']<>'{}'::jsonb
            OR jsonb_typeof(f->'name') IS DISTINCT FROM 'string'
            OR f->>'name' NOT IN ('contract_number','insured','insurer','change_kind','operation',
              'scope_kind','rider_name','clause_label','previous_terms_code','previous_edition_code',
              'new_terms_code','new_edition_code','effective_from','effective_through')
            OR jsonb_typeof(f->'value') IS DISTINCT FROM 'string'
            OR char_length(f->>'value') NOT BETWEEN 1 AND 240
            OR jsonb_typeof(f->'spans') IS DISTINCT FROM 'array'
            OR jsonb_array_length(f->'spans')<>1 THEN RETURN false; END IF;
          s := f->'spans'->0;
          IF jsonb_typeof(s) IS DISTINCT FROM 'object'
            OR NOT s ?& ARRAY['node_id','page_number','start','end','text',
              'anchor_start','anchor_end']
            OR s-ARRAY['node_id','page_number','start','end','text','anchor_start','anchor_end']
              <>'{}'::jsonb
            OR jsonb_typeof(s->'node_id') IS DISTINCT FROM 'string'
            OR char_length(s->>'node_id') NOT BETWEEN 1 AND 256
            OR s->>'text' IS DISTINCT FROM f->>'value' THEN RETURN false; END IF;
          FOREACH key IN ARRAY ARRAY['page_number','start','end','anchor_start','anchor_end'] LOOP
            IF jsonb_typeof(s->key) IS DISTINCT FROM 'number'
              OR s->>key !~ '^(0|[1-9][0-9]{0,6})$' THEN RETURN false; END IF;
          END LOOP;
          IF (s->>'page_number')::int NOT BETWEEN component.page_start AND component.page_end
            OR (s->>'start')::int<(s->>'anchor_start')::int
            OR (s->>'end')::int<=(s->>'start')::int
            OR (s->>'anchor_end')::int<(s->>'end')::int THEN RETURN false; END IF;
          IF cached_page IS DISTINCT FROM (s->>'page_number')::int THEN
            cached_page := (s->>'page_number')::int;
            SELECT document_structure_projection(generation_id,household_id,
              ARRAY[cached_page]) INTO source;
          END IF;
          SELECT jsonb_agg(n) INTO nodes FROM jsonb_array_elements(source->'nodes') n
            WHERE n->>'node_id'=s->>'node_id' AND n->>'page_number'=s->>'page_number';
          IF nodes IS NULL OR jsonb_array_length(nodes)<>1
            OR (s->>'anchor_end')::int>char_length(nodes->0->>'text')
            OR substring(nodes->0->>'text' FROM (s->>'start')::int+1
              FOR (s->>'end')::int-(s->>'start')::int) IS DISTINCT FROM s->>'text'
            THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
      EXCEPTION WHEN others THEN RETURN false;
      END
      $$;
      CREATE FUNCTION terms_change_field_value(fields JSONB,name TEXT) RETURNS TEXT
      LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE WHEN count(DISTINCT metadata_match_key(f->>'value'))=1
          THEN min(metadata_match_key(f->>'value')) ELSE NULL END
        FROM jsonb_array_elements(fields) f WHERE f->>'name'=name
      $$;
      CREATE FUNCTION terms_change_date_value(value TEXT) RETURNS DATE
      LANGUAGE plpgsql IMMUTABLE AS $$
      DECLARE parts TEXT[];
      BEGIN
        parts := regexp_match(value,'^([0-9]{4})[-./]([0-9]{1,2})[-./]([0-9]{1,2})\.?$');
        IF parts IS NULL THEN RETURN NULL; END IF;
        RETURN make_date(parts[1]::int,parts[2]::int,parts[3]::int);
      EXCEPTION WHEN others THEN RETURN NULL;
      END $$;
      CREATE FUNCTION terms_change_semantics_valid(change policy_terms_changes) RETURNS BOOLEAN
      LANGUAGE plpgsql STABLE AS $$
      DECLARE fields JSONB := change.source_fields; name TEXT; required TEXT[];
      BEGIN
        IF change.status<>'MATCH' THEN RETURN true; END IF;
        required := ARRAY['contract_number','insured','insurer','change_kind','operation',
          'scope_kind','new_terms_code','new_edition_code','effective_from'];
        IF change.operation='REPLACE' THEN required := required || ARRAY[
          'previous_terms_code','previous_edition_code']; END IF;
        IF change.scope_kind='RIDER' THEN required := required || ARRAY['rider_name']; END IF;
        IF change.scope_kind='CLAUSE' THEN required := required || ARRAY['clause_label']; END IF;
        FOREACH name IN ARRAY required LOOP
          IF terms_change_field_value(fields,name) IS NULL THEN RETURN false; END IF;
        END LOOP;
        IF terms_change_date_value(terms_change_field_value(fields,'effective_from'))
            IS DISTINCT FROM change.effective_from
          OR terms_change_date_value(terms_change_field_value(fields,'effective_through'))
            IS DISTINCT FROM change.effective_through
          OR (CASE terms_change_field_value(fields,'operation')
            WHEN '교체' THEN 'REPLACE' WHEN 'replace' THEN 'REPLACE'
            WHEN '추가' THEN 'ADD' WHEN 'add' THEN 'ADD' END) IS DISTINCT FROM change.operation
          OR (CASE terms_change_field_value(fields,'change_kind')
            WHEN '조건변경' THEN 'AMENDMENT' WHEN 'amendment' THEN 'AMENDMENT'
            WHEN '갱신' THEN 'RENEWAL' WHEN 'renewal' THEN 'RENEWAL' END)
              IS DISTINCT FROM change.change_kind
          OR (CASE terms_change_field_value(fields,'scope_kind')
            WHEN '계약전체' THEN 'CONTRACT' WHEN 'contract' THEN 'CONTRACT'
            WHEN '특약' THEN 'RIDER' WHEN '담보' THEN 'RIDER' WHEN 'rider' THEN 'RIDER'
            WHEN '조항' THEN 'CLAUSE' WHEN 'clause' THEN 'CLAUSE' END)
              IS DISTINCT FROM change.scope_kind THEN RETURN false; END IF;
        RETURN true;
      END $$;
      CREATE FUNCTION protect_policy_terms_changes() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'terms change history is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.input_context IS DISTINCT FROM terms_change_input_context(
            NEW.source_component_id,NEW.household_space_id)
          OR NEW.input_digest<>encode(sha256(convert_to(NEW.input_context::text,'UTF8')),'hex')
          OR NOT EXISTS(SELECT 1 FROM terms_applicability_component_sources c
            WHERE c.id=NEW.source_component_id AND c.household_space_id=NEW.household_space_id
              AND c.family_member_id=NEW.family_member_id AND c.role='amendment'
              AND c.metadata_publication_id=NEW.source_publication_id
              AND c.generation_id=NEW.source_generation_id
              AND c.content_sha256=NEW.source_content_sha256)
          OR (NEW.policy_contract_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM policy_contracts p JOIN policy_parties party
              ON party.policy_contract_id=p.id
            WHERE p.id=NEW.policy_contract_id AND p.household_space_id=NEW.household_space_id
              AND party.household_space_id=NEW.household_space_id
              AND party.family_member_id=NEW.family_member_id
              AND party.role IN ('primary_insured','additional_insured')
              AND p.deleted_at IS NULL AND party.deleted_at IS NULL))
          OR (NEW.rider_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM riders r
            WHERE r.id=NEW.rider_id AND r.household_space_id=NEW.household_space_id
              AND r.policy_contract_id=NEW.policy_contract_id AND r.deleted_at IS NULL))
          OR (NEW.clause_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM clauses c
            WHERE c.id=NEW.clause_id AND c.household_space_id=NEW.household_space_id
              AND c.deleted_at IS NULL AND c.terms_edition_id IN
                (NEW.previous_edition_id,NEW.new_edition_id)))
          OR EXISTS(SELECT 1 FROM unnest(ARRAY[NEW.previous_edition_id,NEW.new_edition_id])
            AS target(edition_id)
            WHERE target.edition_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM terms_editions e
              JOIN terms_applicability_component_sources c ON c.id=e.source_component_id
              WHERE e.id=target.edition_id AND e.household_space_id=NEW.household_space_id
                AND c.family_member_id=NEW.family_member_id AND c.role='terms'
                AND terms_edition_allows_pages(e.id,NEW.household_space_id,
                  e.source_page_start,e.source_page_end)))
          OR NOT terms_change_fields_valid(NEW.source_generation_id,
            NEW.household_space_id,NEW.source_component_id,NEW.source_fields)
          OR NOT terms_change_semantics_valid(NEW) THEN
          RAISE EXCEPTION 'terms change source mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER policy_terms_changes_guard BEFORE INSERT OR UPDATE OR DELETE
        ON policy_terms_changes FOR EACH ROW EXECUTE FUNCTION protect_policy_terms_changes();
      CREATE VIEW latest_policy_terms_changes AS
        SELECT DISTINCT ON (source_component_id) * FROM policy_terms_changes
        WHERE revision='terms-change-v1' ORDER BY source_component_id,created_at DESC,id DESC;
      CREATE VIEW terms_change_current_inputs AS
        SELECT scope.*,current_input.context FROM (
          SELECT DISTINCT source_component_id,household_space_id FROM policy_terms_changes
          WHERE revision='terms-change-v1'
        ) scope CROSS JOIN LATERAL (
          SELECT terms_change_input_context(scope.source_component_id,scope.household_space_id)
            AS context OFFSET 0
        ) current_input;
      CREATE VIEW current_policy_terms_changes AS
        SELECT a.* FROM policy_terms_changes a JOIN terms_change_current_inputs current_input
          ON current_input.source_component_id=a.source_component_id
            AND current_input.household_space_id=a.household_space_id
        WHERE a.revision='terms-change-v1' AND a.input_context=current_input.context
          AND a.input_digest=encode(sha256(convert_to(current_input.context::text,'UTF8')),'hex');
      CREATE VIEW effective_policy_terms_changes AS
        SELECT DISTINCT ON (a.source_component_id) a.* FROM policy_terms_changes a
        JOIN terms_change_current_inputs current_input
          ON current_input.source_component_id=a.source_component_id
            AND current_input.household_space_id=a.household_space_id
        WHERE a.revision='terms-change-v1' ORDER BY a.source_component_id,
          (a.input_context=current_input.context) DESC NULLS LAST,a.created_at DESC,a.id DESC;
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_terms_changes) THEN
          RAISE EXCEPTION 'terms change history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP VIEW effective_policy_terms_changes;
      DROP VIEW current_policy_terms_changes;
      DROP VIEW terms_change_current_inputs;
      DROP VIEW latest_policy_terms_changes;
      DROP TABLE terms_change_refresh_checks;
      DROP TRIGGER policy_terms_changes_guard ON policy_terms_changes;
      DROP FUNCTION protect_policy_terms_changes();
      DROP FUNCTION terms_change_semantics_valid(policy_terms_changes);
      DROP TABLE policy_terms_changes;
      DROP FUNCTION terms_change_date_value(TEXT);
      DROP FUNCTION terms_change_field_value(JSONB,TEXT);
      DROP FUNCTION terms_change_fields_valid(UUID,UUID,UUID,JSONB);
      DROP FUNCTION terms_change_input_context(UUID,UUID);
    """)
