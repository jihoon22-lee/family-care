"""Bounded, non-authoritative recommendations for reviewing related clauses."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

AssistanceMode = Literal["STRUCTURED_SEARCH", "LLM_ASSISTED", "NONE"]
AssistanceState = Literal["SEARCH_READY", "LLM_PENDING", "LLM_READY"]

_TOKEN_PATTERN = re.compile(r"[^\W_]+(?:_[^\W_]+)*", flags=re.UNICODE)
_REASON_CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class AnalysisAssistanceNotFound(LookupError):
    """Raised when a scoped decision has no assistance projection."""


@dataclass(frozen=True)
class AnalysisRecommendation:
    """One related-clause suggestion, never an eligibility or payment result."""

    id: UUID
    private_claim_candidate_id: UUID
    knowledge_coverage_id: UUID
    terms_section_id: UUID
    knowledge_fact_id: UUID
    source_clause_id: UUID
    fact_citation_id: UUID
    rank: int
    score: Decimal
    contract_label: str
    coverage_label: str
    clause_label: str
    excerpt: str
    page_start: int
    page_end: int
    citation_kind: Literal["FACT_CITATION"]
    reason_code: str
    explanation_code: str | None = None
    question_code: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "id",
            "private_claim_candidate_id",
            "knowledge_coverage_id",
            "terms_section_id",
            "knowledge_fact_id",
            "source_clause_id",
            "fact_citation_id",
        ):
            _require_uuid(getattr(self, name), name)
        if isinstance(self.rank, bool) or not 1 <= self.rank <= 12:
            raise ValueError("recommendation rank must be between 1 and 12")
        if self.score < 0:
            raise ValueError("recommendation score cannot be negative")
        for name, maximum in (
            ("contract_label", 240),
            ("coverage_label", 800),
            ("clause_label", 800),
            ("excerpt", 240),
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError(f"invalid recommendation {name}")
        if (
            isinstance(self.page_start, bool)
            or isinstance(self.page_end, bool)
            or self.page_start < 1
            or self.page_end < self.page_start
            or self.page_end - self.page_start > 20
        ):
            raise ValueError("invalid recommendation page range")
        if self.citation_kind != "FACT_CITATION":
            raise ValueError("unsupported recommendation citation kind")
        if not _REASON_CODE_PATTERN.fullmatch(self.reason_code):
            raise ValueError("invalid recommendation reason code")
        if any(
            value is not None and _REASON_CODE_PATTERN.fullmatch(value) is None
            for value in (self.explanation_code, self.question_code)
        ):
            raise ValueError("invalid recommendation assistance code")


@dataclass(frozen=True)
class AnalysisAssistance:
    """Latest immutable recommendation projection for one decision run."""

    run_id: UUID
    job_id: UUID
    decision_run_id: UUID
    event_version: int
    candidate_digest_sha256: str
    mode: AssistanceMode
    state: AssistanceState
    outcome_code: str
    recommendations: tuple[AnalysisRecommendation, ...]
    created_at: datetime
    provider_label: str | None = None
    model_label: str | None = None
    config_version: str | None = None

    def __post_init__(self) -> None:
        _require_uuid(self.run_id, "assistance run")
        _require_uuid(self.job_id, "assistance job")
        _require_uuid(self.decision_run_id, "decision run")
        if isinstance(self.event_version, bool) or self.event_version < 1:
            raise ValueError("assistance event version must be positive")
        if not re.fullmatch(r"[0-9a-f]{64}", self.candidate_digest_sha256):
            raise ValueError("invalid assistance candidate digest")
        if self.mode not in {"STRUCTURED_SEARCH", "LLM_ASSISTED", "NONE"}:
            raise ValueError("unsupported assistance mode")
        if self.state not in {"SEARCH_READY", "LLM_PENDING", "LLM_READY"}:
            raise ValueError("unsupported assistance state")
        if not _REASON_CODE_PATTERN.fullmatch(self.outcome_code):
            raise ValueError("invalid assistance outcome code")
        if len(self.recommendations) > 12:
            raise ValueError("too many assistance recommendations")
        expected_ranks = tuple(range(1, len(self.recommendations) + 1))
        if tuple(item.rank for item in self.recommendations) != expected_ranks:
            raise ValueError("assistance recommendation ranks must be contiguous")
        if self.mode == "NONE" and self.recommendations:
            raise ValueError("NONE assistance cannot contain recommendations")
        if self.mode == "LLM_ASSISTED":
            if self.state != "LLM_READY" or not all(
                _bounded_label(value, maximum)
                for value, maximum in (
                    (self.provider_label, 64),
                    (self.model_label, 120),
                    (self.config_version, 64),
                )
            ):
                raise ValueError("LLM assistance requires bounded provider provenance")
        elif any(
            value is not None
            for value in (self.provider_label, self.model_label, self.config_version)
        ):
            raise ValueError("local assistance cannot contain provider provenance")


def normalize_search_tokens(situation: str, fact_values: tuple[str, ...]) -> tuple[str, ...]:
    """Return bounded exact Unicode tokens without fuzzy or substring expansion."""

    values = (situation, *fact_values)
    tokens: set[str] = set()
    for value in values:
        normalized = unicodedata.normalize("NFKC", value).casefold()
        for token in _TOKEN_PATTERN.findall(normalized):
            if 2 <= len(token) <= 64:
                tokens.add(token)
    return tuple(sorted(tokens)[:64])


def candidate_digest(recommendations: tuple[AnalysisRecommendation, ...]) -> str:
    """Hash only opaque candidate identity and rank inputs, never source text."""

    identities = sorted(
        (
            str(item.knowledge_coverage_id),
            str(item.terms_section_id),
            str(item.knowledge_fact_id),
            str(item.source_clause_id),
            str(item.fact_citation_id),
            str(item.score),
            str(item.page_start),
            str(item.page_end),
        )
        for item in recommendations
    )
    payload = json.dumps(
        {"schema": "analysis-candidates.v1", "identities": identities},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(payload).hexdigest()


def _require_uuid(value: object, name: str) -> None:
    if not isinstance(value, UUID) or value.int == 0:
        raise ValueError(f"{name} must be a non-zero UUID")


def _bounded_label(value: str | None, maximum: int) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum


__all__ = [
    "AnalysisAssistance",
    "AnalysisAssistanceNotFound",
    "AnalysisRecommendation",
    "AssistanceMode",
    "AssistanceState",
    "candidate_digest",
    "normalize_search_tokens",
]
