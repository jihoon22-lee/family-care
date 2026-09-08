"""Accept source-specific guidance v2 without rewriting historical snapshots."""

from collections.abc import Sequence

from alembic import op

revision: str = "0056_local_guidance_sources"
down_revision: str | Sequence[str] | None = "0055_terms_request_wait"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _constraint(versions: str) -> None:
    op.drop_constraint("ck_decision_runs_local_guidance", "decision_runs", type_="check")
    op.create_check_constraint(
        "ck_decision_runs_local_guidance",
        "decision_runs",
        "local_guidance_json IS NULL OR ("
        "jsonb_typeof(local_guidance_json) = 'object' AND "
        f"local_guidance_json->>'schema_version' IN ({versions}) AND "
        "NOT (local_guidance_json ? 'household_space_id') AND "
        "local_guidance_json->>'medical_event_id' = medical_event_id::text AND "
        "local_guidance_json->>'event_version' = event_version::text AND "
        "octet_length(local_guidance_json::text) <= 2097152) IS TRUE",
    )


def upgrade() -> None:
    _constraint("'1','2'")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM decision_runs
                    WHERE local_guidance_json->>'schema_version'='2') THEN
            RAISE EXCEPTION 'GUIDANCE_V2_HISTORY_REQUIRES_PRESERVATION';
          END IF;
        END $$
    """)
    _constraint("'1'")
