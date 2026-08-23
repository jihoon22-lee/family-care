from familycare_api.main import create_app
from fastapi.testclient import TestClient


def test_liveness_reports_api_identity() -> None:
    client = TestClient(create_app())

    response = client.get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"service": "api", "status": "ok", "version": "0.0.0"}


def test_readiness_reports_process_ready() -> None:
    client = TestClient(create_app())

    response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"service": "api", "status": "ready", "version": "0.0.0"}
