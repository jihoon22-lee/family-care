"""API startup consumes retained facts locally and contains transient failures."""

from threading import Event

from familycare_api.main import create_app
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from fastapi.testclient import TestClient
from pytest import LogCaptureFixture, MonkeyPatch


def test_enabled_api_consumes_without_a_request_and_stops(monkeypatch: MonkeyPatch) -> None:
    called = Event()
    checks = []

    def consume(self: object, **kwargs: object) -> int:
        checks.append(kwargs["stop_requested"])
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", consume)
    with TestClient(create_app()):
        assert called.wait(timeout=1)
    assert checks and checks[0]()


def test_disabled_api_does_not_start_projection(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", raising=False)
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")

    def forbidden(*args: object, **kwargs: object) -> int:
        raise AssertionError("projection is disabled")

    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", forbidden)
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200


def test_transient_projection_failure_retries_without_logging_details(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    recovered = Event()
    attempts = []

    def consume(self: object, **kwargs: object) -> int:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("synthetic private payload")
        recovered.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", consume)
    with TestClient(create_app()) as client:
        assert client.get("/health/live").status_code == 200
        assert recovered.wait(timeout=4)
    assert "range_enrollment_projection_unavailable" in caplog.messages
    assert "synthetic private payload" not in caplog.text
