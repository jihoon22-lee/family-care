"""Persistence mapping tests for MedicalEvent structuring jobs."""

from __future__ import annotations

from uuid import UUID

import pytest
from familycare_api.decisions.errors import DecisionRepositoryUnavailable
from familycare_api.decisions.structuring_repository import _job, _merge_user_overrides

JOB_ID = UUID("00000000-0000-4000-8000-000000000101")
EVENT_ID = UUID("00000000-0000-4000-8000-000000000102")
FACT_ID = UUID("00000000-0000-4000-8000-000000000103")


def _row() -> dict[str, object]:
    return {
        "id": JOB_ID,
        "medical_event_id": EVENT_ID,
        "event_version": 1,
        "state": "succeeded",
        "attempts": 1,
        "error_code": None,
        "facts_json": {
            "condition_class": {
                "fact_id": str(FACT_ID),
                "value": "synthetic-condition",
                "source": "ai",
                "state": "confirmed",
                "confidence": "medium",
                "evidence_ids": [],
            },
            "admission": {
                "fact_id": "00000000-0000-4000-8000-000000000104",
                "value": False,
                "source": "user",
                "state": "confirmed",
                "confidence": "high",
                "evidence_ids": [],
            },
        },
        "questions_json": [{"question_code": "pharmacy", "field_id": "pharmacy"}],
        "issue_codes_json": [{"code": "INVALID_VALUE"}],
    }


def test_job_mapper_returns_bounded_fact_and_question_projection() -> None:
    job = _job(_row())

    assert job.id == JOB_ID
    assert [fact.field_id for fact in job.facts] == ["admission", "condition_class"]
    assert job.facts[0].value is False
    assert job.facts[1].fact_id == FACT_ID
    assert job.questions[0].question_code == "pharmacy"
    assert job.issues[0].code == "INVALID_VALUE"


@pytest.mark.parametrize(
    "mutation",
    [
        {"state": "MATCH"},
        {"attempts": 11},
        {"error_code": "RAW_PROVIDER_FAILURE"},
        {
            "facts_json": {
                "condition_class": {
                    "fact_id": str(FACT_ID),
                    "value": 1000,
                    "source": "ai",
                    "state": "confirmed",
                    "confidence": "high",
                    "evidence_ids": [],
                }
            }
        },
        {
            "facts_json": {
                "decision": {
                    "fact_id": str(FACT_ID),
                    "value": "MATCH",
                    "source": "ai",
                    "state": "confirmed",
                    "confidence": "high",
                    "evidence_ids": [],
                }
            }
        },
        {"questions_json": [{"question_code": "payment", "field_id": "pharmacy"}]},
    ],
)
def test_job_mapper_rejects_authority_money_and_malformed_persistence(
    mutation: dict[str, object],
) -> None:
    row = _row()
    row.update(mutation)

    with pytest.raises(DecisionRepositoryUnavailable):
        _job(row)


def test_user_override_preserves_ai_parent_data_and_removes_answered_question() -> None:
    row = _row()

    facts, questions, changed, conflict = _merge_user_overrides(
        row["facts_json"],
        [
            {"question_code": "condition_class", "field_id": "condition_class"},
            {"question_code": "pharmacy", "field_id": "pharmacy"},
        ],
        {"condition_class": "synthetic-correction", "pharmacy": False},
    )

    assert facts["condition_class"]["source"] == "user"
    assert facts["condition_class"]["value"] == "synthetic-correction"
    assert facts["admission"]["source"] == "user"
    assert facts["pharmacy"]["value"] is False
    assert questions == []
    assert changed == ("condition_class", "pharmacy")
    assert conflict is True
