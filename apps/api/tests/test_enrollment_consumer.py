"""API startup consumes retained facts locally and contains transient failures."""

from threading import Event

from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.main import create_app
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from fastapi.testclient import TestClient
from pytest import LogCaptureFixture, MonkeyPatch, fixture


@fixture(autouse=True)
def stub_canonical_storage(monkeypatch: MonkeyPatch) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    monkeypatch.setattr(CanonicalLinkRepository, "refresh_pending", lambda self: 0)
    monkeypatch.setattr(DocumentMetadataProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(ComponentTermsProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(TermsApplicabilityProjector, "refresh_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(TermsChangeProjector, "refresh_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(ClauseSourceProjector, "refresh_pending", lambda *args, **kwargs: 0)


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


def test_enabled_api_refreshes_canonical_identity_without_http(monkeypatch: MonkeyPatch) -> None:
    from familycare_api.insurance_reconciliation.canonical_repository import CanonicalLinkRepository

    called = Event()

    def refresh(self: object, **kwargs: object) -> int:
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(CanonicalLinkRepository, "refresh_pending", refresh, raising=False)
    with TestClient(create_app()):
        assert called.wait(timeout=1)


def test_enabled_api_publishes_component_metadata_without_http(monkeypatch: MonkeyPatch) -> None:
    called = Event()

    def publish(self: object, **kwargs: object) -> int:
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(DocumentMetadataProjector, "project_pending", publish)
    with TestClient(create_app()):
        assert called.wait(timeout=1)


def test_enabled_api_registers_component_editions_without_http(monkeypatch: MonkeyPatch) -> None:
    called = Event()

    def publish(self: object, **kwargs: object) -> int:
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(ComponentTermsProjector, "project_pending", publish)
    with TestClient(create_app()):
        assert called.wait(timeout=1)


def test_enabled_api_assesses_terms_after_component_registration(monkeypatch: MonkeyPatch) -> None:
    from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector

    called = Event()
    order: list[str] = []

    def editions(self: object, **kwargs: object) -> int:
        order.append("editions")
        return 0

    def refresh(self: object, **kwargs: object) -> int:
        order.append("applicability")
        assert callable(kwargs["stop_requested"])
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(ComponentTermsProjector, "project_pending", editions)
    monkeypatch.setattr(TermsApplicabilityProjector, "refresh_pending", refresh)
    with TestClient(create_app()):
        assert called.wait(timeout=1)
    assert order[:2] == ["editions", "applicability"]


def test_enabled_api_assesses_changes_after_base_terms_without_http(
    monkeypatch: MonkeyPatch,
) -> None:
    called = Event()
    order: list[str] = []

    def base(self: object, **kwargs: object) -> int:
        order.append("base")
        return 0

    def changes(self: object, **kwargs: object) -> int:
        order.append("changes")
        assert callable(kwargs["stop_requested"])
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(TermsApplicabilityProjector, "refresh_pending", base)
    monkeypatch.setattr(TermsChangeProjector, "refresh_pending", changes)
    with TestClient(create_app()):
        assert called.wait(timeout=1)
    assert order[:2] == ["base", "changes"]


def test_enabled_api_replays_clause_sources_before_assessing_changes(
    monkeypatch: MonkeyPatch,
) -> None:
    called = Event()
    order: list[str] = []

    def sources(self: object, **kwargs: object) -> int:
        order.append("sources")
        assert callable(kwargs["stop_requested"])
        return 0

    def changes(self: object, **kwargs: object) -> int:
        order.append("changes")
        called.set()
        return 0

    monkeypatch.setenv("FAMILYCARE_ENABLE_RANGE_ENROLLMENT", "true")
    monkeypatch.setenv("FAMILYCARE_DATABASE_URL", "postgresql://synthetic")
    monkeypatch.setattr(RangeEnrollmentProjector, "project_pending", lambda *args, **kwargs: 0)
    monkeypatch.setattr(ClauseSourceProjector, "refresh_pending", sources)
    monkeypatch.setattr(TermsChangeProjector, "refresh_pending", changes)
    with TestClient(create_app()):
        assert called.wait(timeout=1)
    assert order[:2] == ["sources", "changes"]
