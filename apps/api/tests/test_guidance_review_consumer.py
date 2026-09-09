"""Explicit review results are projected even when document ingestion is disabled."""

from threading import Event

from familycare_api.main import create_app
from fastapi.testclient import TestClient


def test_review_consumer_runs_without_ingestion_opt_in_and_stops(monkeypatch):
    from familycare_api.guidance_review.projector import GuidanceReviewProjector

    called = Event()
    stops = []
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.delenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def project(self, **kwargs):
        stops.append(kwargs["stop_requested"])
        called.set()
        return 0

    monkeypatch.setattr(GuidanceReviewProjector, "project_pending", project)
    with TestClient(create_app(readiness_probe=lambda: True)) as client:
        assert called.wait(timeout=2)
        assert client.get("/health/live").status_code == 200
    assert stops and stops[0]()


def test_review_projection_error_is_sanitized_and_does_not_stop_app(monkeypatch, caplog):
    from familycare_api.guidance_review.projector import GuidanceReviewProjector

    called = Event()
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.delenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", raising=False)

    def fail(self, **kwargs):
        called.set()
        raise RuntimeError("synthetic-private-proposal-must-not-be-logged")

    monkeypatch.setattr(GuidanceReviewProjector, "project_pending", fail)
    with TestClient(create_app(readiness_probe=lambda: True)) as client:
        assert called.wait(timeout=2)
        assert client.get("/health/live").status_code == 200
    assert "synthetic-private-proposal" not in caplog.text
    assert "guidance_review_projection_unavailable" in caplog.text
