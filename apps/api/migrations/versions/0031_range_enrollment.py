"""Record source-bound raw enrollment publication independently of analysis jobs."""

from collections.abc import Sequence

from alembic import op

revision: str = "0031_range_enrollment"
down_revision: str | Sequence[str] | None = "0030_range_candidates"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE range_enrollment_attempts (
          candidate_version_id uuid PRIMARY KEY REFERENCES analysis_candidate_versions(id)
            ON DELETE CASCADE,
          attempted_at timestamptz NOT NULL DEFAULT clock_timestamp(),
          outcome text NOT NULL CHECK (outcome IN ('APPLIED', 'DEFERRED'))
        );
        CREATE TABLE range_enrollment_publications (
          candidate_version_id uuid PRIMARY KEY REFERENCES analysis_candidate_versions(id)
            ON DELETE RESTRICT,
          source_candidate_version_id uuid NOT NULL
            REFERENCES policy_range_candidate_sources(candidate_version_id) ON DELETE RESTRICT,
          household_space_id uuid NOT NULL REFERENCES household_spaces(id) ON DELETE RESTRICT,
          policy_contract_id uuid NOT NULL REFERENCES policy_contracts(id) ON DELETE RESTRICT,
          rider_id uuid REFERENCES riders(id) ON DELETE RESTRICT,
          ledger_version integer NOT NULL CHECK (ledger_version > 0),
          field_values jsonb NOT NULL CHECK (jsonb_typeof(field_values) = 'object'),
          authority text NOT NULL CHECK (authority IN ('PROGRAM_VERIFIED', 'USER_CONFIRMED')),
          insured_evidence_id uuid NOT NULL REFERENCES evidence(id) ON DELETE RESTRICT,
          created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        CREATE INDEX range_enrollment_policy ON range_enrollment_publications
          (household_space_id, policy_contract_id, created_at DESC);
        CREATE INDEX range_enrollment_rider ON range_enrollment_publications
          (household_space_id, rider_id, created_at DESC);
        CREATE FUNCTION protect_range_enrollment_publication() RETURNS trigger
          LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP <> 'INSERT' THEN
            RAISE EXCEPTION 'range enrollment history is immutable' USING ERRCODE = '23514';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM analysis_candidate_versions c
            JOIN analysis_candidate_versions root ON root.id=NEW.source_candidate_version_id
              AND root.review_item_id=c.review_item_id
            JOIN policy_contracts p ON p.id=NEW.policy_contract_id
            JOIN evidence e ON e.id=NEW.insured_evidence_id
            WHERE c.id=NEW.candidate_version_id AND c.household_space_id=NEW.household_space_id
              AND root.household_space_id=c.household_space_id
              AND p.household_space_id=c.household_space_id
              AND e.household_space_id=c.household_space_id
              AND (NEW.rider_id IS NULL OR EXISTS (
                SELECT 1 FROM riders r WHERE r.id=NEW.rider_id
                  AND r.policy_contract_id=p.id AND r.household_space_id=p.household_space_id))
          ) THEN
            RAISE EXCEPTION 'range enrollment scope invalid' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END $$;
        CREATE TRIGGER range_enrollment_publication_guard BEFORE INSERT OR UPDATE OR DELETE
          ON range_enrollment_publications FOR EACH ROW
          EXECUTE FUNCTION protect_range_enrollment_publication();
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM range_enrollment_publications) THEN
            RAISE EXCEPTION 'range enrollment history must be retained';
          END IF;
        END $$;
        DROP TABLE range_enrollment_publications;
        DROP TABLE IF EXISTS range_enrollment_attempts;
        DROP FUNCTION protect_range_enrollment_publication();
    """)
