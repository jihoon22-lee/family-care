"""Refine untouched program components while retaining immutable predecessors."""

from collections.abc import Sequence

from alembic import op

revision: str = "0041_component_supersession"
down_revision: str | Sequence[str] | None = "0040_metadata_revisions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE insurance_document_components ADD COLUMN superseded_by_component_id UUID
          REFERENCES insurance_document_components(id) ON DELETE RESTRICT
          DEFERRABLE INITIALLY DEFERRED,
          ADD CONSTRAINT ck_component_successor_not_self CHECK(superseded_by_component_id<>id);
        DROP INDEX uq_insurance_document_components_active_identity;
        CREATE UNIQUE INDEX uq_insurance_document_components_active_identity
          ON insurance_document_components(household_space_id,family_member_id,
            document_version_id,page_start,page_end,role)
          WHERE deleted_at IS NULL AND superseded_by_component_id IS NULL;
        CREATE TABLE document_component_supersessions (
          predecessor_component_id UUID PRIMARY KEY
            REFERENCES insurance_document_components(id) ON DELETE RESTRICT,
          successor_component_id UUID NOT NULL UNIQUE
            REFERENCES insurance_document_components(id) ON DELETE RESTRICT,
          predecessor_publication_id UUID NOT NULL
            REFERENCES document_metadata_publications(id) ON DELETE RESTRICT,
          successor_publication_id UUID NOT NULL
            REFERENCES document_metadata_publications(id) ON DELETE RESTRICT,
          successor_terms_edition_id UUID REFERENCES terms_editions(id) ON DELETE RESTRICT,
          revision VARCHAR(64) NOT NULL CHECK(revision='metadata-refinement-v1'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          CHECK(predecessor_component_id<>successor_component_id)
        );

        CREATE FUNCTION metadata_proof_refines(previous JSONB,proposed JSONB)
        RETURNS BOOLEAN LANGUAGE sql IMMUTABLE AS $$
          WITH old_facts AS (
            SELECT f->>'field' AS name,f->>'value' AS value
            FROM jsonb_array_elements(previous->'facts') f
            WHERE NOT (previous->'conflicting_fields' ? (f->>'field'))
              AND NOT (previous->'unresolved_fields' ? (f->>'field'))
          ), new_facts AS (
            SELECT f->>'field' AS name,f->>'value' AS value
            FROM jsonb_array_elements(proposed->'facts') f
            WHERE NOT (proposed->'conflicting_fields' ? (f->>'field'))
              AND NOT (proposed->'unresolved_fields' ? (f->>'field'))
          ) SELECT coalesce(
            previous->>'role'=proposed->>'role'
            AND previous->>'role' IN (
              'policy','terms','product_explanation','application','amendment')
            AND (proposed->>'page_start')::int>=1 AND (proposed->>'page_end')::int<=500
            AND (proposed->>'page_start')::int<=(previous->>'page_start')::int
            AND (previous->>'page_start')::int<=(previous->>'page_end')::int
            AND (previous->>'page_end')::int<=(proposed->>'page_end')::int
            AND (previous->>'role' IN ('terms','product_explanation') OR (
              previous->'page_start'=proposed->'page_start'
              AND previous->'page_end'=proposed->'page_end'))
            AND NOT EXISTS(SELECT * FROM old_facts EXCEPT SELECT * FROM new_facts)
            AND (previous->'page_start'<>proposed->'page_start'
              OR previous->'page_end'<>proposed->'page_end'
              OR EXISTS(SELECT * FROM new_facts EXCEPT SELECT * FROM old_facts)),false)
        $$;

        CREATE FUNCTION protect_component_successor() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='INSERT' THEN
            IF NEW.superseded_by_component_id IS NOT NULL THEN
              RAISE EXCEPTION 'new component cannot be retired' USING ERRCODE='23514';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.superseded_by_component_id IS NOT NULL AND (
            TG_OP='DELETE' OR to_jsonb(NEW) IS DISTINCT FROM to_jsonb(OLD)) THEN
            RAISE EXCEPTION 'retired component is immutable' USING ERRCODE='23514';
          END IF;
          IF TG_OP='UPDATE' AND NEW.superseded_by_component_id IS DISTINCT FROM
            OLD.superseded_by_component_id AND (
              OLD.superseded_by_component_id IS NOT NULL OR NEW.superseded_by_component_id IS NULL
              OR OLD.metadata_publication_id IS NULL OR OLD.review_state<>'PROGRAM_VERIFIED'
              OR OLD.version<>1 OR OLD.deleted_at IS NOT NULL
              OR (to_jsonb(NEW)-'superseded_by_component_id') IS DISTINCT FROM
                (to_jsonb(OLD)-'superseded_by_component_id')) THEN
            RAISE EXCEPTION 'invalid component refinement transition' USING ERRCODE='23514';
          END IF;
          RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
        END $$;
        CREATE TRIGGER component_successor_guard BEFORE INSERT OR UPDATE OR DELETE
          ON insurance_document_components FOR EACH ROW
          EXECUTE FUNCTION protect_component_successor();

        CREATE FUNCTION protect_component_supersession() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'component refinement history is immutable' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER component_supersession_history_guard BEFORE INSERT OR UPDATE OR DELETE
          ON document_component_supersessions FOR EACH ROW
          EXECUTE FUNCTION protect_component_supersession();

        CREATE FUNCTION validate_component_supersession() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE predecessor_id UUID; current_pointer UUID; valid BOOLEAN;
        BEGIN
          IF TG_TABLE_NAME='insurance_document_components' THEN
            predecessor_id := NEW.id;
          ELSE
            predecessor_id := NEW.predecessor_component_id;
          END IF;
          SELECT superseded_by_component_id INTO current_pointer
            FROM insurance_document_components WHERE id=predecessor_id;
          IF current_pointer IS NULL AND TG_TABLE_NAME='insurance_document_components' THEN
            RETURN NEW;
          END IF;
          WITH RECURSIVE ancestors(id) AS (
            SELECT predecessor_id UNION
            SELECT receipt.predecessor_component_id FROM document_component_supersessions receipt
              JOIN ancestors a ON receipt.successor_component_id=a.id
          ) SELECT EXISTS(
            SELECT 1 FROM document_component_supersessions receipt
            JOIN insurance_document_components prior ON prior.id=receipt.predecessor_component_id
            JOIN insurance_document_components fresh ON fresh.id=receipt.successor_component_id
            JOIN document_metadata_publications old_proof
              ON old_proof.id=prior.metadata_publication_id
            JOIN document_metadata_publications fresh_proof
              ON fresh_proof.id=fresh.metadata_publication_id
            JOIN document_versions source ON source.id=prior.document_version_id
            WHERE prior.id=predecessor_id AND prior.superseded_by_component_id=fresh.id
              AND prior.household_space_id=fresh.household_space_id
              AND prior.family_member_id=fresh.family_member_id
              AND prior.document_version_id=fresh.document_version_id
              AND prior.role=fresh.role AND prior.review_state='PROGRAM_VERIFIED'
              AND fresh.review_state='PROGRAM_VERIFIED' AND prior.version=1 AND fresh.version=1
              AND prior.deleted_at IS NULL AND fresh.deleted_at IS NULL
              AND fresh.superseded_by_component_id IS NULL
              AND receipt.predecessor_publication_id=old_proof.id
              AND receipt.successor_publication_id=fresh_proof.id
              AND old_proof.component_id=prior.id AND old_proof.outcome='APPLIED'
              AND fresh_proof.component_id=fresh.id AND fresh_proof.outcome='APPLIED'
              AND metadata_proof_refines(old_proof.proof_json,fresh_proof.proof_json)
              AND NOT EXISTS(SELECT 1 FROM insurance_document_set_items item
                WHERE item.insurance_document_component_id=prior.id)
              AND NOT EXISTS(SELECT 1 FROM terms_editions e WHERE e.source_component_id=prior.id
                AND (e.deleted_at IS NOT NULL OR e.version<>1 OR
                  EXISTS(SELECT 1 FROM clauses c WHERE c.terms_edition_id=e.id)))
              AND (NOT EXISTS(SELECT 1 FROM terms_editions e WHERE e.source_component_id=prior.id)
                OR EXISTS(SELECT 1 FROM terms_editions e
                  JOIN component_terms_publications publication ON publication.terms_edition_id=e.id
                  WHERE e.id=receipt.successor_terms_edition_id AND e.source_component_id=fresh.id
                    AND e.deleted_at IS NULL AND e.version=1 AND publication.outcome='APPLIED'))
              AND (receipt.successor_terms_edition_id IS NULL OR EXISTS(
                SELECT 1 FROM terms_editions e WHERE e.id=receipt.successor_terms_edition_id
                  AND e.source_component_id=fresh.id))
              AND NOT EXISTS(
                SELECT 1 FROM insurance_document_components other
                JOIN document_versions other_source ON other_source.id=other.document_version_id
                WHERE other.household_space_id=prior.household_space_id
                  AND other.family_member_id=prior.family_member_id
                  AND other_source.content_sha256=source.content_sha256
                  AND NOT(other.page_end<fresh.page_start OR other.page_start>fresh.page_end)
                  AND other.id<>fresh.id AND other.id NOT IN(SELECT id FROM ancestors))
          ) INTO valid;
          IF valid IS NOT TRUE THEN
            RAISE EXCEPTION 'component refinement proof mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER component_successor_receipt_guard
          AFTER UPDATE ON insurance_document_components DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_component_supersession();
        CREATE CONSTRAINT TRIGGER component_supersession_target_guard
          AFTER INSERT ON document_component_supersessions DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_component_supersession();

        CREATE FUNCTION lock_component_content() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE source_hash TEXT;
        BEGIN
          SELECT content_sha256 INTO source_hash FROM document_versions
            WHERE id=NEW.document_version_id;
          IF NOT pg_try_advisory_xact_lock(hashtextextended(
            NEW.household_space_id::text || ':' || source_hash,0)) THEN
            RAISE EXCEPTION 'component source is busy' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER component_content_lock_guard BEFORE INSERT ON insurance_document_components
          FOR EACH ROW EXECUTE FUNCTION lock_component_content();

        CREATE FUNCTION require_current_set_component() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM 1 FROM insurance_document_components c
            WHERE c.id=NEW.insurance_document_component_id
              AND c.superseded_by_component_id IS NULL FOR SHARE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'component was superseded' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER set_current_component_guard BEFORE INSERT OR UPDATE
          ON insurance_document_set_items FOR EACH ROW
          EXECUTE FUNCTION require_current_set_component();

        CREATE FUNCTION require_current_terms_component() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.source_component_id IS NOT NULL THEN
            PERFORM 1 FROM insurance_document_components c WHERE c.id=NEW.source_component_id
              AND c.superseded_by_component_id IS NULL FOR SHARE;
            IF NOT FOUND THEN
              RAISE EXCEPTION 'edition component was superseded' USING ERRCODE='23514';
            END IF;
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER terms_current_component_guard BEFORE INSERT OR UPDATE ON terms_editions
          FOR EACH ROW EXECUTE FUNCTION require_current_terms_component();

        CREATE OR REPLACE FUNCTION terms_edition_allows_pages(
          edition_id UUID,household_id UUID,page_start INTEGER,page_end INTEGER
        ) RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
          SELECT EXISTS (
            SELECT 1 FROM terms_editions e
            WHERE e.id=edition_id AND e.household_space_id=household_id AND e.deleted_at IS NULL
              AND page_start>=1 AND page_end>=page_start
              AND (e.source_component_id IS NULL OR (
                page_start>=e.source_page_start AND page_end<=e.source_page_end
                AND EXISTS (
                  SELECT 1 FROM insurance_document_components c
                  JOIN family_members m ON m.id=c.family_member_id
                    AND m.household_space_id=c.household_space_id
                  JOIN document_versions v ON v.id=c.document_version_id
                  JOIN documents d ON d.id=v.document_id
                  WHERE c.id=e.source_component_id AND c.household_space_id=e.household_space_id
                    AND c.document_version_id=e.document_version_id
                    AND v.content_sha256=e.content_sha256
                    AND c.role='terms' AND c.review_state IN ('PROGRAM_VERIFIED','USER_CONFIRMED')
                    AND c.page_start=e.source_page_start AND c.page_end=e.source_page_end
                    AND c.superseded_by_component_id IS NULL
                    AND c.deleted_at IS NULL AND m.deleted_at IS NULL AND d.deleted_at IS NULL
                )
              ))
          )
        $$;
        CREATE OR REPLACE FUNCTION validate_clause_component_pages()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM c.id FROM terms_editions e JOIN insurance_document_components c
            ON c.id=e.source_component_id WHERE e.id=NEW.terms_edition_id FOR SHARE OF e,c;
          IF NOT terms_edition_allows_pages(NEW.terms_edition_id,NEW.household_space_id,
            NEW.physical_page_start,NEW.physical_page_end) THEN
            RAISE EXCEPTION 'clause edition source boundary mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_component_supersessions)
            OR EXISTS(SELECT 1 FROM insurance_document_components
              WHERE superseded_by_component_id IS NOT NULL) THEN
            RAISE EXCEPTION 'component refinement history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        DROP TRIGGER terms_current_component_guard ON terms_editions;
        DROP FUNCTION require_current_terms_component();
        DROP TRIGGER set_current_component_guard ON insurance_document_set_items;
        DROP FUNCTION require_current_set_component();
        DROP TRIGGER component_content_lock_guard ON insurance_document_components;
        DROP FUNCTION lock_component_content();
        DROP TRIGGER component_successor_receipt_guard ON insurance_document_components;
        DROP TABLE document_component_supersessions;
        DROP FUNCTION validate_component_supersession();
        DROP FUNCTION protect_component_supersession();
        DROP TRIGGER component_successor_guard ON insurance_document_components;
        DROP FUNCTION protect_component_successor();
        DROP FUNCTION metadata_proof_refines(JSONB,JSONB);
    """)
    # Restore the previous source predicate before removing its referenced column.
    op.execute("""
        DO $$ DECLARE definition TEXT; BEGIN
          SELECT pg_get_functiondef(
            'terms_edition_allows_pages(uuid,uuid,integer,integer)'::regprocedure)
            INTO definition;
          EXECUTE replace(definition,'AND c.superseded_by_component_id IS NULL','');
        END $$;
        DROP INDEX uq_insurance_document_components_active_identity;
        ALTER TABLE insurance_document_components DROP COLUMN superseded_by_component_id;
        CREATE UNIQUE INDEX uq_insurance_document_components_active_identity
          ON insurance_document_components(household_space_id,family_member_id,
            document_version_id,page_start,page_end,role) WHERE deleted_at IS NULL;
        CREATE OR REPLACE FUNCTION validate_clause_component_pages()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT terms_edition_allows_pages(NEW.terms_edition_id,NEW.household_space_id,
            NEW.physical_page_start,NEW.physical_page_end) THEN
            RAISE EXCEPTION 'clause edition source boundary mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
    """)
