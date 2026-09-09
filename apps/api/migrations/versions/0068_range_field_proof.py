"""Append field-proof processing while retaining valid v2 source authority."""

from collections.abc import Sequence

from alembic import op

revision: str = "0068_range_field_proof"
down_revision: str | Sequence[str] | None = "0067_metadata_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _accepted_revisions(*, expanded: bool) -> None:
    # These are migration-owned predicates, not caller input. Alter only the
    # admitted versions; preserve every existing scope and immutable-field guard.
    replacements = (
        (
            "policy_structuring_source_current(uuid)",
            "pipeline_version='retained-policy-association-v2'",
            "pipeline_version IN "
            "('retained-policy-association-v2','retained-policy-association-v3')",
        ),
        (
            "protect_retained_policy_job()",
            "NEW.pipeline_version<>'retained-policy-association-v2'",
            "NEW.pipeline_version NOT IN "
            "('retained-policy-association-v2','retained-policy-association-v3')",
        ),
    )
    for signature, previous, current in replacements:
        if not expanded:
            previous, current = current, previous
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'retained field proof contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)


def upgrade() -> None:
    _accepted_revisions(expanded=True)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE processing_mode='retained' AND pipeline_version='retained-policy-association-v3')
        THEN
          RAISE EXCEPTION 'range field proof history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    _accepted_revisions(expanded=False)
