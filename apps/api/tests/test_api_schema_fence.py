"""Schema checks precede consumers and business handlers without exposing failures."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from familycare_api import main
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def consumer_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")

    @asynccontextmanager
    async def consumers(app: FastAPI) -> AsyncIterator[None]:
        calls.append("start")
        try:
            yield
        finally:
            calls.append("stop")

    monkeypatch.setattr(main, "application_lifespan", consumers)
    return calls


@pytest.mark.parametrize("raises", [False, True])
def test_unavailable_schema_refuses_start_before_consumers(consumer_calls, raises):
    def probe():
        if raises:
            raise RuntimeError("synthetic-private-probe-detail")
        return False

    with (
        pytest.raises(RuntimeError, match="^database schema unavailable$") as failure,
        TestClient(main.create_app(readiness_probe=probe)),
    ):
        pytest.fail("incompatible schema started")
    assert failure.value.__suppress_context__
    assert consumer_calls == []


def test_ready_schema_starts_consumers_with_ai_disabled(consumer_calls, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", raising=False)

    def probe():
        consumer_calls.append("probe")
        return True

    with TestClient(main.create_app(readiness_probe=probe)) as client:
        assert consumer_calls == ["probe", "start"]
        assert client.get("/health/live").status_code == 200
        assert consumer_calls == ["probe", "start"]
    assert consumer_calls[-1] == "stop"


@pytest.mark.parametrize("method", ["GET", "POST", "PATCH", "DELETE"])
def test_runtime_mismatch_blocks_handlers_and_recovers(consumer_calls, method):
    ready = True
    writes = []
    app = main.create_app(readiness_probe=lambda: ready)

    @app.api_route("/api/v1/synthetic-business", methods=[method])
    def business():
        writes.append("handled")
        return {"ok": True}

    with TestClient(app) as client:
        assert client.request(method, "/api/v1/synthetic-business").status_code == 200
        ready = False
        response = client.request(method, "/api/v1/synthetic-business")
        assert response.status_code == 503
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "error_code": "RESOURCE_LIMIT_EXCEEDED",
            "message": "database schema unavailable",
        }
        assert writes == ["handled"]
        assert client.get("/health/live").status_code == 200
        assert client.get("/health/ready").status_code == 503
        ready = True
        assert client.request(method, "/api/v1/synthetic-business").status_code == 200
        assert writes == ["handled", "handled"]


def test_default_probe_failure_is_sanitized_without_running_lifespan(
    consumer_calls, monkeypatch, caplog
):
    def probe():
        raise RuntimeError("synthetic-private-probe-detail")

    monkeypatch.setattr(main, "database_is_ready", probe)
    client = TestClient(main.create_app())
    response = client.get("/api/v1/synthetic-business")
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic-private" not in response.text + caplog.text
    assert consumer_calls == []


def test_database_absent_factory_retains_mock_and_openapi_support(consumer_calls, monkeypatch):
    monkeypatch.delenv("FAMILYCARE_DATABASE_URL", raising=False)

    def forbidden_probe():
        pytest.fail("database-absent mock app must not probe for business requests")

    app = main.create_app(readiness_probe=forbidden_probe)

    @app.get("/api/v1/synthetic-business")
    def business():
        return {"ok": True}

    assert app.openapi()["info"]["title"] == "FamilyCare API"
    with TestClient(app) as client:
        assert client.get("/api/v1/synthetic-business").status_code == 200


def test_database_configured_after_factory_is_checked_before_start(consumer_calls, monkeypatch):
    monkeypatch.delenv("FAMILYCARE_DATABASE_URL", raising=False)
    app = main.create_app(readiness_probe=lambda: False)
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    with (
        pytest.raises(RuntimeError, match="^database schema unavailable$"),
        TestClient(app),
    ):
        pytest.fail("startup ignored database configuration")
    assert consumer_calls == []
