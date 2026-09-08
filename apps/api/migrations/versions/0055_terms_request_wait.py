"""Wait for an existing semantic provider reservation without spending retry attempts."""

from collections.abc import Sequence

from alembic import op

revision: str = "0055_terms_request_wait"
down_revision: str | Sequence[str] | None = "0054_terms_semantic_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE_CODES = """
    'TERMS_STRUCTURING_DISABLED','TERMS_PROVIDER_UNCONFIGURED',
    'TERMS_PROVIDER_DOCUMENT_BUDGET','TERMS_PROVIDER_DAILY_BUDGET',
    'TERMS_SOURCE_CHANGED','TERMS_PRIVACY_CHANGED','TERMS_PRIVACY_UNAVAILABLE',
    'TERMS_STRUCTURING_INVALID','TERMS_PROVIDER_RETRYABLE',
    'TERMS_PROVIDER_FAILED','TERMS_LEASE_EXHAUSTED'
"""


def _replace_constraint(codes: str) -> None:
    op.execute(
        "ALTER TABLE terms_semantic_jobs DROP CONSTRAINT terms_semantic_jobs_error_code_check"
    )
    op.execute(
        "ALTER TABLE terms_semantic_jobs ADD CONSTRAINT terms_semantic_jobs_error_code_check "
        f"CHECK(error_code IN ({codes}))"
    )


def upgrade() -> None:
    _replace_constraint(_BASE_CODES + ",'TERMS_PROVIDER_INFLIGHT'")


def downgrade() -> None:
    op.execute("""
        DO $$ BEGIN
          IF EXISTS(SELECT 1 FROM terms_semantic_jobs
            WHERE error_code='TERMS_PROVIDER_INFLIGHT') THEN
            RAISE EXCEPTION 'semantic request wait prevents downgrade' USING ERRCODE='23514';
          END IF;
        END $$;
    """)
    _replace_constraint(_BASE_CODES)
