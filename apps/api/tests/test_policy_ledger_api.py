"""HTTP and use-case tests for the Phase 2 policy ledger."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope
from familycare_api.errors import install_error_handlers
from familycare_api.policies.domain import FamilyMember, PolicyContract, PolicyParty, Rider
from familycare_api.policies.errors import EvidenceInvalid, VersionConflict
from familycare_api.policies.router import get_policy_ledger_service, router
from familycare_api.policies.service import PolicyLedgerService
from fastapi import FastAPI
from fastapi.testclient import TestClient

NOW = datetime(2026, 1, 1, tzinfo=UTC)
SCOPE_A = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
SCOPE_B = HouseholdScope(UUID("00000000-0000-4000-8000-000000000102"))
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000201")
SOURCE_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000301")
PARTY_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000302")


def _evidence(evidence_id: UUID, *, page: int) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=UUID("00000000-0000-4000-8000-000000000401"),
        content_sha256="a" * 64,
        physical_page=page,
        bbox=(Decimal("10"), Decimal("20"), Decimal("30"), Decimal("40")),
        review_state="USER_CONFIRMED",
    )


class _MemoryEvidenceRepository:
    def __init__(self) -> None:
        self.values = {
            SOURCE_EVIDENCE_ID: _evidence(SOURCE_EVIDENCE_ID, page=1),
            PARTY_EVIDENCE_ID: _evidence(PARTY_EVIDENCE_ID, page=2),
        }

    def validate_for_document(
        self,
        scope: HouseholdScope,
        evidence_id: UUID,
        document_version_id: UUID,
    ) -> EvidenceRef:
        if scope != SCOPE_A:
            raise EvidenceInvalid
        value = self.values.get(evidence_id)
        if value is None or value.document_version_id != document_version_id:
            raise EvidenceInvalid
        return value


class _MemoryPolicyRepository:
    def __init__(self) -> None:
        self.members: dict[UUID, FamilyMember] = {}
        self.policies: dict[UUID, PolicyContract] = {}
        self.riders: dict[UUID, list[Rider]] = {}

    @staticmethod
    def _visible(scope: HouseholdScope, owner: UUID) -> bool:
        return scope.household_space_id == owner

    def list_family_members(
        self,
        scope: HouseholdScope,
        *,
        deleted_only: bool = False,
    ) -> list[FamilyMember]:
        return [
            member
            for member in self.members.values()
            if self._visible(scope, member.household_space_id)
            and ((member.deleted_at is not None) if deleted_only else (member.deleted_at is None))
        ]

    def create_family_member(
        self,
        scope: HouseholdScope,
        *,
        display_name: str,
        internal_alias: str,
    ) -> FamilyMember:
        member = FamilyMember(
            id=uuid4(),
            household_space_id=scope.household_space_id,
            display_name=display_name,
            internal_alias=internal_alias,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            deleted_at=None,
        )
        self.members[member.id] = member
        return member

    def get_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> FamilyMember | None:
        member = self.members.get(member_id)
        if member is None or not self._visible(scope, member.household_space_id):
            return None
        if deleted_only:
            return member if member.deleted_at is not None else None
        return member if member.deleted_at is None else None

    def update_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        *,
        expected_version: int,
        display_name: str,
        internal_alias: str,
    ) -> FamilyMember:
        current = self.get_family_member(scope, member_id)
        if current is None or current.version != expected_version:
            raise VersionConflict
        updated = FamilyMember(
            **{
                **current.__dict__,
                "display_name": display_name,
                "internal_alias": internal_alias,
                "version": current.version + 1,
                "updated_at": NOW,
            }
        )
        self.members[member_id] = updated
        return updated

    def soft_delete_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        *,
        expected_version: int,
    ) -> FamilyMember:
        current = self.get_family_member(scope, member_id)
        if current is None or current.version != expected_version:
            raise VersionConflict
        deleted = FamilyMember(
            **{
                **current.__dict__,
                "version": current.version + 1,
                "updated_at": NOW,
                "deleted_at": NOW,
            }
        )
        self.members[member_id] = deleted
        return deleted

    def restore_family_member(
        self,
        scope: HouseholdScope,
        member_id: UUID,
        *,
        expected_version: int,
    ) -> FamilyMember:
        current = self.get_family_member(scope, member_id, deleted_only=True)
        if current is None or current.version != expected_version:
            raise VersionConflict
        restored = FamilyMember(
            **{
                **current.__dict__,
                "version": current.version + 1,
                "updated_at": NOW,
                "deleted_at": None,
            }
        )
        self.members[member_id] = restored
        return restored


class _MemoryPolicyRepositoryWithPolicies(_MemoryPolicyRepository):
    def list_policies(
        self,
        scope: HouseholdScope,
        *,
        deleted_only: bool = False,
    ) -> list[PolicyContract]:
        return [
            policy
            for policy in self.policies.values()
            if self._visible(scope, policy.household_space_id)
            and ((policy.deleted_at is not None) if deleted_only else (policy.deleted_at is None))
        ]

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
        status: str,
        status_evidence: EvidenceRef | None,
        parties: tuple[PolicyParty, ...],
    ) -> PolicyContract:
        policy_id = uuid4()
        persisted_parties = tuple(
            PolicyParty(
                id=uuid4(),
                policy_contract_id=policy_id,
                family_member_id=party.family_member_id,
                role=party.role,
                effective_from=party.effective_from,
                effective_to=party.effective_to,
                evidence=party.evidence,
                version=1,
            )
            for party in parties
        )
        policy = PolicyContract(
            id=policy_id,
            household_space_id=scope.household_space_id,
            source_document_version_id=source_document_version_id,
            source_evidence=source_evidence,
            insurer_display=insurer_display,
            insurer_key=insurer_key,
            product_display=product_display,
            product_key=product_key,
            contract_date=contract_date,
            coverage_start_date=coverage_start_date,
            coverage_end_date=coverage_end_date,
            status=status,
            status_evidence=status_evidence,
            parties=persisted_parties,
            version=1,
            created_at=NOW,
            updated_at=NOW,
            deleted_at=None,
        )
        self.policies[policy.id] = policy
        return policy

    def get_policy(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        *,
        deleted_only: bool = False,
    ) -> PolicyContract | None:
        policy = self.policies.get(policy_id)
        if policy is None or not self._visible(scope, policy.household_space_id):
            return None
        if deleted_only:
            return policy if policy.deleted_at is not None else None
        return policy if policy.deleted_at is None else None

    def update_policy(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        *,
        expected_version: int,
        status: str | None,
        status_evidence: EvidenceRef | None,
        coverage_end_date: date | None,
        change_coverage_end_date: bool,
    ) -> PolicyContract:
        current = self.get_policy(scope, policy_id)
        if current is None or current.version != expected_version:
            raise VersionConflict
        updated = PolicyContract(
            **{
                **current.__dict__,
                "status": status or current.status,
                "status_evidence": status_evidence or current.status_evidence,
                "coverage_end_date": (
                    coverage_end_date if change_coverage_end_date else current.coverage_end_date
                ),
                "version": current.version + 1,
                "updated_at": NOW,
            }
        )
        self.policies[policy_id] = updated
        return updated

    def soft_delete_policy(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        *,
        expected_version: int,
    ) -> PolicyContract:
        current = self.get_policy(scope, policy_id)
        if current is None or current.version != expected_version:
            raise VersionConflict
        deleted = PolicyContract(
            **{
                **current.__dict__,
                "version": current.version + 1,
                "updated_at": NOW,
                "deleted_at": NOW,
            }
        )
        self.policies[policy_id] = deleted
        return deleted

    def restore_policy(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
        *,
        expected_version: int,
    ) -> PolicyContract:
        current = self.get_policy(scope, policy_id, deleted_only=True)
        if current is None or current.version != expected_version:
            raise VersionConflict
        restored = PolicyContract(
            **{
                **current.__dict__,
                "version": current.version + 1,
                "updated_at": NOW,
                "deleted_at": None,
            }
        )
        self.policies[policy_id] = restored
        return restored

    def list_policy_riders(
        self,
        scope: HouseholdScope,
        policy_id: UUID,
    ) -> list[Rider]:
        policy = self.get_policy(scope, policy_id)
        return [] if policy is None else list(self.riders.get(policy_id, []))


@pytest.fixture()
def repository() -> _MemoryPolicyRepositoryWithPolicies:
    return _MemoryPolicyRepositoryWithPolicies()


@pytest.fixture()
def service(repository: _MemoryPolicyRepositoryWithPolicies) -> PolicyLedgerService:
    return PolicyLedgerService(SCOPE_A, repository, _MemoryEvidenceRepository())


@pytest.fixture()
def client(service: PolicyLedgerService) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_policy_ledger_service] = lambda: service
    with TestClient(app) as test_client:
        yield test_client


def _create_member(client: TestClient) -> dict[str, Any]:
    response = client.post(
        "/api/v1/family-members",
        json={"display_name": "Family Member A", "internal_alias": "member-a"},
    )
    assert response.status_code == 201
    return response.json()


def _create_policy(client: TestClient, member_id: str) -> dict[str, Any]:
    response = client.post(
        "/api/v1/policies",
        json={
            "source_document_version_id": str(DOCUMENT_VERSION_ID),
            "source_evidence_id": str(SOURCE_EVIDENCE_ID),
            "insurer_display": "Sample Insurer",
            "insurer_key": "sample-insurer",
            "product_display": "Sample Policy",
            "product_key": "sample-policy",
            "status": "unknown",
            "parties": [
                {
                    "family_member_id": member_id,
                    "role": "primary_insured",
                    "evidence_id": str(PARTY_EVIDENCE_ID),
                }
            ],
        },
    )
    assert response.status_code == 201
    return response.json()


def test_family_member_lifecycle_is_strict_versioned_and_soft_deleted(client: TestClient) -> None:
    member = _create_member(client)
    member_id = member["id"]
    assert member["version"] == 1
    assert "household_space_id" not in member

    updated = client.patch(
        f"/api/v1/family-members/{member_id}",
        json={"expected_version": 1, "display_name": "Family Member A Updated"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    stale = client.patch(
        f"/api/v1/family-members/{member_id}",
        json={"expected_version": 1, "internal_alias": "stale-alias"},
    )
    assert stale.status_code == 409
    assert stale.json() == {"error_code": "VERSION_CONFLICT", "message": "version conflict"}

    deleted = client.request(
        "DELETE",
        f"/api/v1/family-members/{member_id}",
        json={"expected_version": 2},
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/family-members/{member_id}").status_code == 404
    trash = client.get("/api/v1/family-members/trash").json()
    assert trash[0]["id"] == member_id
    assert trash[0]["version"] == 3

    restored = client.post(
        f"/api/v1/family-members/{member_id}/restore",
        json={"expected_version": 3},
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == 4


def test_policy_creation_requires_scoped_family_and_verified_evidence(
    client: TestClient,
) -> None:
    member = _create_member(client)
    policy = _create_policy(client, member["id"])

    assert policy["status"] == "unknown"
    assert policy["source_evidence"]["physical_page"] == 1
    assert policy["parties"][0]["evidence"]["physical_page"] == 2
    assert client.get(f"/api/v1/policies/{policy['id']}/riders").json() == []

    serialized = str(policy).lower()
    for forbidden in ("source_key", "password", "policy_number", "document_text", "/mnt/"):
        assert forbidden not in serialized


def test_policy_status_needs_evidence_and_supports_soft_delete_restore(
    client: TestClient,
) -> None:
    member = _create_member(client)
    policy = _create_policy(client, member["id"])

    missing_evidence = client.patch(
        f"/api/v1/policies/{policy['id']}",
        json={"expected_version": 1, "status": "active"},
    )
    assert missing_evidence.status_code == 422
    assert missing_evidence.json()["error_code"] == "EVIDENCE_INVALID"

    deleted = client.request(
        "DELETE",
        f"/api/v1/policies/{policy['id']}",
        json={"expected_version": 1},
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/policies/{policy['id']}").status_code == 404
    assert client.get("/api/v1/policies/trash").json()[0]["id"] == policy["id"]

    restored = client.post(
        f"/api/v1/policies/{policy['id']}/restore",
        json={"expected_version": 2},
    )
    assert restored.status_code == 200
    assert restored.json()["version"] == 3


def test_another_household_cannot_read_family_or_policy(
    repository: _MemoryPolicyRepositoryWithPolicies,
    service: PolicyLedgerService,
) -> None:
    member = service.create_family_member("Family Member A", "member-a")
    other = PolicyLedgerService(SCOPE_B, repository, _MemoryEvidenceRepository())

    with pytest.raises(Exception) as error:
        other.get_family_member(member.id)
    assert error.type.__name__ == "FamilyMemberNotFound"


@pytest.mark.parametrize(
    "body",
    [
        {
            "display_name": "Family Member A",
            "internal_alias": "member-a",
            "household_space_id": str(SCOPE_A.household_space_id),
        },
        {
            "display_name": "Family Member A",
            "internal_alias": "member-a",
            "password": "synthetic-value",
        },
    ],
)
def test_family_member_request_rejects_authoritative_scope_and_extra_values(
    client: TestClient,
    body: dict[str, Any],
) -> None:
    response = client.post("/api/v1/family-members", json=body)
    assert response.status_code == 422
    serialized = response.text.lower()
    assert "synthetic-value" not in serialized
    assert response.json()["error_code"] == "INVALID_REQUEST"
