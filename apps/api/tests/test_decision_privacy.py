"""Privacy and serialization boundaries for coverage decision responses."""

from __future__ import annotations

import ast
import copy
import json
import logging
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from familycare_api.decisions.errors import DecisionInvalid
from familycare_api.decisions.router import get_decision_service, router
from familycare_api.decisions.schemas import CoverageDecisionResponse
from familycare_api.errors import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[3]
SCHEMA_PATH = ROOT / "packages/contracts/schemas/coverage-decision.v1.schema.json"
EXAMPLE_PATH = ROOT / "packages/contracts/examples/coverage-decision.v1.json"
DECISION_SOURCE_ROOT = ROOT / "apps/api/src/familycare_api/decisions"

RAW_FACT_SENTINEL = "SYNTHETIC_FACT_VALUE_SENTINEL"
DIAGNOSIS_SENTINEL = "SYNTHETIC_DIAGNOSIS_SENTINEL"
PRIVATE_PATH_SENTINEL = "synthetic/private/document/path.pdf"
SCOPE_ID = "00000000-0000-4000-8000-000000000101"
MEMBER_ID = "00000000-0000-4000-8000-000000000202"

_FORBIDDEN_KEYS = {
    "absolute_path",
    "archive_key",
    "amount",
    "diagnosis",
    "diagnosis_text",
    "document_path",
    "document_text",
    "file_path",
    "guarantee",
    "guaranteed",
    "household_space_id",
    "password",
    "pdf_path",
    "raw_description",
    "raw_text",
    "source_path",
}
_FORBIDDEN_SOURCE_TOKENS = (
    "openai",
    "gemini",
    "google.generativeai",
    "anthropic",
    "litellm",
)
_FORBIDDEN_CALL_NAMES = {
    "OpenAI",
    "Gemini",
    "GenerativeModel",
    "Anthropic",
    "completion",
    "chat_completion",
}


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _schema_validator() -> Any:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.check_document_contracts import validate_schema_instance

    return validate_schema_instance


def _walk_keys(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def _walk_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from _walk_strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_strings(child)


def _assert_private_response_boundary(value: Any) -> None:
    keys = {key.lower() for key in _walk_keys(value)}
    assert not keys & _FORBIDDEN_KEYS
    assert "facts" not in keys
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True).lower()
    for sentinel in (RAW_FACT_SENTINEL, DIAGNOSIS_SENTINEL, PRIVATE_PATH_SENTINEL):
        assert sentinel.lower() not in serialized
    for item in _walk_strings(value):
        assert "/mnt/" not in item
        assert not re.match(r"^(?:/|~/|[A-Za-z]:[\\/])", item)


def _event_body() -> dict[str, Any]:
    return {
        "family_member_id": MEMBER_ID,
        "mode": "post_treatment",
        "event_date": "2026-08-25",
        "visit_date": "2026-08-25",
        "facts": {
            "MedicalEvent.classification": {
                "value": RAW_FACT_SENTINEL,
                "confirmation": "user",
            }
        },
    }


class _InvalidService:
    def create_medical_event(self, request: object) -> None:
        del request
        raise DecisionInvalid


@contextmanager
def _invalid_request_client() -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[get_decision_service] = lambda: _InvalidService()
    with TestClient(app) as client:
        yield client


def test_actual_coverage_decision_response_serialization_matches_schema() -> None:
    schema = _load_json(SCHEMA_PATH)
    example = _load_json(EXAMPLE_PATH)
    example["evaluations"][1]["evidence"][0]["bbox"] = [
        72.1234,
        120.5678,
        480.0001,
        180.9999,
    ]
    example["policy_snapshot_at"] = "2026-08-25T09:00:00.123456Z"
    response = CoverageDecisionResponse.from_value(example)
    serialized = json.loads(response.model_dump_json())

    assert not _schema_validator()(schema, serialized)
    assert set(serialized) == set(schema["required"])
    _assert_private_response_boundary(serialized)


def test_decision_response_and_contract_objects_are_strict() -> None:
    schema = _load_json(SCHEMA_PATH)
    example = _load_json(EXAMPLE_PATH)
    validate_schema_instance = _schema_validator()

    objects = []
    pending: list[Any] = [schema]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if item.get("type") == "object":
                objects.append(item)
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    assert objects
    assert all(item.get("additionalProperties") is False for item in objects)

    with pytest.raises(ValidationError):
        CoverageDecisionResponse.model_validate({**example, "private_extra": "value"})

    nested = copy.deepcopy(example)
    nested["evaluations"][0]["evidence"][0]["private_extra"] = "value"
    with pytest.raises(ValidationError):
        CoverageDecisionResponse.model_validate(nested)
    assert validate_schema_instance(schema, {**example, "private_extra": "value"})
    assert validate_schema_instance(schema, nested)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda body: body["facts"].update(
            {"PolicyContract.contract_start": {"value": "2026-01-01", "confirmation": "user"}}
        ),
        lambda body: body["facts"].update(
            {"Rider.status": {"value": "active", "confirmation": "user"}}
        ),
        lambda body: body["facts"].update(
            {"ClaimHistory.counted_occurrence": {"value": 0, "confirmation": "user"}}
        ),
        lambda body: body["facts"]["MedicalEvent.classification"].update({"evidence_ids": []}),
        lambda body: body.update({"result": "MATCH"}),
        lambda body: body.update({"amount": 1}),
        lambda body: body.update({"description": DIAGNOSIS_SENTINEL}),
    ],
    ids=[
        "policy-field",
        "rider-field",
        "history-field",
        "evidence-ids",
        "tri-state",
        "amount",
        "raw-description",
    ],
)
def test_client_cannot_submit_private_or_decision_fields(
    mutation: Any,
) -> None:
    with _invalid_request_client() as client:
        body = _event_body()
        mutation(body)
        response = client.post("/api/v1/medical-events", json=body)

    assert response.status_code == 422
    payload = response.json()
    assert payload["error_code"] == "INVALID_REQUEST"
    assert "detail" not in payload
    assert RAW_FACT_SENTINEL not in response.text
    assert DIAGNOSIS_SENTINEL not in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_domain_error_is_value_free_and_uncached() -> None:
    with _invalid_request_client() as client:
        response = client.post("/api/v1/medical-events", json=_event_body())

    assert response.status_code == 422
    assert response.json() == {
        "error_code": "DECISION_INVALID",
        "message": "decision request is invalid",
    }
    assert RAW_FACT_SENTINEL not in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_validation_does_not_log_supplied_fact_diagnosis_or_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    body = _event_body()
    body["description"] = DIAGNOSIS_SENTINEL
    body["source_path"] = PRIVATE_PATH_SENTINEL
    caplog.set_level(logging.DEBUG)
    with _invalid_request_client() as client:
        response = client.post("/api/v1/medical-events", json=body)

    assert response.status_code == 422
    assert RAW_FACT_SENTINEL not in caplog.text
    assert DIAGNOSIS_SENTINEL not in caplog.text
    assert PRIVATE_PATH_SENTINEL not in caplog.text


def test_decision_service_and_engine_have_no_ai_provider_calls() -> None:
    for filename in ("service.py", "engine.py"):
        source = (DECISION_SOURCE_ROOT / filename).read_text(encoding="utf-8")
        lowered = source.lower()
        assert not any(token in lowered for token in _FORBIDDEN_SOURCE_TOKENS)

        tree = ast.parse(source, filename=filename)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in _FORBIDDEN_CALL_NAMES
