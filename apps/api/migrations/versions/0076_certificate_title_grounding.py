"""Version certificate-title proof while preserving previous normalization receipts."""

from collections.abc import Sequence

from alembic import op

revision: str = "0076_certificate_title_grounding"
down_revision: str | Sequence[str] | None = "0075_initial_policy_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _admission(*, expanded: bool) -> None:
    previous = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4','retained-policy-association-v5',"
        "'retained-policy-association-v6')"
    )
    current = previous[:-1] + ",'retained-policy-association-v7')"
    if not expanded:
        previous, current = current, previous
    for signature in ("policy_structuring_source_current(uuid)", "protect_retained_policy_job()"):
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'certificate title contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)


def _normalization_constraint(*, expanded: bool) -> None:
    revisions = "'policy-draft-normalization-v1'"
    if expanded:
        revisions += ",'policy-draft-normalization-v2'"
    op.execute(f"""
      ALTER TABLE policy_range_replay_sources
        DROP CONSTRAINT policy_range_replay_sources_normalization_revision_check;
      ALTER TABLE policy_range_replay_sources
        ADD CONSTRAINT policy_range_replay_sources_normalization_revision_check
        CHECK(normalization_revision IN ({revisions}));
    """)


def upgrade() -> None:
    _admission(expanded=True)
    _normalization_constraint(expanded=True)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs WHERE pipeline_version IN
            ('retained-policy-association-v7','policy-range-normalized-v2'))
          OR EXISTS(SELECT 1 FROM policy_range_replay_sources
            WHERE normalization_revision='policy-draft-normalization-v2') THEN
          RAISE EXCEPTION 'certificate title history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    _normalization_constraint(expanded=False)
    _admission(expanded=False)
