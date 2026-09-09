"""Retire legacy provider packets without rewriting retained processing history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0066_provider_privacy_revision"
down_revision: str | Sequence[str] | None = "0065_retained_policy_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _retained_revision(previous: str, current: str) -> None:
    # Both strings are migration-owned literals. Keep every existing scope and
    # immutability condition while advancing only the admitted processing revision.
    op.execute(f"""
      DO $$ DECLARE definition TEXT; BEGIN
        definition := pg_get_functiondef('policy_structuring_source_current(uuid)'::regprocedure);
        IF strpos(definition,'{previous}')=0 THEN
          RAISE EXCEPTION 'retained privacy source contract changed';
        END IF;
        EXECUTE replace(definition,'{previous}','{current}');
        definition := pg_get_functiondef('protect_retained_policy_job()'::regprocedure);
        IF strpos(definition,'{previous}')=0 THEN
          RAISE EXCEPTION 'retained privacy identity contract changed';
        END IF;
        EXECUTE replace(definition,'{previous}','{current}');
      END $$;
    """)


def upgrade() -> None:
    _retained_revision("retained-policy-association-v1", "retained-policy-association-v2")
    op.execute("""
      CREATE OR REPLACE FUNCTION terms_semantic_privacy_digest(household_id UUID)
      RETURNS TEXT LANGUAGE sql STABLE AS $$
        SELECT encode(sha256(convert_to(jsonb_build_array('source-window-minimizer-v3',
          coalesce(jsonb_agg(jsonb_build_array(m.id,m.display_name,m.internal_alias,m.version)
            ORDER BY m.id),'[]'::jsonb))::text,'UTF8')),'hex')
        FROM family_members m WHERE m.household_space_id=household_id AND m.deleted_at IS NULL
      $$;
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE processing_mode='retained' AND pipeline_version='retained-policy-association-v2')
          OR EXISTS(SELECT 1 FROM policy_provider_requests)
          OR EXISTS(SELECT 1 FROM terms_semantic_jobs)
          OR EXISTS(SELECT 1 FROM guidance_review_jobs) THEN
          RAISE EXCEPTION 'privacy revision history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    _retained_revision("retained-policy-association-v2", "retained-policy-association-v1")
    op.execute("""
      CREATE OR REPLACE FUNCTION terms_semantic_privacy_digest(household_id UUID)
      RETURNS TEXT LANGUAGE sql STABLE AS $$
        SELECT encode(sha256(convert_to(coalesce(jsonb_agg(jsonb_build_array(
          m.id,m.display_name,m.internal_alias,m.version) ORDER BY m.id),'[]'::jsonb)::text,
          'UTF8')),'hex') FROM family_members m
        WHERE m.household_space_id=household_id AND m.deleted_at IS NULL
      $$;
    """)
