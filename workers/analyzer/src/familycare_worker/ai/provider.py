"""Bounded provider protocol and OpenAI Responses adapter."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol, cast
from uuid import UUID

import openai

_MAX_EVIDENCE_TEXT = 240
MAX_PROVIDER_OUTPUT_TOKENS = 20_000
EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME = "event_clause_recommendations_v1"
DEFAULT_RECOMMENDER_OUTPUT_TOKENS = 1_200
MAX_RECOMMENDER_OUTPUT_TOKENS = 4_000
PROVIDER_REQUEST_TIMEOUT_SECONDS = 120.0
EVENT_STRUCTURER_REQUEST_TIMEOUT_SECONDS = 50.0
DEFAULT_EVENT_STRUCTURER_OUTPUT_TOKENS = 2_000
DEFAULT_STRUCTURER_MODEL = "gpt-5.6-luna"
DEFAULT_VERIFIER_MODEL = "gpt-5.6-terra"
_FORBIDDEN_INPUT_KEYS = frozenset(
    {
        "absolute_path",
        "api_key",
        "archive_key",
        "cookie",
        "household_space_id",
        "password",
        "policy_number",
        "raw_pdf",
        "raw_provider_response",
        "source_path",
    }
)


class ProviderBoundaryError(RuntimeError):
    """Fixed-message provider error that never contains request data."""


class RetryableProviderError(ProviderBoundaryError):
    def __init__(self) -> None:
        super().__init__("RETRYABLE_PROVIDER_ERROR")


class ProviderTimeoutError(RetryableProviderError):
    """The provider call exceeded its bounded request time."""


class ProviderRateLimitError(RetryableProviderError):
    """The provider rejected a call at its temporary rate boundary."""


class ProviderUnavailableError(RetryableProviderError):
    """The provider could not be reached or returned a temporary server error."""


class ProviderConfigurationError(ProviderBoundaryError):
    def __init__(self) -> None:
        super().__init__("CONFIGURATION_ERROR")


class ProviderValidationError(ProviderBoundaryError):
    def __init__(self) -> None:
        super().__init__("VALIDATION_ERROR")


@dataclass(frozen=True)
class EvidenceSlice:
    """One bounded extraction slice allowed to cross the AI boundary."""

    evidence_id: UUID
    document_version_id: UUID
    page: int
    text: str = field(repr=False)
    bbox: tuple[float, float, float, float] | None
    document_kind: str = "policy"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.evidence_id, UUID)
            or self.evidence_id.int == 0
            or not isinstance(self.document_version_id, UUID)
            or self.document_version_id.int == 0
            or isinstance(self.page, bool)
            or not isinstance(self.page, int)
            or not 1 <= self.page <= 500
            or not isinstance(self.text, str)
            or not 1 <= len(self.text) <= _MAX_EVIDENCE_TEXT
            or self.document_kind not in {"policy", "terms"}
        ):
            raise ValueError("invalid Evidence slice")
        if self.bbox is None:
            return
        if len(self.bbox) != 4 or any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in self.bbox
        ):
            raise ValueError("invalid Evidence slice")
        x0, y0, x1, y1 = (float(value) for value in self.bbox)
        if x0 < 0 or y0 < 0 or x1 <= x0 or y1 <= y0:
            raise ValueError("invalid Evidence slice")

    def to_provider_payload(self) -> Mapping[str, object]:
        """Return the only Evidence representation sent to a provider."""

        return {
            "evidence_id": str(self.evidence_id),
            "document_version_id": str(self.document_version_id),
            "page": self.page,
            "text": self.text,
            "bbox": list(self.bbox) if self.bbox is not None else None,
        }


@dataclass(frozen=True)
class ProviderResponse:
    """Validated provider output with only its request identifier retained."""

    payload: Mapping[str, object]
    request_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise ProviderValidationError
        if not isinstance(self.request_id, str) or not 1 <= len(self.request_id) <= 128:
            raise ProviderValidationError
        object.__setattr__(self, "payload", MappingProxyType(dict(self.payload)))


class AiProvider(Protocol):
    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse: ...


class _ResponsesResource(Protocol):
    def create(self, **kwargs: object) -> _OpenAiResponse: ...


class _OpenAiResponse(Protocol):
    id: str
    output_text: str


class _OpenAiClient(Protocol):
    responses: _ResponsesResource


ClientFactory = Callable[[str], _OpenAiClient]


def _default_client_factory(api_key: str) -> _OpenAiClient:
    return cast(_OpenAiClient, openai.OpenAI(api_key=api_key))


def _forbidden_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            normalized = str(key).lower()
            if normalized in _FORBIDDEN_INPUT_KEYS:
                keys.add(normalized)
            keys.update(_forbidden_keys(child))
    elif isinstance(value, list | tuple):
        for child in value:
            keys.update(_forbidden_keys(child))
    return keys


class OpenAiResponsesAdapter:
    """Call strict Responses structured output without retaining raw content."""

    def __init__(
        self,
        schema_registry: Mapping[str, Mapping[str, object]],
        *,
        output_token_limits: Mapping[str, int] | None = None,
        request_timeouts: Mapping[str, float] | None = None,
        client_factory: ClientFactory = _default_client_factory,
    ) -> None:
        if not schema_registry or _forbidden_keys(schema_registry):
            raise ProviderConfigurationError
        requested_limits = dict(output_token_limits or {})
        requested_timeouts = dict(request_timeouts or {})
        if (set(requested_limits) | set(requested_timeouts)) - set(schema_registry):
            raise ProviderConfigurationError
        resolved_limits: dict[str, int] = {}
        for name in schema_registry:
            value = requested_limits.get(
                name,
                (
                    DEFAULT_RECOMMENDER_OUTPUT_TOKENS
                    if name == EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME
                    else MAX_PROVIDER_OUTPUT_TOKENS
                ),
            )
            maximum = (
                MAX_RECOMMENDER_OUTPUT_TOKENS
                if name == EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME
                else MAX_PROVIDER_OUTPUT_TOKENS
            )
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
                raise ProviderConfigurationError
            resolved_limits[name] = value
        resolved_timeouts: dict[str, float] = {}
        for name in schema_registry:
            timeout = requested_timeouts.get(name, PROVIDER_REQUEST_TIMEOUT_SECONDS)
            if (
                isinstance(timeout, bool)
                or not isinstance(timeout, int | float)
                or not math.isfinite(timeout)
                or not 0 < timeout <= PROVIDER_REQUEST_TIMEOUT_SECONDS
            ):
                raise ProviderConfigurationError
            resolved_timeouts[name] = float(timeout)
        self._schemas = MappingProxyType(
            {name: MappingProxyType(dict(schema)) for name, schema in schema_registry.items()}
        )
        self._output_token_limits = MappingProxyType(resolved_limits)
        self._request_timeouts = MappingProxyType(resolved_timeouts)
        self._client_factory = client_factory

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        if (
            not isinstance(model, str)
            or not model
            or not isinstance(system_instruction, str)
            or not system_instruction
            or schema_name not in self._schemas
            or _forbidden_keys(input_payload)
        ):
            raise ProviderValidationError
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ProviderConfigurationError
        try:
            response = self._client_factory(api_key).responses.create(
                model=model,
                instructions=system_instruction,
                input=json.dumps(input_payload, sort_keys=True, separators=(",", ":")),
                max_output_tokens=self._output_token_limits[schema_name],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": schema_name,
                        "schema": dict(self._schemas[schema_name]),
                        "strict": True,
                    }
                },
                store=False,
                timeout=self._request_timeouts[schema_name],
            )
        except openai.APITimeoutError:
            raise ProviderTimeoutError from None
        except openai.RateLimitError:
            raise ProviderRateLimitError from None
        except openai.APIConnectionError, openai.InternalServerError:
            raise ProviderUnavailableError from None
        except (
            openai.AuthenticationError,
            openai.PermissionDeniedError,
        ):
            raise ProviderConfigurationError from None
        except openai.APIError:
            raise ProviderValidationError from None
        try:
            request_id = response.id
            output_text = response.output_text
            payload = json.loads(output_text)
        except AttributeError, json.JSONDecodeError, TypeError:
            raise ProviderValidationError from None
        if not isinstance(payload, dict):
            raise ProviderValidationError
        return ProviderResponse(payload=payload, request_id=request_id)


def provider_payload(response: object) -> tuple[Mapping[str, object], str]:
    """Normalize real and fake responses without retaining their representations."""

    if isinstance(response, ProviderResponse):
        return response.payload, response.request_id
    payload = getattr(response, "payload", response)
    request_id = getattr(response, "request_id", "provider-request-unavailable")
    if not isinstance(payload, Mapping) or not isinstance(request_id, str):
        raise ProviderValidationError
    return cast(Mapping[str, object], payload), request_id


__all__ = [
    "AiProvider",
    "DEFAULT_STRUCTURER_MODEL",
    "DEFAULT_VERIFIER_MODEL",
    "DEFAULT_RECOMMENDER_OUTPUT_TOKENS",
    "DEFAULT_EVENT_STRUCTURER_OUTPUT_TOKENS",
    "EvidenceSlice",
    "EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME",
    "MAX_PROVIDER_OUTPUT_TOKENS",
    "MAX_RECOMMENDER_OUTPUT_TOKENS",
    "OpenAiResponsesAdapter",
    "ProviderBoundaryError",
    "ProviderConfigurationError",
    "ProviderRateLimitError",
    "ProviderResponse",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderValidationError",
    "PROVIDER_REQUEST_TIMEOUT_SECONDS",
    "EVENT_STRUCTURER_REQUEST_TIMEOUT_SECONDS",
    "RetryableProviderError",
    "provider_payload",
]
