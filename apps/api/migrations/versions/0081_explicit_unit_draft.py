"""Admit explicit same-row unit recovery without changing earlier processing history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0081_explicit_unit_draft"
down_revision: str | Sequence[str] | None = "0080_scoped_policy_verifier"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _admission(expanded: bool) -> None:
    previous = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4','retained-policy-association-v5',"
        "'retained-policy-association-v6','retained-policy-association-v7',"
        "'retained-policy-association-v8','retained-policy-association-v9',"
        "'retained-policy-association-v10','retained-policy-association-v11')"
    )
    current = previous[:-1] + ",'retained-policy-association-v12')"
    if not expanded:
        previous, current = current, previous
    for signature in ("policy_structuring_source_current(uuid)", "protect_retained_policy_job()"):
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'explicit unit draft contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)


def upgrade() -> None:
    _admission(True)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE pipeline_version='retained-policy-association-v12') THEN
          RAISE EXCEPTION 'explicit unit draft history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    _admission(False)
