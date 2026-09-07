"""Pure applicability assessment from independently validated component metadata.

The repository owns source identity, household scope, currentness and user history.
Reference flags attest applicability semantics of the original anchors; ordinary
citations do not acquire that meaning merely because their code matches.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal


@dataclass(frozen=True)
class TermsApplicabilityAssessment:
    status: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    reason_codes: tuple[str, ...]
    matched_by: str | None
    evidence_fields: tuple[tuple[str, str], ...]


_REFERENCES = {
    "terms_reference": ("terms_code", "TERMS_REFERENCE_MISMATCH"),
    "edition_reference": ("edition_code", "EDITION_REFERENCE_MISMATCH"),
}
_DATE_FIELDS = (
    ("policy", "contract_date"),
    ("terms", "applicability_start"),
    ("terms", "applicability_end"),
)


@dataclass(frozen=True)
class _Metadata:
    values: dict[str, set[str]]
    flagged: frozenset[str]

    def uncertain(self, field: str) -> bool:
        values = self.values.get(field, set())
        return field in self.flagged or len(values) > 1 or "" in values

    def scalar(self, field: str) -> str | None:
        values = self.values.get(field, set())
        return next(iter(values)) if len(values) == 1 and not self.uncertain(field) else None

    def present(self, field: str) -> bool:
        return field in self.values or field in self.flagged


def _metadata(proof: dict[str, Any]) -> _Metadata:
    flags: list[str] = []
    for key in ("conflicting_fields", "unresolved_fields"):
        supplied = proof[key]
        if not isinstance(supplied, (list, tuple)) or any(
            not isinstance(field, str) for field in supplied
        ):
            raise ValueError("invalid metadata flags")
        flags.extend(supplied)
    if not isinstance(proof["facts"], (list, tuple)):
        raise ValueError("invalid metadata facts")
    values: dict[str, set[str]] = {}
    for fact in proof["facts"]:
        field, value = fact["field"], fact["value"]
        if not isinstance(field, str) or not isinstance(value, str):
            raise ValueError("invalid metadata scalar")
        normalized = " ".join(unicodedata.normalize("NFKC", value).casefold().split())
        values.setdefault(field, set()).add(normalized)
    return _Metadata(values, frozenset(flags))


def _result(
    status: Literal["MATCH", "NO_MATCH", "UNKNOWN"],
    reasons: list[str],
    fields: set[tuple[str, str]],
    matched_by: str | None = None,
) -> TermsApplicabilityAssessment:
    return TermsApplicabilityAssessment(
        status, tuple(dict.fromkeys(reasons)), matched_by, tuple(sorted(fields))
    )


def _date(value: str | None) -> date | None:
    if value is None:
        return None
    if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("invalid metadata date")
    return date.fromisoformat(value)


def assess_terms_applicability(
    policy: dict[str, Any],
    terms: dict[str, Any],
    *,
    explicit_application_fields: frozenset[str] = frozenset(),
) -> TermsApplicabilityAssessment:
    """Use either exact applicable references or product code and a printed period.

    Missing optional fields do not veto another sufficient route. Resolved
    contradictions do; uncertain identities or a period being compared cannot
    be made certain by choosing the other route. Generic references are unused
    by the date route. Display names and edition dates establish neither route.
    """
    if not isinstance(explicit_application_fields, frozenset) or not (
        explicit_application_fields <= _REFERENCES.keys()
    ):
        return _result("UNKNOWN", ["UNSUPPORTED_APPLICATION_FIELDS"], set())
    try:
        if policy["role"] != "policy" or terms["role"] != "terms":
            return _result("UNKNOWN", ["UNSUPPORTED_COMPONENT_ROLE"], set())
        sources = {"policy": _metadata(policy), "terms": _metadata(terms)}
    except KeyError, TypeError, ValueError, AttributeError:
        return _result("UNKNOWN", ["INVALID_METADATA"], set())

    certificate, edition = sources["policy"], sources["terms"]
    negative: list[str] = []
    negative_fields: set[tuple[str, str]] = set()
    uncertain: list[str] = []
    uncertain_fields: set[tuple[str, str]] = set()
    support: set[tuple[str, str]] = set()
    comparisons = [
        ("insurer", "insurer", "INSURER_MISMATCH"),
        ("product_code", "product_code", "PRODUCT_CODE_MISMATCH"),
        *[(field, *_REFERENCES[field]) for field in sorted(explicit_application_fields)],
    ]
    for policy_field, terms_field, reason in comparisons:
        addresses = {("policy", policy_field), ("terms", terms_field)}
        left, right = certificate.scalar(policy_field), edition.scalar(terms_field)
        if left is not None and right is not None:
            if left != right:
                negative.append(reason)
                negative_fields.update(addresses)
            else:
                support.update(addresses)
        for side, field in sorted(addresses):
            if sources[side].uncertain(field):
                uncertain.append("IDENTITY_METADATA_UNCERTAIN")
                uncertain_fields.add((side, field))
        if (
            policy_field in explicit_application_fields
            and len(certificate.values.get(policy_field, set())) > 1
        ):
            uncertain.append("MULTIPLE_APPLICATION_REFERENCES")

    period_used = certificate.present("contract_date") and any(
        edition.present(field) for field in ("applicability_start", "applicability_end")
    )
    period_fields = {(side, field) for side, field in _DATE_FIELDS if sources[side].present(field)}
    period_uncertain = period_used and any(
        sources[side].uncertain(field) for side, field in _DATE_FIELDS
    )
    if period_uncertain:
        uncertain.append("PERIOD_METADATA_UNCERTAIN")
        uncertain_fields.update(period_fields)
    parsed: list[date | None] = []
    invalid_period = False
    for side, field in _DATE_FIELDS:
        try:
            parsed.append(_date(sources[side].scalar(field)))
        except ValueError:
            parsed.append(None)
            invalid_period = True
            uncertain.append(
                "INVALID_CONTRACT_DATE" if side == "policy" else "INVALID_PRINTED_PERIOD"
            )
            uncertain_fields.add((side, field))
    contract_date, start, end = parsed
    if start is not None and end is not None and end < start:
        invalid_period = True
        uncertain.append("INVALID_PRINTED_PERIOD")
        uncertain_fields.update({("terms", "applicability_start"), ("terms", "applicability_end")})
    if contract_date is not None and not invalid_period and not period_uncertain:
        if (start is not None and contract_date < start) or (
            end is not None and contract_date > end
        ):
            negative.append("CONTRACT_DATE_OUTSIDE_PRINTED_PERIOD")
            negative_fields.update(period_fields)
        elif period_used:
            support.update(period_fields)

    if negative:
        return _result("NO_MATCH", negative, negative_fields)
    if uncertain:
        return _result("UNKNOWN", uncertain, support | uncertain_fields)
    same_insurer = certificate.scalar("insurer") is not None and certificate.scalar(
        "insurer"
    ) == edition.scalar("insurer")
    exact_references = explicit_application_fields == frozenset(_REFERENCES) and all(
        certificate.scalar(reference) is not None
        and certificate.scalar(reference) == edition.scalar(code)
        for reference, (code, _) in _REFERENCES.items()
    )
    if same_insurer and exact_references:
        return _result(
            "MATCH", ["EXPLICIT_EDITION_REFERENCE_MATCH"], support, "EXPLICIT_EDITION_REFERENCE"
        )
    same_product = certificate.scalar("product_code") is not None and certificate.scalar(
        "product_code"
    ) == edition.scalar("product_code")
    if same_insurer and same_product and contract_date is not None and start is not None:
        return _result(
            "MATCH", ["PRODUCT_CODE_PRINTED_PERIOD_MATCH"], support, "PRODUCT_CODE_PRINTED_PERIOD"
        )
    reasons = ["INSUFFICIENT_APPLICABILITY_EVIDENCE"]
    if any(
        certificate.present(field) and field not in explicit_application_fields
        for field in _REFERENCES
    ):
        reasons.append("APPLICATION_REFERENCE_UNPROVEN")
    return _result("UNKNOWN", reasons, support)
