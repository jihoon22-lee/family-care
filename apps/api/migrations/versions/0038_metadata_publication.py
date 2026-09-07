"""Keep program component provenance distinct from manual confirmation."""

from collections.abc import Sequence

from alembic import op

revision: str = "0038_metadata_publication"
down_revision: str | Sequence[str] | None = "0037_document_metadata_proposals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE document_metadata_publications (
          id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
          proposal_id UUID NOT NULL REFERENCES document_metadata_proposals(id) ON DELETE RESTRICT,
          component_identity VARCHAR(64) NOT NULL CHECK(component_identity ~ '^[0-9a-f]{64}$'),
          validator_revision VARCHAR(64) NOT NULL,
          outcome VARCHAR(16) NOT NULL CHECK(outcome IN ('APPLIED','DEFERRED','INVALID')),
          component_id UUID REFERENCES insurance_document_components(id) ON DELETE RESTRICT
            DEFERRABLE INITIALLY DEFERRED,
          proof_json JSONB NOT NULL CHECK(jsonb_typeof(proof_json)='object'
            AND octet_length(proof_json::text)<=8388608),
          created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(proposal_id,component_identity,validator_revision),
          CHECK(validator_revision='document-metadata-api-v1'),
          CHECK((outcome='APPLIED')=(component_id IS NOT NULL))
        );
        ALTER TABLE insurance_document_components
          ADD COLUMN metadata_publication_id UUID
            REFERENCES document_metadata_publications(id) ON DELETE RESTRICT,
          ALTER COLUMN created_by DROP NOT NULL,
          DROP CONSTRAINT ck_insurance_components_review_state,
          DROP CONSTRAINT ck_insurance_components_role,
          ADD CONSTRAINT ck_insurance_components_review_state CHECK(review_state IN
            ('SUGGESTED','USER_CONFIRMED','CONFLICT','REJECTED','PROGRAM_VERIFIED')),
          ADD CONSTRAINT ck_insurance_components_role CHECK(role IN
            ('policy','terms','product_explanation','application','supporting','amendment')),
          ADD CONSTRAINT ck_insurance_components_creation_origin CHECK(
            (metadata_publication_id IS NULL AND created_by IS NOT NULL)
            OR (metadata_publication_id IS NOT NULL AND created_by IS NULL)),
          ADD CONSTRAINT ck_insurance_components_program_proof CHECK(
            review_state<>'PROGRAM_VERIFIED' OR metadata_publication_id IS NOT NULL);
        CREATE UNIQUE INDEX uq_insurance_component_publication
          ON insurance_document_components(metadata_publication_id)
          WHERE metadata_publication_id IS NOT NULL;
        ALTER TABLE insurance_document_set_items
          DROP CONSTRAINT ck_insurance_set_items_role,
          ADD CONSTRAINT ck_insurance_set_items_role CHECK(role IN
            ('policy','terms','product_explanation','application','supporting','amendment'));

        CREATE FUNCTION protect_document_metadata_publication() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP<>'INSERT' THEN
            RAISE EXCEPTION 'metadata publication history is immutable' USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM document_metadata_proposals p,
              jsonb_array_elements(p.proposal_json->'components') component
            WHERE p.id=NEW.proposal_id AND p.state='PREPARED'
              AND component->>'identity'=NEW.component_identity AND component=NEW.proof_json
          ) THEN
            RAISE EXCEPTION 'metadata publication source mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER metadata_publication_guard BEFORE INSERT OR UPDATE OR DELETE
          ON document_metadata_publications FOR EACH ROW
          EXECUTE FUNCTION protect_document_metadata_publication();

        CREATE FUNCTION validate_metadata_component_origin() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' AND
            NEW.metadata_publication_id IS DISTINCT FROM OLD.metadata_publication_id THEN
            RAISE EXCEPTION 'component creation origin is immutable' USING ERRCODE='23514';
          END IF;
          IF NEW.metadata_publication_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM document_metadata_publications publication
            JOIN document_metadata_proposals proposal ON proposal.id=publication.proposal_id
            JOIN document_structure_generations g ON g.id=proposal.generation_id
            WHERE publication.id=NEW.metadata_publication_id AND publication.component_id=NEW.id
              AND publication.outcome='APPLIED'
              AND g.household_space_id=NEW.household_space_id
              AND g.family_member_id=NEW.family_member_id
              AND g.document_version_id=NEW.document_version_id
              AND g.batch_item_id=NEW.document_batch_item_id
              AND (NEW.review_state<>'PROGRAM_VERIFIED' OR (
                publication.proof_json->>'role'=NEW.role
                AND (publication.proof_json->>'page_start')::int=NEW.page_start
                AND (publication.proof_json->>'page_end')::int=NEW.page_end))
          ) THEN
            RAISE EXCEPTION 'component publication scope mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER metadata_component_origin_guard BEFORE INSERT OR UPDATE
          ON insurance_document_components FOR EACH ROW
          EXECUTE FUNCTION validate_metadata_component_origin();

        CREATE FUNCTION validate_metadata_publication_target() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.outcome='APPLIED' AND NOT EXISTS (
            SELECT 1 FROM insurance_document_components component
            WHERE component.id=NEW.component_id AND component.metadata_publication_id=NEW.id
          ) THEN
            RAISE EXCEPTION 'metadata publication target mismatch' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE CONSTRAINT TRIGGER metadata_publication_target_guard
          AFTER INSERT ON document_metadata_publications DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION validate_metadata_publication_target();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM document_metadata_publications)
            OR EXISTS(SELECT 1 FROM insurance_document_components WHERE role='amendment')
            OR EXISTS(SELECT 1 FROM insurance_document_set_items WHERE role='amendment') THEN
            RAISE EXCEPTION 'metadata publication history prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
        DROP TRIGGER metadata_component_origin_guard ON insurance_document_components;
        DROP FUNCTION validate_metadata_component_origin();
        ALTER TABLE insurance_document_components
          DROP COLUMN metadata_publication_id,
          ALTER COLUMN created_by SET NOT NULL,
          DROP CONSTRAINT ck_insurance_components_review_state,
          DROP CONSTRAINT ck_insurance_components_role,
          ADD CONSTRAINT ck_insurance_components_review_state CHECK(review_state IN
            ('SUGGESTED','USER_CONFIRMED','CONFLICT','REJECTED')),
          ADD CONSTRAINT ck_insurance_components_role CHECK(role IN
            ('policy','terms','product_explanation','application','supporting'));
        ALTER TABLE insurance_document_set_items
          DROP CONSTRAINT ck_insurance_set_items_role,
          ADD CONSTRAINT ck_insurance_set_items_role CHECK(role IN
            ('policy','terms','product_explanation','application','supporting'));
        DROP TABLE document_metadata_publications;
        DROP FUNCTION IF EXISTS validate_metadata_publication_target();
        DROP FUNCTION protect_document_metadata_publication();
    """)
