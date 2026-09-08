"""Real synthetic PostgreSQL/API round trip and immutable guidance boundaries."""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

import httpx2
import psycopg
import pytest
from alembic import command
from alembic.config import Config
from familycare_api.common.scope import resolve_household_scope
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.router import get_decision_service, router
from familycare_api.decisions.service import DecisionService
from familycare_api.errors import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from apps.api.tests.test_decision_integration import _psycopg_url, _reset_database, _seed
from apps.api.tests.test_private_knowledge_decision_integration import _seed_private_publication

pytestmark = pytest.mark.integration


@pytest.fixture()
def database_url() -> str:
    value = os.environ["FAMILYCARE_TEST_DATABASE_URL"]
    _reset_database(value)
    return value


def test_api_returns_and_preserves_document_guidance_without_ai_or_latest_status(
    database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    outbound_requests: list[object] = []

    def reject_outbound_http(*args: object, **kwargs: object) -> NoReturn:
        outbound_requests.append(args)
        raise AssertionError("default analysis attempted an external HTTP request")

    monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", reject_outbound_http)
    monkeypatch.setattr(httpx2.AsyncHTTPTransport, "handle_async_request", reject_outbound_http)
    seed = _seed(database_url)
    _seed_private_publication(database_url, seed, tmp_path)
    # The stored interpretation was published earlier. There is no current status
    # confirmation and its historical interval does not cover this new event.
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "UPDATE private_knowledge_contract_confirmations "
            "SET is_current = false, superseded_at = clock_timestamp() WHERE is_current"
        )
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))
    event = service.create_medical_event(
        family_member_id=seed.member_a,
        mode="post_treatment",
        situation="Synthetic event for Sample Policy",
        event_date=date(2026, 6, 15),
        visit_date=date(2026, 6, 16),
        facts={"MedicalEvent.classification": "sample_category"},
        confirmation={"MedicalEvent.classification": "user"},
    )
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: seed.scope_a
    app.dependency_overrides[get_decision_service] = lambda: service
    with TestClient(app) as client:
        response = client.post(f"/api/v1/medical-events/{event.id}/analyze")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        guidance = payload["local_guidance"]
        assert guidance["candidates"][0]["freshness"] == "DOCUMENT_CONTINUITY"
        assert guidance["candidates"][0]["estimate"]["amount"] == "1"
        assert guidance["review_state"] == "NOT_REQUESTED"
        assert payload["assistance"]["state"] == "SEARCH_READY"

        stored = client.get(f"/api/v1/medical-events/{event.id}/results/1")
        assert stored.status_code == 200
        assert stored.json()["local_guidance"] == guidance

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT count(*) FROM analysis_assistance_jobs WHERE state IN ('QUEUED', 'RUNNING')"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT count(*) FROM private_knowledge_contract_confirmations WHERE is_current"
        ).fetchone() == (0,)
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id = %s", (payload["run_id"],)
        ).fetchone() == (guidance,)
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            changed = {**guidance, "event_version": 2}
            connection.execute(
                "UPDATE decision_runs SET local_guidance_json = %s WHERE id = %s",
                (Jsonb(changed), payload["run_id"]),
            )

    service.update_medical_event(
        event.id,
        expected_version=1,
        facts={"MedicalEvent.classification": "other_category"},
        confirmation={"MedicalEvent.classification": "user"},
    )
    newer = service.analyze_medical_event(event.id)
    assert newer.local_guidance is not None
    assert not newer.local_guidance.candidates
    old = service.get_decision_result(event.id, 1)
    assert old.stale
    assert old.local_guidance is not None
    assert old.local_guidance.model_dump(mode="json") == guidance
    assert outbound_requests == []


def test_guidance_storage_rejects_missing_null_and_mismatched_snapshot_identity(
    database_url: str, tmp_path: Path
) -> None:
    seed = _seed(database_url)
    _seed_private_publication(database_url, seed, tmp_path)
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))
    event = service.create_medical_event(
        family_member_id=seed.member_a,
        mode="post_treatment",
        situation="Synthetic event",
        event_date=date(2025, 6, 15),
        facts={"MedicalEvent.classification": "sample_category"},
        confirmation={"MedicalEvent.classification": "user"},
    )
    result = service.analyze_medical_event(event.id)
    assert result.local_guidance is not None
    snapshot = result.local_guidance.model_dump(mode="json")
    statement = """
        INSERT INTO decision_runs
        SELECT (jsonb_populate_record(NULL::decision_runs, to_jsonb(source) || %s)).*
        FROM decision_runs AS source WHERE source.id = %s
    """
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert (
            connection.execute(
                statement,
                (Jsonb({"id": str(uuid4()), "local_guidance_json": snapshot}), result.run_id),
            ).rowcount
            == 1
        )
        invalid = [
            {},
            {**snapshot, "schema_version": None},
            {**snapshot, "household_space_id": None},
            {**snapshot, "household_space_id": str(seed.scope_b.household_space_id)},
            {**snapshot, "medical_event_id": None},
            {**snapshot, "medical_event_id": str(uuid4())},
            {**snapshot, "event_version": None},
            {**snapshot, "event_version": 99},
        ]
        for value in invalid:
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(
                    statement,
                    (Jsonb({"id": str(uuid4()), "local_guidance_json": value}), result.run_id),
                )


