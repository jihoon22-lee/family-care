"""Append spaced-label processing and retire older provider minimization packets."""

from collections.abc import Sequence

from alembic import op

revision: str = "0074_policy_label_spacing"
down_revision: str | Sequence[str] | None = "0073_metadata_header_regions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _change(*, expanded: bool) -> None:
    previous = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4')"
    )
    current = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4','retained-policy-association-v5')"
    )
    old_privacy, new_privacy = "source-window-minimizer-v3", "source-window-minimizer-v4"
    if not expanded:
        previous, current = current, previous
        old_privacy, new_privacy = new_privacy, old_privacy
    for signature in ("policy_structuring_source_current(uuid)", "protect_retained_policy_job()"):
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'policy label source contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)
    op.execute(f"""
      DO $$ DECLARE definition TEXT; BEGIN
        definition := pg_get_functiondef('terms_semantic_privacy_digest(uuid)'::regprocedure);
        IF strpos(definition,'{old_privacy}')=0 THEN
          RAISE EXCEPTION 'policy label privacy contract changed';
        END IF;
        EXECUTE replace(definition,'{old_privacy}','{new_privacy}');
      END $$;
    """)


def upgrade() -> None:
    _change(expanded=True)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE pipeline_version='retained-policy-association-v5')
          OR EXISTS(SELECT 1 FROM policy_provider_requests)
          OR EXISTS(SELECT 1 FROM terms_semantic_jobs)
          OR EXISTS(SELECT 1 FROM guidance_review_jobs)
        THEN
          RAISE EXCEPTION 'policy label spacing history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
    """)
    _change(expanded=False)
