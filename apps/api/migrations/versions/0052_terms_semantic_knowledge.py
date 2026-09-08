"""Retain source-bound semantic candidates and immutable partial publications."""

from collections.abc import Sequence

from alembic import op

revision: str = "0052_terms_semantic_knowledge"
down_revision: str | Sequence[str] | None = "0051_metadata_reading_guides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      CREATE FUNCTION terms_semantic_input_context(edition_id UUID,household_id UUID)
      RETURNS JSONB LANGUAGE sql STABLE AS $$
        SELECT jsonb_build_object(
          'revision','terms-semantic-input-v1',
          'edition_id',e.id,'edition_version',e.version,
          'household_space_id',e.household_space_id,
          'document_version_id',e.document_version_id,
          'content_sha256',e.content_sha256,
          'component_id',s.id,'component_version',s.version,
          'metadata_publication_id',s.metadata_publication_id,
          'generation_id',g.id,'structure_identity_sha256',g.identity_sha256,
          'page_start',s.page_start,'page_end',s.page_end,'cancelled',g.cancelled)
        FROM terms_editions e JOIN terms_applicability_component_sources s
          ON s.id=e.source_component_id AND s.household_space_id=e.household_space_id
          AND s.role='terms' AND s.document_version_id=e.document_version_id
          AND s.content_sha256=e.content_sha256
        JOIN document_structure_generations g ON g.id=s.generation_id
          AND g.household_space_id=e.household_space_id
          AND g.document_version_id=e.document_version_id
        WHERE e.id=edition_id AND e.household_space_id=household_id
          AND e.deleted_at IS NULL AND NOT g.cancelled
          AND terms_edition_allows_pages(e.id,e.household_space_id,s.page_start,s.page_end)
      $$;
      CREATE TABLE terms_semantic_candidates(
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
        input_context JSONB NOT NULL CHECK(jsonb_typeof(input_context)='object'),
        input_digest TEXT NOT NULL CHECK(input_digest ~ '^[0-9a-f]{64}$'),
        graph_json JSONB NOT NULL CHECK(jsonb_typeof(graph_json)='object'
          AND octet_length(graph_json::text)<=16777216),
        graph_sha256 TEXT NOT NULL CHECK(graph_sha256 ~ '^[0-9a-f]{64}$'),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(terms_edition_id,input_digest,graph_sha256)
      );
      CREATE TABLE terms_semantic_publications(
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        candidate_id UUID NOT NULL REFERENCES terms_semantic_candidates(id) ON DELETE RESTRICT,
        household_space_id UUID NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
        terms_edition_id UUID NOT NULL REFERENCES terms_editions(id) ON DELETE RESTRICT,
        verifier_revision TEXT NOT NULL CHECK(verifier_revision='terms-semantic-source-v1'),
        compiler_revision TEXT NOT NULL CHECK(compiler_revision='terms-semantic-compiler-v1'),
        proof_sha256 TEXT NOT NULL CHECK(proof_sha256 ~ '^[0-9a-f]{64}$'),
        outcome TEXT NOT NULL CHECK(outcome IN ('VERIFIED','PARTIAL','UNRESOLVED')),
        processing_complete BOOLEAN NOT NULL,
        result_json JSONB NOT NULL CHECK(jsonb_typeof(result_json)='object'
          AND octet_length(result_json::text)<=16777216),
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        UNIQUE(candidate_id,verifier_revision,compiler_revision)
      );
      CREATE TABLE terms_semantic_root_publications(
        publication_id UUID NOT NULL REFERENCES terms_semantic_publications(id) ON DELETE RESTRICT,
        root_node_id TEXT NOT NULL CHECK(root_node_id ~ '^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$'),
        semantic_sha256 TEXT NOT NULL CHECK(semantic_sha256 ~ '^[0-9a-f]{64}$'),
        manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
        executable BOOLEAN NOT NULL,
        root_json JSONB NOT NULL CHECK(jsonb_typeof(root_json)='object'
          AND octet_length(root_json::text)<=8388608),
        PRIMARY KEY(publication_id,root_node_id)
      );
      CREATE FUNCTION protect_terms_semantic_history() RETURNS TRIGGER LANGUAGE plpgsql AS $$
      DECLARE candidate terms_semantic_candidates; parent terms_semantic_publications;
      BEGIN
        IF TG_OP<>'INSERT' THEN
          RAISE EXCEPTION 'terms semantic history is immutable' USING ERRCODE='23514';
        END IF;
        IF TG_TABLE_NAME='terms_semantic_candidates' THEN
          IF NEW.input_context IS DISTINCT FROM terms_semantic_input_context(
              NEW.terms_edition_id,NEW.household_space_id)
            OR NEW.input_digest<>encode(sha256(convert_to(NEW.input_context::text,'UTF8')),'hex')
            OR NEW.graph_sha256<>encode(sha256(convert_to(NEW.graph_json::text,'UTF8')),'hex') THEN
            RAISE EXCEPTION 'terms semantic inputs changed' USING ERRCODE='23514';
          END IF;
        ELSIF TG_TABLE_NAME='terms_semantic_publications' THEN
          SELECT * INTO candidate FROM terms_semantic_candidates WHERE id=NEW.candidate_id;
          IF NOT FOUND OR candidate.household_space_id<>NEW.household_space_id
            OR candidate.terms_edition_id<>NEW.terms_edition_id
            OR candidate.input_context IS DISTINCT FROM terms_semantic_input_context(
              NEW.terms_edition_id,NEW.household_space_id) THEN
            RAISE EXCEPTION 'terms semantic publication scope changed' USING ERRCODE='23514';
          END IF;
        ELSE
          SELECT * INTO parent FROM terms_semantic_publications WHERE id=NEW.publication_id;
          IF NOT FOUND OR NOT EXISTS(
            SELECT 1 FROM jsonb_array_elements(parent.result_json->'roots') root
            WHERE root->>'root_node_id'=NEW.root_node_id AND root=NEW.root_json
              AND root->>'semantic_sha256'=NEW.semantic_sha256
              AND root->>'manifest_sha256'=NEW.manifest_sha256) THEN
            RAISE EXCEPTION 'terms semantic root mismatch' USING ERRCODE='23514';
          END IF;
          IF NEW.executable IS DISTINCT FROM (
            jsonb_array_length(NEW.root_json->'rules')>0 OR
            jsonb_typeof(NEW.root_json->'calculation')='object') THEN
            RAISE EXCEPTION 'terms semantic root usability mismatch' USING ERRCODE='23514';
          END IF;
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER terms_semantic_candidate_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_candidates FOR EACH ROW EXECUTE FUNCTION protect_terms_semantic_history();
      CREATE TRIGGER terms_semantic_publication_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_publications
        FOR EACH ROW EXECUTE FUNCTION protect_terms_semantic_history();
      CREATE TRIGGER terms_semantic_root_guard BEFORE INSERT OR UPDATE OR DELETE
        ON terms_semantic_root_publications
        FOR EACH ROW EXECUTE FUNCTION protect_terms_semantic_history();
      CREATE INDEX terms_semantic_publication_lookup ON terms_semantic_publications
        (household_space_id,terms_edition_id,created_at DESC,id);
      COMMENT ON TABLE terms_semantic_publications IS
        'Audit output; authoritative readers must replay sources and compilation';
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM terms_semantic_candidates) THEN
          RAISE EXCEPTION 'terms semantic history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP TABLE terms_semantic_root_publications;
      DROP TABLE terms_semantic_publications;
      DROP TABLE terms_semantic_candidates;
      DROP FUNCTION protect_terms_semantic_history();
      DROP FUNCTION terms_semantic_input_context(UUID,UUID);
    """)
