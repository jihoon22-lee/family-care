"""TDD coverage for the provider-neutral medical-event structurer."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from uuid import UUID

import pytest
from familycare_worker.ai.event_structurer import (
    EVENT_STRUCTURER_SCHEMA_NAME,
    EventStructuringPayloadInvalid,
    EventStructuringProviderError,
    EventStructuringRequest,
    OptionalQuestion,
    StructuredFactCandidate,
    event_structurer_schema,
    structure_event,
)
from familycare_worker.ai.provider import ProviderResponse, RetryableProviderError


class FakeProvider:
    """Provider fake that records only the bounded request sent to it."""

    def __init__(self, payload: Mapping[str, object] | BaseException) -> None:
        self.payload = payload
        self.calls: list[dict[str, object]] = []

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        self.calls.append(
            {
                "model": model,
                "schema_name": schema_name,
                "system_instruction": system_instruction,
                "input_payload": dict(input_payload),
            }
        )
        if isinstance(self.payload, BaseException):
            raise self.payload
        return ProviderResponse(payload=self.payload, request_id="synthetic-event-request-001")


def _request() -> EventStructuringRequest:
    return EventStructuringRequest(
        situation="Synthetic pre-visit situation",
        mode="pre_visit",
        event_date=date(2026, 8, 25),
    )


def _fact(
    fact_id: str,
    field_id: str,
    value: str | bool | None,
    state: str,
    confidence: str = "medium",
) -> dict[str, object]:
    return {
        "fact_id": fact_id,
        "field_id": field_id,
        "value": value,
        "source": "ai",
        "state": state,
        "confidence": confidence,
        "evidence_ids": [],
    }


def _question(field_id: str) -> dict[str, str]:
    return {"question_code": field_id, "field_id": field_id}


def _valid_payload() -> dict[str, object]:
    return {
        "schema_version": "1",
        "facts": [
            _fact(
                "00000000-0000-4000-8000-000000000a01",
                "condition_class",
                "synthetic-condition",
                "confirmed",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a02",
                "admission",
                None,
                "missing",
                "low",
            ),
        ],
        "questions": [_question("admission")],
    }


def test_provider_schema_declares_version_type_and_all_supported_fact_fields() -> None:
    schema = event_structurer_schema()
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    assert properties["schema_version"] == {"const": "1", "type": "string"}

    facts = properties["facts"]
    assert isinstance(facts, Mapping)
    items = facts["items"]
    assert isinstance(items, Mapping)
    fact_properties = items["properties"]
    assert isinstance(fact_properties, Mapping)
    field = fact_properties["field_id"]
    assert isinstance(field, Mapping)
    assert {
        "anatomical_site_code",
        "diagnosis_code",
        "pathology_code",
        "procedure_code",
        "separately_billed_treatment",
        "treatment_context",
        "treatment_setting",
    }.issubset(set(field["enum"]))


def test_normalized_decision_fact_candidates_survive_provider_validation() -> None:
    payload = {
        "schema_version": "1",
        "facts": [
            _fact(
                "00000000-0000-4000-8000-000000000a10",
                "anatomical_site_code",
                "sample_site",
                "confirmed",
                "high",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a11",
                "treatment_setting",
                "sample_setting",
                "confirmed",
                "high",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a12",
                "separately_billed_treatment",
                True,
                "confirmed",
                "high",
            ),
        ],
        "questions": [],
    }

    result = structure_event(
        request=_request(),
        provider=FakeProvider(payload),
        model="synthetic",
    )

    assert [(fact.field_id, fact.value) for fact in result.facts] == [
        ("anatomical_site_code", "sample_site"),
        ("treatment_setting", "sample_setting"),
        ("separately_billed_treatment", True),
    ]


def test_provider_receives_bounded_reviewed_normalization_hints() -> None:
    provider = FakeProvider(_valid_payload())
    request = EventStructuringRequest(
        situation="Synthetic procedure situation",
        mode="post_treatment",
        normalization_hints={
            "anatomical_site_code": ("sample_site",),
            "treatment_setting": ("sample_setting",),
        },
    )

    structure_event(request=request, provider=provider, model="synthetic")

    assert provider.calls[0]["input_payload"] == {
        "schema_version": "1",
        "situation": "Synthetic procedure situation",
        "mode": "post_treatment",
        "event_date": None,
        "visit_date": None,
        "normalization_hints": {
            "anatomical_site_code": ["sample_site"],
            "treatment_setting": ["sample_setting"],
        },
    }


def test_fake_provider_success_returns_only_bounded_fact_candidates() -> None:
    provider = FakeProvider(_valid_payload())

    result = structure_event(request=_request(), provider=provider, model="gpt-5.6-luna")

    assert result.provider_request_id == "synthetic-event-request-001"
    assert result.facts == (
        StructuredFactCandidate(
            field_id="condition_class",
            value="synthetic-condition",
            state="confirmed",
            fact_id=UUID("00000000-0000-4000-8000-000000000a01"),
            confidence="medium",
        ),
        StructuredFactCandidate(
            field_id="admission",
            value=None,
            state="missing",
            fact_id=UUID("00000000-0000-4000-8000-000000000a02"),
            confidence="low",
        ),
    )
    assert result.questions == (OptionalQuestion("admission", "admission"),)
    assert result.issues == ()
    assert provider.calls[0]["schema_name"] == EVENT_STRUCTURER_SCHEMA_NAME


def test_ambiguous_and_missing_facts_are_preserved_without_guessing() -> None:
    payload = {
        "schema_version": "1",
        "facts": [
            _fact(
                "00000000-0000-4000-8000-000000000a03",
                "treatment_kind",
                "synthetic-treatment",
                "ambiguous",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a04",
                "outpatient",
                None,
                "missing",
                "low",
            ),
        ],
        "questions": [_question("treatment_kind"), _question("outpatient")],
    }

    result = structure_event(request=_request(), provider=FakeProvider(payload), model="synthetic")

    assert [(fact.field_id, fact.value, fact.state) for fact in result.facts] == [
        ("treatment_kind", "synthetic-treatment", "ambiguous"),
        ("outpatient", None, "missing"),
    ]
    assert [question.question_code for question in result.questions] == [
        "treatment_kind",
        "outpatient",
    ]


def test_invented_authority_fields_are_rejected_without_echoing_payload() -> None:
    payload = _valid_payload()
    payload["decision"] = "MATCH"

    with pytest.raises(EventStructuringPayloadInvalid) as error:
        structure_event(request=_request(), provider=FakeProvider(payload), model="synthetic")

    assert repr(error.value) == "EventStructuringPayloadInvalid('EVENT_STRUCTURING_INVALID')"
    assert "MATCH" not in repr(error.value)


def test_one_invalid_fact_does_not_discard_valid_facts() -> None:
    payload = {
        "schema_version": "1",
        "facts": [
            _fact(
                "00000000-0000-4000-8000-000000000a05",
                "condition_class",
                "synthetic-condition",
                "confirmed",
                "high",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a06",
                "admission",
                "yes",
                "confirmed",
                "high",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a07",
                "invented_field",
                "synthetic",
                "confirmed",
                "high",
            ),
        ],
        "questions": [],
    }

    result = structure_event(request=_request(), provider=FakeProvider(payload), model="synthetic")

    assert [fact.field_id for fact in result.facts] == ["condition_class"]
    assert {(issue.field_id, issue.code) for issue in result.issues} == {
        ("admission", "INVALID_VALUE"),
        ("invalid", "INVENTED_FIELD"),
    }


def test_provider_timeout_is_retryable_and_does_not_expose_exception_detail() -> None:
    with pytest.raises(RetryableProviderError) as error:
        structure_event(
            request=_request(),
            provider=FakeProvider(TimeoutError("synthetic-private-timeout")),
            model="synthetic",
        )

    assert repr(error.value) == "RetryableProviderError('RETRYABLE_PROVIDER_ERROR')"
    assert "synthetic-private-timeout" not in repr(error.value)


def test_provider_failure_is_sanitized() -> None:
    with pytest.raises(EventStructuringProviderError) as error:
        structure_event(
            request=_request(),
            provider=FakeProvider(RuntimeError("synthetic-provider-secret")),
            model="synthetic",
        )

    assert repr(error.value) == "EventStructuringProviderError('EVENT_STRUCTURING_PROVIDER_ERROR')"
    assert "synthetic-provider-secret" not in repr(error.value)


def test_validation_is_deterministic_and_deduplicates_question_codes() -> None:
    payload = {
        "schema_version": "1",
        "facts": [
            _fact(
                "00000000-0000-4000-8000-000000000a08",
                "condition_class",
                "synthetic-condition",
                "confirmed",
                "high",
            ),
            _fact(
                "00000000-0000-4000-8000-000000000a09",
                "condition_class",
                "synthetic-other",
                "confirmed",
                "high",
            ),
        ],
        "questions": [_question("admission"), _question("admission")],
    }
    first = structure_event(request=_request(), provider=FakeProvider(payload), model="synthetic")
    second = structure_event(request=_request(), provider=FakeProvider(payload), model="synthetic")

    assert first == second
    assert first.facts[0].field_id == "condition_class"
    assert first.facts[0].fact_id == UUID("00000000-0000-4000-8000-000000000a08")
    assert first.questions == (OptionalQuestion("admission", "admission"),)
    assert {(issue.field_id, issue.code) for issue in first.issues} == {
        ("condition_class", "DUPLICATE_FIELD")
    }


def test_result_has_no_authority_or_private_fields() -> None:
    result = structure_event(
        request=_request(),
        provider=FakeProvider(_valid_payload()),
        model="synthetic",
    )
    representation = repr(result).lower()

    assert not {"decision", "tri_state", "eligible", "amount", "payment"}.intersection(
        result.__dict__ if hasattr(result, "__dict__") else set()
    )
    assert "source_path" not in representation
    assert "password" not in representation
    assert "household_space_id" not in representation
    assert "synthetic pre-visit situation" not in representation
