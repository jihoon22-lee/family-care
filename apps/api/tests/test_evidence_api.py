"""HTTP contract tests for bounded Evidence disclosure."""

from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.errors import EvidenceNotFound
from familycare_api.decisions.evidence_router import get_evidence_service, router
from familycare_api.errors import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000201")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000202")


class _FakeEvidenceService:
    visible = True

    def get_evidence(self, evidence_id: UUID) -> dict[str, object]:
        if not self.visible:
            raise EvidenceNotFound
        return {
            "schema_version": "1",
            "evidence_id": str(evidence_id),
            "document_version_id": str(DOCUMENT_VERSION_ID),
            "document_label": "Sample Terms",
            "physical_page": 3,
            "clause_label": "Sample Clause 3",
            "bounded_excerpt": "Wholly synthetic bounded clause excerpt.",
            "bbox": [10.0, 20.0, 200.0, 80.0],
            "review_state": "AI_VERIFIED",
        }


@pytest.fixture()
def service() -> _FakeEvidenceService:
    return _FakeEvidenceService()


@pytest.fixture()
def client(service: _FakeEvidenceService) -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_evidence_service] = lambda: service
    app.dependency_overrides[resolve_household_scope] = lambda: SCOPE
    with TestClient(app) as test_client:
        yield test_client


def test_get_evidence_returns_only_bounded_page_clause_and_coordinates(
    client: TestClient,
) -> None:
    response = client.get(f"/api/v1/evidence/{EVIDENCE_ID}")

    assert response.status_code == 200
    assert response.json()["physical_page"] == 3
    assert response.json()["clause_label"] == "Sample Clause 3"
    assert response.json()["bounded_excerpt"] == "Wholly synthetic bounded clause excerpt."
    assert response.headers["cache-control"] == "no-store"
    serialized = response.text.lower()
    for forbidden in ("source_key", "full_text", "path", "password", "/mnt/", "c:\\"):
        assert forbidden not in serialized


def test_cross_scope_evidence_is_not_found_and_uncached(
    client: TestClient,
    service: _FakeEvidenceService,
) -> None:
    service.visible = False

    response = client.get(f"/api/v1/evidence/{EVIDENCE_ID}")

    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "Sample Terms" not in response.text
