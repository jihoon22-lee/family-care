"""Bounded provider protocol and OpenAI Responses adapter."""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, Protocol, cast
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
_METADATA_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_SERVICE_TIERS = frozenset({"auto", "default", "flex", "scale", "priority", "fast", "ultrafast"})


@dataclass(frozen=True)
class ProviderUsage:
    """Provider-reported token counters, without pricing or charge inference."""

    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    reasoning_output_tokens: int | None = None

    def __post_init__(self) -> None:
        required = (self.input_tokens, self.output_tokens, self.total_tokens)
        optional = (
            self.cached_input_tokens,
            self.cache_write_input_tokens,
            self.reasoning_output_tokens,
        )
        if (
            any(type(value) is not int or not 0 <= value <= 2**63 - 1 for value in required)
            or any(
                value is not None and (type(value) is not int or not 0 <= value <= 2**63 - 1)
                for value in optional
            )
            or self.input_tokens + self.output_tokens != self.total_tokens
            or any(
                value is not None and value > self.input_tokens
                for value in (self.cached_input_tokens, self.cache_write_input_tokens)
            )
            or (
                self.reasoning_output_tokens is not None
                and self.reasoning_output_tokens > self.output_tokens
            )
        ):
            raise ProviderValidationError


@dataclass(frozen=True)
class ProviderCallMetadata:
    """Validated accounting fields only; absence never means zero consumption."""

    usage: ProviderUsage | None = None
    model: str | None = None
    service_tier: str | None = None
    response_id: str | None = None

    def __post_init__(self) -> None:
        if (
            (self.usage is not None and not isinstance(self.usage, ProviderUsage))
            or any(
                value is not None
                and (not isinstance(value, str) or _METADATA_TOKEN.fullmatch(value) is None)
                for value in (self.model, self.response_id)
            )
            or (
                self.service_tier is not None
                and (
                    not isinstance(self.service_tier, str)
                    or self.service_tier not in _SERVICE_TIERS
                )
            )
        ):
            raise ProviderValidationError


class ProviderBoundaryError(RuntimeError):
    """Fixed-message provider error that never contains request data."""

    def __init__(self, message: str, *, metadata: ProviderCallMetadata | None = None) -> None:
        super().__init__(message)
        self.metadata = metadata


class RetryableProviderError(ProviderBoundaryError):
    def __init__(self, *, metadata: ProviderCallMetadata | None = None) -> None:
        super().__init__("RETRYABLE_PROVIDER_ERROR", metadata=metadata)


class ProviderTimeoutError(RetryableProviderError):
    """The provider call exceeded its bounded request time."""


class ProviderRateLimitError(RetryableProviderError):
    """The provider rejected a call at its temporary rate boundary."""


class ProviderUnavailableError(RetryableProviderError):
    """The provider could not be reached or returned a temporary server error."""


class ProviderConfigurationError(ProviderBoundaryError):
    def __init__(self, *, metadata: ProviderCallMetadata | None = None) -> None:
        super().__init__("CONFIGURATION_ERROR", metadata=metadata)


class ProviderValidationError(ProviderBoundaryError):
    def __init__(self, *, metadata: ProviderCallMetadata | None = None) -> None:
        super().__init__("VALIDATION_ERROR", metadata=metadata)


class ProviderRefusalError(ProviderValidationError):
    """An explicit refusal, whose text is never retained in the error."""

    def __init__(self, *, metadata: ProviderCallMetadata | None = None) -> None:
        ProviderBoundaryError.__init__(self, "PROVIDER_REFUSAL", metadata=metadata)


class ProviderIncompleteError(ProviderValidationError):
    """An incomplete response cannot publish even when its text parses as JSON."""

    def __init__(self, *, metadata: ProviderCallMetadata | None = None) -> None:
        ProviderBoundaryError.__init__(self, "PROVIDER_INCOMPLETE", metadata=metadata)


@dataclass(frozen=True)
class EvidenceSlice:
    """One bounded extraction slice allowed to cross the AI boundary."""

    evidence_id: UUID
    document_version_id: UUID
    page: int
    text: str = field(repr=False)
    bbox: tuple[float, float, float, float] | None
    document_kind: str = "policy"
    maximum_text_characters: ClassVar[int] = _MAX_EVIDENCE_TEXT

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
            or not 1 <= len(self.text) <= self.maximum_text_characters
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
    """Validated provider output and optional, bounded accounting metadata."""

    payload: Mapping[str, object]
    request_id: str
    metadata: ProviderCallMetadata | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.payload, Mapping):
            raise ProviderValidationError(metadata=self.metadata)
        if (
            not isinstance(self.request_id, str)
            or _METADATA_TOKEN.fullmatch(self.request_id) is None
        ):
            raise ProviderValidationError(metadata=self.metadata)
        if self.metadata is not None and not isinstance(self.metadata, ProviderCallMetadata):
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


class _HttpResponse(Protocol):
    def json(self) -> object: ...


class _RawResponse(Protocol):
    http_response: _HttpResponse

    def parse(self) -> _OpenAiResponse: ...


