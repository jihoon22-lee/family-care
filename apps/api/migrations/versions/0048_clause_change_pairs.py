"""Bind amendment targets to both original Clause identities without widening scope."""

from collections.abc import Sequence

from alembic import op

revision: str = "0048_clause_change_pairs"
down_revision: str | Sequence[str] | None = "0047_clause_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_FUNCTIONS_V1 = r"""
      CREATE OR REPLACE FUNCTION terms_change_fields_valid(generation_id UUID,household_id UUID,
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
      CREATE OR REPLACE FUNCTION terms_change_field_value(fields JSONB,name TEXT) RETURNS TEXT
      LANGUAGE sql IMMUTABLE AS $$
        SELECT CASE WHEN count(DISTINCT metadata_match_key(f->>'value'))=1
          THEN min(metadata_match_key(f->>'value')) ELSE NULL END
        FROM jsonb_array_elements(fields) f WHERE f->>'name'=name
      $$;
      CREATE OR REPLACE FUNCTION terms_change_date_value(value TEXT) RETURNS DATE
      LANGUAGE plpgsql IMMUTABLE AS $$
      DECLARE parts TEXT[];
      BEGIN
        parts := regexp_match(value,'^([0-9]{4})[-./]([0-9]{1,2})[-./]([0-9]{1,2})\.?$');
        IF parts IS NULL THEN RETURN NULL; END IF;
        RETURN make_date(parts[1]::int,parts[2]::int,parts[3]::int);
      EXCEPTION WHEN others THEN RETURN NULL;
      END $$;
      CREATE OR REPLACE FUNCTION terms_change_semantics_valid(change policy_terms_changes) RETURNS
        BOOLEAN
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
"""
_VIEWS_V1 = r"""
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
"""


_PROTECT_V1 = r"""
      CREATE OR REPLACE FUNCTION protect_policy_terms_changes() RETURNS trigger LANGUAGE plpgsql
        AS $$
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
"""


_DECISION_SNAPSHOT_V1 = r"""
      CREATE OR REPLACE FUNCTION decision_terms_snapshot_valid(
        payload JSONB,household_id UUID,event_id UUID,event_revision INTEGER
      ) RETURNS BOOLEAN LANGUAGE plpgsql STABLE AS $$
      DECLARE item JSONB; scope JSONB; edition JSONB; relation_id UUID;
        member_id UUID; event_day DATE; current_revision INTEGER;
      BEGIN
        IF payload IS NULL THEN RETURN true; END IF;
        IF jsonb_typeof(payload)<>'array' OR jsonb_array_length(payload)>1024
          OR octet_length(payload::text)>8388608 THEN RETURN false; END IF;
        SELECT e.family_member_id,e.event_date,e.version INTO member_id,event_day,current_revision
          FROM medical_events e WHERE e.id=event_id AND e.household_space_id=household_id;
        IF NOT FOUND OR current_revision<>event_revision THEN RETURN false; END IF;
        IF (SELECT count(*)<>count(DISTINCT s->'scope')
          FROM jsonb_array_elements(payload) s) THEN RETURN false; END IF;
        FOR item IN SELECT * FROM jsonb_array_elements(payload) LOOP
          IF jsonb_typeof(item)<>'object' OR NOT item ?& ARRAY['scope','event_date','editions',
            'applied_relation_ids','uncertain_relation_ids','scope_uncertainties','base_assessment_ids']
            OR item-ARRAY['scope','event_date','editions','applied_relation_ids',
              'uncertain_relation_ids','scope_uncertainties','base_assessment_ids']<>'{}'::jsonb
            OR (item->>'event_date')::date IS DISTINCT FROM event_day THEN RETURN false; END IF;
          scope:=item->'scope';
          IF jsonb_typeof(scope)<>'object' OR NOT scope ?& ARRAY['household_space_id',
              'policy_contract_id','family_member_id','rider_id','clause_id']
            OR scope-ARRAY['household_space_id','policy_contract_id','family_member_id',
              'rider_id','clause_id']<>'{}'::jsonb
            OR (scope->>'household_space_id')::uuid IS DISTINCT FROM household_id
            OR (scope->>'family_member_id')::uuid IS DISTINCT FROM member_id
            OR NOT EXISTS(SELECT 1 FROM riders r JOIN policy_contracts p
              ON p.id=r.policy_contract_id
              AND p.household_space_id=r.household_space_id
              JOIN policy_parties party ON party.policy_contract_id=p.id
              AND party.household_space_id=p.household_space_id AND party.family_member_id=member_id
              AND party.role IN ('primary_insured','additional_insured')
              AND party.deleted_at IS NULL
              WHERE r.id=(scope->>'rider_id')::uuid AND r.household_space_id=household_id
              AND p.id=(scope->>'policy_contract_id')::uuid AND p.deleted_at IS NULL
              AND r.deleted_at IS NULL) THEN RETURN false; END IF;
          IF scope->>'clause_id' IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM rider_clause_links l JOIN clauses c ON c.id=l.clause_id
            AND c.household_space_id=l.household_space_id WHERE l.household_space_id=household_id
            AND l.rider_id=(scope->>'rider_id')::uuid AND c.id=(scope->>'clause_id')::uuid
          ) THEN RETURN false; END IF;
          IF jsonb_typeof(item->'editions')<>'array' OR jsonb_array_length(item->'editions')>512
            OR jsonb_typeof(item->'applied_relation_ids')<>'array'
            OR jsonb_typeof(item->'uncertain_relation_ids')<>'array'
            OR jsonb_typeof(item->'scope_uncertainties')<>'array'
            OR jsonb_typeof(item->'base_assessment_ids')<>'array'
            OR jsonb_array_length(item->'base_assessment_ids')>512 THEN RETURN false; END IF;
          IF EXISTS(SELECT 1 FROM jsonb_array_elements_text(item->'base_assessment_ids') ids(value)
            WHERE NOT EXISTS(SELECT 1 FROM policy_terms_applicability a WHERE a.id=ids.value::uuid
              AND a.household_space_id=household_id AND a.family_member_id=member_id
              AND a.policy_contract_id=(scope->>'policy_contract_id')::uuid))
            THEN RETURN false; END IF;
          FOR edition IN SELECT * FROM jsonb_array_elements(item->'editions') LOOP
            IF jsonb_typeof(edition)<>'object' OR NOT edition ?& ARRAY[
                'edition_id','status','relation_ids','reason_codes']
              OR edition-ARRAY['edition_id','status','relation_ids','reason_codes']<>'{}'::jsonb
              OR coalesce(edition->>'status','') NOT IN ('MATCH','NO_MATCH','UNKNOWN')
              OR NOT EXISTS(SELECT 1 FROM terms_editions e
                WHERE e.id=(edition->>'edition_id')::uuid AND e.household_space_id=household_id)
              THEN RETURN false; END IF;
          END LOOP;
          FOR relation_id IN
            SELECT value::uuid FROM jsonb_array_elements_text(
              (item->'applied_relation_ids')||(item->'uncertain_relation_ids'))
            UNION SELECT refs.identifier::uuid
              FROM jsonb_array_elements(item->'editions') AS editions(entry),
              LATERAL jsonb_array_elements_text(editions.entry->'relation_ids') AS refs(identifier)
            UNION SELECT (u->>'relation_id')::uuid
              FROM jsonb_array_elements(item->'scope_uncertainties') u
          LOOP
            IF NOT EXISTS(SELECT 1 FROM policy_terms_changes a WHERE a.id=relation_id
              AND a.household_space_id=household_id AND a.family_member_id=member_id
              AND a.policy_contract_id=(scope->>'policy_contract_id')::uuid
              AND (a.rider_id IS NULL OR a.rider_id=(scope->>'rider_id')::uuid)
              AND (a.clause_id IS NULL OR a.clause_id=(scope->>'clause_id')::uuid))
              THEN RETURN false; END IF;
          END LOOP;
        END LOOP;
        RETURN true;
      EXCEPTION WHEN OTHERS THEN RETURN false;
      END $$;
