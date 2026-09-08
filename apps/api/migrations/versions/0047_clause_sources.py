"""Retain whole-Clause source assessments and their immutable input dependencies."""

from collections.abc import Sequence

from alembic import op

revision: str = "0047_clause_sources"
down_revision: str | Sequence[str] | None = "0046_event_terms_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION clause_source_input_context(clause_id UUID,household_id UUID)
      RETURNS JSONB LANGUAGE sql STABLE AS $$
        SELECT jsonb_build_object(
          'revision','clause-source-v1',
          'clause',jsonb_build_array(c.id,c.version,c.terms_edition_id,c.parent_clause_id,
            c.clause_type,c.label,c.physical_page_start,c.physical_page_end,c.normalization_version,
            encode(sha256(convert_to(c.normalized_title,'UTF8')),'hex'),
            encode(sha256(convert_to(c.normalized_text,'UTF8')),'hex'),c.deleted_at),
          'edition',jsonb_build_array(e.id,e.version,e.document_version_id,e.content_sha256,
            e.source_component_id,e.source_page_start,e.source_page_end,e.deleted_at,
            encode(sha256(convert_to(e.source_metadata_json::text,'UTF8')),'hex')),
          'component',jsonb_build_array(component.id,component.version,component.role,
            component.family_member_id,component.review_state,component.metadata_publication_id,
            component.deleted_at,component.superseded_by_component_id),
          'source',jsonb_build_array(source.id,source.generation_id,source.content_sha256),
          'evidence',coalesce((SELECT jsonb_agg(jsonb_build_array(ev.id,ev.document_version_id,
              ev.extraction_id,ev.content_sha256,ev.physical_page,ev.x0,ev.y0,ev.x1,ev.y1,
              ev.review_state,x.status,v.content_sha256,d.deleted_at) ORDER BY ev.id)
            FROM clause_evidence ce JOIN evidence ev ON ev.id=ce.evidence_id
            JOIN document_versions v ON v.id=ev.document_version_id
            JOIN documents d ON d.id=v.document_id JOIN extractions x ON x.id=ev.extraction_id
            WHERE ce.clause_id=c.id AND ev.household_space_id=household_id),'[]'::jsonb))
        FROM clauses c JOIN terms_editions e ON e.id=c.terms_edition_id
          AND e.household_space_id=c.household_space_id
        LEFT JOIN insurance_document_components component ON component.id=e.source_component_id
          AND component.household_space_id=c.household_space_id
        LEFT JOIN terms_applicability_component_sources source ON source.id=component.id
        WHERE c.id=clause_id AND c.household_space_id=household_id
      $$;
      -- Storage checks establish addresses and dependencies. Whole-region semantics
      -- are replayed by read_verified_clause_source before any authoritative use.
      CREATE FUNCTION clause_source_spans_valid(generation_id UUID,household_id UUID,
        requested_page INTEGER,region JSONB) RETURNS BOOLEAN LANGUAGE plpgsql STABLE AS $$
      DECLARE span JSONB; node JSONB; start_at INTEGER; end_at INTEGER;
      BEGIN
        IF jsonb_typeof(region)<>'object' OR NOT region ?& ARRAY['label','heading','body',
          'body_text','content_sha256','source_sha256','complete','boundary_kind','boundary',
          'reason_codes','table_context'] OR region-ARRAY['label','heading','body','body_text',
          'content_sha256','source_sha256','complete','boundary_kind','boundary',
          'reason_codes','table_context']<>'{}'::jsonb OR region->'complete'<>'true'::jsonb
          OR coalesce(region->>'boundary_kind','') NOT IN ('NEXT_HEADING','COMPONENT_END')
          OR jsonb_typeof(region->'body')<>'array' OR jsonb_array_length(region->'body')<1
          OR jsonb_array_length(region->'body')>4096
          OR jsonb_typeof(region->'table_context')<>'array'
          OR jsonb_array_length(region->'table_context')>4096
          OR jsonb_typeof(region->'body_text')<>'string'
          OR coalesce(region->>'source_sha256','') !~ '^[0-9a-f]{64}$'
          OR region->'reason_codes'<>'[]'::jsonb
          OR (region->>'boundary_kind'='NEXT_HEADING')
             IS DISTINCT FROM (jsonb_typeof(region->'boundary')='object')
          THEN RETURN false; END IF;
        FOR span IN SELECT value FROM jsonb_array_elements(
          jsonb_build_array(region->'heading')||(region->'body')||(region->'table_context')||
          CASE WHEN region->'boundary'='null'::jsonb THEN '[]'::jsonb
            ELSE jsonb_build_array(region->'boundary') END)
        LOOP
          IF jsonb_typeof(span)<>'object' OR NOT span ?& ARRAY['node_id','page_number',
            'start','end','text','source_layer','bbox'] OR span-ARRAY['node_id','page_number',
            'start','end','text','source_layer','bbox']<>'{}'::jsonb
            OR jsonb_typeof(span->'text')<>'string'
            OR (span->>'page_number')::int IS DISTINCT FROM requested_page
            OR jsonb_typeof(span->'start')<>'number' OR jsonb_typeof(span->'end')<>'number'
            OR span->>'start' !~ '^[0-9]+$' OR span->>'end' !~ '^[0-9]+$'
            THEN RETURN false; END IF;
          SELECT source.node INTO node FROM document_structure_nodes_by_ids(
            generation_id,household_id,ARRAY[span->>'node_id']) source;
          IF NOT FOUND THEN RETURN false; END IF;
          start_at:=(span->>'start')::int; end_at:=(span->>'end')::int;
          IF start_at<0 OR end_at<=start_at OR end_at>length(node->>'text')
            OR node->>'page_number' IS DISTINCT FROM span->>'page_number'
            OR node->>'source_layer' IS DISTINCT FROM span->>'source_layer'
            OR substring(node->>'text' FROM start_at+1 FOR end_at-start_at)
              IS DISTINCT FROM span->>'text'
            OR span->'bbox' IS DISTINCT FROM (CASE WHEN node->>'kind'='TABLE_ROW'
              AND jsonb_array_length(node->'cells')=1 THEN node->'cells'->0->'bbox'
              ELSE node->'bbox' END) THEN RETURN false; END IF;
        END LOOP;
        RETURN true;
      EXCEPTION WHEN OTHERS THEN RETURN false;
      END $$;
      CREATE TABLE clause_source_assessments(
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        clause_id UUID NOT NULL REFERENCES clauses(id) ON DELETE RESTRICT,
        clause_version INTEGER NOT NULL CHECK(clause_version>=1),
        terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
        source_component_id UUID REFERENCES insurance_document_components(id) ON DELETE RESTRICT,
        source_publication_id UUID REFERENCES document_metadata_publications(id) ON DELETE RESTRICT,
        source_generation_id UUID REFERENCES document_structure_generations(id) ON DELETE RESTRICT,
        source_content_sha256 TEXT CHECK(source_content_sha256 ~ '^[0-9a-f]{64}$'),
        source_region JSONB CHECK(source_region IS NULL OR
          (jsonb_typeof(source_region)='object' AND octet_length(source_region::text)<=8388608)),
        status TEXT NOT NULL CHECK(status IN ('MATCH','UNKNOWN')),
        reason_codes JSONB NOT NULL CHECK(jsonb_typeof(reason_codes)='array'
          AND jsonb_array_length(reason_codes)<=32),
        revision TEXT NOT NULL CHECK(revision='clause-source-v1'),
        input_context JSONB NOT NULL CHECK(jsonb_typeof(input_context)='object'
          AND octet_length(input_context::text)<=1048576),
        input_digest TEXT NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(clause_id,revision,input_digest),
        CHECK(status<>'UNKNOWN' OR source_region IS NULL),
        CHECK(num_nonnulls(source_component_id,source_publication_id,source_generation_id,
          source_content_sha256) IN (0,4)),
        CHECK(status<>'MATCH' OR (source_component_id IS NOT NULL
          AND source_publication_id IS NOT NULL AND source_generation_id IS NOT NULL
          AND source_content_sha256 IS NOT NULL AND source_region IS NOT NULL))
      );
      CREATE FUNCTION protect_clause_source_assessment() RETURNS TRIGGER LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'clause source history is immutable' USING ERRCODE='23514';
        END IF;
        IF NEW.input_context IS DISTINCT FROM clause_source_input_context(
            NEW.clause_id,NEW.household_space_id)
          OR NEW.input_digest<>encode(sha256(convert_to(NEW.input_context::text,'UTF8')),'hex')
          OR NOT EXISTS(SELECT 1 FROM clauses c WHERE c.id=NEW.clause_id
            AND c.household_space_id=NEW.household_space_id AND c.version=NEW.clause_version
            AND c.terms_edition_id=NEW.terms_edition_id) THEN
          RAISE EXCEPTION 'clause source inputs changed' USING ERRCODE='23514';
        END IF;
        IF NEW.source_component_id IS NOT NULL AND NOT EXISTS(
          SELECT 1 FROM terms_editions e JOIN terms_applicability_component_sources s
            ON s.id=e.source_component_id AND s.household_space_id=e.household_space_id
          WHERE e.id=NEW.terms_edition_id AND e.household_space_id=NEW.household_space_id
            AND s.id=NEW.source_component_id AND s.metadata_publication_id=NEW.source_publication_id
            AND s.generation_id=NEW.source_generation_id
            AND s.content_sha256=NEW.source_content_sha256) THEN
          RAISE EXCEPTION 'clause source references mismatch' USING ERRCODE='23514';
        END IF;
        IF NEW.status='MATCH' AND NOT EXISTS(SELECT 1 FROM clauses c JOIN terms_editions e
          ON e.id=c.terms_edition_id AND e.household_space_id=c.household_space_id
          JOIN terms_applicability_component_sources s ON s.id=e.source_component_id
          AND s.household_space_id=c.household_space_id AND s.role='terms'
          WHERE c.id=NEW.clause_id AND c.household_space_id=NEW.household_space_id
            AND c.deleted_at IS NULL AND e.deleted_at IS NULL AND c.clause_type='article'
            AND s.id=NEW.source_component_id AND s.metadata_publication_id=NEW.source_publication_id
            AND s.generation_id=NEW.source_generation_id
            AND s.content_sha256=NEW.source_content_sha256
            AND NEW.source_region->>'content_sha256'=NEW.source_content_sha256
            AND NEW.source_region->>'label'=c.label
            AND c.physical_page_start=c.physical_page_end
            AND terms_edition_allows_pages(e.id,e.household_space_id,
              c.physical_page_start,c.physical_page_end)
            AND clause_source_spans_valid(s.generation_id,s.household_space_id,
              c.physical_page_start,NEW.source_region)) THEN
          RAISE EXCEPTION 'clause source region mismatch' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER clause_source_guard BEFORE INSERT OR UPDATE OR DELETE
        ON clause_source_assessments
        FOR EACH ROW EXECUTE FUNCTION protect_clause_source_assessment();
      CREATE VIEW current_clause_source_assessments AS
        SELECT a.* FROM clause_source_assessments a
          CROSS JOIN LATERAL(SELECT clause_source_input_context(a.clause_id,a.household_space_id)
            AS context OFFSET 0) current_input
        WHERE a.input_context=current_input.context AND a.input_digest=
          encode(sha256(convert_to(current_input.context::text,'UTF8')),'hex');
      COMMENT ON VIEW current_clause_source_assessments IS
        'Current audit candidates only; whole-source replay in the API is required for authority';
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM clause_source_assessments) THEN
          RAISE EXCEPTION 'clause source history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP VIEW current_clause_source_assessments;
      DROP TRIGGER clause_source_guard ON clause_source_assessments;
      DROP FUNCTION protect_clause_source_assessment();
      DROP TABLE clause_source_assessments;
      DROP FUNCTION clause_source_spans_valid(UUID,UUID,INTEGER,JSONB);
      DROP FUNCTION clause_source_input_context(UUID,UUID);
    """)
