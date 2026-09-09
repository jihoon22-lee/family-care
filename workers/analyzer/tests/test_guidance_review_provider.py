"""Real installed SDK transport tests for bounded review response accounting."""

from copy import deepcopy
from typing import Any

import httpx2
import openai
import pytest
from familycare_worker.ai import provider

_SCHEMA = {
    "type": "object",
    "properties": {"schema_version": {"type": "string", "const": "1"}},
    "required": ["schema_version"],
    "additionalProperties": False,
}
_RAW_MARKER = "synthetic-raw-provider-detail"


def _response() -> dict[str, Any]:
    return {
        "id": "resp_synthetic_usage",
        "object": "response",
        "created_at": 1,
        "status": "completed",
        "model": "gpt-5.6-luna",
        "service_tier": "default",
        "error": None,
        "incomplete_details": None,
        "output": [
            {
                "id": "msg_synthetic",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {"type": "output_text", "text": '{"schema_version":"1"}', "annotations": []}
                ],
            }
        ],
        "usage": {
            "input_tokens": 100,
            "input_tokens_details": {"cached_tokens": 20, "cache_write_tokens": 10},
            "output_tokens": 40,
            "output_tokens_details": {"reasoning_tokens": 30},
            "total_tokens": 140,
        },
    }


def _adapter(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any] | None = None,
    *,
    status_code: int = 200,
    transport_error: type[httpx2.TransportError] | None = None,
) -> tuple[provider.OpenAiResponsesAdapter, list[httpx2.Request], httpx2.Client]:
    attempts: list[httpx2.Request] = []

    def handle(request: httpx2.Request) -> httpx2.Response:
        attempts.append(request)
        if transport_error is not None:
            raise transport_error(_RAW_MARKER, request=request)
        return httpx2.Response(status_code, json=payload or _response())

    transport = httpx2.Client(transport=httpx2.MockTransport(handle))
    sdk_class = openai.OpenAI

    def client(**kwargs: Any) -> openai.OpenAI:
        # Exercise the production factory's retry choice through the actual SDK.
        return sdk_class(
            **kwargs,
            base_url="https://synthetic.invalid/v1",
            organization="synthetic-org",
            project="synthetic-project",
            http_client=transport,
        )

    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    monkeypatch.setattr(openai, "OpenAI", client)
    return provider.OpenAiResponsesAdapter({"synthetic_review": _SCHEMA}), attempts, transport


def _complete(adapter: provider.OpenAiResponsesAdapter):
    return adapter.complete(
        model="gpt-5.6-luna",
        schema_name="synthetic_review",
        system_instruction="Return only the supplied synthetic schema.",
        input_payload={"schema_version": "1"},
    )


def test_sdk_success_retains_safe_usage_and_single_http_attempt(monkeypatch: pytest.MonkeyPatch):
    adapter, attempts, transport = _adapter(monkeypatch)
    with transport:
        result = _complete(adapter)
    assert len(attempts) == 1
    assert result.payload == {"schema_version": "1"}
    assert result.metadata.model == "gpt-5.6-luna"
    assert result.metadata.service_tier == "default"
    assert result.metadata.response_id == result.request_id == "resp_synthetic_usage"
    usage = result.metadata.usage
    assert (usage.input_tokens, usage.output_tokens, usage.total_tokens) == (100, 40, 140)
    assert usage.cached_input_tokens == 20
    assert usage.cache_write_input_tokens == 10
    assert usage.reasoning_output_tokens == 30
    import json

    sent = json.loads(attempts[0].content)
    assert sent["store"] is False
    assert sent["max_output_tokens"] == 20_000
    assert sent["text"]["format"]["strict"] is True
    assert attempts[0].extensions["timeout"]["read"] == 120.0


@pytest.mark.parametrize(
    ("status_code", "transport_error", "expected"),
    [
        (429, None, "ProviderRateLimitError"),
        (500, None, "ProviderUnavailableError"),
        (200, httpx2.ConnectError, "ProviderUnavailableError"),
        (200, httpx2.ReadTimeout, "ProviderTimeoutError"),
    ],
)
def test_sdk_transport_failures_make_one_attempt_without_raw_details(
    monkeypatch: pytest.MonkeyPatch, status_code: int, transport_error, expected: str
):
    adapter, attempts, transport = _adapter(
        monkeypatch,
        {"error": {"message": _RAW_MARKER, "type": "server_error"}},
        status_code=status_code,
        transport_error=transport_error,
    )
    with transport, pytest.raises(getattr(provider, expected)) as raised:
        _complete(adapter)
    assert len(attempts) == 1
    assert raised.value.metadata is None
    assert _RAW_MARKER not in repr(raised.value)
    assert _RAW_MARKER not in str(vars(raised.value))


