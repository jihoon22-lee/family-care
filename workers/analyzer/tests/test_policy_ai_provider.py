"""OpenAI adapter boundary tests with no external request."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pytest
from familycare_worker.ai.provider import (
    OpenAiResponsesAdapter,
    ProviderConfigurationError,
    ProviderValidationError,
)


@dataclass
class _Response:
    id: str = "synthetic-provider-request-001"
    output_text: str = '{"schema_version":"1"}'


class _Responses:
    def __init__(self, response: _Response | None = None) -> None:
        self.response = response or _Response()
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _Response:
        self.requests.append(dict(kwargs))
        return self.response


class _Client:
    def __init__(self, responses: _Responses) -> None:
        self.responses = responses


def _schema(properties: Mapping[str, object] | None = None) -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": dict(properties or {"schema_version": {"const": "1"}}),
        "required": ["schema_version"],
    }


def test_adapter_rejects_a_response_schema_with_forbidden_private_fields() -> None:
    """A registry cannot legitimize private fields before the first request."""

    schema = _schema(
        {
            "schema_version": {"const": "1"},
            "source_path": {"type": "string"},
        }
    )

    with pytest.raises(ProviderConfigurationError) as error:
        OpenAiResponsesAdapter({"synthetic_schema": schema})

    assert repr(error.value) == "ProviderConfigurationError('CONFIGURATION_ERROR')"


def test_adapter_reads_the_key_only_when_a_real_call_is_attempted(
    monkeypatch: Any,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    factory_calls: list[str] = []

    def unexpected_factory(key: str) -> _Client:
        factory_calls.append(key)
        raise AssertionError("client factory must not run without configuration")

    adapter = OpenAiResponsesAdapter(
        {"synthetic_schema": _schema()},
        client_factory=unexpected_factory,
    )

    with pytest.raises(ProviderConfigurationError):
        adapter.complete(
            model="gpt-5.6-luna",
            schema_name="synthetic_schema",
            system_instruction="Return the strict synthetic schema.",
            input_payload={"evidence": []},
        )

    assert factory_calls == []


def test_adapter_uses_strict_non_stored_responses_without_key_echo(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    responses = _Responses()
    keys: list[str] = []

    def factory(key: str) -> _Client:
        keys.append(key)
        return _Client(responses)

    adapter = OpenAiResponsesAdapter({"synthetic_schema": _schema()}, client_factory=factory)
    result = adapter.complete(
        model="gpt-5.6-luna",
        schema_name="synthetic_schema",
        system_instruction="Return the strict synthetic schema.",
        input_payload={"evidence": []},
    )

    assert keys == ["synthetic-api-key-marker"]
    assert result.request_id == "synthetic-provider-request-001"
    assert result.payload == {"schema_version": "1"}
    assert len(responses.requests) == 1
    request = responses.requests[0]
    assert request["store"] is False
    assert request["text"] == {
        "format": {
            "type": "json_schema",
            "name": "synthetic_schema",
            "schema": _schema(),
            "strict": True,
        }
    }
    assert "synthetic-api-key-marker" not in repr(request)
    assert "synthetic-api-key-marker" not in repr(result)


def test_adapter_drops_malformed_provider_output_and_exception_detail(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    marker = "synthetic-raw-provider-marker"
    responses = _Responses(_Response(output_text=f"not-json-{marker}"))
    adapter = OpenAiResponsesAdapter(
        {"synthetic_schema": _schema()},
        client_factory=lambda _: _Client(responses),
    )

    with pytest.raises(ProviderValidationError) as error:
        adapter.complete(
            model="gpt-5.6-luna",
            schema_name="synthetic_schema",
            system_instruction="Return the strict synthetic schema.",
            input_payload={"evidence": []},
        )

    assert repr(error.value) == "ProviderValidationError('VALIDATION_ERROR')"
    assert marker not in repr(error.value)
