"""OpenAI adapter boundary tests with no external request."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import openai
import pytest
from familycare_worker.ai.provider import (
    EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME,
    OpenAiResponsesAdapter,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)


@dataclass
class _Response:
    id: str = "synthetic-provider-request-001"
    output_text: str = '{"schema_version":"1"}'


class _Responses:
    def __init__(self, response: _Response | BaseException | None = None) -> None:
        self.response = response if response is not None else _Response()
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> _Response:
        self.requests.append(dict(kwargs))
        if isinstance(self.response, BaseException):
            raise self.response
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
    assert request["timeout"] == 120.0
    assert request["max_output_tokens"] == 20_000
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


def test_recommender_schema_uses_1200_tokens_and_accepts_bounded_override(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    responses = _Responses()
    adapter = OpenAiResponsesAdapter(
        {EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME: _schema()},
        client_factory=lambda _: _Client(responses),
    )

    adapter.complete(
        model="synthetic-model-v1",
        schema_name=EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME,
        system_instruction="Return the strict synthetic schema.",
        input_payload={"evidence": []},
    )

    assert responses.requests[0]["max_output_tokens"] == 1_200

    overridden_responses = _Responses()
    overridden = OpenAiResponsesAdapter(
        {EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME: _schema()},
        output_token_limits={EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME: 4_000},
        client_factory=lambda _: _Client(overridden_responses),
    )
    overridden.complete(
        model="synthetic-model-v1",
        schema_name=EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME,
        system_instruction="Return the strict synthetic schema.",
        input_payload={"evidence": []},
    )
    assert overridden_responses.requests[0]["max_output_tokens"] == 4_000


def test_recommender_schema_refuses_output_limit_above_hard_maximum() -> None:
    with pytest.raises(ProviderConfigurationError):
        OpenAiResponsesAdapter(
            {EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME: _schema()},
            output_token_limits={EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME: 4_001},
        )


def test_adapter_applies_a_bounded_per_schema_request_timeout(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    responses = _Responses()
    adapter = OpenAiResponsesAdapter(
        {"synthetic_schema": _schema()},
        request_timeouts={"synthetic_schema": 50.0},
        client_factory=lambda _: _Client(responses),
    )

    adapter.complete(
        model="synthetic-model-v1",
        schema_name="synthetic_schema",
        system_instruction="Return the strict synthetic schema.",
        input_payload={"evidence": []},
    )

    assert responses.requests[0]["timeout"] == 50.0


@pytest.mark.parametrize("timeout", [0, 120.1, float("inf"), True])
def test_adapter_refuses_invalid_schema_request_timeout(timeout: object) -> None:
    with pytest.raises(ProviderConfigurationError):
        OpenAiResponsesAdapter(
            {"synthetic_schema": _schema()},
            request_timeouts={"synthetic_schema": timeout},  # type: ignore[dict-item]
        )


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


@pytest.mark.parametrize(
    ("openai_error_name", "expected_error"),
    [
        ("APITimeoutError", ProviderTimeoutError),
        ("RateLimitError", ProviderRateLimitError),
        ("APIConnectionError", ProviderUnavailableError),
        ("InternalServerError", ProviderUnavailableError),
    ],
)
def test_adapter_preserves_retry_reason_without_exception_detail(
    monkeypatch: Any,
    openai_error_name: str,
    expected_error: type[BaseException],
) -> None:
    class SyntheticOpenAiError(Exception):
        pass

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    monkeypatch.setattr(openai, openai_error_name, SyntheticOpenAiError)
    responses = _Responses(SyntheticOpenAiError("synthetic private detail"))
    adapter = OpenAiResponsesAdapter(
        {"synthetic_schema": _schema()},
        client_factory=lambda _: _Client(responses),
    )

    with pytest.raises(expected_error) as raised:
        adapter.complete(
            model="gpt-5.6-luna",
            schema_name="synthetic_schema",
            system_instruction="Return the strict synthetic schema.",
            input_payload={"evidence": []},
        )

    assert "synthetic private detail" not in str(raised.value)
