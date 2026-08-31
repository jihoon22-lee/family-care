from familycare_api.health import database_is_ready
from familycare_api.main import create_app
from fastapi.testclient import TestClient
from pytest import MonkeyPatch


def test_liveness_reports_api_identity() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"service": "api", "status": "ok", "version": "0.3.1"}


def test_readiness_reports_process_ready() -> None:
    client = TestClient(create_app(readiness_probe=lambda: True))

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"service": "api", "status": "ready", "version": "0.3.1"}


def test_readiness_reports_database_unavailable() -> None:
    client = TestClient(create_app(readiness_probe=lambda: False))

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {
        "service": "api",
        "status": "unavailable",
        "version": "0.3.1",
    }


def test_database_probe_is_unavailable_without_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("FAMILYCARE_DATABASE_URL", raising=False)

    assert database_is_ready() is False


def test_database_probe_is_unavailable_for_invalid_url() -> None:
    assert database_is_ready("not-a-database-url") is False
