"""Strict one-call refinement of already selected local clause candidates."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from familycare_worker.ai.minimizer import EvidenceMinimizationError, minimize_text
from familycare_worker.ai.provider import (
    EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME,
    AiProvider,
    provider_payload,
)

RECOMMENDER_SCHEMA_NAME = EVENT_CLAUSE_RECOMMENDER_SCHEMA_NAME
DEFAULT_ASSISTANCE_MODEL = "gpt-5.6-luna"

_TOKEN_PATTERN = re.compile(r"^candidate-(?:0[1-9]|1[0-2])$")
_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_FIELD_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.]{0,63}$")
_UUID_PATTERN = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b"
)
_PRIVATE_PATH_PATTERN = re.compile(
    r"(?i)(?:[A-Z]:[\\/]|file://|(?:^|\s)/(?:home|mnt|private|tmp|users?)/)"
)
_RESIDUAL_IDENTIFIER_PATTERN = re.compile(
    r"(?i)\b(?:customer|member|person|policy|contract|certificate)"
    r"[_-](?:id|number|no)[_:-][a-z0-9][a-z0-9._/-]{4,}\b"
)


class RecommendationValidationError(RuntimeError):
    """Stable failure for an invalid recommender request or response."""

    def __init__(self) -> None:
        super().__init__("INVALID_RECOMMENDATION_RESPONSE")


@dataclass(frozen=True)
class RecommendationFact:
    """One bounded event fact allowed to cross the provider boundary."""

    field_id: str
    value: str = field(repr=False)
    confirmation: Literal["USER_CONFIRMED", "AI_SUGGESTED"]

    def __post_init__(self) -> None:
        if (
            not isinstance(self.field_id, str)
            or not _FIELD_PATTERN.fullmatch(self.field_id)
            or not _safe_text(self.value, 120)
            or self.confirmation not in {"USER_CONFIRMED", "AI_SUGGESTED"}
        ):
            raise ValueError("invalid recommendation fact")


@dataclass(frozen=True)
class RecommendationCandidate:
    """One request-local token and its bounded local display projection."""

    token: str
    contract_label: str = field(repr=False)
    coverage_label: str = field(repr=False)
    clause_label: str = field(repr=False)
    excerpt: str = field(repr=False)
    page_start: int
    page_end: int
    citation_kind: Literal["FACT_CITATION"]

    def __post_init__(self) -> None:
        if not isinstance(self.token, str) or not _TOKEN_PATTERN.fullmatch(self.token):
            raise ValueError("invalid recommendation token")
        if not all(
            _safe_text(value, maximum)
            for value, maximum in (
                (self.contract_label, 240),
                (self.coverage_label, 800),
                (self.clause_label, 800),
                (self.excerpt, 240),
            )
        ):
            raise ValueError("invalid recommendation candidate text")
        if (
            isinstance(self.page_start, bool)
            or isinstance(self.page_end, bool)
            or self.page_start < 1
            or self.page_end < self.page_start
            or self.page_end - self.page_start > 20
            or self.citation_kind != "FACT_CITATION"
        ):
            raise ValueError("invalid recommendation citation")


@dataclass(frozen=True)
class RecommendationRequest:
    """Sensitive request values whose representation is deliberately empty."""

    situation: str = field(repr=False)
    facts: tuple[RecommendationFact, ...] = field(repr=False)
    candidates: tuple[RecommendationCandidate, ...] = field(repr=False)

    def __post_init__(self) -> None:
        if not _safe_text(self.situation, 800):
            raise ValueError("invalid recommendation situation")
        if len(self.facts) > 32 or not 1 <= len(self.candidates) <= 12:
            raise ValueError("invalid recommendation request size")
        if any(not isinstance(item, RecommendationFact) for item in self.facts):
            raise ValueError("invalid recommendation facts")
        if any(not isinstance(item, RecommendationCandidate) for item in self.candidates):
            raise ValueError("invalid recommendation candidates")
        tokens = tuple(item.token for item in self.candidates)
        if len(set(tokens)) != len(tokens) or tokens != tuple(
            f"candidate-{index:02d}" for index in range(1, len(tokens) + 1)
        ):
            raise ValueError("recommendation tokens must be contiguous")


@dataclass(frozen=True)
class RecommendationSelection:
    """Sanitized model choice that can only reference a supplied token."""

    token: str
    explanation_code: str
    question_code: str | None

    def __post_init__(self) -> None:
        if not _TOKEN_PATTERN.fullmatch(self.token):
            raise RecommendationValidationError
        if not _CODE_PATTERN.fullmatch(self.explanation_code):
            raise RecommendationValidationError
        if self.question_code is not None and not _CODE_PATTERN.fullmatch(self.question_code):
            raise RecommendationValidationError


@dataclass(frozen=True)
class RecommendationResult:
    """Validated ordering with provider metadata hidden from representations."""

    selections: tuple[RecommendationSelection, ...]
    request_id: str = field(repr=False)

    def __post_init__(self) -> None:
        if not 1 <= len(self.selections) <= 12:
            raise RecommendationValidationError
        if len({item.token for item in self.selections}) != len(self.selections):
            raise RecommendationValidationError
        if not isinstance(self.request_id, str) or not 1 <= len(self.request_id) <= 128:
            raise RecommendationValidationError


def recommender_schema() -> dict[str, object]:
    """Return the closed provider response schema."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {"type": "string", "const": "1"},
            "recommendations": {
                "type": "array",
                "minItems": 1,
                "maxItems": 12,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "token": {"type": "string", "pattern": _TOKEN_PATTERN.pattern},
                        "explanation_code": {
                            "type": "string",
                            "pattern": _CODE_PATTERN.pattern,
                        },
                        "question_code": {
                            "anyOf": [
                                {"type": "string", "pattern": _CODE_PATTERN.pattern},
                                {"type": "null"},
                            ]
                        },
                    },
                    "required": ["token", "explanation_code", "question_code"],
                },
            },
        },
        "required": ["schema_version", "recommendations"],
    }