def test_upgrade_preserves_a_pre_guidance_result_without_recalculating_it(
    database_url: str,
) -> None:
    seed = _seed(database_url)
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))
    event = service.create_medical_event(
        family_member_id=seed.member_a,
        mode="post_treatment",
        situation="Synthetic historical event",
        event_date=date(2025, 6, 15),
    )
    config = Config(str(Path(__file__).resolve().parents[3] / "apps/api/alembic.ini"))
    run_id = uuid4()
    upgraded = False
    command.downgrade(config, "0024_insurance_reconciliation")
    try:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute(
                """
                INSERT INTO decision_runs (
                  id, household_space_id, medical_event_id, engine_version,
                  rule_set_version, event_version, policy_snapshot_at, status
                ) VALUES (%s, %s, %s, 'decision-engine-v1', 'coverage-rules-v1', 1,
                          clock_timestamp(), 'succeeded')
                """,
                (run_id, seed.scope_a.household_space_id, event.id),
            )
            before = connection.execute(
                "SELECT to_jsonb(run) FROM decision_runs AS run WHERE id = %s", (run_id,)
            ).fetchone()
        command.upgrade(config, "head")
        upgraded = True
        historical = service.get_decision_result(event.id, 1)
        assert historical.run_id == run_id
        assert historical.local_guidance is None
        assert historical.candidates == ()
        assert historical.terms_selections == ()
        assert historical.source_rule_version_ids == ()
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            after = connection.execute(
                "SELECT to_jsonb(run) - ARRAY['local_guidance_json','terms_selections_json',"
                "'source_rule_version_ids'], local_guidance_json IS NULL "
                "AND terms_selections_json IS NULL AND source_rule_version_ids IS NULL "
                "FROM decision_runs AS run WHERE id = %s",
                (run_id,),
            ).fetchone()
        assert after is not None and after[1] is True
        assert (after[0],) == before
    finally:
        if not upgraded:
            command.upgrade(config, "head")


def test_private_source_fallback_preserves_other_family_member_scope(
    database_url, tmp_path, monkeypatch
):
    from apps.api.tests import test_private_knowledge_decision_integration as fixtures
    from apps.api.tests.private_knowledge_publication_fixtures import mutate_publication_jsonl

    original_writer = fixtures.write_synthetic_rule_publication_package

    def admission_package(path):
        root = original_writer(path)

        def admission_rule(row):
            row["rule_document"]["input_field_paths"] = ["MedicalEvent.admission_days"]
            row["rule_document"]["expression"] = {
                "op": "range",
                "field": "MedicalEvent.admission_days",
                "value": {"min": 1, "max": 365},
                "unit": "days",
            }

        mutate_publication_jsonl(root, "rule-publications.jsonl", admission_rule)
        return root

    monkeypatch.setattr(fixtures, "write_synthetic_rule_publication_package", admission_package)
    seed = _seed(database_url)
    _seed_private_publication(database_url, seed, tmp_path)
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            "INSERT INTO family_members(household_space_id,display_name,internal_alias) "
            "VALUES (%s,'Synthetic Sibling','synthetic-sibling')",
            (seed.scope_a.household_space_id,),
        )

    def unavailable(*args, **kwargs):
        raise ValueError("SYNTHETIC_OPERATIONAL_SOURCE_UNAVAILABLE")

    monkeypatch.setattr(
        "familycare_api.decisions.repository.read_operational_guidance", unavailable
    )
    service = DecisionService(seed.scope_a, DecisionRepository(database_url))
    for text, expected_count in (
        ("Synthetic Sibling는 5일 입원했습니다.", 0),
        ("5일 입원했습니다.", 1),
    ):
        event = service.create_medical_event(
            family_member_id=seed.member_a,
            mode="post_treatment",
            situation=text,
            event_date=date(2025, 6, 15),
            facts={},
        )
        result = service.analyze_medical_event(event.id)
        assert result.local_guidance is not None
        assert len(result.local_guidance.candidates) == expected_count
        assert "OPERATIONAL_GUIDANCE_SOURCE_UNAVAILABLE" in result.source_failure_codes
