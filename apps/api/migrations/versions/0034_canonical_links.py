"""Retain independently revalidated coverage identity without mutating snapshots."""

from collections.abc import Sequence

from alembic import op

revision: str = "0034_canonical_links"
down_revision: str | Sequence[str] | None = "0033_knowledge_source_bindings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE private_knowledge_canonical_links (
          id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
          household_space_id uuid NOT NULL,
          import_run_id uuid NOT NULL,
          knowledge_coverage_id uuid NOT NULL,
          knowledge_contract_id uuid NOT NULL,
          family_member_id uuid NOT NULL,
          policy_contract_id uuid NOT NULL,
          rider_id uuid NOT NULL,
          ledger_version integer NOT NULL CHECK (ledger_version>0),
          field_value_conflict boolean NOT NULL,
          authority text NOT NULL DEFAULT 'PROGRAM_VERIFIED_SOURCE_IDENTITY'
            CHECK (authority='PROGRAM_VERIFIED_SOURCE_IDENTITY'),
          proofs jsonb NOT NULL CHECK (jsonb_typeof(proofs)='array' AND jsonb_array_length(proofs)>0
            AND octet_length(proofs::text)<=1048576),
          fingerprint char(64) NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
          created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          UNIQUE(knowledge_coverage_id,fingerprint),
          FOREIGN KEY(import_run_id,household_space_id)
            REFERENCES private_knowledge_import_runs(id,household_space_id) ON DELETE RESTRICT,
          FOREIGN KEY(knowledge_coverage_id,import_run_id)
            REFERENCES private_knowledge_coverages(id,import_run_id) ON DELETE RESTRICT,
          FOREIGN KEY(knowledge_contract_id,import_run_id)
            REFERENCES private_knowledge_contracts(id,import_run_id) ON DELETE RESTRICT,
          FOREIGN KEY(family_member_id,household_space_id)
            REFERENCES family_members(id,household_space_id) ON DELETE RESTRICT,
          FOREIGN KEY(policy_contract_id,household_space_id)
            REFERENCES policy_contracts(id,household_space_id) ON DELETE RESTRICT,
          FOREIGN KEY(rider_id,household_space_id)
            REFERENCES riders(id,household_space_id) ON DELETE RESTRICT
        );
        CREATE FUNCTION protect_canonical_link() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'canonical identity history is immutable' USING ERRCODE='23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM private_knowledge_coverages c
            JOIN private_knowledge_contracts k ON k.id=c.knowledge_contract_id
            JOIN private_knowledge_subjects s ON s.id=k.subject_id
            JOIN private_knowledge_import_runs run ON run.id=c.import_run_id
              AND run.is_current AND run.state='APPLIED'
            JOIN riders r ON r.id=NEW.rider_id AND r.policy_contract_id=NEW.policy_contract_id
              AND r.household_space_id=NEW.household_space_id AND r.version=NEW.ledger_version
              AND r.deleted_at IS NULL
            WHERE c.id=NEW.knowledge_coverage_id AND c.import_run_id=NEW.import_run_id
              AND c.household_space_id=NEW.household_space_id
              AND k.id=NEW.knowledge_contract_id AND s.family_member_id=NEW.family_member_id
              AND s.binding_decision='MATCH' AND NOT s.binding_conflict
          ) OR EXISTS (
            SELECT 1 FROM jsonb_array_elements(NEW.proofs) proof WHERE NOT EXISTS (
              SELECT 1 FROM private_knowledge_source_bindings b
              JOIN range_enrollment_publications p ON p.candidate_version_id::text=
                proof->>'publication_candidate_version_id'
              JOIN policy_range_candidate_sources src ON
              src.candidate_version_id=p.source_candidate_version_id
              JOIN document_policy_range_plans plan ON plan.job_id=src.job_id
              WHERE b.id::text=proof->>'source_binding_id' AND b.is_current
                AND b.import_run_id=NEW.import_run_id AND
                b.household_space_id=NEW.household_space_id
                AND p.household_space_id=NEW.household_space_id AND p.rider_id=NEW.rider_id
                AND p.policy_contract_id=NEW.policy_contract_id AND p.authority='PROGRAM_VERIFIED'
                AND plan.generation_id::text=proof->>'generation_id'
            )
          ) THEN
            RAISE EXCEPTION 'canonical identity scope invalid' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER canonical_link_guard BEFORE INSERT OR UPDATE OR DELETE
          ON private_knowledge_canonical_links FOR EACH ROW EXECUTE FUNCTION
          protect_canonical_link();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM private_knowledge_canonical_links) THEN
            RAISE EXCEPTION 'canonical identity history must be retained';
          END IF;
        END $$;
        DROP TABLE private_knowledge_canonical_links;
        DROP FUNCTION protect_canonical_link();
    """)
