"""Preserve event terms selections independently of later source and event edits."""

from collections.abc import Sequence

from alembic import op

revision: str = "0046_event_terms_snapshots"
down_revision: str | Sequence[str] | None = "0045_terms_changes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
      ALTER TABLE decision_runs ADD COLUMN terms_selections_json JSONB;
      ALTER TABLE decision_runs ADD COLUMN source_rule_version_ids UUID[];
      CREATE FUNCTION decision_terms_snapshot_valid(
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
      CREATE FUNCTION protect_decision_terms_snapshot() RETURNS TRIGGER LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_OP='UPDATE' THEN
          IF NEW.terms_selections_json IS DISTINCT FROM OLD.terms_selections_json
            OR NEW.source_rule_version_ids IS DISTINCT FROM OLD.source_rule_version_ids
            OR (OLD.terms_selections_json IS NOT NULL AND
              (NEW.household_space_id,NEW.medical_event_id,NEW.event_version)
                IS DISTINCT FROM (OLD.household_space_id,OLD.medical_event_id,OLD.event_version))
            THEN RAISE EXCEPTION 'immutable event terms snapshot' USING ERRCODE='23514'; END IF;
          RETURN NEW;
        END IF;
        IF (NEW.engine_version='decision-engine-v2' AND
            (NEW.terms_selections_json IS NULL OR NEW.source_rule_version_ids IS NULL))
          OR NOT decision_terms_snapshot_valid(NEW.terms_selections_json,
            NEW.household_space_id,NEW.medical_event_id,NEW.event_version)
          THEN RAISE EXCEPTION 'invalid event terms snapshot' USING ERRCODE='23514'; END IF;
        IF NEW.source_rule_version_ids IS NOT NULL AND (
          NEW.terms_selections_json IS NULL OR cardinality(NEW.source_rule_version_ids)>8192
          OR array_position(NEW.source_rule_version_ids,NULL) IS NOT NULL
          OR (cardinality(NEW.source_rule_version_ids)>0
            AND array_ndims(NEW.source_rule_version_ids)<>1)
          OR cardinality(NEW.source_rule_version_ids)<>(SELECT count(DISTINCT identifier)
            FROM unnest(NEW.source_rule_version_ids) ids(identifier))
          OR EXISTS(SELECT 1 FROM unnest(NEW.source_rule_version_ids) ids(identifier)
            WHERE NOT EXISTS(SELECT 1 FROM coverage_rule_versions v JOIN coverage_rules r
              ON r.id=v.coverage_rule_id JOIN rider_clause_links l ON l.id=r.rider_clause_link_id
              AND l.household_space_id=r.household_space_id
              WHERE v.id=ids.identifier AND r.household_space_id=NEW.household_space_id
              AND v.executable AND v.published_at IS NOT NULL
              AND EXISTS(SELECT 1 FROM jsonb_array_elements(NEW.terms_selections_json) s
                WHERE (s->'scope'->>'rider_id')::uuid=l.rider_id
                AND (s->'scope'->>'clause_id')::uuid=l.clause_id)))
        ) THEN RAISE EXCEPTION 'invalid captured rule versions' USING ERRCODE='23514'; END IF;
        RETURN NEW;
      END $$;
      CREATE TRIGGER protect_decision_terms_snapshot BEFORE INSERT OR UPDATE ON decision_runs
        FOR EACH ROW EXECUTE FUNCTION protect_decision_terms_snapshot();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM decision_runs WHERE terms_selections_json IS NOT NULL
          OR source_rule_version_ids IS NOT NULL OR engine_version='decision-engine-v2') THEN
          RAISE EXCEPTION 'event terms history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    op.execute("DROP TRIGGER protect_decision_terms_snapshot ON decision_runs")
    op.execute("DROP FUNCTION protect_decision_terms_snapshot()")
    op.execute("DROP FUNCTION decision_terms_snapshot_valid(JSONB,UUID,UUID,INTEGER)")
    op.execute("ALTER TABLE decision_runs DROP COLUMN terms_selections_json")
    op.execute("ALTER TABLE decision_runs DROP COLUMN source_rule_version_ids")
