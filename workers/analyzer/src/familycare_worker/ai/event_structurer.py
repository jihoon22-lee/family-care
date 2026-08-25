"""Provider-neutral structuring of a bounded medical-event situation.

This module is deliberately narrower than the policy candidate pipeline.  It
turns a short user-written situation into editable fact *candidates* only.  A
candidate is not an eligibility decision, a coverage result, or a calculation.
The API/event queue can persist the returned value without retaining the
original situation or a raw provider response.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from familycare_worker.ai.provider import (
    AiProvider,
    ProviderBoundaryError,
    ProviderConfigurationError,
    ProviderValidationError,
    RetryableProviderError,
    provider_payload,
)

EVENT_STRUCTURER_SCHEMA_NAME = "medical_event_structurer_v1"
# Keep the provider-stage naming parallel with ``ai.structurer``.
STRUCTURER_SCHEMA_NAME = EVENT_STRUCTURER_SCHEMA_NAME
_SCHEMA_VERSION = "1"
_MAX_SITUATION_LENGTH = 2_000
_MAX_VALUE_LENGTH = 160
_MAX_FACTS = 32
_MAX_QUESTIONS = 16
_MAX_ISSUES = 16
_MAX_PROVIDER_REQUEST_ID_LENGTH = 128

type EventMode = Literal["pre_visit", "post_treatment"]
type EventFactField = Literal[
    "event_date",
    "visit_date",
    "condition_class",
    "diagnosis_label",
    "treatment_kind",
    "admission",
    "outpatient",
    "pharmacy",
]
type FactSource = Literal["user", "ai", "system"]
type FactState = Literal["confirmed", "ambiguous", "missing", "conflict"]
type FactConfidence = Literal["high", "medium", "low"]
type QuestionCode = EventFactField
type FactIssueCode = Literal[
    "INVENTED_FIELD",
    "INVALID_VALUE",
    "INVALID_STATE",
    "DUPLICATE_FIELD",
    "INVENTED_QUESTION",
    "INVENTED_EVIDENCE",
    "UNSUPPORTED_SOURCE",
    "INVALID_CONFIDENCE",
]
type FactValue = str | bool | None

_EVENT_FACT_FIELDS = frozenset(
    {
        "event_date",
        "visit_date",
        "condition_class",
        "diagnosis_label",
        "treatment_kind",
        "admission",
        "outpatient",
        "pharmacy",
    }
)
_FACT_STATES = frozenset({"confirmed", "ambiguous", "missing", "conflict"})
_BOOLEAN_FACT_FIELDS = frozenset({"admission", "outpatient", "pharmacy"})
_DATE_FACT_FIELDS = frozenset({"event_date", "visit_date"})
_QUESTION_CODES = frozenset(_EVENT_FACT_FIELDS)
_FORBIDDEN_KEYS = frozenset(
    {
        "absolute_path",
        "api_key",
        "archive_key",
        "amount",
        "cookie",
        "decision",
        "eligible",
        "household_space_id",
        "match",
        "no_match",
        "password",
        "payment",
        "policy_number",
        "raw_pdf",
        "raw_provider_response",
        "source_path",
        "tri_state",
        "unknown",
    }
)


class EventStructuringPayloadInvalid(ProviderValidationError):
    """The provider response did not satisfy the event structuring boundary."""

    def __init__(self) -> None:
        ProviderBoundaryError.__init__(self, "EVENT_STRUCTURING_INVALID")


class EventStructuringProviderError(ProviderBoundaryError):
    """An unexpected provider failure with no provider detail exposed."""

    def __init__(self) -> None:
        super().__init__("EVENT_STRUCTURING_PROVIDER_ERROR")


@dataclass(frozen=True, slots=True)
class EventStructuringRequest:
    """The only user context allowed into the event structuring provider."""

    situation: str
    mode: EventMode
    event_date: date | None = None
    visit_date: date | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.situation, str)
            or not self.situation.strip()
            or len(self.situation) > _MAX_SITUATION_LENGTH
            or any(ord(character) < 32 and character not in "\n\t" for character in self.situation)
        ):
            raise ValueError("invalid event situation")
        if self.mode not in {"pre_visit", "post_treatment"}:
            raise ValueError("invalid event mode")
        if self.event_date is not None and type(self.event_date) is not date:
            raise ValueError("invalid event date")
        if self.visit_date is not None and type(self.visit_date) is not date:
            raise ValueError("invalid visit date")

    def to_provider_payload(self) -> Mapping[str, object]:
        """Return a bounded, identifier-free provider input."""

        return {
            "schema_version": _SCHEMA_VERSION,
            "situation": self.situation.strip(),
            "mode": self.mode,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "visit_date": self.visit_date.isoformat() if self.visit_date else None,
        }


@dataclass(frozen=True, slots=True)
class StructuredFactCandidate:
    """One editable, non-authoritative fact candidate."""

    field_id: EventFactField
    value: FactValue
    state: FactState
    fact_id: UUID | None = None
    source: FactSource = "ai"
    confidence: FactConfidence = "low"
    evidence_ids: tuple[UUID, ...] = ()

    def __post_init__(self) -> None:
        if self.field_id not in _EVENT_FACT_FIELDS:
            raise ValueError("invalid event fact field")
        if self.state not in _FACT_STATES:
            raise ValueError("invalid event fact state")
        if self.source not in {"user", "ai", "system"}:
            raise ValueError("invalid event fact source")
        if self.confidence not in {"high", "medium", "low"}:
            raise ValueError("invalid event fact confidence")
        if self.fact_id is None:
            generated_id = UUID(
                int=uuid5(
                    NAMESPACE_URL,
                    json.dumps(
                        {"field_id": self.field_id, "value": self.value, "state": self.state},
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                ).int,
                version=4,
            )
            object.__setattr__(self, "fact_id", generated_id)
        elif (
            not isinstance(self.fact_id, UUID) or self.fact_id.version != 4 or self.fact_id.int == 0
        ):
            raise ValueError("invalid event fact id")
        if not isinstance(self.evidence_ids, tuple) or len(self.evidence_ids) > 8:
            raise ValueError("too many event fact evidence ids")
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or any(
            not isinstance(evidence_id, UUID) or evidence_id.version != 4 or evidence_id.int == 0
            for evidence_id in self.evidence_ids
        ):
            raise ValueError("invalid event fact evidence ids")
        if self.field_id in _BOOLEAN_FACT_FIELDS:
            if self.value is not None and not isinstance(self.value, bool):
                raise ValueError("boolean event fact has an invalid value")
        elif self.value is not None:
            if not isinstance(self.value, str) or not self.value.strip():
                raise ValueError("text event fact has an invalid value")
            if len(self.value) > _MAX_VALUE_LENGTH:
                raise ValueError("event fact value is too long")
            if self.field_id in _DATE_FACT_FIELDS:
                try:
                    date.fromisoformat(self.value)
                except ValueError:
                    raise ValueError("date event fact has an invalid value") from None


@dataclass(frozen=True, slots=True)
class OptionalQuestion:
    """A bounded question suggestion that never gates deterministic analysis."""

    question_code: QuestionCode
    field_id: EventFactField

    def __post_init__(self) -> None:
        if self.question_code not in _QUESTION_CODES or self.field_id not in _EVENT_FACT_FIELDS:
            raise ValueError("invalid event question code")
        if self.question_code != self.field_id:
            raise ValueError("invalid event question code")


@dataclass(frozen=True, slots=True)
class FactValidationIssue:
    """A stable, non-sensitive reason why one candidate was discarded."""

    field_id: str
    code: FactIssueCode

    def __post_init__(self) -> None:
        if not isinstance(self.field_id, str) or not 1 <= len(self.field_id) <= 64:
            raise ValueError("invalid event fact issue field")
        if self.code not in {
            "INVENTED_FIELD",
            "INVALID_VALUE",
            "INVALID_STATE",
            "DUPLICATE_FIELD",
            "INVENTED_QUESTION",
            "INVENTED_EVIDENCE",
            "UNSUPPORTED_SOURCE",
            "INVALID_CONFIDENCE",
        }:
            raise ValueError("invalid event fact issue code")


@dataclass(frozen=True, slots=True)
class EventStructuringResult:
    """Sanitized structuring output safe to hand to the event queue."""

    facts: tuple[StructuredFactCandidate, ...]
    questions: tuple[OptionalQuestion, ...]
    provider_request_id: str | None
    issues: tuple[FactValidationIssue, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.facts, tuple) or len(self.facts) > _MAX_FACTS:
            raise ValueError("too many event facts")
        if not isinstance(self.questions, tuple) or len(self.questions) > _MAX_QUESTIONS:
            raise ValueError("too many event questions")
        if not isinstance(self.issues, tuple) or len(self.issues) > _MAX_ISSUES:
            raise ValueError("too many event fact issues")
        if self.provider_request_id is not None and (
            not isinstance(self.provider_request_id, str)
            or not 1 <= len(self.provider_request_id) <= _MAX_PROVIDER_REQUEST_ID_LENGTH
        ):
            raise ValueError("invalid provider request id")


class EventStructurer(Protocol):
    """Provider-neutral event structurer protocol for queue workers and fakes."""

    def structure(self, request: EventStructuringRequest) -> EventStructuringResult: ...


# The shorter alias is convenient for queue code while retaining the explicit
# candidate name at the provider boundary.
StructuredFact = StructuredFactCandidate


def event_structurer_schema() -> Mapping[str, object]:
    """Return the strict Responses JSON Schema for this boundary."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"const": _SCHEMA_VERSION},
            "facts": {
                "type": "array",
                "maxItems": _MAX_FACTS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "fact_id": {
                            "type": "string",
                            "format": "uuid",
                            "pattern": (
                                "^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-"
                                "[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
                            ),
                        },
                        "field_id": {"enum": sorted(_EVENT_FACT_FIELDS)},
                        "value": {
                            "anyOf": [
                                {
                                    "type": "string",
                                    "minLength": 1,
                                    "maxLength": _MAX_VALUE_LENGTH,
                                },
                                {"type": "boolean"},
                                {"type": "null"},
                            ]
                        },
                        "source": {"enum": ["ai"]},
                        "state": {"enum": sorted(_FACT_STATES)},
                        "confidence": {"enum": ["high", "medium", "low"]},
                        "evidence_ids": {
                            "type": "array",
                            "maxItems": 8,
                            "uniqueItems": True,
                            "items": {"type": "string", "format": "uuid"},
                        },
                    },
                    "required": [
                        "fact_id",
                        "field_id",
                        "value",
                        "source",
                        "state",
                        "confidence",
                        "evidence_ids",
                    ],
                },
            },
            "questions": {
                "type": "array",
                "maxItems": _MAX_QUESTIONS,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "question_code": {"enum": sorted(_QUESTION_CODES)},
                        "field_id": {"enum": sorted(_EVENT_FACT_FIELDS)},
                    },
                    "required": ["question_code", "field_id"],
                },
            },
        },
        "required": ["schema_version", "facts", "questions"],
    }


