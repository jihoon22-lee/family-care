"""Separate raw enrollment from benefit classification without inferring a payout."""

from collections.abc import Sequence

from alembic import op

revision: str = "0032_unclassified_riders"
down_revision: str | Sequence[str] | None = "0031_range_enrollment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE riders DROP CONSTRAINT ck_riders_benefit_type;
        ALTER TABLE riders ADD CONSTRAINT ck_riders_benefit_type
          CHECK (benefit_type IN ('fixed','indemnity','unknown'));
        ALTER TABLE claim_candidates DROP CONSTRAINT ck_claim_candidates_rider_type;
        ALTER TABLE claim_candidates ADD CONSTRAINT ck_claim_candidates_rider_type
          CHECK (rider_type IN ('fixed','indemnity','unknown'));
    """)


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS (SELECT 1 FROM riders WHERE benefit_type='unknown') OR
             EXISTS (SELECT 1 FROM claim_candidates WHERE rider_type='unknown') THEN
            RAISE EXCEPTION 'unclassified enrollment and candidate history must be retained';
          END IF;
        END $$;
        ALTER TABLE claim_candidates DROP CONSTRAINT ck_claim_candidates_rider_type;
        ALTER TABLE claim_candidates ADD CONSTRAINT ck_claim_candidates_rider_type
          CHECK (rider_type IN ('fixed','indemnity'));
        ALTER TABLE riders DROP CONSTRAINT ck_riders_benefit_type;
        ALTER TABLE riders ADD CONSTRAINT ck_riders_benefit_type
          CHECK (benefit_type IN ('fixed','indemnity'));
    """)
