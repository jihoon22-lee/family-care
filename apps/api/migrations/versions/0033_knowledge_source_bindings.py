"""Bind retained knowledge aliases through exact external source manifests."""

from collections.abc import Sequence

from alembic import op

revision: str = "0033_knowledge_source_bindings"
down_revision: str | Sequence[str] | None = "0032_unclassified_riders"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE private_knowledge_source_bindings (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          import_run_id uuid NOT NULL REFERENCES private_knowledge_import_runs(id)
            ON DELETE RESTRICT,
          household_space_id uuid NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
          document_binding_id uuid NOT NULL REFERENCES private_knowledge_document_bindings(id)
            ON DELETE RESTRICT,
          source_alias_digest_sha256 text NOT NULL
            CHECK (source_alias_digest_sha256 ~ '^[0-9a-f]{64}$'),
          document_version_id uuid NOT NULL REFERENCES document_versions(id) ON DELETE RESTRICT,
          evidence_id uuid NOT NULL,
          content_sha256 text NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
          page_count integer NOT NULL CHECK (page_count BETWEEN 1 AND 500),
          document_kind text NOT NULL CHECK (document_kind IN
            ('policy','terms','application','amendment','claim','supporting')),
          authority text NOT NULL CHECK (authority='PROGRAM_VERIFIED_CONTENT_MANIFEST'),
          manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
          binding_sha256 text NOT NULL CHECK (binding_sha256 ~ '^[0-9a-f]{64}$'),
          is_current boolean NOT NULL DEFAULT true,
          superseded_at timestamptz,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE (id,import_run_id,household_space_id),
          UNIQUE (import_run_id,binding_sha256),
          FOREIGN KEY (evidence_id,household_space_id,document_version_id)
            REFERENCES evidence(id,household_space_id,document_version_id) ON DELETE RESTRICT,
          CHECK ((is_current AND superseded_at IS NULL)
            OR (NOT is_current AND superseded_at IS NOT NULL))
        );
        CREATE UNIQUE INDEX pk_source_bindings_current ON private_knowledge_source_bindings
          (document_binding_id) WHERE is_current;
        CREATE FUNCTION verify_knowledge_source_binding() RETURNS trigger
          LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM private_knowledge_document_bindings b
            JOIN private_knowledge_import_runs run ON run.id=b.import_run_id
            JOIN household_spaces h ON h.id=run.household_space_id AND h.deleted_at IS NULL
            JOIN document_versions v ON v.id=NEW.document_version_id
            JOIN documents d ON d.id=v.document_id AND d.deleted_at IS NULL
            JOIN evidence e ON e.id=NEW.evidence_id AND e.document_version_id=v.id
            JOIN extractions x ON x.id=e.extraction_id AND x.document_version_id=v.id
            WHERE b.id=NEW.document_binding_id AND b.import_run_id=NEW.import_run_id
              AND b.household_space_id=NEW.household_space_id
              AND run.household_space_id=NEW.household_space_id AND run.is_current
              AND run.state='APPLIED' AND e.household_space_id=NEW.household_space_id
              AND b.source_alias_digest_sha256=NEW.source_alias_digest_sha256
              AND v.content_sha256=NEW.content_sha256 AND e.content_sha256=v.content_sha256
              AND v.page_count=NEW.page_count AND d.document_kind=NEW.document_kind
              AND x.status='succeeded' AND e.physical_page BETWEEN 1 AND v.page_count
          ) THEN
            RAISE EXCEPTION 'knowledge source binding invalid' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER knowledge_source_binding_insert BEFORE INSERT
          ON private_knowledge_source_bindings FOR EACH ROW
          EXECUTE FUNCTION verify_knowledge_source_binding();
        CREATE TRIGGER knowledge_source_binding_history BEFORE UPDATE OR DELETE
          ON private_knowledge_source_bindings FOR EACH ROW
          EXECUTE FUNCTION enforce_insurance_reconciliation_history();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM private_knowledge_source_bindings) THEN
            RAISE EXCEPTION 'knowledge source binding history must be retained';
          END IF;
        END $$;
        DROP TABLE private_knowledge_source_bindings;
        DROP FUNCTION verify_knowledge_source_binding();
    """)
