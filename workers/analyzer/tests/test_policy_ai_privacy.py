"""Privacy-boundary tests for policy candidate provider calls and projections."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from familycare_worker.ai.policy_pipeline import run_policy_pipeline

from workers.analyzer.tests.fixtures.policy_ai_responses import (
    SYNTHETIC_RAW_PROVIDER_MARKER,
    VALID_STRUCTURED,
    VALID_VERIFIED,
    FakeProvider,
    synthetic_policy_evidence,
)

STRUCTURER_MODEL = "gpt-5.6-luna"
VERIFIER_MODEL = "gpt-5.6-terra"
_FORBIDDEN_KEYS = {
    "source_path",
    "absolute_path",
    "policy_number",
    "raw_pdf",
    "password",
    "archive_key",
    "household_space_id",
    "api_key",
    "cookie",
    "raw_provider_response",
}


def _run(provider: FakeProvider) -> Any:
    return run_policy_pipeline(
        evidence=synthetic_policy_evidence(),
        provider=provider,
        structurer_model=STRUCTURER_MODEL,
        verifier_model=VERIFIER_MODEL,
    )


def _walk(value: object) -> tuple[set[str], list[str]]:
    """Collect mapping keys and scalar text without depending on result internals."""

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


def test_provider_receives_only_bounded_evidence_and_minimal_context() -> None:
    """The fake observes Evidence slices, never paths, credentials, or household scope."""

    provider = FakeProvider()
    _run(provider)

    assert len(provider.calls) == 2
    first_request = provider.calls[0]
    keys, scalars = _walk(first_request.input_payload)
    assert "evidence" in keys
    assert not _FORBIDDEN_KEYS.intersection(keys)
    assert all(len(text) <= 240 for text in scalars if "Sample" in text)
    assert "synthetic/local/path" not in scalars

    evidence_batch = first_request.input_payload["evidence"]
    assert isinstance(evidence_batch, Sequence)
    assert len(evidence_batch) == 2
    first_evidence = evidence_batch[0]
    assert isinstance(first_evidence, Mapping)
    assert set(first_evidence) == {"evidence_id", "document_version_id", "page", "text", "bbox"}
    assert first_evidence["page"] == 1
    assert len(str(first_evidence["text"])) <= 240


def test_provider_request_does_not_read_or_transmit_an_api_key(monkeypatch: Any) -> None:
    """A CI fake remains provider-neutral even if a synthetic key is present in the environment."""

    # ``monkeypatch`` is typed as an object so this test does not need an
    # application settings helper that could accidentally read the real key.
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-api-key-marker")
    provider = FakeProvider()

    _run(provider)

    serialized_calls = repr(provider.calls)
    assert "synthetic-api-key-marker" not in serialized_calls
    for call in provider.calls:
        _, scalars = _walk(call.input_payload)
        assert "synthetic-api-key-marker" not in scalars


def test_raw_provider_response_and_evidence_text_are_absent_from_logs_and_result(
    caplog: Any,
) -> None:
    """Only validated fields and a request ID may survive the provider boundary."""

    response = deepcopy(VALID_STRUCTURED)
    response["raw_provider_response"] = SYNTHETIC_RAW_PROVIDER_MARKER
    provider = FakeProvider(structurer=response, verifier=VALID_VERIFIED)
    caplog.set_level(logging.DEBUG)

    result = _run(provider)

    assert SYNTHETIC_RAW_PROVIDER_MARKER not in repr(result)
    assert SYNTHETIC_RAW_PROVIDER_MARKER not in caplog.text
    assert "Sample Policy contract starts" not in caplog.text
    assert "Sample Rider is enrolled" not in caplog.text
    encoded_result = json.dumps(repr(result))
    assert "raw_provider_response" not in encoded_result


def test_successful_result_contains_no_decision_engine_or_raw_input_fields() -> None:
    """AI output remains a candidate and cannot carry tri-state or raw-input authority."""

    provider = FakeProvider()
    result = _run(provider)
    keys, _ = _walk(result.candidates)

    assert not {"decision", "tri_state", "match", "no_match", "unknown"}.intersection(keys)
    assert not {"source_path", "raw_pdf", "password", "archive_key"}.intersection(keys)
