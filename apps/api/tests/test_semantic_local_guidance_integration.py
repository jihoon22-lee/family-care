"""Original policy + bound original Clause reaches the actual local analysis route."""

from datetime import date
from typing import NoReturn
from uuid import uuid4

import httpx2
import psycopg
import pytest
from familycare_api.claims.errors import ClaimInvalid
from familycare_api.claims.repository import ClaimRepository
from familycare_api.clauses.repository import RiderClauseLinkRepository
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.decisions.router import get_decision_service, router
from familycare_api.decisions.schemas import MedicalEventUpdateRequest
from familycare_api.errors import install_error_handlers
from familycare_api.guidance.repository import read_operational_guidance
from familycare_api.terms_knowledge.projector import TermsSemanticProjector
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from apps.api.tests.test_clause_change_integration import _native_link
from apps.api.tests.test_clause_source_publication import _clause
from apps.api.tests.test_decision_integration import _service
from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    enrollment_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)
from apps.api.tests.test_terms_change_integration import (
    ranges_database as ranges_database,
)
from apps.api.tests.test_terms_change_integration import (
    seeded_policy_database as seeded_policy_database,
)
from apps.api.tests.test_terms_change_integration import (
    structure_database as structure_database,
)
from apps.api.tests.test_terms_source_verification import DAILY, FOOTNOTE, LIMIT

pytestmark = pytest.mark.integration