@pytest.mark.parametrize("outcome", ["refusal", "incomplete", "failed", "invalid_json"])
def test_post_response_failure_preserves_known_usage_without_raw_output(
    monkeypatch: pytest.MonkeyPatch, outcome: str
):
    payload = _response()
    expected = "ProviderValidationError"
    if outcome == "refusal":
        payload["output"][0]["content"].append({"type": "refusal", "refusal": _RAW_MARKER})
        expected = "ProviderRefusalError"
    elif outcome == "incomplete":
        payload["status"] = "incomplete"
        payload["incomplete_details"] = {"reason": "max_output_tokens"}
        expected = "ProviderIncompleteError"
    elif outcome == "failed":
        payload["status"] = "failed"
        payload["error"] = {"code": "server_error", "message": _RAW_MARKER}
    else:
        payload["output"][0]["content"][0]["text"] = _RAW_MARKER
    adapter, attempts, transport = _adapter(monkeypatch, payload)
    with transport, pytest.raises(getattr(provider, expected)) as raised:
        _complete(adapter)
    assert len(attempts) == 1
    assert raised.value.metadata.usage.total_tokens == 140
    assert raised.value.metadata.model == "gpt-5.6-luna"
    assert _RAW_MARKER not in repr(raised.value)
    assert _RAW_MARKER not in str(vars(raised.value))


@pytest.mark.parametrize("bad", [-1, True, 1.5, "100", None])
def test_sdk_malformed_usage_is_unknown_and_never_validated_as_zero(
    monkeypatch: pytest.MonkeyPatch, bad: object
):
    payload = _response()
    payload["usage"]["input_tokens"] = bad
    adapter, attempts, transport = _adapter(monkeypatch, payload)
    with transport, pytest.raises(provider.ProviderValidationError) as raised:
        _complete(adapter)
    assert len(attempts) == 1
    assert raised.value.metadata.usage is None
    assert raised.value.metadata.response_id == "resp_synthetic_usage"


@pytest.mark.parametrize("field", ["total", "cached", "cache_write", "reasoning"])
def test_sdk_usage_rejects_inconsistent_totals_and_breakdowns(
    monkeypatch: pytest.MonkeyPatch, field: str
):
    payload = _response()
    if field == "total":
        payload["usage"]["total_tokens"] = 139
    elif field == "reasoning":
        payload["usage"]["output_tokens_details"]["reasoning_tokens"] = 41
    else:
        payload["usage"]["input_tokens_details"][
            "cached_tokens" if field == "cached" else "cache_write_tokens"
        ] = 101
    adapter, attempts, transport = _adapter(monkeypatch, payload)
    with transport, pytest.raises(provider.ProviderValidationError) as raised:
        _complete(adapter)
    assert len(attempts) == 1
    assert raised.value.metadata.usage is None


@pytest.mark.parametrize("field", ["model", "service_tier", "id"])
def test_sdk_rejects_unbounded_metadata_but_preserves_valid_usage(
    monkeypatch: pytest.MonkeyPatch, field: str
):
    payload = _response()
    payload[field] = _RAW_MARKER + " /private/path " * 20
    adapter, attempts, transport = _adapter(monkeypatch, payload)
    with transport, pytest.raises(provider.ProviderValidationError) as raised:
        _complete(adapter)
    assert len(attempts) == 1
    assert raised.value.metadata.usage.total_tokens == 140
    assert _RAW_MARKER not in str(vars(raised.value))


def test_usage_absence_remains_unknown_and_legacy_response_construction_works(
    monkeypatch: pytest.MonkeyPatch,
):
    payload = _response()
    payload["usage"] = None
    adapter, attempts, transport = _adapter(monkeypatch, deepcopy(payload))
    with transport:
        result = _complete(adapter)
    assert len(attempts) == 1
    assert result.metadata.usage is None
    assert provider.ProviderResponse({"schema_version": "1"}, "synthetic-request").metadata is None
