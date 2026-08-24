"""Deterministic policy candidate and Evidence validator."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from uuid import UUID

from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.ai.schemas import (
    CandidateField,
    IssueCode,
    StructurerCandidate,
    VerifierDecision,
)

_CURRENCY = re.compile(r"^[A-Z]{3}$")
_DATE_FIELDS = frozenset({"contract_start", "contract_end", "coverage_start", "coverage_end"})


def _append_issue(issues: list[IssueCode], issue: IssueCode) -> None:
    if issue not in issues:
        issues.append(issue)


def _parse_date(field: CandidateField, issues: list[IssueCode]) -> date | None:
    if not isinstance(field.value, str):
        _append_issue(issues, "INVALID_DATE")
        return None
    try:
        return date.fromisoformat(field.value)
    except ValueError:
        _append_issue(issues, "INVALID_DATE")
        return None


def _validate_units(fields: Sequence[CandidateField], issues: list[IssueCode]) -> None:
    dates: dict[str, date] = {}
    for field in fields:
        if field.field_id in _DATE_FIELDS:
            parsed = _parse_date(field, issues)
            if parsed is not None:
                dates[field.field_id] = parsed
        elif field.field_id == "currency" and (
            not isinstance(field.value, str) or _CURRENCY.fullmatch(field.value) is None
        ):
            _append_issue(issues, "INVALID_UNIT")
        elif field.field_id == "sum_assured":
            if isinstance(field.value, bool) or not isinstance(field.value, int | float | str):
                _append_issue(issues, "INVALID_UNIT")
                continue
            try:
                amount = Decimal(str(field.value))
            except InvalidOperation:
                _append_issue(issues, "INVALID_UNIT")
                continue
            if not amount.is_finite() or amount < 0:
                _append_issue(issues, "INVALID_UNIT")
    for start, end in (
        ("contract_start", "contract_end"),
        ("coverage_start", "coverage_end"),
    ):
        if start in dates and end in dates and dates[end] < dates[start]:
            _append_issue(issues, "INVALID_DATE")


def validate_candidate(
    *,
    candidate: StructurerCandidate,
    verifier: VerifierDecision,
    evidence: Sequence[EvidenceSlice],
) -> tuple[IssueCode, ...]:
    """Return stable issues; an empty tuple is the publication boundary."""

    issues: list[IssueCode] = list(verifier.issue_codes)
    evidence_by_id = {item.evidence_id: item for item in evidence}
    candidate_evidence: set[UUID] = set()
    field_ids = [field.field_id for field in candidate.fields]
    if len(set(field_ids)) != len(field_ids):
        _append_issue(issues, "UNSUPPORTED_STRUCTURE")
    for field in candidate.fields:
        if not field.evidence_ids:
            _append_issue(issues, "MISSING_EVIDENCE")
            continue
        for evidence_id in field.evidence_ids:
            if evidence_id not in evidence_by_id:
                _append_issue(issues, "MISSING_EVIDENCE")
            else:
                candidate_evidence.add(evidence_id)
    if candidate.candidate_id != verifier.candidate_id:
        _append_issue(issues, "CONFLICTING_EVIDENCE")
    if not set(verifier.evidence_ids).issubset(candidate_evidence):
        _append_issue(issues, "INVENTED_EVIDENCE")
    if verifier.decision == "approved" and not candidate_evidence.issubset(
        set(verifier.evidence_ids)
    ):
        _append_issue(issues, "MISSING_EVIDENCE")
    referenced_documents = {
        evidence_by_id[evidence_id].document_version_id
        for evidence_id in candidate_evidence
        if evidence_id in evidence_by_id
    }
    if len(referenced_documents) > 1:
        _append_issue(issues, "CONFLICTING_EVIDENCE")
    if candidate.candidate_kind == "rider" and candidate_evidence:
        evidence_kinds = {
            evidence_by_id[evidence_id].document_kind for evidence_id in candidate_evidence
        }
        if evidence_kinds == {"terms"}:
            _append_issue(issues, "TERMS_ONLY_RIDER")
    _validate_units(candidate.fields, issues)
    return tuple(issues)


__all__ = ["validate_candidate"]
