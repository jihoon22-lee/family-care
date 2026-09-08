"""Review routes accept server references and expose uncached scoped state."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.errors import install_error_handlers
from familycare_api.guidance_review.models import GuidanceReviewJob
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_explicit_review_routes_use_server_scope_and_no_store() -> None:
    from familycare_api.guidance_review.router import get_review_repository, router

    scope = HouseholdScope(uuid4())
    event_id, run_id, job_id = uuid4(), uuid4(), uuid4()
    job = GuidanceReviewJob(
        id=job_id,
        medical_event_id=event_id,
        decision_run_id=run_id,
        event_version=1,
        state="queued",
        http_attempts=0,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    calls = []

    class Repository:
        def enqueue(self, supplied_scope, supplied_event, **kwargs):
            calls.append(("request", supplied_scope, supplied_event, kwargs))
            return job

        def get_job(self, supplied_scope, supplied_id):
            calls.append(("read", supplied_scope, supplied_id))
            return job

        def cancel(self, supplied_scope, supplied_id):
            calls.append(("cancel", supplied_scope, supplied_id))
            return job.model_copy(update={"state": "cancelled"})

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: scope
    app.dependency_overrides[get_review_repository] = Repository
    with TestClient(app) as client:
        requested = client.post(
            f"/api/v1/medical-events/{event_id}/guidance-reviews",
            json={
                "decision_run_id": str(run_id),
                "expected_event_version": 1,
            },
        )
        fetched = client.get(f"/api/v1/guidance-reviews/{job_id}")
        cancelled = client.post(f"/api/v1/guidance-reviews/{job_id}/cancel")
        assert requested.status_code == 202
        assert fetched.status_code == cancelled.status_code == 200
        assert all(
            response.headers["cache-control"] == "no-store"
            for response in (requested, fetched, cancelled)
        )
        assert requested.json()["decision_run_id"] == str(run_id)
        assert cancelled.json()["state"] == "cancelled"
    assert calls == [
        ("request", scope, event_id, {"run_id": run_id, "expected_event_version": 1}),
        ("read", scope, job_id),
        ("cancel", scope, job_id),
    ]


@pytest.mark.parametrize("extra", ["household_space_id", "amount", "source_text", "model"])
def test_review_request_rejects_client_controlled_scope_or_result(extra: str) -> None:
    from familycare_api.guidance_review.router import get_review_repository, router

    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: HouseholdScope(uuid4())
    app.dependency_overrides[get_review_repository] = object
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/medical-events/{uuid4()}/guidance-reviews",
            json={
                "decision_run_id": str(uuid4()),
                "expected_event_version": 1,
                extra: "synthetic-client-value",
            },
        )
    assert response.status_code == 422
    assert "synthetic-client-value" not in response.text