def _contains_forbidden_key(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key).lower() in _FORBIDDEN_KEYS or _contains_forbidden_key(child):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_key(child) for child in value)
    return False


def _issue(field_id: object, code: FactIssueCode) -> FactValidationIssue:
    # Never reflect an unregistered provider string (which could be a path,
    # identifier, or authority-bearing field) into the persisted issue.
    safe_field_id = (
        field_id if isinstance(field_id, str) and field_id in _EVENT_FACT_FIELDS else "invalid"
    )
    return FactValidationIssue(field_id=safe_field_id, code=code)


def _parse_fact(raw: object) -> StructuredFactCandidate | FactValidationIssue:
    if not isinstance(raw, Mapping):
        return _issue("invalid", "INVALID_VALUE")
    field_id = raw.get("field_id")
    if not isinstance(field_id, str) or field_id not in _EVENT_FACT_FIELDS:
        return _issue(field_id, "INVENTED_FIELD")
    if set(raw) != {
        "fact_id",
        "field_id",
        "value",
        "source",
        "state",
        "confidence",
        "evidence_ids",
    }:
        return _issue(field_id, "INVALID_VALUE")
    raw_fact_id = raw.get("fact_id")
    if not isinstance(raw_fact_id, str):
        return _issue(field_id, "INVALID_VALUE")
    try:
        fact_id = UUID(raw_fact_id)
    except ValueError:
        return _issue(field_id, "INVALID_VALUE")
    if fact_id.version != 4 or fact_id.int == 0:
        return _issue(field_id, "INVALID_VALUE")
    source = raw.get("source")
    if source != "ai":
        return _issue(field_id, "UNSUPPORTED_SOURCE")
    confidence = raw.get("confidence")
    if confidence not in {"high", "medium", "low"}:
        return _issue(field_id, "INVALID_CONFIDENCE")
    evidence_ids_raw = raw.get("evidence_ids")
    if (
        not isinstance(evidence_ids_raw, Sequence)
        or isinstance(evidence_ids_raw, (str, bytes, bytearray))
        or len(evidence_ids_raw) > 8
    ):
        return _issue(field_id, "INVALID_VALUE")
    evidence_ids: list[UUID] = []
    for raw_evidence_id in evidence_ids_raw:
        if not isinstance(raw_evidence_id, str):
            return _issue(field_id, "INVALID_VALUE")
        try:
            evidence_id = UUID(raw_evidence_id)
        except ValueError:
            return _issue(field_id, "INVALID_VALUE")
        if evidence_id.version != 4 or evidence_id.int == 0:
            return _issue(field_id, "INVALID_VALUE")
        evidence_ids.append(evidence_id)
    if len(set(evidence_ids)) != len(evidence_ids):
        return _issue(field_id, "INVALID_VALUE")
    if evidence_ids:
        return _issue(field_id, "INVENTED_EVIDENCE")
    state = raw.get("state")
    if not isinstance(state, str) or state not in _FACT_STATES:
        return _issue(field_id, "INVALID_STATE")
    value = raw.get("value")
    if value is not None and not isinstance(value, str | bool):
        return _issue(field_id, "INVALID_VALUE")
    try:
        return StructuredFactCandidate(
            field_id=cast(EventFactField, field_id),
            value=value,
            state=cast(FactState, state),
            fact_id=fact_id,
            source="ai",
            confidence=cast(FactConfidence, confidence),
            evidence_ids=(),
        )
    except ValueError:
        return _issue(field_id, "INVALID_VALUE")


