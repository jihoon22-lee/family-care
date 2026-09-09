"""PostgreSQL integration tests for scoped structuring and user precedence."""

from __future__ import annotations

import os
from collections.abc import Iterator
from uuid import UUID

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import DecisionInvalid, MedicalEventNotFound
from familycare_api.decisions.repository import DecisionRepository
from familycare_api.decisions.schemas import MedicalEventUpdateRequest
from familycare_api.decisions.service import DecisionService
from familycare_api.decisions.structuring_repository import EventStructuringRepository
from familycare_api.policies.errors import VersionConflict
from psycopg.types.json import Jsonb

pytestmark = pytest.mark.integration

HOUSEHOLD_A = UUID("00000000-0000-4000-8000-000000000101")
HOUSEHOLD_B = UUID("00000000-0000-4000-8000-000000000102")
MEMBER_A = UUID("00000000-0000-4000-8000-000000000201")
FACT_ID = UUID("00000000-0000-4000-8000-000000000301")


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(autouse=True)
def clean_database(database_url: str) -> Iterator[None]:
    with psycopg.connect(database_url) as connection:
        connection.execute("TRUNCATE household_spaces CASCADE")
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, %s, %s), (%s, %s, %s)
            """,
            (
                HOUSEHOLD_A,
                "synthetic-household-a",
                "Synthetic Household A",
                HOUSEHOLD_B,
                "synthetic-household-b",
                "Synthetic Household B",
            ),
        )
        connection.execute(
            """
            INSERT INTO family_members (
              id, household_space_id, display_name, internal_alias
            ) VALUES (%s, %s, %s, %s)
            """,
            (MEMBER_A, HOUSEHOLD_A, "Synthetic Member A", "synthetic-member-a"),
        )
    yield


def _event(database_url: str) -> object:
    return DecisionService(
        HouseholdScope(HOUSEHOLD_A),
        DecisionRepository(database_url),
    ).create_medical_event(
        family_member_id=MEMBER_A,
        mode="pre_visit",
        situation="Synthetic Member plans an outpatient visit.",
    )


def test_enqueue_is_scoped_idempotent_and_version_checked(database_url: str) -> None:
    event = _event(database_url)
    repository = EventStructuringRepository(database_url)
    scope_a = HouseholdScope(HOUSEHOLD_A)

    first = repository.enqueue(scope_a, event.id, event.version)
    duplicate = repository.enqueue(scope_a, event.id, event.version)

    assert first.id == duplicate.id
    assert first.state == "queued"
    loaded_event = DecisionRepository(database_url).get_medical_event(scope_a, event.id)
    assert loaded_event.auto_structuring_attempted is True
    with psycopg.connect(database_url) as connection:
        max_attempts = connection.execute(
            "SELECT max_attempts FROM medical_event_structuring_jobs WHERE id = %s",
            (first.id,),
        ).fetchone()
    assert max_attempts == (1,)
    assert repository.get_job(scope_a, first.id).id == first.id
    with pytest.raises(VersionConflict):
        repository.enqueue(scope_a, event.id, event.version + 1)
    with pytest.raises(MedicalEventNotFound):
        repository.get_job(HouseholdScope(HOUSEHOLD_B), first.id)


def test_enqueue_reuses_terminal_automatic_attempt_for_same_event_version(
    database_url: str,
) -> None:
    event = _event(database_url)
    repository = EventStructuringRepository(database_url)
    scope = HouseholdScope(HOUSEHOLD_A)
    first = repository.enqueue(scope, event.id, event.version)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE medical_event_structuring_jobs
            SET state = 'permanently_failed', attempts = max_attempts,
                error_code = 'STRUCTURING_PROVIDER_TIMEOUT'
            WHERE id = %s
            """,
            (first.id,),
        )

    repeated = repository.enqueue(scope, event.id, event.version)

    assert repeated.id == first.id
    assert repeated.state == "permanently_failed"


