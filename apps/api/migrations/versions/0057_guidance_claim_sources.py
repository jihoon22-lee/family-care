"""Preserve private claim identity without inventing operational Riders."""

from collections.abc import Sequence

from alembic import op

revision: str = "0057_guidance_claim_sources"
down_revision: str | Sequence[str] | None = "0056_local_guidance_sources"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("claim_cases", "claim_history"):
        op.execute(f"""
          ALTER TABLE {table}
            ALTER COLUMN policy_contract_id DROP NOT NULL,
            ALTER COLUMN rider_id DROP NOT NULL,
            ADD COLUMN private_contract_id UUID
              REFERENCES private_knowledge_contracts(id) ON DELETE RESTRICT,
            ADD COLUMN private_coverage_id UUID
              REFERENCES private_knowledge_coverages(id) ON DELETE RESTRICT,
            ADD CONSTRAINT ck_{table}_coverage_source CHECK (
              (policy_contract_id IS NOT NULL AND rider_id IS NOT NULL
               AND private_contract_id IS NULL AND private_coverage_id IS NULL)
              OR (policy_contract_id IS NULL AND rider_id IS NULL
               AND private_contract_id IS NOT NULL AND private_coverage_id IS NOT NULL));
        """)
    op.execute("""
      ALTER TABLE claim_cases ALTER COLUMN insurer_key DROP NOT NULL,
        ADD COLUMN insurer_display VARCHAR(240),
        ADD CONSTRAINT ck_claim_cases_insurer_source CHECK (
          (rider_id IS NOT NULL AND insurer_key IS NOT NULL)
          OR (private_coverage_id IS NOT NULL AND insurer_key IS NULL
              AND insurer_display IS NOT NULL AND btrim(insurer_display)<>''));
      CREATE UNIQUE INDEX uq_claim_cases_active_private_coverage
        ON claim_cases(household_space_id,medical_event_id,private_coverage_id)
        WHERE deleted_at IS NULL AND private_coverage_id IS NOT NULL;
      CREATE INDEX ix_claim_history_private_coverage
        ON claim_history(private_coverage_id,created_at,id)
        WHERE private_coverage_id IS NOT NULL;
      CREATE FUNCTION validate_private_claim_source() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_TABLE_NAME='claim_history' AND NEW.private_coverage_id IS NOT NULL
          AND EXISTS (
            SELECT 1 FROM claim_cases c WHERE c.household_space_id=NEW.household_space_id
              AND c.medical_event_id=NEW.medical_event_id
              AND c.family_member_id=NEW.family_member_id
              AND c.private_contract_id=NEW.private_contract_id
              AND c.private_coverage_id=NEW.private_coverage_id
          ) THEN RETURN NEW; END IF;
        IF NEW.private_coverage_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM private_knowledge_coverages v
          JOIN private_knowledge_contracts c ON c.id=v.knowledge_contract_id
            AND c.import_run_id=v.import_run_id
          JOIN private_knowledge_subjects s ON s.id=c.subject_id
            AND s.import_run_id=c.import_run_id
          JOIN medical_events e ON e.id=NEW.medical_event_id
            AND e.household_space_id=NEW.household_space_id
            AND e.family_member_id=NEW.family_member_id
          WHERE v.id=NEW.private_coverage_id AND c.id=NEW.private_contract_id
            AND v.household_space_id=NEW.household_space_id
            AND c.household_space_id=NEW.household_space_id
            AND s.family_member_id=NEW.family_member_id AND s.binding_decision='MATCH'
        ) THEN
          RAISE EXCEPTION 'CLAIM_SOURCE_SCOPE_INVALID' USING ERRCODE='23514';
        END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER claim_cases_private_source_guard BEFORE INSERT OR UPDATE OF
        household_space_id,medical_event_id,family_member_id,private_contract_id,private_coverage_id
        ON claim_cases
        FOR EACH ROW EXECUTE FUNCTION validate_private_claim_source();
      CREATE TRIGGER claim_history_private_source_guard BEFORE INSERT OR UPDATE ON claim_history
        FOR EACH ROW EXECUTE FUNCTION validate_private_claim_source();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM claim_cases WHERE private_coverage_id IS NOT NULL)
          OR EXISTS(SELECT 1 FROM claim_history WHERE private_coverage_id IS NOT NULL)
          OR EXISTS(SELECT 1 FROM claim_case_snapshots
                    WHERE candidate_snapshot_json ? 'local_guidance') THEN
          RAISE EXCEPTION 'GUIDANCE_CLAIM_HISTORY_REQUIRES_PRESERVATION';
        END IF;
      END $$;
      DROP TRIGGER claim_cases_private_source_guard ON claim_cases;
      DROP TRIGGER claim_history_private_source_guard ON claim_history;
      DROP FUNCTION validate_private_claim_source();
      DROP INDEX uq_claim_cases_active_private_coverage;
      DROP INDEX ix_claim_history_private_coverage;
      ALTER TABLE claim_cases DROP CONSTRAINT ck_claim_cases_insurer_source,
        DROP COLUMN insurer_display, ALTER COLUMN insurer_key SET NOT NULL;
    """)
    for table in ("claim_cases", "claim_history"):
        op.execute(f"""
          ALTER TABLE {table} DROP CONSTRAINT ck_{table}_coverage_source,
            DROP COLUMN private_contract_id, DROP COLUMN private_coverage_id,
            ALTER COLUMN policy_contract_id SET NOT NULL,
            ALTER COLUMN rider_id SET NOT NULL;
        """)