def validate_event_structuring_payload(
    payload: Mapping[str, object],
    *,
    provider_request_id: str | None = None,
) -> EventStructuringResult:
    """Validate and sanitize one provider payload deterministically."""

    if (
        not isinstance(payload, Mapping)
        or _contains_forbidden_key(payload)
        or set(payload) != {"schema_version", "facts", "questions"}
        or payload.get("schema_version") != _SCHEMA_VERSION
    ):
        raise EventStructuringPayloadInvalid
    facts_raw = payload.get("facts")
    questions_raw = payload.get("questions")
    if (
        not isinstance(facts_raw, Sequence)
        or isinstance(facts_raw, (str, bytes, bytearray))
        or len(facts_raw) > _MAX_FACTS
        or not isinstance(questions_raw, Sequence)
        or isinstance(questions_raw, (str, bytes, bytearray))
        or len(questions_raw) > _MAX_QUESTIONS
    ):
        raise EventStructuringPayloadInvalid
    facts: list[StructuredFactCandidate] = []
    issues: list[FactValidationIssue] = []
    seen_fields: set[str] = set()
    for raw_fact in facts_raw:
        parsed = _parse_fact(raw_fact)
        if isinstance(parsed, FactValidationIssue):
            issues.append(parsed)
            continue
        if parsed.field_id in seen_fields:
            issues.append(_issue(parsed.field_id, "DUPLICATE_FIELD"))
            continue
        seen_fields.add(parsed.field_id)
        facts.append(parsed)
    questions: list[OptionalQuestion] = []
    seen_questions: set[str] = set()
    for raw_question in questions_raw:
        if not isinstance(raw_question, Mapping):
            issues.append(_issue("invalid", "INVENTED_QUESTION"))
            continue
        if set(raw_question) != {"question_code", "field_id"}:
            issues.append(_issue("invalid", "INVENTED_QUESTION"))
            continue
        question_code = raw_question.get("question_code")
        field_id = raw_question.get("field_id")
        if (
            not isinstance(question_code, str)
            or question_code not in _QUESTION_CODES
            or not isinstance(field_id, str)
            or field_id not in _EVENT_FACT_FIELDS
            or question_code != field_id
        ):
            issue_field = field_id if isinstance(field_id, str) else "invalid"
            issues.append(_issue(issue_field, "INVENTED_QUESTION"))
            continue
        if question_code in seen_questions:
            continue
        seen_questions.add(question_code)
        questions.append(
            OptionalQuestion(
                question_code=cast(QuestionCode, question_code),
                field_id=cast(EventFactField, field_id),
            )
        )
    if len(issues) > _MAX_ISSUES:
        issues = issues[:_MAX_ISSUES]
    return EventStructuringResult(
        facts=tuple(facts),
        questions=tuple(questions),
        provider_request_id=provider_request_id,
        issues=tuple(issues),
    )


