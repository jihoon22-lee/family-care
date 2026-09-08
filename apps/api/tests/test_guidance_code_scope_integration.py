"""Explicit code-system/version metadata survives ordinary event edits and history."""

import psycopg
import pytest
from familycare_api.decisions.schemas import MedicalEventUpdateRequest
from psycopg.rows import dict_row

from apps.api.tests.test_decision_integration import _create_event, _psycopg_url, _service
from apps.api.tests.test_decision_integration import database_url as database_url
from apps.api.tests.test_decision_integration import seed as seed

pytestmark = pytest.mark.integration


def test_explicit_code_scope_is_preserved_and_never_inherited_by_a_changed_code(database_url, seed):
    service = _service(database_url, seed.scope_a)
    event = _create_event(service, seed.member_a)
    updated = service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": event.version,
                "structured_facts": [
                    {
                        "field_id": "diagnosis_code",
                        "value": "synthetic-a",
                        "code_system": "synthetic-system",
                        "code_version": "edition-1",
                    }
                ],
            }
        ),
    )
    fact = next(f for f in updated.structured_facts if f["field_id"] == "diagnosis_code")
    assert fact["code_system"] == "synthetic-system" and fact["code_version"] == "edition-1"
    unrelated = service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": updated.version,
                "structured_facts": [{"field_id": "admission", "value": True}],
            }
        ),
    )
    assert next(f for f in unrelated.structured_facts if f["field_id"] == "diagnosis_code") == fact
    changed = service.update_medical_event(
        event.id,
        MedicalEventUpdateRequest.model_validate(
            {
                "expected_version": unrelated.version,
                "structured_facts": [{"field_id": "diagnosis_code", "value": "synthetic-b"}],
            }
        ),
    )
    new_fact = next(f for f in changed.structured_facts if f["field_id"] == "diagnosis_code")
    assert new_fact["code_system"] is None and new_fact["code_version"] is None
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        history = connection.execute(
            "SELECT facts_json FROM medical_event_fact_versions "
            "WHERE medical_event_id=%s ORDER BY version",
            (event.id,),
        ).fetchall()
        assert history[0]["facts_json"]["diagnosis_code"]["code_version"] == "edition-1"
        assert len(history) == 3