class _RawResponsesResource(Protocol):
    def create(self, **kwargs: object) -> _RawResponse: ...


class _OpenAiClient(Protocol):
    responses: _ResponsesResource


ClientFactory = Callable[[str], _OpenAiClient]


def _default_client_factory(api_key: str) -> _OpenAiClient:
    return cast(_OpenAiClient, openai.OpenAI(api_key=api_key, max_retries=0))


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


def _field(value: object, name: str) -> object:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _reported_usage(raw: object) -> ProviderUsage | None:
    if raw is None:
        return None
    inputs = _field(raw, "input_tokens_details")
    outputs = _field(raw, "output_tokens_details")
    for details, field_name in ((inputs, "cached_tokens"), (outputs, "reasoning_tokens")):
        if (
            details is not None
            and not isinstance(details, Mapping)
            and not hasattr(details, field_name)
        ):
            raise ProviderValidationError
    return ProviderUsage(
        input_tokens=cast(int, _field(raw, "input_tokens")),
        output_tokens=cast(int, _field(raw, "output_tokens")),
        total_tokens=cast(int, _field(raw, "total_tokens")),
        cached_input_tokens=cast(int | None, _field(inputs, "cached_tokens")),
        cache_write_input_tokens=cast(int | None, _field(inputs, "cache_write_tokens")),
        reasoning_output_tokens=cast(int | None, _field(outputs, "reasoning_tokens")),
    )


def _response_metadata(response: object) -> tuple[ProviderCallMetadata, bool]:
    """Keep independently valid usage even if output or another field is invalid."""
    invalid = False
    usage = None
    try:
        usage = _reported_usage(_field(response, "usage"))
    except ProviderValidationError:
        invalid = True
    labels: dict[str, str | None] = {}
    for field_name in ("model", "service_tier", "id"):
        value = _field(response, field_name)
        if value is not None and (
            not isinstance(value, str)
            or (
                value not in _SERVICE_TIERS
                if field_name == "service_tier"
                else _METADATA_TOKEN.fullmatch(value) is None
            )
        ):
            invalid = True
            value = None
        labels[field_name] = value
    return ProviderCallMetadata(
        usage=usage,
        model=labels["model"],
        service_tier=labels["service_tier"],
        response_id=labels["id"],
    ), invalid


def _has_refusal(response: object) -> bool:
    output = _field(response, "output")
    if not isinstance(output, (list, tuple)):
        return False
    for item in output:
        content = _field(item, "content")
        if isinstance(content, (list, tuple)) and any(
            _field(part, "type") == "refusal" for part in content
        ):
            return True
    return False


def _reject_non_json_number(value: str) -> object:
    raise ValueError("INVALID_JSON_NUMBER")


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
        metadata: ProviderCallMetadata | None = None
        request = dict(
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
        try:
            responses = self._client_factory(api_key).responses
            raw_resource = getattr(responses, "with_raw_response", None)
            response_source: object
            if raw_resource is not None:
                envelope = cast(_RawResponsesResource, raw_resource).create(**request)
                response_source = envelope.http_response.json()
                # The installed SDK coerces numeric strings in usage. Validate
                # accounting against wire JSON before parsing its typed model.
                metadata, invalid_metadata = _response_metadata(response_source)
                response = envelope.parse()
            else:
                response = responses.create(**request)
                response_source = response
                metadata, invalid_metadata = _response_metadata(response_source)
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
            raise ProviderValidationError(metadata=metadata) from None
        except AttributeError, ValueError, TypeError:
            raise ProviderValidationError(metadata=metadata) from None
        if _has_refusal(response_source):
            raise ProviderRefusalError(metadata=metadata)
        # Legacy injected response doubles expose only id/output_text. Real SDK
        # responses expose status, including None when a malformed body omits it.
        status = (
            response_source.get("status")
            if isinstance(response_source, Mapping)
            else getattr(response_source, "status", "completed")
        )
        if status == "incomplete":
            raise ProviderIncompleteError(metadata=metadata)
        if (
            invalid_metadata
            or status != "completed"
            or _field(response_source, "error") is not None
        ):
            raise ProviderValidationError(metadata=metadata)
        try:
            request_id = response.id
            output_text = response.output_text
            payload = json.loads(output_text, parse_constant=_reject_non_json_number)
        except AttributeError, ValueError, TypeError:
            raise ProviderValidationError(metadata=metadata) from None
        if not isinstance(payload, dict):
            raise ProviderValidationError(metadata=metadata)
        return ProviderResponse(payload=payload, request_id=request_id, metadata=metadata)


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
    "ProviderCallMetadata",
    "ProviderIncompleteError",
    "ProviderRateLimitError",
    "ProviderResponse",
    "ProviderRefusalError",
    "ProviderTimeoutError",
    "ProviderUnavailableError",
    "ProviderUsage",
    "ProviderValidationError",
    "PROVIDER_REQUEST_TIMEOUT_SECONDS",
    "EVENT_STRUCTURER_REQUEST_TIMEOUT_SECONDS",
    "RetryableProviderError",
    "provider_payload",
]
