"""Direct-psycopg policy-ledger persistence."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from familycare_api.common.evidence import EvidenceBbox, EvidenceRef, EvidenceReviewState
from familycare_api.common.scope import HouseholdScope
from familycare_api.common.versions import require_expected_version
from familycare_api.policies.domain import (
    BenefitType,
    CreatePolicyParty,
    FamilyMember,
    PartyRole,
    PolicyContract,
    PolicyParty,
    PolicyStatus,
    Rider,
)
from familycare_api.policies.errors import (
    PolicyRepositoryUnavailable,
    PolicyStateConflict,
    VersionConflict,
)


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


def _evidence(row: dict[str, Any], prefix: str) -> EvidenceRef | None:
    evidence_id = row.get(f"{prefix}_id")
    if evidence_id is None:
        return None
    coordinates = tuple(row.get(f"{prefix}_{name}") for name in ("x0", "y0", "x1", "y1"))
    bbox = None if coordinates == (None, None, None, None) else cast(EvidenceBbox, coordinates)
    return EvidenceRef(
        evidence_id=cast(UUID, evidence_id),
        document_version_id=cast(UUID, row[f"{prefix}_document_version_id"]),
        extraction_id=cast(UUID, row[f"{prefix}_extraction_id"]),
        content_sha256=cast(str, row[f"{prefix}_content_sha256"]),
        physical_page=int(row[f"{prefix}_physical_page"]),
        bbox=bbox,
        review_state=cast(EvidenceReviewState, row[f"{prefix}_review_state"]),
    )


_POLICY_SELECT = """
    SELECT
        policy.id, policy.household_space_id, policy.source_document_version_id,
        policy.insurer_display, policy.insurer_key, policy.product_display,
        policy.product_key, policy.contract_date, policy.coverage_start_date,
        policy.coverage_end_date, policy.status, policy.version,
        policy.created_at, policy.updated_at, policy.deleted_at,
        source.id AS source_id,
        source.document_version_id AS source_document_version_id,
        source.extraction_id AS source_extraction_id,
        source.content_sha256 AS source_content_sha256,
        source.physical_page AS source_physical_page,
        source.x0 AS source_x0, source.y0 AS source_y0,
        source.x1 AS source_x1, source.y1 AS source_y1,
        source.review_state AS source_review_state,
        status_source.id AS status_id,
        status_source.document_version_id AS status_document_version_id,
        status_source.extraction_id AS status_extraction_id,
        status_source.content_sha256 AS status_content_sha256,
        status_source.physical_page AS status_physical_page,
        status_source.x0 AS status_x0, status_source.y0 AS status_y0,
        status_source.x1 AS status_x1, status_source.y1 AS status_y1,
        status_source.review_state AS status_review_state
    FROM policy_contracts AS policy
    JOIN evidence AS source ON source.id = policy.source_evidence_id
    LEFT JOIN evidence AS status_source ON status_source.id = policy.status_evidence_id
"""

_PARTY_SELECT = """
    SELECT
        party.id, party.policy_contract_id, party.family_member_id, party.role,
        party.effective_from, party.effective_to, party.version,
        source.id AS evidence_id,
        source.document_version_id AS evidence_document_version_id,
        source.extraction_id AS evidence_extraction_id,
        source.content_sha256 AS evidence_content_sha256,
        source.physical_page AS evidence_physical_page,
        source.x0 AS evidence_x0, source.y0 AS evidence_y0,
        source.x1 AS evidence_x1, source.y1 AS evidence_y1,
        source.review_state AS evidence_review_state
    FROM policy_parties AS party
    JOIN evidence AS source ON source.id = party.evidence_id
    WHERE party.policy_contract_id = %s
      AND party.household_space_id = %s
      AND party.deleted_at IS NULL
    ORDER BY party.created_at, party.id
