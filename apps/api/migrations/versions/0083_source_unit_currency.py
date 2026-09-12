"""Admit source-proven unit labels in currency fields before fresh verification."""

from collections.abc import Sequence

from alembic import op

revision: str = "0083_source_unit_currency"
down_revision: str | Sequence[str] | None = "0082_proven_draft_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _admission(expanded: bool) -> None:
    previous = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4','retained-policy-association-v5',"
        "'retained-policy-association-v6','retained-policy-association-v7',"
        "'retained-policy-association-v8','retained-policy-association-v9',"
        "'retained-policy-association-v10','retained-policy-association-v11',"
        "'retained-policy-association-v12','retained-policy-association-v13')"
    )
    current = previous[:-1] + ",'retained-policy-association-v14')"
    if not expanded:
        previous, current = current, previous
    for signature in ("policy_structuring_source_current(uuid)", "protect_retained_policy_job()"):
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'source unit currency contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)

    revisions = (
        "'policy-draft-normalization-v1','policy-draft-normalization-v2',"
        "'policy-draft-normalization-v3','policy-draft-normalization-v4',"
        "'policy-draft-normalization-v5','policy-draft-normalization-v6','policy-draft-normalization-v7'"
    )
    if expanded:
        revisions += ",'policy-draft-normalization-v8'"
    op.execute(f"""
      ALTER TABLE policy_range_replay_sources
        DROP CONSTRAINT policy_range_replay_sources_normalization_revision_check,
        ADD CONSTRAINT policy_range_replay_sources_normalization_revision_check
        CHECK(normalization_revision IN ({revisions}));
    """)


def upgrade() -> None:
    _admission(True)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE pipeline_version='retained-policy-association-v14')
          OR EXISTS(SELECT 1 FROM policy_range_replay_sources
            WHERE normalization_revision='policy-draft-normalization-v8') THEN
          RAISE EXCEPTION 'source unit currency history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    _admission(False)