def structure_event(
    *,
    request: EventStructuringRequest,
    provider: AiProvider,
    model: str,
) -> EventStructuringResult:
    """Call a provider and return only validated event fact candidates."""

    if not isinstance(model, str) or not 1 <= len(model) <= 128:
        raise ValueError("invalid event structurer model")
    try:
        response = provider.complete(
            model=model,
            schema_name=EVENT_STRUCTURER_SCHEMA_NAME,
            system_instruction=(
                "Return only bounded medical-event fact candidates and optional question codes. "
                "Do not invent fields or emit authority-bearing outcomes."
            ),
            input_payload=request.to_provider_payload(),
        )
    except RetryableProviderError:
        raise
    except TimeoutError, ConnectionError:
        raise RetryableProviderError from None
    except ProviderConfigurationError:
        raise
    except ProviderValidationError:
        raise EventStructuringPayloadInvalid from None
    except ProviderBoundaryError:
        raise EventStructuringProviderError from None
    except Exception:
        raise EventStructuringProviderError from None
    try:
        payload, provider_request_id = provider_payload(response)
    except ProviderValidationError:
        raise EventStructuringPayloadInvalid from None
    if not isinstance(provider_request_id, str) or not (
        1 <= len(provider_request_id) <= _MAX_PROVIDER_REQUEST_ID_LENGTH
    ):
        raise EventStructuringPayloadInvalid
    try:
        # Round-trip through JSON to ensure fake providers and real adapters
        # receive the same scalar-only validation path.
        normalized = cast(Mapping[str, object], json.loads(json.dumps(dict(payload))))
    except TypeError, ValueError:
        raise EventStructuringPayloadInvalid from None
    return validate_event_structuring_payload(
        normalized,
        provider_request_id=provider_request_id,
    )


__all__ = [
    "EVENT_STRUCTURER_SCHEMA_NAME",
    "STRUCTURER_SCHEMA_NAME",
    "EventFactField",
    "EventMode",
    "EventStructurer",
    "EventStructuringPayloadInvalid",
    "EventStructuringProviderError",
    "EventStructuringRequest",
    "EventStructuringResult",
    "FactIssueCode",
    "FactConfidence",
    "FactSource",
    "FactValidationIssue",
    "FactValue",
    "FactState",
    "OptionalQuestion",
    "QuestionCode",
    "StructuredFactCandidate",
    "StructuredFact",
    "event_structurer_schema",
    "structure_event",
    "validate_event_structuring_payload",
]
