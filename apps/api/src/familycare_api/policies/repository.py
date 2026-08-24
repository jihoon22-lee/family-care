"""Direct-psycopg policy-ledger persistence."""

from __future__ import annotations

from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.scope import HouseholdScope
from familycare_api.common.versions import require_expected_version
from familycare_api.policies.domain import FamilyMember
from familycare_api.policies.errors import PolicyRepositoryUnavailable, VersionConflict


def _database_url(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise PolicyRepositoryUnavailable
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def _family_member(row: dict[str, Any]) -> FamilyMember:
    return FamilyMember(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        display_name=cast(str, row["display_name"]),
        internal_alias=cast(str, row["internal_alias"]),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row.get("deleted_at"),
    )


class PolicyLedgerRepository:
    """Apply a server-owned household predicate to every business query."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def get_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
    ) -> FamilyMember | None:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    SELECT
                        id,
                        household_space_id,
                        display_name,
                        internal_alias,
                        version,
                        created_at,
                        updated_at,
                        deleted_at
                    FROM family_members
                    WHERE id = %s
                      AND household_space_id = %s
                      AND deleted_at IS NULL
                    """,
                    (member_id, scope.household_space_id),
                ).fetchone()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        return _family_member(row) if row is not None else None

    def update_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        *,
        expected_version: int,
        display_name: str,
        internal_alias: str,
    ) -> FamilyMember:
        version = require_expected_version(expected_version)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE family_members
                    SET
                        display_name = %s,
                        internal_alias = %s,
                        version = version + 1,
                        updated_at = clock_timestamp()
                    WHERE id = %s
                      AND household_space_id = %s
                      AND version = %s
                      AND deleted_at IS NULL
                    RETURNING
                        id,
                        household_space_id,
                        display_name,
                        internal_alias,
                        version,
                        created_at,
                        updated_at,
                        deleted_at
                    """,
                    (
                        display_name,
                        internal_alias,
                        member_id,
                        scope.household_space_id,
                        version,
                    ),
                ).fetchone()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        return _family_member(row)


__all__ = ["PolicyLedgerRepository"]
