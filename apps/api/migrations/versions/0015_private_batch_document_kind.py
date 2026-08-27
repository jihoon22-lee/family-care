"""Store the explicit document kind selected for each private batch item."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_private_batch_document_kind"
down_revision: str | Sequence[str] | None = "0014_private_import_capacity"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DOCUMENT_KINDS = "'policy', 'terms', 'supporting'"
_CONSTRAINT_NAME = "ck_document_batch_items_document_kind"


def upgrade() -> None:
    """Add a bounded, non-authoritative classification to each batch item."""

    op.add_column(
        "document_batch_items",
        sa.Column(
            "document_kind",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'supporting'"),
        ),
    )
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "document_batch_items",
        f"document_kind IN ({_DOCUMENT_KINDS})",
    )
    op.alter_column(
        "document_batch_items",
        "document_kind",
        existing_type=sa.String(length=16),
        existing_nullable=False,
        server_default=None,
    )


def downgrade() -> None:
    """Remove the additive private-batch classification column."""

    op.drop_constraint(_CONSTRAINT_NAME, "document_batch_items", type_="check")
    op.drop_column("document_batch_items", "document_kind")
