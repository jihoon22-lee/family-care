"""Append locally normalized policy draft replay and preserve provider history."""

from collections.abc import Sequence

from alembic import op

revision: str = "0069_policy_draft_replay"
down_revision: str | Sequence[str] | None = "0068_range_field_proof"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _accepted_revisions(*, expanded: bool) -> None:
    previous = "('retained-policy-association-v2','retained-policy-association-v3')"
    current = (
        "('retained-policy-association-v2','retained-policy-association-v3',"
        "'retained-policy-association-v4')"
    )
    if not expanded:
        previous, current = current, previous
    for signature in (
        "policy_structuring_source_current(uuid)",
        "protect_retained_policy_job()",
    ):
        # Alter only migration-owned revision admission, retaining every source guard.
        op.execute(f"""
          DO $$ DECLARE definition TEXT; BEGIN
            definition := pg_get_functiondef('{signature}'::regprocedure);
            IF strpos(definition,$previous${previous}$previous$)=0 THEN
              RAISE EXCEPTION 'retained draft replay contract changed';
            END IF;
            EXECUTE replace(definition,$previous${previous}$previous$,$current${current}$current$);
          END $$;
        """)


def upgrade() -> None:
    _accepted_revisions(expanded=True)
    op.execute("""
      CREATE TABLE policy_range_replay_sources (
        job_id UUID NOT NULL,
        envelope_id TEXT NOT NULL,
        source_job_id UUID NOT NULL,
        source_envelope_id TEXT NOT NULL,
        source_provider_request_id UUID NOT NULL REFERENCES policy_provider_requests(id),
        source_response_hash TEXT NOT NULL CHECK(source_response_hash ~ '^[0-9a-f]{64}$'),
        normalization_revision TEXT NOT NULL
          CHECK(normalization_revision='policy-draft-normalization-v1'),
        normalized_batch_json JSONB NOT NULL,
        adjustments_json JSONB NOT NULL,
        partial BOOLEAN NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
        PRIMARY KEY(job_id,envelope_id),
        FOREIGN KEY(job_id,envelope_id) REFERENCES document_policy_ranges(job_id,envelope_id),
        FOREIGN KEY(source_job_id,source_envelope_id)
          REFERENCES document_policy_ranges(job_id,envelope_id)
      );
      CREATE FUNCTION protect_policy_range_replay_source() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        RAISE EXCEPTION 'policy range replay history is immutable' USING ERRCODE='23514';
      END $$;
      CREATE TRIGGER policy_range_replay_source_immutable
        BEFORE UPDATE OR DELETE ON policy_range_replay_sources
        FOR EACH ROW EXECUTE FUNCTION protect_policy_range_replay_source();
    """)


def downgrade() -> None:
    op.execute("""
      DO $$ BEGIN
        IF EXISTS(SELECT 1 FROM policy_structuring_jobs
            WHERE pipeline_version='retained-policy-association-v4')
          OR EXISTS(SELECT 1 FROM policy_range_replay_sources)
        THEN
          RAISE EXCEPTION 'policy draft replay history prevents downgrade' USING ERRCODE='23514';
        END IF;
      END $$;
      DROP TABLE policy_range_replay_sources;
      DROP FUNCTION protect_policy_range_replay_source();
    """)
    _accepted_revisions(expanded=False)