"""


def _policy_party(row: dict[str, Any]) -> PolicyParty:
    evidence = _evidence(row, "evidence")
    if evidence is None:
        raise PolicyRepositoryUnavailable
    return PolicyParty(
        id=cast(UUID, row["id"]),
        policy_contract_id=cast(UUID, row["policy_contract_id"]),
        family_member_id=cast(UUID, row["family_member_id"]),
        role=cast(PartyRole, row["role"]),
        effective_from=cast(date | None, row.get("effective_from")),
        effective_to=cast(date | None, row.get("effective_to")),
        evidence=evidence,
        version=int(row["version"]),
    )


def _policy(row: dict[str, Any], parties: list[dict[str, Any]]) -> PolicyContract:
    source = _evidence(row, "source")
    if source is None:
        raise PolicyRepositoryUnavailable
    return PolicyContract(
        id=cast(UUID, row["id"]),
        household_space_id=cast(UUID, row["household_space_id"]),
        source_document_version_id=cast(UUID, row["source_document_version_id"]),
        source_evidence=source,
        insurer_display=cast(str, row["insurer_display"]),
        insurer_key=cast(str, row["insurer_key"]),
        product_display=cast(str, row["product_display"]),
        product_key=cast(str, row["product_key"]),
        contract_date=cast(date | None, row.get("contract_date")),
        coverage_start_date=cast(date | None, row.get("coverage_start_date")),
        coverage_end_date=cast(date | None, row.get("coverage_end_date")),
        status=cast(PolicyStatus, row["status"]),
        status_evidence=_evidence(row, "status"),
        parties=tuple(_policy_party(party) for party in parties),
        version=int(row["version"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row.get("deleted_at"),
    )


class PolicyLedgerRepository:
    """Apply a server-owned household predicate to every business query."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _database_url(database_url)

    def list_family_members(
        self, scope: HouseholdScope, *, deleted_only: bool = False
    ) -> list[FamilyMember]:
        predicate = "deleted_at IS NOT NULL" if deleted_only else "deleted_at IS NULL"
        query = (
            "SELECT id, household_space_id, display_name, internal_alias, version, "
            "created_at, updated_at, deleted_at FROM family_members "
            "WHERE household_space_id = %s AND " + predicate + " ORDER BY created_at, id"
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(query, (scope.household_space_id,)).fetchall()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        return [_family_member(row) for row in rows]

    def create_family_member(
        self, scope: HouseholdScope, *, display_name: str, internal_alias: str
    ) -> FamilyMember:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO family_members (household_space_id, display_name, internal_alias)
                    VALUES (%s, %s, %s)
                    RETURNING id, household_space_id, display_name, internal_alias,
                              version, created_at, updated_at, deleted_at
                    """,
                    (scope.household_space_id, display_name, internal_alias),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise PolicyStateConflict from None
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise PolicyRepositoryUnavailable
        return _family_member(row)

    def get_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> FamilyMember | None:
        predicate = "deleted_at IS NOT NULL" if deleted_only else "deleted_at IS NULL"
        query = (
            "SELECT id, household_space_id, display_name, internal_alias, version, "
            "created_at, updated_at, deleted_at FROM family_members "
            "WHERE id = %s AND household_space_id = %s AND " + predicate
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(query, (member_id, scope.household_space_id)).fetchone()
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
                    SET display_name = %s, internal_alias = %s,
                        version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                      AND deleted_at IS NULL
                    RETURNING id, household_space_id, display_name, internal_alias,
                              version, created_at, updated_at, deleted_at
                    """,
                    (display_name, internal_alias, member_id, scope.household_space_id, version),
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise PolicyStateConflict from None
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        return _family_member(row)

    def soft_delete_family_member(
        self, scope: HouseholdScope, member_id: UUID, *, expected_version: int
    ) -> FamilyMember:
        return self._set_family_member_deleted(scope, member_id, expected_version, restore=False)

    def restore_family_member(
        self, scope: HouseholdScope, member_id: UUID, *, expected_version: int
    ) -> FamilyMember:
        return self._set_family_member_deleted(scope, member_id, expected_version, restore=True)

    def _set_family_member_deleted(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        expected_version: int,
        *,
        restore: bool,
    ) -> FamilyMember:
        version = require_expected_version(expected_version)
        current = "deleted_at IS NOT NULL" if restore else "deleted_at IS NULL"
        target = "NULL" if restore else "clock_timestamp()"
        query = (
            "UPDATE family_members SET deleted_at = "
            + target
            + ", version = version + 1, updated_at = clock_timestamp() "
            "WHERE id = %s AND household_space_id = %s AND version = %s AND "
            + current
            + " RETURNING id, household_space_id, display_name, internal_alias, "
            "version, created_at, updated_at, deleted_at"
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    query, (member_id, scope.household_space_id, version)
                ).fetchone()
        except psycopg.errors.UniqueViolation:
            raise PolicyStateConflict from None
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        return _family_member(row)

    def list_policies(
        self, scope: HouseholdScope, *, deleted_only: bool = False
    ) -> list[PolicyContract]:
        predicate = "policy.deleted_at IS NOT NULL" if deleted_only else "policy.deleted_at IS NULL"
        query = (
            _POLICY_SELECT
            + " WHERE policy.household_space_id = %s AND "
            + predicate
            + " ORDER BY policy.created_at, policy.id"
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(query, (scope.household_space_id,)).fetchall()
                result = [
                    _policy(
                        row,
                        connection.execute(
                            _PARTY_SELECT, (row["id"], scope.household_space_id)
                        ).fetchall(),
                    )
                    for row in rows
                ]
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        return result

    def get_policy(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> PolicyContract | None:
        predicate = "policy.deleted_at IS NOT NULL" if deleted_only else "policy.deleted_at IS NULL"
        query = (
            _POLICY_SELECT
            + " WHERE policy.id = %s AND policy.household_space_id = %s AND "
            + predicate
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(query, (policy_id, scope.household_space_id)).fetchone()
                if row is None:
                    return None
                parties = connection.execute(
                    _PARTY_SELECT, (policy_id, scope.household_space_id)
                ).fetchall()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        return _policy(row, parties)

    def create_policy(
        self,
        scope: HouseholdScope,
        *,
        source_document_version_id: UUID,
        source_evidence: EvidenceRef,
        insurer_display: str,
        insurer_key: str,
        product_display: str,
        product_key: str,
        contract_date: date | None,
        coverage_start_date: date | None,
        coverage_end_date: date | None,
        status: PolicyStatus,
        status_evidence: EvidenceRef | None,
        parties: tuple[CreatePolicyParty, ...],
    ) -> PolicyContract:
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    INSERT INTO policy_contracts (
                        household_space_id, source_document_version_id, source_evidence_id,
                        insurer_display, insurer_key, product_display, product_key,
                        contract_date, coverage_start_date, coverage_end_date, status,
                        status_evidence_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        scope.household_space_id,
                        source_document_version_id,
                        source_evidence.evidence_id,
                        insurer_display,
                        insurer_key,
                        product_display,
                        product_key,
                        contract_date,
                        coverage_start_date,
                        coverage_end_date,
                        status,
                        status_evidence.evidence_id if status_evidence else None,
                    ),
                ).fetchone()
                if row is None:
                    raise PolicyRepositoryUnavailable
                policy_id = cast(UUID, row["id"])
                for party in parties:
                    connection.execute(
                        """
                        INSERT INTO policy_parties (
                            household_space_id, policy_contract_id, family_member_id,
                            role, effective_from, effective_to, evidence_id
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            scope.household_space_id,
                            policy_id,
                            party.family_member_id,
                            party.role,
                            party.effective_from,
                            party.effective_to,
                            party.evidence.evidence_id,
                        ),
                    )
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        created = self.get_policy(scope, policy_id)
        if created is None:
            raise PolicyRepositoryUnavailable
        return created

    def update_policy(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        *,
        expected_version: int,
        status: PolicyStatus | None,
        status_evidence: EvidenceRef | None,
        coverage_end_date: date | None,
        change_coverage_end_date: bool,
    ) -> PolicyContract:
        version = require_expected_version(expected_version)
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    """
                    UPDATE policy_contracts
                    SET status = CASE WHEN %s THEN %s ELSE status END,
                        status_evidence_id = CASE WHEN %s THEN %s ELSE status_evidence_id END,
                        coverage_end_date = CASE WHEN %s THEN %s ELSE coverage_end_date END,
                        version = version + 1, updated_at = clock_timestamp()
                    WHERE id = %s AND household_space_id = %s AND version = %s
                      AND deleted_at IS NULL
                    RETURNING id
                    """,
                    (
                        status is not None,
                        status,
                        status is not None,
                        status_evidence.evidence_id if status_evidence else None,
                        change_coverage_end_date,
                        coverage_end_date,
                        policy_id,
                        scope.household_space_id,
                        version,
                    ),
                ).fetchone()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        updated = self.get_policy(scope, policy_id)
        if updated is None:
            raise PolicyRepositoryUnavailable
        return updated

    def soft_delete_policy(
        self, scope: HouseholdScope, policy_id: UUID, *, expected_version: int
    ) -> PolicyContract:
        return self._set_policy_deleted(scope, policy_id, expected_version, restore=False)

    def restore_policy(
        self, scope: HouseholdScope, policy_id: UUID, *, expected_version: int
    ) -> PolicyContract:
        return self._set_policy_deleted(scope, policy_id, expected_version, restore=True)

    def _set_policy_deleted(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        expected_version: int,
        *,
        restore: bool,
    ) -> PolicyContract:
        version = require_expected_version(expected_version)
        current = "deleted_at IS NOT NULL" if restore else "deleted_at IS NULL"
        target = "NULL" if restore else "clock_timestamp()"
        query = (
            "UPDATE policy_contracts SET deleted_at = "
            + target
            + ", version = version + 1, updated_at = clock_timestamp() "
            "WHERE id = %s AND household_space_id = %s AND version = %s AND "
            + current
            + " RETURNING id"
        )
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                row = connection.execute(
                    query, (policy_id, scope.household_space_id, version)
                ).fetchone()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        if row is None:
            raise VersionConflict
        updated = self.get_policy(scope, policy_id, deleted_only=not restore)
        if updated is None:
            raise PolicyRepositoryUnavailable
        return updated

    def list_policy_riders(self, scope: HouseholdScope, policy_id: UUID) -> list[Rider]:
        query = """
            SELECT
                rider.id, rider.policy_contract_id, rider.display_name,
                rider.normalized_key, rider.benefit_type, rider.insured_amount,
                rider.currency, rider.coverage_start_date, rider.coverage_end_date,
                rider.renewable, rider.status, rider.version,
                source.id AS source_id,
                source.document_version_id AS source_document_version_id,
                source.extraction_id AS source_extraction_id,
                source.content_sha256 AS source_content_sha256,
                source.physical_page AS source_physical_page,
                source.x0 AS source_x0, source.y0 AS source_y0,
                source.x1 AS source_x1, source.y1 AS source_y1,
                source.review_state AS source_review_state,
                status_source.id AS status_id,
                status_source.document_version_id AS status_document_version_id,
                status_source.extraction_id AS status_extraction_id,
                status_source.content_sha256 AS status_content_sha256,
                status_source.physical_page AS status_physical_page,
                status_source.x0 AS status_x0, status_source.y0 AS status_y0,
                status_source.x1 AS status_x1, status_source.y1 AS status_y1,
                status_source.review_state AS status_review_state
            FROM riders AS rider
            JOIN policy_contracts AS policy ON policy.id = rider.policy_contract_id
            JOIN evidence AS source ON source.id = rider.source_evidence_id
            LEFT JOIN evidence AS status_source ON status_source.id = rider.status_evidence_id
            WHERE rider.policy_contract_id = %s
              AND rider.household_space_id = %s
              AND policy.household_space_id = %s
              AND rider.deleted_at IS NULL AND policy.deleted_at IS NULL
            ORDER BY rider.created_at, rider.id
        """
        try:
            with psycopg.connect(self.database_url, row_factory=dict_row) as connection:
                rows = connection.execute(
                    query,
                    (policy_id, scope.household_space_id, scope.household_space_id),
                ).fetchall()
        except psycopg.Error:
            raise PolicyRepositoryUnavailable from None
        result: list[Rider] = []
        for row in rows:
            source = _evidence(row, "source")
            if source is None:
                raise PolicyRepositoryUnavailable
            result.append(
                Rider(
                    id=cast(UUID, row["id"]),
                    policy_contract_id=cast(UUID, row["policy_contract_id"]),
                    display_name=cast(str, row["display_name"]),
                    normalized_key=cast(str, row["normalized_key"]),
                    benefit_type=cast(BenefitType, row["benefit_type"]),
                    insured_amount=cast(Decimal | None, row.get("insured_amount")),
                    currency=cast(str | None, row.get("currency")),
                    coverage_start_date=cast(date | None, row.get("coverage_start_date")),
                    coverage_end_date=cast(date | None, row.get("coverage_end_date")),
                    renewable=cast(bool | None, row.get("renewable")),
                    status=cast(PolicyStatus, row["status"]),
                    source_evidence=source,
                    status_evidence=_evidence(row, "status"),
                    version=int(row["version"]),
                )
            )
        return result


__all__ = ["PolicyLedgerRepository"]
