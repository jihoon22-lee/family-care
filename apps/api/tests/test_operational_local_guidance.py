"""Operational source facts reach the shared local result without a private import."""

from typing import NoReturn

import httpx2
import psycopg
import pytest
from familycare_api.common.scope import resolve_household_scope
from familycare_api.decisions.router import get_decision_service, router
from familycare_api.errors import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from apps.api.tests.test_decision_integration import (
    _create_event,
    _psycopg_url,
    _service,
)
from apps.api.tests.test_decision_integration import (
    database_url as database_url,
)
from apps.api.tests.test_decision_integration import (
    seed as seed,
)

pytestmark = pytest.mark.integration


def test_operational_ledger_produces_local_guidance_without_private_import(
    database_url, seed, monkeypatch
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    outbound = []

    def reject_http(*args, **kwargs) -> NoReturn:
        outbound.append(1)
        raise AssertionError("local analysis attempted external HTTP")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject_http)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", reject_http)
    service = _service(database_url, seed.scope_a)
    event = _create_event(service, seed.member_a)
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: seed.scope_a
    app.dependency_overrides[get_decision_service] = lambda: service
    with TestClient(app) as client:
        response = client.post(f"/api/v1/medical-events/{event.id}/analyze")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        guidance = response.json()["local_guidance"]
        assert guidance is not None
        assert guidance["versions"]["catalog_import_run_id"] is None
        candidate = next(
            item
            for item in guidance["candidates"]
            if item["ref"]["coverage_id"] == str(seed.good_rider_id)
        )
        assert candidate["ref"]["kind"] == "OPERATIONAL_RIDER"
        assert candidate["estimate"]["amount"] is None
        assert candidate["conditions"]
        assert all(
            evidence["kind"] == "OPERATIONAL_EVIDENCE"
            for condition in candidate["conditions"]
            for evidence in condition["evidence"]
        )
        assert str(seed.uninsured_rider_id) not in {
            item["ref"]["coverage_id"] for item in guidance["candidates"]
        }
        historical = client.get(f"/api/v1/medical-events/{event.id}/results/1")
        assert historical.json()["local_guidance"] == guidance
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM private_knowledge_import_runs").fetchone()[0]
            == 0
        )
    assert not outbound


@pytest.mark.parametrize("status_case", ["conflict", "unreviewed", "absent"])
def test_event_status_uncertainty_is_distinct_from_no_status_record(
    database_url, seed, status_case
):
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        if status_case == "conflict":
            connection.execute(
                "INSERT INTO policy_status_snapshots(household_space_id,rider_id,status,"
                "effective_at,evidence_id) SELECT household_space_id,rider_id,'inactive',"
                "effective_at,evidence_id FROM policy_status_snapshots WHERE rider_id=%s",
                (seed.good_rider_id,),
            )
        elif status_case == "unreviewed":
            # The clause evidence remains usable, but this status source cannot establish status.
            connection.execute(
                "UPDATE policy_status_snapshots SET evidence_id=%s WHERE rider_id=%s",
                (seed.bad_terms_evidence_id, seed.good_rider_id),
            )
            connection.execute(
                "UPDATE evidence SET review_state='NEEDS_REVIEW' WHERE id=%s",
                (seed.bad_terms_evidence_id,),
            )
        else:
            connection.execute("DELETE FROM policy_status_snapshots")
    service = _service(database_url, seed.scope_a)
    event = _create_event(service, seed.member_a)
    from familycare_api.guidance.engine import _event_status
    from familycare_api.guidance.repository import read_operational_guidance

    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        context = read_operational_guidance(connection, seed.scope_a, event, service.repository)
    coverage = next(
        item for item in context.coverages if item.ref.coverage_id == seed.good_rider_id
    )
    assert _event_status(event, coverage) == (
        "DOCUMENT_CONTINUITY" if status_case == "absent" else "STATUS_UNRESOLVED"
    )