"""


def _drop_views() -> None:
    op.execute("""
      DROP VIEW effective_policy_terms_changes;
      DROP VIEW current_policy_terms_changes;
      DROP VIEW terms_change_current_inputs;
      DROP VIEW latest_policy_terms_changes;
    """)


def upgrade() -> None:
    _drop_views()
    op.execute(r"""
      ALTER FUNCTION terms_change_input_context(UUID,UUID) RENAME TO terms_change_input_context_v1;
      CREATE FUNCTION terms_change_input_context(component_id UUID,household_id
        UUID,requested_scope TEXT)
      RETURNS JSONB LANGUAGE sql STABLE AS $$
        SELECT base.context ||
          jsonb_build_object('revision','terms-change-v2','scope_kind',requested_scope)
          || CASE WHEN requested_scope='CLAUSE' THEN jsonb_build_object('clauses',coalesce((SELECT
            jsonb_agg(jsonb_build_array(c.id,
              clause_source_input_context(c.id,household_id),
              (SELECT jsonb_agg(jsonb_build_array(a.id,a.input_digest,a.status) ORDER BY a.id)
                FROM current_clause_source_assessments a WHERE a.clause_id=c.id)) ORDER BY c.id)
            FROM clauses c JOIN terms_editions e ON e.id=c.terms_edition_id
              AND e.household_space_id=c.household_space_id
            JOIN insurance_document_components source ON source.id=e.source_component_id
            WHERE c.household_space_id=household_id AND source.family_member_id=
              (base.context->'source'->>5)::uuid),'[]'::jsonb),
          'clause_links',coalesce((SELECT jsonb_agg(jsonb_build_array(l.id,l.rider_id,
              l.terms_edition_id,l.clause_id,l.candidate_version_id,l.deleted_at,
              l.review_state='rejected',candidate.candidate_kind,candidate.aggregate_id,
              candidate.status,candidate.is_current,candidate.deleted_at,candidate.issues,
              (SELECT jsonb_agg(to_jsonb(f) ORDER BY f.field_id) FROM analysis_candidate_fields f
                WHERE f.candidate_version_id=candidate.id),
              (SELECT jsonb_agg(to_jsonb(ev)-'bounded_excerpt' ORDER BY ev.field_id,ev.evidence_id)
                FROM analysis_candidate_evidence ev WHERE ev.candidate_version_id=candidate.id),
              (SELECT jsonb_agg(ev.evidence_id ORDER BY ev.evidence_id)
                FROM rider_clause_link_evidence ev WHERE ev.rider_clause_link_id=l.id)) ORDER BY
                  l.id)
            FROM rider_clause_links l JOIN analysis_candidate_versions candidate
              ON candidate.id=l.candidate_version_id AND
                candidate.household_space_id=l.household_space_id
            WHERE l.household_space_id=household_id),'[]'::jsonb)) ELSE '{}'::jsonb END
        FROM (SELECT terms_change_input_context_v1(component_id,household_id) AS context) base
        WHERE base.context IS NOT NULL
      $$;
      CREATE FUNCTION terms_change_input_context(component_id UUID,household_id UUID)
      RETURNS JSONB LANGUAGE sql STABLE AS $$
        SELECT terms_change_input_context(component_id,household_id,(
          SELECT a.scope_kind::text FROM policy_terms_changes a
          WHERE a.source_component_id=component_id AND a.household_space_id=household_id
          ORDER BY a.created_at DESC,a.id DESC LIMIT 1))
      $$;
      ALTER TABLE policy_terms_changes DROP CONSTRAINT policy_terms_changes_revision_check;
      ALTER TABLE policy_terms_changes ADD CONSTRAINT policy_terms_changes_revision_check
        CHECK(revision IN ('terms-change-v1','terms-change-v2'));
      ALTER TABLE policy_terms_changes
        ADD COLUMN previous_clause_id UUID REFERENCES clauses(id) ON DELETE RESTRICT,
        ADD COLUMN new_clause_id UUID REFERENCES clauses(id) ON DELETE RESTRICT,
        ADD COLUMN previous_clause_source_id UUID REFERENCES clause_source_assessments(id) ON
          DELETE RESTRICT,
        ADD COLUMN new_clause_source_id UUID REFERENCES clause_source_assessments(id) ON DELETE
          RESTRICT,
        ADD CONSTRAINT ck_change_clause_pair_shape CHECK(
          (revision='terms-change-v1' AND num_nonnulls(previous_clause_id,new_clause_id,
            previous_clause_source_id,new_clause_source_id)=0)
          OR (revision='terms-change-v2'
            AND num_nonnulls(previous_clause_id,previous_clause_source_id) IN (0,2)
            AND num_nonnulls(new_clause_id,new_clause_source_id) IN (0,2)
            AND CASE WHEN scope_kind='CLAUSE' THEN
              clause_id IS NOT DISTINCT FROM coalesce(previous_clause_id,new_clause_id)
              AND (status<>'MATCH' OR (rider_id IS NOT NULL AND new_clause_id IS NOT NULL
                AND (operation='ADD' AND previous_clause_id IS NULL OR operation='REPLACE'
                  AND previous_clause_id IS NOT NULL AND previous_clause_id<>new_clause_id)))
            ELSE num_nonnulls(previous_clause_id,new_clause_id,
              previous_clause_source_id,new_clause_source_id)=0 END));
      CREATE FUNCTION protect_terms_change_clause_pair() RETURNS TRIGGER LANGUAGE plpgsql AS $$
      DECLARE side RECORD;
      BEGIN
        IF NEW.revision<>'terms-change-v2' THEN RETURN NEW; END IF;
        FOR side IN SELECT * FROM (VALUES
          ('previous',NEW.previous_clause_id,NEW.previous_clause_source_id,NEW.previous_edition_id),
          ('new',NEW.new_clause_id,NEW.new_clause_source_id,NEW.new_edition_id))
          AS sides(kind,clause_id,source_id,edition_id)
        LOOP
          IF side.clause_id IS NOT NULL AND NOT EXISTS(
            SELECT 1 FROM current_clause_source_assessments a JOIN clauses c ON c.id=a.clause_id
            WHERE a.id=side.source_id AND a.clause_id=side.clause_id
              AND a.household_space_id=NEW.household_space_id AND a.terms_edition_id=side.edition_id
              AND a.status='MATCH' AND c.deleted_at IS NULL AND EXISTS(
                SELECT 1 FROM rider_clause_links l JOIN analysis_candidate_versions candidate
                  ON candidate.id=l.candidate_version_id AND
                    candidate.household_space_id=l.household_space_id
                WHERE l.household_space_id=NEW.household_space_id AND l.rider_id=NEW.rider_id
                  AND l.clause_id=c.id AND l.terms_edition_id=side.edition_id
                  AND l.deleted_at IS NULL AND l.review_state<>'rejected'
                  AND candidate.candidate_kind='rider_clause' AND candidate.aggregate_id=l.id
                  AND candidate.is_current AND candidate.deleted_at IS NULL
                  AND candidate.status IN ('AI_VERIFIED','USER_CONFIRMED')))
            AND NOT (NEW.status='UNKNOWN' AND EXISTS(
              SELECT 1 FROM policy_terms_changes prior JOIN clause_source_assessments a
                ON a.id=side.source_id JOIN clauses c ON c.id=a.clause_id
              WHERE prior.status='MATCH' AND prior.scope_kind='CLAUSE'
                AND prior.source_component_id=NEW.source_component_id
                AND prior.source_publication_id=NEW.source_publication_id
                AND prior.source_generation_id=NEW.source_generation_id
                AND prior.source_content_sha256=NEW.source_content_sha256
                AND prior.household_space_id=NEW.household_space_id
                AND prior.family_member_id=NEW.family_member_id
                AND prior.policy_contract_id=NEW.policy_contract_id AND prior.rider_id=NEW.rider_id
                AND prior.source_fields=NEW.source_fields
                AND a.household_space_id=NEW.household_space_id AND a.clause_id=side.clause_id
                AND a.terms_edition_id=side.edition_id AND a.status='MATCH'
                AND c.deleted_at IS NULL AND c.terms_edition_id=side.edition_id
                AND CASE side.kind WHEN 'previous' THEN prior.previous_clause_id=side.clause_id
                  AND prior.previous_clause_source_id=side.source_id
                  AND prior.previous_edition_id=side.edition_id
                ELSE prior.new_clause_id=side.clause_id AND
                  prior.new_clause_source_id=side.source_id
                  AND prior.new_edition_id=side.edition_id END)) THEN
            RAISE EXCEPTION 'terms change clause source mismatch' USING ERRCODE='23514';
          END IF;
        END LOOP;
        RETURN NEW;
      END $$;
      CREATE TRIGGER terms_change_clause_pair_guard BEFORE INSERT ON policy_terms_changes
        FOR EACH ROW EXECUTE FUNCTION protect_terms_change_clause_pair();
    """)
    op.execute(
        _FUNCTIONS_V1.replace(
            "'clause_label','previous_terms_code'",
            "'clause_label','new_clause_label','previous_terms_code'",
        ).replace(
            "IF change.scope_kind='CLAUSE' THEN required := required || ARRAY['clause_label']; "
            "END IF;",
            "IF change.scope_kind='CLAUSE' THEN required := required || ARRAY[CASE WHEN "
            "change.operation='ADD' AND terms_change_field_value(fields,'new_clause_label') IS "
            "NOT NULL "
            "THEN 'new_clause_label' ELSE 'clause_label' END]; END IF;",
        )
    )
    op.execute(
        _PROTECT_V1.replace(
            "NEW.source_component_id,NEW.household_space_id)",
            "NEW.source_component_id,NEW.household_space_id,NEW.scope_kind)",
        )
    )
    op.execute(
        _VIEWS_V1.replace(
            "WHERE revision='terms-change-v1'",
            "WHERE revision IN ('terms-change-v1','terms-change-v2')",
        )
        .replace("WHERE a.revision='terms-change-v1' AND", "WHERE a.revision='terms-change-v2' AND")
        .replace(
            "WHERE a.revision='terms-change-v1' ORDER",
            "WHERE a.revision IN ('terms-change-v1','terms-change-v2') ORDER",
        )
        .replace(
            "SELECT DISTINCT source_component_id,household_space_id FROM",
            "SELECT DISTINCT source_component_id,household_space_id,scope_kind FROM",
        )
        .replace(
            "scope.source_component_id,scope.household_space_id)",
            "scope.source_component_id,scope.household_space_id,scope.scope_kind)",
        )
        .replace(
            "current_input.household_space_id=a.household_space_id",
            "current_input.household_space_id=a.household_space_id "
            "AND current_input.scope_kind IS NOT DISTINCT FROM a.scope_kind",
        )
    )

    op.execute(r"""
      CREATE FUNCTION terms_change_snapshot_clause_scope(relation_id UUID,scope JSONB,lineage JSONB)
      RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
        WITH RECURSIVE edges AS (
          SELECT a.previous_clause_id AS previous,a.new_clause_id AS successor
          FROM policy_terms_changes a
          WHERE a.id IN (SELECT value::uuid FROM jsonb_array_elements_text(lineage))
            AND a.household_space_id=(scope->>'household_space_id')::uuid
            AND a.family_member_id=(scope->>'family_member_id')::uuid
            AND a.policy_contract_id=(scope->>'policy_contract_id')::uuid
            AND a.rider_id=(scope->>'rider_id')::uuid AND a.scope_kind='CLAUSE'
            AND a.status='MATCH' AND a.previous_clause_id IS NOT NULL
            AND a.new_clause_id IS NOT NULL
        ), reached(id) AS (
          SELECT (scope->>'clause_id')::uuid UNION
          SELECT CASE WHEN e.previous=r.id THEN e.successor ELSE e.previous END
            FROM reached r JOIN edges e ON r.id IN (e.previous,e.successor)
        ) SELECT EXISTS(SELECT 1 FROM policy_terms_changes a
          WHERE a.id=relation_id AND (a.previous_clause_id IN (SELECT id FROM reached)
            OR a.new_clause_id IN (SELECT id FROM reached)))
      $$;
    """)
    op.execute(
        _DECISION_SNAPSHOT_V1.replace(
            "AND (a.clause_id IS NULL OR a.clause_id=(scope->>'clause_id')::uuid))",
            "AND (a.clause_id IS NULL OR a.clause_id=(scope->>'clause_id')::uuid "
            "OR relation_id IN (SELECT value::uuid FROM "
            "jsonb_array_elements_text(item->'uncertain_relation_ids')) "
            "OR terms_change_snapshot_clause_scope(a.id,scope,"
            "coalesce(item->'scope_relation_ids',item->'applied_relation_ids'))))",
        )
        .replace(
            "THEN RETURN false; END IF;\n          END LOOP;\n        END LOOP;",
            "THEN RETURN false; END IF;\n"
            "            IF relation_id IN (SELECT value::uuid FROM "
            "jsonb_array_elements_text(item->'applied_relation_ids')) "
            "AND EXISTS(SELECT 1 FROM policy_terms_changes a WHERE a.id=relation_id AND "
            "a.status<>'MATCH') "
            "THEN RETURN false; END IF;\n          END LOOP;\n        END LOOP;",
        )
        .replace(
            "'uncertain_relation_ids','scope_uncertainties','base_assessment_ids']<>",
            "'uncertain_relation_ids','scope_uncertainties','base_assessment_ids',"
            "'scope_relation_ids']<>",
        )
        .replace(
            "          FOR relation_id IN\n",
            """
          IF item ? 'scope_relation_ids' THEN
            IF jsonb_typeof(item->'scope_relation_ids') IS DISTINCT FROM 'array'
              OR jsonb_array_length(item->'scope_relation_ids')>128
              THEN RETURN false; END IF;
            FOR relation_id IN SELECT value::uuid FROM
              jsonb_array_elements_text(item->'scope_relation_ids') LOOP
              IF NOT EXISTS(SELECT 1 FROM policy_terms_changes a WHERE a.id=relation_id
                AND a.household_space_id=household_id AND a.family_member_id=member_id
                AND a.policy_contract_id=(scope->>'policy_contract_id')::uuid
                AND a.rider_id=(scope->>'rider_id')::uuid AND a.scope_kind='CLAUSE'
                AND a.status='MATCH' AND terms_change_snapshot_clause_scope(
                  a.id,scope,item->'scope_relation_ids')) THEN RETURN false; END IF;
            END LOOP;
          END IF;
          FOR relation_id IN
