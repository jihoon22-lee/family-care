"""Establish the FamilyCare migration baseline.

Revision ID: 0001_foundation
Revises:
Create Date: 2026-08-24
"""

from collections.abc import Sequence

revision: str = "0001_foundation"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Apply the empty Foundation schema baseline."""


def downgrade() -> None:
    """Revert the empty Foundation schema baseline."""
