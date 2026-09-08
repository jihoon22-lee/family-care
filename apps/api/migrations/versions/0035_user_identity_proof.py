"""Retain user publication authority during independent native identity verification."""

from collections.abc import Sequence

from alembic import op

revision: str = "0035_user_identity_proof"
down_revision: str | Sequence[str] | None = "0034_canonical_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _guard(*, user_publications: bool) -> str:
    authority = (
        "p.authority IN ('PROGRAM_VERIFIED','USER_CONFIRMED')"
        if user_publications
        else "p.authority='PROGRAM_VERIFIED'"
    )
    return f"""
        CREATE OR REPLACE FUNCTION protect_canonical_link() RETURNS trigger
          LANGUAGE plpgsql AS $$
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
              JOIN analysis_candidate_fields name ON name.field_id='rider_name'
                AND name.candidate_version_id IN
                  (src.candidate_version_id,p.candidate_version_id)
                AND name.candidate_version_id::text=COALESCE(
                  proof->>'name_source_candidate_version_id',p.candidate_version_id::text)
              WHERE b.id::text=proof->>'source_binding_id' AND b.is_current
                AND b.import_run_id=NEW.import_run_id
                AND b.household_space_id=NEW.household_space_id
                AND p.household_space_id=NEW.household_space_id AND p.rider_id=NEW.rider_id
                AND p.policy_contract_id=NEW.policy_contract_id AND {authority}
                AND p.authority=COALESCE(proof->>'publication_authority','PROGRAM_VERIFIED')
                AND plan.generation_id::text=proof->>'generation_id'
            )
          ) THEN
            RAISE EXCEPTION 'canonical identity scope invalid' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$;
    """


def upgrade() -> None:
    op.execute(_guard(user_publications=True))


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (
            SELECT 1 FROM private_knowledge_canonical_links link
            CROSS JOIN LATERAL jsonb_array_elements(link.proofs) proof
            WHERE proof->>'publication_authority'='USER_CONFIRMED'
          ) THEN
            RAISE EXCEPTION 'user publication identity history must be retained';
          END IF;
        END $$;
    """)
    op.execute(_guard(user_publications=False))