""",
        )
    )


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_terms_changes WHERE revision='terms-change-v2') THEN
          RAISE EXCEPTION 'clause change history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    op.execute(_DECISION_SNAPSHOT_V1)
    op.execute("DROP FUNCTION terms_change_snapshot_clause_scope(UUID,JSONB,JSONB)")
    _drop_views()
    op.execute("""
      DROP TRIGGER terms_change_clause_pair_guard ON policy_terms_changes;
      DROP FUNCTION protect_terms_change_clause_pair();
      ALTER TABLE policy_terms_changes DROP CONSTRAINT ck_change_clause_pair_shape;
      ALTER TABLE policy_terms_changes DROP COLUMN previous_clause_id,DROP COLUMN new_clause_id,
        DROP COLUMN previous_clause_source_id,DROP COLUMN new_clause_source_id;
      ALTER TABLE policy_terms_changes DROP CONSTRAINT policy_terms_changes_revision_check;
      ALTER TABLE policy_terms_changes ADD CONSTRAINT policy_terms_changes_revision_check
        CHECK(revision='terms-change-v1');
      DROP FUNCTION terms_change_input_context(UUID,UUID);
      DROP FUNCTION terms_change_input_context(UUID,UUID,TEXT);
      ALTER FUNCTION terms_change_input_context_v1(UUID,UUID) RENAME TO terms_change_input_context;
    """)
    op.execute(_FUNCTIONS_V1)
    op.execute(_PROTECT_V1)
    op.execute(_VIEWS_V1)
