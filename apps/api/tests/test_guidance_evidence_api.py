"""Common evidence disclosure keeps source kinds explicit and uses no-store HTTP."""

from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.errors import EvidenceNotFound
from familycare_api.errors import install_error_handlers
from familycare_api.guidance_evidence.models import GuidanceEvidenceDetail, GuidanceEvidenceRequest
from familycare_api.guidance_evidence.router import get_guidance_evidence_service, router
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError


def _request():
    return {
        "decision_run_id": str(UUID(int=101)),
        "expected_event_version": 1,
        "coverage": {
            "kind": "OPERATIONAL_RIDER",
            "contract_id": str(UUID(int=102)),
            "coverage_id": str(UUID(int=103)),
        },
        "evidence": {
            "kind": "OPERATIONAL_EVIDENCE",
            "evidence_id": str(UUID(int=104)),
            "page_start": 2,
            "page_end": 2,
        },
    }


class FakeService:
    failure = False

    def get_detail(self, event_id, request):
        if self.failure:
            raise EvidenceNotFound
        return GuidanceEvidenceDetail(
            evidence=request.evidence,
            content_kind="ORIGINAL",
            document_label="Sample Policy",
            document_version_id=UUID(int=105),
            page_start=2,
            page_end=2,
            text="Synthetic policy evidence.",
        )


@pytest.fixture()
def client():
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    service = FakeService()
    app.dependency_overrides[resolve_household_scope] = lambda: HouseholdScope(UUID(int=106))
    app.dependency_overrides[get_guidance_evidence_service] = lambda: service
    with TestClient(app) as client:
        yield client, service


def test_saved_guidance_evidence_http_contract_is_bounded_and_uncached(client):
    http, _ = client
    response = http.post(
        f"/api/v1/medical-events/{UUID(int=107)}/guidance-evidence", json=_request()
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    value = response.json()
    assert value["content_kind"] == "ORIGINAL"
    assert value["text"] == "Synthetic policy evidence."
    assert value["document_version_id"] == str(UUID(int=105))
    assert value["terms_edition_label"] is None
    assert value["source_document_ref"] is None
    assert not value["truncated"]


def test_missing_membership_returns_value_free_uncached_error(client):
    http, service = client
    service.failure = True
    response = http.post(
        f"/api/v1/medical-events/{UUID(int=107)}/guidance-evidence", json=_request()
    )
    assert response.status_code == 404
    assert response.headers["cache-control"] == "no-store"
    assert "Sample Policy" not in response.text


@pytest.mark.parametrize("extra", [{"source_path": "/synthetic/private.pdf"}, {"text": "override"}])
def test_request_cannot_supply_paths_or_replacement_content(extra):
    with pytest.raises(ValidationError):
        GuidanceEvidenceRequest.model_validate({**_request(), **extra})


def test_unavailable_is_a_typed_item_without_fabricated_content():
    detail = GuidanceEvidenceDetail(
        evidence=GuidanceEvidenceRequest.model_validate(_request()).evidence,
        content_kind="UNAVAILABLE",
        document_label="보험 근거 문서",
        page_start=2,
        page_end=2,
        reason_codes=("EVIDENCE_ORIGINAL_UNAVAILABLE",),
    )
    assert detail.text is None
    with pytest.raises(ValidationError):
        GuidanceEvidenceDetail.model_validate({**detail.model_dump(), "text": "Invented text"})


def test_common_evidence_requires_an_authenticated_session():
    from familycare_api.identity.context import get_session_service

    class NoSession:
        def resolve(self, *args):
            return None

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_session_service] = NoSession
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/medical-events/{UUID(int=107)}/guidance-evidence", json=_request()
        )
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