def test_cancelled_claimed_job_remains_a_persistent_automatic_attempt(
    database_url: str,
) -> None:
    event = _event(database_url)
    repository = EventStructuringRepository(database_url)
    scope = HouseholdScope(HOUSEHOLD_A)
    job = repository.enqueue(scope, event.id, event.version)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE medical_event_structuring_jobs
            SET state = 'cancelled', attempts = 1
            WHERE id = %s
            """,
            (job.id,),
        )

    loaded = DecisionRepository(database_url).get_medical_event(scope, event.id)

    assert loaded.auto_structuring_attempted is True


def test_user_override_preserves_ai_version_and_wins_in_decision_facts(
    database_url: str,
) -> None:
    event = _event(database_url)
    scope = HouseholdScope(HOUSEHOLD_A)
    structuring = EventStructuringRepository(database_url)
    job = structuring.enqueue(scope, event.id, event.version)
    ai_facts = {
        "condition_class": {
            "fact_id": str(FACT_ID),
            "value": "synthetic-ai-value",
            "source": "ai",
            "state": "confirmed",
            "confidence": "medium",
            "evidence_ids": [],
        }
    }
    with psycopg.connect(database_url) as connection:
        connection.execute(
            """
            UPDATE medical_event_structuring_jobs
            SET state = 'succeeded', attempts = 1, completed_at = clock_timestamp()
            WHERE id = %s
            """,
            (job.id,),
        )
        connection.execute(
            """
            INSERT INTO medical_event_fact_versions (
              household_space_id, medical_event_id, structuring_job_id,
              event_version, version, source, version_state, facts_json,
              questions_json, issue_codes_json, is_current
            ) VALUES (%s, %s, %s, 1, 1, 'ai', 'candidate', %s, %s, '[]', true)
            """,
            (
                HOUSEHOLD_A,
                event.id,
                job.id,
                Jsonb(ai_facts),
                Jsonb([{"question_code": "admission", "field_id": "admission"}]),
            ),
        )

    service = DecisionService(scope, DecisionRepository(database_url))
    response_event = service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": 1,
                "structured_facts": [
                    {"field_id": "condition_class", "value": "synthetic-user-value"},
                    {"field_id": "admission", "value": False},
                ],
            }
        ),
    )

    assert response_event.version == 2
    assert response_event.auto_structuring_attempted is True
    assert response_event.facts["MedicalEvent.classification"].value == "synthetic-user-value"
    assert response_event.facts["MedicalEvent.classification"].confirmation == "user"
    assert response_event.facts["MedicalEvent.admission_days"].value == 0
    projected = {item["field_id"]: item for item in response_event.structured_facts}
    assert projected["condition_class"]["source"] == "user"
    assert projected["condition_class"]["value"] == "synthetic-user-value"
    assert structuring.get_job(scope, job.id).facts[0].value == "synthetic-ai-value"

    with psycopg.connect(database_url) as connection:
        versions = connection.execute(
            """
            SELECT source, version_state, is_current
            FROM medical_event_fact_versions
            WHERE medical_event_id = %s
            ORDER BY version
            """,
            (event.id,),
        ).fetchall()
        audit = connection.execute(
            """
            SELECT action, actor_kind, reason_code
            FROM medical_event_fact_audit
            WHERE medical_event_id = %s
            """,
            (event.id,),
        ).fetchone()
    assert versions == [("ai", "superseded", False), ("user", "applied", True)]
    assert audit == ("conflict_detected", "user", "USER_AI_CONFLICT")


@pytest.mark.parametrize(("admission", "days"), [(True, 5), (False, 0), (None, 5)])
def test_combined_admission_answers_preserve_explicit_days(
    database_url: str, admission: bool | None, days: int
) -> None:
    event = _event(database_url)
    service = DecisionService(HouseholdScope(HOUSEHOLD_A), DecisionRepository(database_url))
    updated = service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": event.version,
                "facts": {"MedicalEvent.admission_days": {"value": days, "confirmation": "user"}},
                "structured_facts": [{"field_id": "admission", "value": admission}],
            }
        ),
    )
    assert updated.facts["MedicalEvent.admission_days"].value == days
    assert updated.facts["MedicalEvent.admission_days"].confirmation == "user"


def test_conflicting_combined_admission_answers_do_not_change_event(database_url: str) -> None:
    event = _event(database_url)
    scope = HouseholdScope(HOUSEHOLD_A)
    repository = DecisionRepository(database_url)
    with pytest.raises(DecisionInvalid):
        DecisionService(scope, repository).update_medical_event(
            event.id,
            MedicalEventUpdateRequest.model_validate(
                {
                    "expected_version": event.version,
                    "facts": {"MedicalEvent.admission_days": {"value": 5, "confirmation": "user"}},
                    "structured_facts": [{"field_id": "admission", "value": False}],
                }
            ),
        )
    assert repository.get_medical_event(scope, event.id).version == event.version


def test_diagnosis_confirmation_update_roundtrip_is_scoped_versioned_and_reversible(database_url):
    event = _event(database_url)
    scope = HouseholdScope(HOUSEHOLD_A)
    repository = DecisionRepository(database_url)
    service = DecisionService(scope, repository)
    first_version = event.version
    for value in (True, False, None):
        previous_version = event.version
        change = MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": previous_version,
                "structured_facts": [{"field_id": "diagnosis_confirmed", "value": value}],
            }
        )
        with pytest.raises(MedicalEventNotFound):
            DecisionService(HouseholdScope(HOUSEHOLD_B), repository).update_medical_event(
                event.id, change
            )
        event = service.update_medical_event(event.id, change)
        assert event.version == previous_version + 1
        stored = repository.get_medical_event(scope, event.id)
        fact = next(
            fact for fact in stored.structured_facts if fact["field_id"] == "diagnosis_confirmed"
        )
        assert fact["value"] is value and fact["source"] == "user"
        assert fact["state"] == ("missing" if value is None else "confirmed")
        with pytest.raises(VersionConflict):
            service.update_medical_event(event.id, change)
    with psycopg.connect(database_url) as connection:
        history = connection.execute(
            "SELECT event_version, facts_json->'diagnosis_confirmed'->'value' "
            "FROM medical_event_fact_versions "
            "WHERE medical_event_id=%s ORDER BY event_version",
            (event.id,),
        ).fetchall()
    assert history == [
        (first_version + 1, True),
        (first_version + 2, False),
        (first_version + 3, None),
    ]


@pytest.mark.parametrize("value", [True, False, None])
def test_diagnosis_history_blocks_downgrade_before_any_semantic_publication(database_url, value):
    from apps.api.tests.test_metadata_navigation_publication import _migrate

    migration_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    event = _event(database_url)
    service = DecisionService(HouseholdScope(HOUSEHOLD_A), DecisionRepository(database_url))
    event = service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": event.version,
                "structured_facts": [{"field_id": "diagnosis_confirmed", "value": value}],
            }
        ),
    )
    with psycopg.connect(database_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM terms_semantic_publications"
        ).fetchone() == (0,)
    try:
        refused = _migrate(migration_url, "downgrade", "0069_policy_draft_replay")
        assert refused.returncode != 0
        assert "event diagnosis history prevents downgrade" in refused.stderr
        assert service.repository.get_medical_event(service.scope, event.id) == event
    finally:
        assert _migrate(migration_url, "upgrade", "head").returncode == 0


def test_diagnosis_question_without_a_fact_also_preserves_rollback_boundary(database_url):
    from apps.api.tests.test_metadata_navigation_publication import _migrate

    event = _event(database_url)
    scope = HouseholdScope(HOUSEHOLD_A)
    job = EventStructuringRepository(database_url).enqueue(scope, event.id, event.version)
    with psycopg.connect(database_url) as connection:
        connection.execute(
            "INSERT INTO medical_event_fact_versions (household_space_id,medical_event_id,"
            "structuring_job_id,event_version,version,source,version_state,facts_json,"
            "questions_json,issue_codes_json,is_current) VALUES(%s,%s,%s,1,1,'ai','candidate',"
            "'{}',%s,'[]',true)",
            (
                HOUSEHOLD_A,
                event.id,
                job.id,
                Jsonb(
                    [
                        {"question_code": "diagnosis_confirmed", "field_id": "diagnosis_confirmed"},
                    ]
                ),
            ),
        )
    migration_url = database_url.replace("postgresql://", "postgresql+psycopg://", 1)
    refused = _migrate(migration_url, "downgrade", "0069_policy_draft_replay")
    assert refused.returncode != 0
    assert "event diagnosis history prevents downgrade" in refused.stderr
    retained = DecisionRepository(database_url).get_medical_event(scope, event.id)
    assert any(
        question["field_id"] == "diagnosis_confirmed" for question in retained.optional_questions
    )


def test_parser_revision_marks_saved_guidance_stale_without_rewriting_it(database_url, monkeypatch):
    from familycare_api.guidance import interpretation

    event = _event(database_url)
    service = DecisionService(HouseholdScope(HOUSEHOLD_A), DecisionRepository(database_url))
    original = service.analyze_medical_event(event.id)
    assert original.local_guidance is not None
    assert service.get_decision_result(event.id, event.version).local_guidance_stale is False
    monkeypatch.setattr(interpretation, "INTERPRETATION_REVISION", "synthetic-next-parser-revision")
    saved = service.get_decision_result(event.id, event.version)
    assert saved.local_guidance_stale is True
    assert saved.local_guidance == original.local_guidance
