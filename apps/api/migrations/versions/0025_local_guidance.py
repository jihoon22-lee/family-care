"""Add immutable, versioned local guidance beside historical decision results."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025_local_guidance"
down_revision: str | Sequence[str] | None = "0024_insurance_reconciliation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_runs", sa.Column("local_guidance_json", postgresql.JSONB(), nullable=True)
    )
    op.create_check_constraint(
        "ck_decision_runs_local_guidance",
        "decision_runs",
        "local_guidance_json IS NULL OR ("
        "jsonb_typeof(local_guidance_json) = 'object' AND "
        "local_guidance_json->>'schema_version' = '1' AND "
        "NOT (local_guidance_json ? 'household_space_id') AND "
        "local_guidance_json->>'medical_event_id' = medical_event_id::text AND "
        "local_guidance_json->>'event_version' = event_version::text AND "
        "octet_length(local_guidance_json::text) <= 2097152) IS TRUE",
    )
    op.execute(
        """
        CREATE FUNCTION protect_local_guidance_snapshot() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            IF OLD.local_guidance_json IS NOT NULL THEN
              RAISE EXCEPTION 'local guidance snapshots are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN OLD;
          END IF;
          IF OLD.local_guidance_json IS DISTINCT FROM NEW.local_guidance_json THEN
            RAISE EXCEPTION 'local guidance snapshots are immutable' USING ERRCODE = '23514';
          END IF;
          RETURN NEW;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_local_guidance_snapshot
        BEFORE UPDATE OR DELETE ON decision_runs
        FOR EACH ROW EXECUTE FUNCTION protect_local_guidance_snapshot()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_local_guidance_snapshot ON decision_runs")
    op.execute("DROP FUNCTION protect_local_guidance_snapshot()")
    op.drop_constraint("ck_decision_runs_local_guidance", "decision_runs", type_="check")
    op.drop_column("decision_runs", "local_guidance_json")
