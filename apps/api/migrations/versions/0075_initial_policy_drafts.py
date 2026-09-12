"""Preserve initial raw drafts before fresh verification of normalized candidates."""

from collections.abc import Sequence

from alembic import op

revision: str = "0075_initial_policy_drafts"
down_revision: str | Sequence[str] | None = "0074_policy_label_spacing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _admission(*, expanded: bool) -> None:
    previous = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4','retained-policy-association-v5')"
    )
    current = previous[:-1] + ",'retained-policy-association-v6')"
    if not expanded:
        previous, current = current, previous
    for signature in ("policy_structuring_source_current(uuid)", "protect_retained_policy_job()"):
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'initial policy draft contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)


def upgrade() -> None:
    _admission(expanded=True)
    op.execute("""
      ALTER TABLE policy_range_replay_sources ADD COLUMN origin TEXT NOT NULL DEFAULT 'replay'
        CHECK(origin IN ('initial','replay'));
      ALTER TABLE policy_range_replay_sources ADD CONSTRAINT ck_policy_draft_origin
        CHECK(origin<>'initial' OR source_job_id=job_id);
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs WHERE pipeline_version IN
            ('retained-policy-association-v6','policy-range-normalized-v1'))
          OR EXISTS(SELECT 1 FROM policy_range_replay_sources WHERE origin='initial') THEN
          RAISE EXCEPTION 'initial policy draft history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      ALTER TABLE policy_range_replay_sources DROP CONSTRAINT ck_policy_draft_origin,
        DROP COLUMN origin;
    """)
    _admission(expanded=False)
