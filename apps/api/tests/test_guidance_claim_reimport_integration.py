"""New private imports keep proven claim identities and recorded payments."""

from datetime import date
from uuid import UUID

import psycopg
import pytest
from familycare_api.claims.errors import ClaimInvalid
from familycare_api.claims.repository import ClaimRepository
from familycare_api.guidance.private_adapter import adapt_private_guidance
from familycare_api.guidance.repository import read_operational_guidance
from psycopg.rows import dict_row

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    LinkedPrivateClaim,
    _psycopg_url,
)
from apps.api.tests.test_guidance_history_aliases_integration import (
    enrollment_database,  # noqa: F401
    native_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_history_aliases_integration import (
    linked_private_claim as linked_private_claim,
)
from apps.api.tests.test_guidance_history_aliases_integration import (
    reimported_claim as reimported_claim,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("entrypoint", ["guidance", "legacy"])
def test_reimported_source_reuses_original_private_claim_and_snapshot(
    reimported_claim: tuple[LinkedPrivateClaim, UUID],
    entrypoint: str,
) -> None:
    linked, _ = reimported_claim
    saved = linked.saved
    fresh = linked.service.analyze_medical_event(saved.event_id)
    claims = ClaimRepository(saved.url)
    if entrypoint == "guidance":
        result = claims.create_guidance_claim_case(
            saved.scope,
            saved.event_id,
            run_id=fresh.run_id,
            expected_event_version=1,
            coverage=linked.operational_ref,
        )
    else:
        result = claims.create_claim_case(
            saved.scope,
            saved.event_id,
            rider_id=linked.operational_ref.coverage_id,
        )
    assert result["id"] == linked.original.id
    assert result["snapshot"]["snapshot_sha256"] == linked.original.snapshot.snapshot_sha256
    with psycopg.connect(_psycopg_url(saved.url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM claim_cases WHERE medical_event_id=%s AND deleted_at IS NULL",
            (saved.event_id,),
        ).fetchone() == (1,)


@pytest.mark.parametrize("reimported_claim", ["paid"], indirect=True)
def test_payment_recorded_before_reimport_reaches_both_guidance_readers(
    reimported_claim: tuple[LinkedPrivateClaim, UUID],
) -> None:
    linked, current_coverage_id = reimported_claim
    saved, service = linked.saved, linked.service
    original_event = service.get_medical_event(saved.event_id)

    def event_on(day: int):
        return service.create_medical_event(
            family_member_id=original_event.family_member_id,
            mode="post_treatment",
            situation="Synthetic sample category phrase follow-up.",
            event_date=date(2025, 6, day),
            facts={"MedicalEvent.classification": "sample_category"},
            confirmation={"MedicalEvent.classification": "user"},
        )

    def counts(event):
        with psycopg.connect(_psycopg_url(saved.url), row_factory=dict_row) as connection:
            operational = read_operational_guidance(
                connection, saved.scope, event, service.repository
            )
            private = service.repository.knowledge_repository.read_context(
                connection, saved.scope, event
            )
        assert private.context is not None
        coverages = (
            next(c for c in operational.coverages if c.ref == linked.operational_ref),
            next(
                c
                for c in adapt_private_guidance(private.context).coverages
                if c.ref.coverage_id == current_coverage_id
            ),
        )
        return tuple(
            c.claim_history_counted_occurrence.value if c.claim_history_counted_occurrence else None
            for c in coverages
        )

    assert counts(event_on(21)) == (1, 1)
    assert counts(event_on(19)) == (None, None)
    assert counts(original_event) == (None, None)
    claim = ClaimRepository(saved.url).get_claim_case(saved.scope, linked.original.id)
    assert claim["status"] == "paid"
    assert claim["snapshot"]["snapshot_sha256"] == linked.original.snapshot.snapshot_sha256
    with psycopg.connect(_psycopg_url(saved.url)) as connection:
        assert connection.execute(
            "SELECT rider_id,private_coverage_id,counted_occurrence FROM claim_history "
            "WHERE medical_event_id=%s",
            (saved.event_id,),
        ).fetchone() == (None, saved.ref.coverage_id, True)


def test_reimported_private_claim_restore_rejects_its_active_operational_alias(
    reimported_claim: tuple[LinkedPrivateClaim, UUID],
) -> None:
    linked, _ = reimported_claim
    saved = linked.saved
    claims = ClaimRepository(saved.url)
    claims.soft_delete_claim_case(saved.scope, linked.original.id, expected_version=1)
    fresh = linked.service.analyze_medical_event(saved.event_id)
    replacement = claims.create_guidance_claim_case(
        saved.scope,
        saved.event_id,
        run_id=fresh.run_id,
        expected_event_version=1,
        coverage=linked.operational_ref,
    )
    assert replacement["id"] != linked.original.id
    with pytest.raises(ClaimInvalid):
        claims.restore_claim_case(saved.scope, linked.original.id, expected_version=2)
    original = claims.get_claim_case(saved.scope, linked.original.id, deleted_only=True)
    assert original["version"] == 2
    assert original["snapshot"]["snapshot_sha256"] == linked.original.snapshot.snapshot_sha256


def test_unavailable_history_alias_reader_keeps_candidates_and_reports_source_failure(
    linked_private_claim: LinkedPrivateClaim,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from familycare_api.insurance_reconciliation import claim_aliases

    linked = linked_private_claim

    def fail(connection, *args, **kwargs):
        connection.execute("SELECT synthetic_unavailable_claim_alias_column")

    monkeypatch.setattr(claim_aliases, "read_claim_coverage_aliases", fail)
    result = linked.service.analyze_medical_event(linked.saved.event_id)
    assert result.local_guidance is not None and result.local_guidance.candidates
    assert "CLAIM_HISTORY_ALIAS_UNAVAILABLE" in result.local_guidance.support.failure_codes
    assert "CLAIM_HISTORY_ALIAS_UNAVAILABLE" in result.source_failure_codes
    assert linked.service.get_decision_result(linked.saved.event_id, 1).local_guidance is not None
