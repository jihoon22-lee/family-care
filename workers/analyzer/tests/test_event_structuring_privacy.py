"""Privacy-boundary tests for event structuring requests and results."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date

import pytest
from familycare_worker.ai.event_structurer import (
    EventStructuringRequest,
    structure_event,
)
from familycare_worker.ai.provider import ProviderResponse


class RecordingProvider:
    def __init__(self, payload: Mapping[str, object]) -> None:
        self.payload = payload
        self.request: Mapping[str, object] | None = None

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        self.request = dict(input_payload)
        return ProviderResponse(payload=self.payload, request_id="synthetic-event-request-privacy")


def _walk(value: object) -> tuple[set[str], list[str]]:
    keys: set[str] = set()
    scalars: list[str] = []
    if isinstance(value, Mapping):
        for key, child in value.items():
            keys.add(str(key).lower())
            nested_keys, nested_scalars = _walk(child)
            keys.update(nested_keys)
            scalars.extend(nested_scalars)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            nested_keys, nested_scalars = _walk(child)
            keys.update(nested_keys)
            scalars.extend(nested_scalars)
    elif value is not None:
        scalars.append(str(value))
    return keys, scalars


def test_provider_request_contains_only_event_situation_and_temporal_context() -> None:
    provider = RecordingProvider(
        {
            "schema_version": "1",
            "facts": [],
            "questions": [],
        }
    )

    structure_event(
        request=EventStructuringRequest(
            situation="Synthetic situation",
            mode="post_treatment",
            event_date=date(2026, 8, 25),
            visit_date=date(2026, 8, 24),
        ),
        provider=provider,
        model="synthetic",
    )

    assert provider.request is not None
    keys, scalars = _walk(provider.request)
    assert keys == {"schema_version", "situation", "mode", "event_date", "visit_date"}
    assert "Synthetic situation" in scalars
    assert not {
        "source_path",
        "absolute_path",
        "policy_number",
        "raw_pdf",
        "password",
        "archive_key",
        "household_space_id",
        "api_key",
        "cookie",
        "user_id",
        "member_id",
    }.intersection(keys)


def test_structurer_does_not_retain_situation_text_or_private_markers_in_result() -> None:
    provider = RecordingProvider(
        {
            "schema_version": "1",
            "facts": [
                {
                    "fact_id": "00000000-0000-4000-8000-000000000b01",
                    "field_id": "condition_class",
                    "value": "synthetic-condition",
                    "source": "ai",
                    "state": "confirmed",
                    "confidence": "medium",
                    "evidence_ids": [],
                }
            ],
            "questions": [],
        }
    )
    result = structure_event(
        request=EventStructuringRequest(situation="Synthetic situation", mode="pre_visit"),
        provider=provider,
        model="synthetic",
    )

    serialized = repr(result).lower()
    assert "synthetic situation" not in serialized
    assert "source_path" not in serialized
    assert "raw_provider_response" not in serialized
    assert "policy_number" not in serialized
    assert "amount" not in serialized
    assert "decision" not in serialized


def test_invalid_provider_field_names_are_not_reflected_in_sanitized_issues() -> None:
    provider = RecordingProvider(
        {
            "schema_version": "1",
            "facts": [
                {
                    "fact_id": "00000000-0000-4000-8000-000000000b02",
                    "field_id": "source_path",
                    "value": "synthetic/private/path",
                    "source": "ai",
                    "state": "confirmed",
                    "confidence": "high",
                    "evidence_ids": [],
                }
            ],
            "questions": [],
        }
    )

    result = structure_event(
        request=EventStructuringRequest(situation="Synthetic situation", mode="pre_visit"),
        provider=provider,
        model="synthetic",
    )

    serialized = repr(result).lower()
    assert result.facts == ()
    assert result.issues[0].field_id == "invalid"
    assert "source_path" not in serialized
    assert "synthetic/private/path" not in serialized


def test_request_rejects_unbounded_or_authority_bearing_constructor_values() -> None:
    with pytest.raises(ValueError):
        EventStructuringRequest(situation=" ", mode="pre_visit")
    with pytest.raises(ValueError):
        EventStructuringRequest(situation="x" * 2001, mode="pre_visit")
    with pytest.raises(ValueError):
        EventStructuringRequest(situation="Synthetic", mode="unsupported")  # type: ignore[arg-type]
