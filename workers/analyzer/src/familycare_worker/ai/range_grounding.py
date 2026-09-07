"""Conservative source checks for range publication, independent of AI agreement."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation

from familycare_worker.ai.policy_ranges import RangeEvidenceSlice
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate

_DATE_LABELS = {
    "contract_start": r"계약(?:시작|개시)일|보험(?:시작|개시)일|contract start",
    "contract_end": r"계약(?:종료|만기)일|보험(?:종료|만기)일|contract end",
    "coverage_start": r"보장(?:시작|개시)일|coverage start",
    "coverage_end": r"보장(?:종료|만기)일|coverage end",
}
_AMOUNT = re.compile(
    r"(?:가입금액|보험가입금액|sum assured)\s*[:：|]?\s*"
    r"(?P<number>[0-9]+(?:,[0-9]{3})*(?:\.[0-9]+)?)\s*"
    r"(?P<unit>백만원|만원|천원|억원|원|KRW|USD|EUR|JPY)(?!\w)",
    re.IGNORECASE,
)
_UNITS = {"원": 1, "천원": 1000, "만원": 10000, "백만원": 1000000, "억원": 100000000}


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _contains(text: str, value: str) -> bool:
    normalized = _normalize(value)
    return (
        bool(normalized)
        and re.search(r"(?<!\w)" + re.escape(normalized) + r"(?!\w)", _normalize(text)) is not None
    )


def _grounded(field: CandidateField, text: str, candidate: PolicyCandidate) -> bool:
    value = field.value
    if field.field_id in {"rider_status", "policy_status"}:
        # A certificate's historical status is not evidence of current validity.
        return value == "unknown"
    if field.field_id in {"insurer", "product_name", "rider_name"}:
        return isinstance(value, str) and _contains(text, value)
    if field.field_id == "rider_key":
        name = next(
            (item.value for item in candidate.fields if item.field_id == "rider_name"), None
        )
        if not isinstance(name, str) or not isinstance(value, str):
            return False

        def key(item: str) -> str:
            return re.sub(r"[\W_]+", "", _normalize(item))

        return bool(key(value)) and key(value) == key(name) and _contains(text, name)
    if field.field_id in _DATE_LABELS:
        if not isinstance(value, str):
            return False
        try:
            expected = date.fromisoformat(value)
        except ValueError:
            return False
        matches = re.finditer(
            rf"(?:{_DATE_LABELS[field.field_id]})\s*[:：|]?\s*"
            r"([0-9]{4})[-./년]\s*([0-9]{1,2})[-./월]\s*([0-9]{1,2})(?:일)?(?![0-9])",
            text,
            re.IGNORECASE,
        )
        for match in matches:
            try:
                if date(*map(int, match.groups())) == expected:
                    return True
            except ValueError:
                continue
        return False
    if field.field_id in {"sum_assured", "currency"}:
        for match in _AMOUNT.finditer(text):
            unit = match["unit"].upper()
            currency = "KRW" if unit in _UNITS else unit
            if field.field_id == "currency":
                if value == currency:
                    return True
                continue
            if isinstance(value, bool) or not isinstance(value, int | float | str):
                return False
            try:
                amount = Decimal(match["number"].replace(",", "")) * _UNITS.get(unit, 1)
                if amount == Decimal(str(value)):
                    return True
            except InvalidOperation:
                return False
        return False
    if field.field_id == "benefit_type":
        terms = {"fixed": ("정액", "fixed"), "indemnity": ("실손", "indemnity")}
        return isinstance(value, str) and any(
            _contains(text, term) for term in terms.get(value, ())
        )
    if field.field_id == "renewable":
        return isinstance(value, bool) and any(
            _contains(text, term)
            for term in (
                ("갱신형", "renewable: true")
                if value
                else ("비갱신", "비갱신형", "renewable: false")
            )
        )
    return False


def ground_range_candidate(
    candidate: PolicyCandidate, evidence: Sequence[RangeEvidenceSlice]
) -> PolicyCandidate:
    """Retain unsupported facts for review; never promote rejected/review candidates."""
    if candidate.status != "AI_VERIFIED":
        return candidate
    sources = {item.evidence_id: item for item in evidence}
    unsupported = False
    for field in candidate.fields:
        cited = [sources[key] for key in field.evidence_ids if key in sources]
        if (
            not cited
            or len(cited) != len(field.evidence_ids)
            or not any(item.primary and item.source_role == "policy" for item in cited)
            or not _grounded(field, "\n".join(item.text for item in cited), candidate)
        ):
            unsupported = True
            break
    if not unsupported:
        return candidate
    return candidate.model_copy(
        update={
            "status": "NEEDS_REVIEW",
            "issue_codes": tuple(dict.fromkeys((*candidate.issue_codes, "INVENTED_FIELD"))),
        }
    )