def test_native_enrollment_and_original_semantic_clause_calculate_without_private_import(
    changes_database,
    monkeypatch,
):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    outbound = []

    def reject_http(*args, **kwargs) -> NoReturn:
        outbound.append(1)
        raise AssertionError("default guidance attempted external HTTP")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject_http)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", reject_http)
    url, job = changes_database
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "INSERT INTO family_members(household_space_id,display_name,internal_alias) "
            "VALUES (%s,'Synthetic Sibling','synthetic-sibling')",
            (job.household_space_id,),
        )
    body = "\n".join(
        (
            DAILY,
            LIMIT,
            "Eligible admission days range from 1 to 365 inclusive.",
            "Apply Footnote 1.",
        )
    )
    # The footnote belongs beneath the actual Article; the next Article closes its region.
    body = f"{body}\nFootnote 1\n{FOOTNOTE}"
    terms = f"Article 1\n{body}\nArticle 2\nSynthetic unrelated context."
    sources = _sources(
        url,
        job,
        terms_body=terms,
        new_terms_body=terms.replace("first 2 admission", "first 1 admission"),
        sample_amount=100,
    )
    clause = _clause(url, job, sources["EDITION-A"], body=body, label="Article 1")
    new_clause = _clause(
        url,
        job,
        sources["EDITION-B"],
        body=body.replace("first 2 admission", "first 1 admission"),
        label="Article 1",
    )
    assert ClauseSourceProjector(url).refresh_pending() == 2
    assert TermsChangeProjector(url).refresh_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        links = [
            _native_link(
                connection,
                scope.household_space_id,
                sources["Sample Rider"],
                linked_clause,
                sources[edition],
            )
            for edition, linked_clause in (("EDITION-A", clause), ("EDITION-B", new_clause))
        ]
    for link in links:
        RiderClauseLinkRepository(url).confirm(scope, link, expected_version=1)
    assert TermsSemanticProjector(url).project_pending() == 2
    service = _service(url, scope)
    other_event = service.create_medical_event(
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="Synthetic Sibling는 5일 입원했습니다.",
        event_date=date(2025, 6, 15),
        facts={},
    )
    other_result = service.analyze_medical_event(other_event.id)
    assert other_result.local_guidance is not None
    assert not other_result.local_guidance.candidates
    event = service.create_medical_event(
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="5일 입원했습니다.",
        event_date=date(2025, 6, 15),
        visit_date=None,
        facts={"MedicalEvent.admission_days": 5},
        confirmation={"MedicalEvent.admission_days": "user"},
    )
    result = service.analyze_medical_event(event.id)
    assert result.local_guidance is not None
    candidate = next(
        c for c in result.local_guidance.candidates if c.ref.coverage_id == sources["Sample Rider"]
    )
    assert candidate.ref.kind == "OPERATIONAL_RIDER"
    assert candidate.estimate.amount == "300"
    assert candidate.estimate.evidence
    assert all(c.kind == "SEMANTIC_CITATION" for c in candidate.estimate.evidence)
    assert service.get_decision_result(event.id, 1).local_guidance == result.local_guidance
    claims = ClaimRepository(url)
    for selected_scope, selected_event, selected_version, selected_ref in (
        (HouseholdScope(uuid4()), event.id, 1, candidate.ref),
        (scope, other_event.id, 1, candidate.ref),
        (scope, event.id, 2, candidate.ref),
        (scope, event.id, 1, candidate.ref.model_copy(update={"contract_id": uuid4()})),
    ):
        with pytest.raises(ClaimInvalid):
            claims.create_guidance_claim_case(
                selected_scope,
                selected_event,
                run_id=result.run_id,
                expected_event_version=selected_version,
                coverage=selected_ref,
            )
    claim = claims.create_guidance_claim_case(
        scope,
        event.id,
        run_id=result.run_id,
        expected_event_version=1,
        coverage=candidate.ref,
    )
    assert claim["status"] == "preparing" and claim["paid_amount"] is None
    assert claim["snapshot"]["local_guidance"]["candidate"]["estimate"]["amount"] == "300"
    snapshot_hash = claim["snapshot"]["snapshot_sha256"]
    assert (
        claims.create_guidance_claim_case(
            scope,
            event.id,
            run_id=result.run_id,
            expected_event_version=1,
            coverage=candidate.ref,
        )["id"]
        == claim["id"]
    )
    service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest(expected_version=event.version, event_date=date(2025, 7, 15)),
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: scope
    app.dependency_overrides[get_decision_service] = lambda: service
    with TestClient(app) as client:
        response = client.post(f"/api/v1/medical-events/{event.id}/analyze")
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        updated = response.json()["local_guidance"]
        assert updated["schema_version"] == "2"
        assert updated["candidates"]
        assert (
            next(
                c
                for c in updated["candidates"]
                if c["ref"]["coverage_id"] == str(sources["Sample Rider"])
            )["estimate"]["amount"]
            == "400"
        )
        historical = client.get(f"/api/v1/medical-events/{event.id}/results/1")
        assert historical.json()["local_guidance"] == result.local_guidance.model_dump(mode="json")
        assert historical.json()["local_guidance_stale"] is True
        saved_claim = claims.get_claim_case(scope, claim["id"])
        assert saved_claim["snapshot"]["snapshot_sha256"] == snapshot_hash
        assert saved_claim["snapshot"]["local_guidance"]["candidate"]["estimate"]["amount"] == "300"
        with pytest.raises(ClaimInvalid):
            claims.create_guidance_claim_case(
                scope,
                event.id,
                run_id=result.run_id,
                expected_event_version=1,
                coverage=candidate.ref,
            )
        current = client.get(f"/api/v1/medical-events/{event.id}/results/2")
        assert current.json()["local_guidance_stale"] is False
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute(
                "UPDATE rider_clause_links SET review_state='rejected',version=version+1 "
                "WHERE id=%s",
                (links[1],),
            )
        retired = client.get(f"/api/v1/medical-events/{event.id}/results/2")
        assert retired.json()["local_guidance_stale"] is True
        assert retired.json()["local_guidance"] == updated
    # Changing a ledger number cannot acquire the original publication's authority.
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE riders SET insured_amount=999 WHERE id=%s", (sources["Sample Rider"],)
        )
        context = read_operational_guidance(connection, scope, event, service.repository)
        changed = next(c for c in context.coverages if c.ref.coverage_id == sources["Sample Rider"])
        assert changed.insured_amount is None
        assert changed.certificate_amount_decision == "UNKNOWN"
        connection.rollback()
    assert not outbound
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM private_knowledge_import_runs").fetchone()[0]
            == 0
        )