def recommend_clauses(
    *,
    request: RecommendationRequest,
    provider: AiProvider,
    model: str,
    sensitive_terms: Sequence[str],
) -> RecommendationResult:
    """Call the configured provider exactly once and validate supplied-token output."""

    if not isinstance(request, RecommendationRequest) or not _safe_model_label(model):
        raise ValueError("invalid recommender configuration")
    request = _minimized_request(request, sensitive_terms=sensitive_terms)
    response = provider.complete(
        model=model,
        schema_name=RECOMMENDER_SCHEMA_NAME,
        system_instruction=(
            "Order only the supplied candidate tokens for clause review. "
            "Do not decide eligibility, payment, or claim readiness."
        ),
        input_payload={
            "schema_version": "1",
            "event": {
                "situation": request.situation,
                "facts": [
                    {
                        "field_id": item.field_id,
                        "value": item.value,
                        "confirmation": item.confirmation,
                    }
                    for item in request.facts
                ],
            },
            "candidates": [
                {
                    "token": item.token,
                    "contract_label": item.contract_label,
                    "coverage_label": item.coverage_label,
                    "clause_label": item.clause_label,
                    "excerpt": item.excerpt,
                    "page_start": item.page_start,
                    "page_end": item.page_end,
                    "citation_kind": item.citation_kind,
                }
                for item in request.candidates
            ],
        },
    )
    payload, request_id = provider_payload(response)
    return _validated_result(payload, request_id, request)


def _minimized_request(
    request: RecommendationRequest,
    *,
    sensitive_terms: Sequence[str],
) -> RecommendationRequest:
    """Apply the shared provider minimizer to every free-text projection field."""

    return RecommendationRequest(
        situation=_minimized_text(request.situation, sensitive_terms=sensitive_terms),
        facts=tuple(
            RecommendationFact(
                field_id=item.field_id,
                value=_minimized_text(item.value, sensitive_terms=sensitive_terms),
                confirmation=item.confirmation,
            )
            for item in request.facts
        ),
        candidates=tuple(
            RecommendationCandidate(
                token=item.token,
                contract_label=_minimized_text(
                    item.contract_label,
                    sensitive_terms=sensitive_terms,
                ),
                coverage_label=_minimized_text(
                    item.coverage_label,
                    sensitive_terms=sensitive_terms,
                ),
                clause_label=_minimized_text(
                    item.clause_label,
                    sensitive_terms=sensitive_terms,
                ),
                excerpt=_minimized_text(item.excerpt, sensitive_terms=sensitive_terms),
                page_start=item.page_start,
                page_end=item.page_end,
                citation_kind=item.citation_kind,
            )
            for item in request.candidates
        ),
    )


def _minimized_text(text: str, *, sensitive_terms: Sequence[str]) -> str:
    minimized = minimize_text(text, sensitive_terms=sensitive_terms)
    if (
        not _safe_text(minimized, 240)
        or _RESIDUAL_IDENTIFIER_PATTERN.search(minimized) is not None
        or any(term.casefold() in minimized.casefold() for term in sensitive_terms)
    ):
        raise EvidenceMinimizationError
    return minimized


def _validated_result(
    payload: object,
    request_id: str,
    request: RecommendationRequest,
) -> RecommendationResult:
    if not isinstance(payload, Mapping) or set(payload) != {
        "schema_version",
        "recommendations",
    }:
        raise RecommendationValidationError
    if payload.get("schema_version") != "1":
        raise RecommendationValidationError
    rows = payload.get("recommendations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= len(request.candidates):
        raise RecommendationValidationError
    supplied = {item.token for item in request.candidates}
    selections: list[RecommendationSelection] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != {
            "token",
            "explanation_code",
            "question_code",
        }:
            raise RecommendationValidationError
        token = row.get("token")
        explanation_code = row.get("explanation_code")
        question_code = row.get("question_code")
        if (
            not isinstance(token, str)
            or token not in supplied
            or not isinstance(explanation_code, str)
            or (question_code is not None and not isinstance(question_code, str))
        ):
            raise RecommendationValidationError
        selections.append(
            RecommendationSelection(
                token=token,
                explanation_code=explanation_code,
                question_code=question_code,
            )
        )
    return RecommendationResult(selections=tuple(selections), request_id=request_id)


def _safe_text(value: object, maximum: int) -> bool:
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= maximum
        and _UUID_PATTERN.search(value) is None
        and _PRIVATE_PATH_PATTERN.search(value) is None
    )


def _safe_model_label(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 120


__all__ = [
    "RECOMMENDER_SCHEMA_NAME",
    "DEFAULT_ASSISTANCE_MODEL",
    "RecommendationCandidate",
    "RecommendationFact",
    "RecommendationRequest",
    "RecommendationResult",
    "RecommendationSelection",
    "RecommendationValidationError",
    "recommend_clauses",
    "recommender_schema",
]
