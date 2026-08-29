"""Minimize provider-bound policy Evidence without logging source text."""

from __future__ import annotations

import re
from collections.abc import Sequence

from familycare_worker.ai.provider import EvidenceSlice

_REDACTED = "[REDACTED]"
_EMAIL_PATTERN = re.compile(
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,63}\b",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(
    r"(?<!\d)(?:\+82[- .]?)?0?(?:1[016789]|2|[3-6][1-5])[- .]?\d{3,4}[- .]?\d{4}(?!\d)"
)
_POLICY_IDENTIFIER_PATTERN = re.compile(
    r"(?P<label>(?:(?:보험\s*)?증권|계약)\s*번호|"
    r"(?:policy|contract|certificate)\s*(?:number|no\.?|id))"
    r"\s*[:#]?\s*(?P<value>[A-Z0-9][A-Z0-9._/-]{4,})",
    re.IGNORECASE,
)
_IDENTITY_LABEL = (
    r"계약자(?:명)?|피보험자(?:명)?|보험\s*수익자|수익자(?:명)?|성명|이름|"
    r"주소|거주지|소재지|생년월일|주민등록번호|"
    r"policyholder|insured\s+(?:person|name)|beneficiary(?:\s+name)?|"
    r"full\s+name|customer\s+name|address|date\s+of\s+birth"
)
_FOLLOWING_FIELD_LABEL = (
    rf"{_IDENTITY_LABEL}|보험사|상품명|계약일|보험기간|보장기간|보장개시일|"
    r"보장종료일|계약상태|담보명|가입금액|통화|갱신여부|증권번호|계약번호|"
    r"insurer|product(?:\s+name)?|contract\s+(?:date|status)|coverage\s+(?:start|end)|"
    r"rider(?:\s+name)?|insured\s+amount|currency|renewable|policy\s+(?:number|no\.?|id)"
)
_LABELLED_IDENTITY_PATTERN = re.compile(
    rf"(?P<label>(?<![가-힣A-Z])(?:{_IDENTITY_LABEL})(?![가-힣A-Z]))"
    rf"\s*[:#]?\s*(?P<value>.{{2,160}}?)"
    rf"(?=(?:[;|\r\n])|\s+(?:{_FOLLOWING_FIELD_LABEL})(?![가-힣A-Z])\s*[:#]?|$)",
    re.IGNORECASE,
)


class EvidenceMinimizationError(RuntimeError):
    """Fixed-message rejection that never contains the source or matched value."""

    def __init__(self) -> None:
        super().__init__("EVIDENCE_MINIMIZATION_ERROR")


def _redact(text: str, sensitive_terms: tuple[str, ...]) -> str:
    minimized = _EMAIL_PATTERN.sub(_REDACTED, text)
    minimized = _PHONE_PATTERN.sub(_REDACTED, minimized)
    minimized = _POLICY_IDENTIFIER_PATTERN.sub(
        lambda match: f"{match.group('label')}: {_REDACTED}",
        minimized,
    )
    minimized = _LABELLED_IDENTITY_PATTERN.sub(
        lambda match: f"{match.group('label')}: {_REDACTED}",
        minimized,
    )
    for term in sensitive_terms:
        minimized = re.sub(re.escape(term), _REDACTED, minimized, flags=re.IGNORECASE)
    return minimized[:240]


def minimize_evidence(
    evidence: Sequence[EvidenceSlice],
    *,
    sensitive_terms: Sequence[str],
) -> tuple[EvidenceSlice, ...]:
    """Return identity-preserving slices with unnecessary identifiers removed."""

    bounded_evidence = tuple(evidence)
    bounded_terms = tuple(sensitive_terms)
    if (
        not 1 <= len(bounded_evidence) <= 64
        or any(not isinstance(item, EvidenceSlice) for item in bounded_evidence)
        or len({item.evidence_id for item in bounded_evidence}) != len(bounded_evidence)
        or len(bounded_terms) > 16
        or len(set(bounded_terms)) != len(bounded_terms)
        or any(
            not isinstance(term, str) or term != term.strip() or not 2 <= len(term) <= 160
            for term in bounded_terms
        )
    ):
        raise EvidenceMinimizationError
    minimized: list[EvidenceSlice] = []
    try:
        for item in bounded_evidence:
            minimized.append(
                EvidenceSlice(
                    evidence_id=item.evidence_id,
                    document_version_id=item.document_version_id,
                    page=item.page,
                    text=_redact(item.text, bounded_terms),
                    bbox=item.bbox,
                    document_kind=item.document_kind,
                )
            )
    except ValueError:
        raise EvidenceMinimizationError from None
    return tuple(minimized)


__all__ = ["EvidenceMinimizationError", "minimize_evidence"]
