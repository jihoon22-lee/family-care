"""Retain component-scoped Terms editions and immutable publication origins."""

from collections.abc import Sequence

from alembic import op

revision: str = "0039_component_terms"
down_revision: str | Sequence[str] | None = "0038_metadata_publication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE terms_editions
          ADD COLUMN source_component_id UUID REFERENCES insurance_document_components(id)
            ON DELETE RESTRICT,
          ADD COLUMN source_page_start INTEGER,
          ADD COLUMN source_page_end INTEGER,
          ADD COLUMN edition_date DATE,
          ADD COLUMN source_metadata_json JSONB,
          DROP CONSTRAINT uq_terms_editions_household_document_content,
          ADD CONSTRAINT ck_terms_component_source CHECK(
            (source_component_id IS NULL AND source_page_start IS NULL
              AND source_page_end IS NULL AND source_metadata_json IS NULL)
            OR (source_component_id IS NOT NULL AND source_page_start IS NOT NULL
              AND source_page_end IS NOT NULL AND source_page_start>=1
              AND source_page_end>=source_page_start AND source_metadata_json IS NOT NULL
              AND jsonb_typeof(source_metadata_json)='object'
              AND octet_length(source_metadata_json::text)<=8388608));
        CREATE UNIQUE INDEX uq_terms_editions_legacy_source
          ON terms_editions(household_space_id,document_version_id,content_sha256)
          WHERE source_component_id IS NULL;
        CREATE UNIQUE INDEX uq_terms_editions_component ON terms_editions(source_component_id)
          WHERE source_component_id IS NOT NULL;
        CREATE TABLE component_terms_publications (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          component_id UUID NOT NULL REFERENCES insurance_document_components(id)
            ON DELETE RESTRICT,
          revision VARCHAR(64) NOT NULL CHECK(revision='component-terms-v1'),
          outcome VARCHAR(16) NOT NULL CHECK(outcome IN ('APPLIED','DEFERRED')),
          reason_code VARCHAR(64) NOT NULL CHECK(reason_code IN
            ('SOURCE_REGISTERED','IDENTITY_METADATA_INCOMPLETE','LEGACY_EDITION_EXISTS')),
          terms_edition_id UUID REFERENCES terms_editions(id) ON DELETE RESTRICT,
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(component_id,revision),
          CHECK((outcome='APPLIED')=(terms_edition_id IS NOT NULL))
        );
        CREATE FUNCTION protect_component_terms_publication() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'component edition publication is immutable' USING ERRCODE='23514';
          END IF;
          IF NEW.outcome='APPLIED' AND NOT EXISTS (
            SELECT 1 FROM terms_editions e WHERE e.id=NEW.terms_edition_id
              AND e.source_component_id=NEW.component_id
          ) THEN
            RAISE EXCEPTION 'component edition publication target mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER component_terms_publication_guard BEFORE INSERT OR UPDATE OR DELETE
          ON component_terms_publications FOR EACH ROW
          EXECUTE FUNCTION protect_component_terms_publication();

        CREATE FUNCTION protect_terms_component_origin() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' AND (
            NEW.source_component_id IS DISTINCT FROM OLD.source_component_id
            OR NEW.source_page_start IS DISTINCT FROM OLD.source_page_start
            OR NEW.source_page_end IS DISTINCT FROM OLD.source_page_end
            OR NEW.source_metadata_json IS DISTINCT FROM OLD.source_metadata_json
            OR (OLD.source_component_id IS NOT NULL AND (
              NEW.household_space_id IS DISTINCT FROM OLD.household_space_id
              OR NEW.document_version_id IS DISTINCT FROM OLD.document_version_id
              OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
              OR NEW.edition_date IS DISTINCT FROM OLD.edition_date))) THEN
            RAISE EXCEPTION 'terms component source is immutable' USING ERRCODE='23514';
          END IF;
          IF TG_OP='INSERT' AND NEW.source_component_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM insurance_document_components c
            JOIN document_metadata_publications p ON p.id=c.metadata_publication_id
            JOIN document_versions v ON v.id=c.document_version_id
            WHERE c.id=NEW.source_component_id AND c.household_space_id=NEW.household_space_id
              AND c.document_version_id=NEW.document_version_id
              AND v.content_sha256=NEW.content_sha256
              AND c.role='terms' AND c.review_state='PROGRAM_VERIFIED' AND c.deleted_at IS NULL
              AND c.page_start=NEW.source_page_start AND c.page_end=NEW.source_page_end
              AND p.outcome='APPLIED' AND p.component_id=c.id
              AND p.proof_json=NEW.source_metadata_json
          ) THEN
            RAISE EXCEPTION 'terms component source mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER terms_component_origin_guard BEFORE INSERT OR UPDATE ON terms_editions
          FOR EACH ROW EXECUTE FUNCTION protect_terms_component_origin();

        CREATE FUNCTION terms_edition_has_printed_period(edition_id UUID)
        RETURNS BOOLEAN LANGUAGE sql STABLE AS $$
          SELECT EXISTS (
            SELECT 1 FROM terms_editions e WHERE e.id=edition_id
              AND e.source_component_id IS NOT NULL
              AND NOT (e.source_metadata_json->'conflicting_fields'
                ?| ARRAY['applicability_start','applicability_end'])
              AND NOT (e.source_metadata_json->'unresolved_fields'
                ?| ARRAY['applicability_start','applicability_end'])
              AND e.applicability_start IS NOT NULL
              AND EXISTS (SELECT 1 FROM jsonb_array_elements(e.source_metadata_json->'facts') f
                WHERE f->>'field'='applicability_start'
                  AND f->>'value'=to_char(e.applicability_start,'YYYY-MM-DD'))
              AND ((e.applicability_end IS NULL AND NOT EXISTS (
                SELECT 1 FROM jsonb_array_elements(e.source_metadata_json->'facts') f
                WHERE f->>'field'='applicability_end')) OR EXISTS (
                SELECT 1 FROM jsonb_array_elements(e.source_metadata_json->'facts') f
                WHERE f->>'field'='applicability_end'
                  AND f->>'value'=to_char(e.applicability_end,'YYYY-MM-DD')))
          )
        $$;
        CREATE FUNCTION terms_edition_allows_pages(
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
                    AND c.deleted_at IS NULL AND m.deleted_at IS NULL AND d.deleted_at IS NULL
                )
              ))
          )
        $$;
        CREATE FUNCTION validate_clause_component_pages() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT terms_edition_allows_pages(NEW.terms_edition_id,NEW.household_space_id,
            NEW.physical_page_start,NEW.physical_page_end) THEN
            RAISE EXCEPTION 'clause edition source boundary mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER clause_component_pages_guard BEFORE INSERT OR UPDATE OF
          terms_edition_id,household_space_id,physical_page_start,physical_page_end ON clauses
          FOR EACH ROW EXECUTE FUNCTION validate_clause_component_pages();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM component_terms_publications)
            OR EXISTS(SELECT 1 FROM terms_editions WHERE source_component_id IS NOT NULL) THEN
            RAISE EXCEPTION 'component edition history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        DROP TRIGGER clause_component_pages_guard ON clauses;
        DROP FUNCTION validate_clause_component_pages();
        DROP FUNCTION terms_edition_allows_pages(UUID,UUID,INTEGER,INTEGER);
        DROP FUNCTION IF EXISTS terms_edition_has_printed_period(UUID);
        DROP TRIGGER terms_component_origin_guard ON terms_editions;
        DROP FUNCTION protect_terms_component_origin();
        DROP TABLE component_terms_publications;
        DROP FUNCTION protect_component_terms_publication();
        DROP INDEX uq_terms_editions_legacy_source;
        DROP INDEX uq_terms_editions_component;
        ALTER TABLE terms_editions DROP CONSTRAINT ck_terms_component_source,
          DROP COLUMN source_component_id,DROP COLUMN source_page_start,
          DROP COLUMN source_page_end,DROP COLUMN edition_date,DROP COLUMN source_metadata_json,
          ADD CONSTRAINT uq_terms_editions_household_document_content
            UNIQUE(household_space_id,document_version_id,content_sha256);
    """)
