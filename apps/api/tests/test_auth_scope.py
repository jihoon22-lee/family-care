"""Authentication is the only source of business HouseholdScope."""

from __future__ import annotations

from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.identity.context import AuthContext, resolve_auth_context
from familycare_api.policies.router import router as policy_router
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_client_household_id_cannot_replace_server_context() -> None:
    household_id = UUID("00000000-0000-4000-8000-000000000001")
    app = FastAPI()
    app.include_router(policy_router)
    app.dependency_overrides[resolve_auth_context] = lambda: AuthContext(
        user_id=UUID("00000000-0000-4000-8000-000000000011"),
        household_space_id=household_id,
        session_id=UUID("00000000-0000-4000-8000-000000000021"),
        needs_reauthentication=False,
    )

    from familycare_api.policies.router import get_policy_ledger_service

    class _Service:
        def list_family_members(self) -> list[object]:
            return []

    app.dependency_overrides[get_policy_ledger_service] = lambda: _Service()
    response = TestClient(app).get(
        "/api/v1/family-members",
        params={"household_space_id": "00000000-0000-4000-8000-000000000099"},
    )

    assert response.status_code == 200
    assert response.json() == []

    from familycare_api.common.scope import resolve_household_scope

    context = app.dependency_overrides[resolve_auth_context]()
    assert resolve_household_scope(context).household_space_id == household_id
    assert isinstance(resolve_household_scope(context), HouseholdScope)
