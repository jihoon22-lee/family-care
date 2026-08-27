"""Raise managed archive capacity for 128 MiB private PDF inputs."""

from collections.abc import Sequence

from alembic import op

revision: str = "0014_private_import_capacity"
down_revision: str | Sequence[str] | None = "0013_selective_ocr"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HISTORICAL_CIPHERTEXT_SIZE_LIMIT = 64 * 1024 * 1024
_CIPHERTEXT_SIZE_LIMIT = 128 * 1024 * 1024
_CONSTRAINT_NAME = "ck_managed_archives_ciphertext_size_limit"


def _replace_constraint(limit: int) -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "managed_archives", type_="check")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "managed_archives",
        f"ciphertext_size <= {limit}",
    )


def upgrade() -> None:
    """Allow 128 MiB ciphertext; the AES-GCM tag remains a separate column."""

    _replace_constraint(_CIPHERTEXT_SIZE_LIMIT)


def downgrade() -> None:
    """Restore the historical 64 MiB ciphertext check."""

    _replace_constraint(_HISTORICAL_CIPHERTEXT_SIZE_LIMIT)
