"""Conservative source checks for range publication, independent of AI agreement."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from familycare_worker.ai.policy_ranges import RangeEvidenceSlice
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate
from familycare_worker.ai.table_grounding import explicitly_unenrolled, table_field_proof

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


def _certificate_title(text: str, value: str) -> bool:
    """Accept the exact product before a line-final certificate label only."""
    normalized = _normalize(value)
    if not normalized:
        return False
    pattern = r"(?<!\w)" + re.escape(normalized) + r"_보험증권$"
    return any(re.search(pattern, _normalize(line)) is not None for line in text.splitlines())


def issuer_field_context(text: str, value: str) -> bool:
    """Require an explicit issuer label, not an incidental company mention."""
    normalized = _normalize(value)
    if not normalized:
        return False
    label = r"(?:보험사|보험회사|인수회사|발급회사|발급기관|발행기관|insurer|issuer)"
    pattern = r"^" + label + r"\s*[:：]\s*" + re.escape(normalized) + r"(?:$|\s*[;|])"
    return any(re.match(pattern, _normalize(line)) is not None for line in text.splitlines())


def _named_enrollment_line(text: str, name: str) -> bool:
    normalized = _normalize(text)
    value = re.escape(_normalize(name)) + r"(?!\w)"
    if re.match(
        r"^(?:담보명|특약명|가입담보|가입특약|rider name|enrolled rider)\s*[:：]\s*" + value,
        normalized,
    ):
        return True
    normalized = re.sub(r"^\s*(?:(?:[0-9]+[.)]|[-•·])\s*)?", "", normalized)
    return re.match(value, normalized) is not None and len(tuple(_AMOUNT.finditer(text))) == 1


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
        date_matches = tuple(
            re.finditer(
                rf"(?:{_DATE_LABELS[field.field_id]})\s*[:：|]?\s*"
                r"([0-9]{4})[-./년]\s*([0-9]{1,2})[-./월]\s*([0-9]{1,2})(?:일)?(?![0-9])",
                text,
                re.IGNORECASE,
            )
        )
        if len(date_matches) != 1:
            return False
        for match in date_matches:
            try:
                if date(*map(int, match.groups())) == expected:
                    return True
            except ValueError:
                continue
        return False
    if field.field_id in {"sum_assured", "currency"}:
        matches = tuple(_AMOUNT.finditer(text))
        if len(matches) != 1:
            return False
        for match in matches:
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
        if value == "unknown":
            return True
        terms = {"fixed": ("정액", "fixed"), "indemnity": ("실손", "indemnity")}
        found = {
            kind
            for kind, labels in terms.items()
            if any(_contains(text, label) for label in labels)
        }
        return isinstance(value, str) and found == {value}
    if field.field_id == "renewable":
        conditions = {
            True: ("갱신형", "renewable: true"),
            False: ("비갱신", "비갱신형", "renewable: false"),
        }
        found_conditions = {
            kind
            for kind, labels in conditions.items()
            if any(_contains(text, label) for label in labels)
        }
        return isinstance(value, bool) and found_conditions == {value}
    return False


def _rider_line(text: str, name: str) -> tuple[str, bool]:
    lines = text.splitlines()
    rows = tuple(dict.fromkeys(line for line in lines if _contains(line, name)))
    selected = rows[0] if len(rows) == 1 else ""
    statuses = [
        line
        for index, line in enumerate(lines)
        if index > 0
        and _contains(lines[index - 1], name)
        and re.fullmatch(r"\s*(?:미가입|미선택|not enrolled|example only)\s*", line, re.IGNORECASE)
    ]
    notices = [line for line in lines if re.match(r"\s*(?:이|해당)\s*표", line)]
    return selected, explicitly_unenrolled("\n".join((*rows, *statuses, *notices)))


def _excluded_enrollment(
    candidate: PolicyCandidate,
    evidence: Sequence[RangeEvidenceSlice],
    nodes: Mapping[str, Mapping[str, Any]],
) -> bool:
    if candidate.candidate_kind != "rider":
        return False
    name = next((field for field in candidate.fields if field.field_id == "rider_name"), None)
    if name is None or not isinstance(name.value, str):
        return False
    for item in evidence:
        if item.evidence_id not in name.evidence_ids or not item.primary:
            continue
        if _rider_line(item.text, name.value)[1]:
            return True
        node = nodes.get(item.node_id, {})
        if node.get("kind") == "TABLE_ROW" and (
            explicitly_unenrolled(node.get("text", ""))
            or any(
                explicitly_unenrolled(nodes.get(key, {}).get("text", ""))
                for key in node.get("context_node_ids", ())
            )
        ):
            return True
    return False


def ground_range_candidate(
    candidate: PolicyCandidate,
    evidence: Sequence[RangeEvidenceSlice],
    *,
    local_nodes: Mapping[str, Mapping[str, Any]] | None = None,
    allow_certificate_title: bool = False,
    require_issuer_context: bool = False,
) -> PolicyCandidate:
    """Retain unsupported facts for review; never promote rejected/review candidates."""
    if _excluded_enrollment(candidate, evidence, local_nodes or {}):
        return candidate.model_copy(
            update={
                "status": "NEEDS_REVIEW" if candidate.status == "AI_VERIFIED" else candidate.status,
                "issue_codes": tuple(dict.fromkeys((*candidate.issue_codes, "NOT_ENROLLED"))),
            }
        )
    if candidate.status != "AI_VERIFIED":
        return candidate
    if candidate.candidate_kind == "rider" and not any(
        f.field_id == "benefit_type" for f in candidate.fields
    ):
        name_field = next((f for f in candidate.fields if f.field_id == "rider_name"), None)
        if name_field is not None:
            candidate = candidate.model_copy(
                update={
                    "fields": (
                        *candidate.fields,
                        CandidateField(
                            field_id="benefit_type",
                            value="unknown",
                            evidence_ids=name_field.evidence_ids,
                        ),
                    )
                }
            )
    sources = {item.evidence_id: item for item in evidence}
    rider_name = next(
        (field.value for field in candidate.fields if field.field_id == "rider_name"), None
    )
    unclassified = any(
        field.field_id == "benefit_type" and field.value == "unknown" for field in candidate.fields
    )
    unsupported = False
    issue_code = "INVENTED_FIELD"
    fields = []
    for field in candidate.fields:
        cited = [sources[key] for key in field.evidence_ids if key in sources]
        text = "\n".join(item.text for item in cited)
        if candidate.candidate_kind == "rider" and isinstance(rider_name, str):
            # Several enrollment rows may share a PDF block. A neighboring row's
            # amount/date is not authority for this rider, even if both AIs agree.
            text, excluded = _rider_line(text, rider_name)
            if excluded:
                unsupported = True
                issue_code = "NOT_ENROLLED"
                break
        proof = None
        if local_nodes is not None:
            proof = table_field_proof(candidate, field, cited, evidence, local_nodes)
            if proof is not None:
                if proof.issue_code == "UNCLASSIFIED_BENEFIT_TYPE":
                    field = field.model_copy(update={"value": "unknown"})
                elif proof.issue_code is not None:
                    unsupported = True
                    issue_code = proof.issue_code
                    break
                text = proof.text
                field = field.model_copy(
                    update={
                        "evidence_ids": tuple(
                            dict.fromkeys(
                                (
                                    *field.evidence_ids,
                                    *proof.evidence_ids,
                                )
                            )
                        )
                    }
                )
        if (
            field.field_id == "rider_name"
            and unclassified
            and proof is None
            and isinstance(rider_name, str)
            and not _named_enrollment_line(text, rider_name)
        ):
            unsupported = True
            break
        if (
            field.field_id == "benefit_type"
            and field.value in {"fixed", "indemnity"}
            and proof is None
            and isinstance(rider_name, str)
            and _named_enrollment_line(text, rider_name)
            and not any(_contains(text, label) for label in ("정액", "fixed", "실손", "indemnity"))
        ):
            field = field.model_copy(update={"value": "unknown"})
        fields.append(field)
        if (
            not cited
            or any(key not in sources for key in field.evidence_ids)
            or len(field.evidence_ids) > 16
            or not any(item.primary and item.source_role == "policy" for item in cited)
            or (
                require_issuer_context
                and field.field_id == "insurer"
                and (
                    not isinstance(field.value, str) or not issuer_field_context(text, field.value)
                )
            )
            or not (
                _grounded(field, text, candidate)
                or (
                    allow_certificate_title
                    and candidate.candidate_kind == "policy_contract"
                    and field.field_id == "product_name"
                    and isinstance(field.value, str)
                    and _certificate_title(text, field.value)
                )
            )
        ):
            unsupported = True
            break
    if not unsupported:
        return candidate.model_copy(update={"fields": tuple(fields)})
    return candidate.model_copy(
        update={
            "status": "NEEDS_REVIEW",
            "issue_codes": tuple(dict.fromkeys((*candidate.issue_codes, issue_code))),
        }
    )
