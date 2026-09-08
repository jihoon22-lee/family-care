"""Conditional arithmetic over complete fixed estimates from one actual snapshot.

Neither distinct contract IDs nor the lack of combination metadata proves a
right to simultaneous payment. Every subtotal explicitly assumes that the listed
contracts pay independently, without a shared cap or cross-contract reduction.
This helper never applies that assumption to establish event facts or eligibility.
The caller owns authorization, source evaluation and the persisted response DTO.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from decimal import Context, Decimal, localcontext
from typing import Any

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.guidance.models import GuidanceCandidate, LocalGuidanceResponse
from familycare_api.guidance.trace_projection import decimal_text

SUBTOTAL_REVISION = "fixed-subtotals-v1"
COMBINATION_ASSUMPTION = "INDEPENDENT_FIXED_PAYMENTS_ASSUMED"
type _RefKey = tuple[str, str, str]
type _ContractKey = tuple[str, str]


def _key(ref: CanonicalCoverageRef) -> _RefKey:
    return ref.kind, str(ref.contract_id), str(ref.coverage_id)


def _reference(key: _RefKey) -> dict[str, str]:
    return dict(zip(("kind", "contract_id", "coverage_id"), key, strict=True))


def _case_key(candidate: GuidanceCandidate) -> str | None:
    return candidate.cases[0].case_key if len(candidate.cases) == 1 else None


def _reason(candidate: GuidanceCandidate, response: LocalGuidanceResponse) -> str | None:
    if candidate.benefit_kind != "FIXED":
        return "NON_FIXED_BENEFIT"
    if len(candidate.cases) > 1 or candidate.case_relation == "MUTUALLY_EXCLUSIVE":
        return "MULTIPLE_PAYOUT_CASES"
    if candidate.condition_result != "MATCH":
        return "EVENT_CONDITIONS_UNRESOLVED"
    if candidate.cases:
        case = candidate.cases[0]
        if (
            case.condition_result != "MATCH"
            or case.benefit_kind != "FIXED"
            or case.estimate != candidate.estimate
        ):
            return "PAYOUT_CASE_UNRESOLVED"
    estimate = candidate.estimate
    if estimate.basis != "DOCUMENT_FORMULA" or candidate.scenarios:
        return "ESTIMATE_CONTEXT_UNSUPPORTED"
    if estimate.kind != "POINT" or estimate.amount is None or estimate.currency is None:
        return "POINT_ESTIMATE_UNAVAILABLE"
    if estimate.partial_amount is not None or estimate.missing_inputs:
        return "PARTIAL_ESTIMATE"
    trace = estimate.trace
    if (
        trace is None
        or trace.status != "COMPLETE"
        or trace.unit != "MONEY"
        or trace.currency != estimate.currency
        or trace.value is None
        or Decimal(trace.value) != Decimal(estimate.amount)
        or trace.missing_paths
        or not trace.source_refs
        or any(
            step.status != "AVAILABLE"
            or any(operand.status != "AVAILABLE" or operand.stale for operand in step.operands)
            for step in trace.steps
        )
    ):
        return "CALCULATION_TRACE_UNAVAILABLE"
    references = [
        *trace.source_refs,
        *(ref for step in trace.steps for ref in step.unit_source_refs),
        *(ref for step in trace.steps for operand in step.operands for ref in operand.source_refs),
    ]
    if any(
        ref.source_kind == "EVENT_SCENARIO"
        or ref.source_kind in {"EVENT_FACT", "EVENT_TEXT", "EVENT_RECEIPT_SET"}
        and (
            ref.source_id != str(response.medical_event_id)
            or type(ref.version) is not int
            or ref.version != response.event_version
        )
        for ref in references
    ) or any(
        operand.provenance == "SCENARIO_ASSUMPTION"
        for step in trace.steps
        for operand in step.operands
    ):
        return "CALCULATION_CONTEXT_MISMATCH"
    return None


def _signature(candidate: GuidanceCandidate) -> dict[str, Any]:
    # A known identical coverage may arrive via either canonical source. Select
    # neither competing calculation when their preserved results actually differ.
    return candidate.model_dump(
        mode="json", exclude={"ref", "canonical_identity", "contract_label", "coverage_label"}
    )


def fixed_subtotal_projection(response: LocalGuidanceResponse) -> dict[str, Any]:
    """Return proposed DTO dictionaries, without changing the input snapshot.

    Only actual document-formula points are supported. Planned-care combinations,
    multiple payouts within one contract and uncanonicalized private contracts
    remain individually visible through omission records. No subtotal is emitted
    unless at least two different canonical contracts contribute in one currency.
    """
    aliases: dict[_RefKey, _RefKey] = {}
    parents: dict[_ContractKey, _ContractKey] = {}
    invalid_aliases: set[_RefKey] = set()

    def contract(key: _ContractKey) -> _ContractKey:
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    for candidate in response.candidates:
        identity = candidate.canonical_identity
        if identity is None:
            continue
        canonical = _key(identity.ref)
        for ref in identity.source_refs:
            source = _key(ref)
            if source in aliases and aliases[source] != canonical:
                invalid_aliases.update((source, canonical, aliases[source]))
            aliases[source] = canonical
            left, right = contract(source[:2]), contract(canonical[:2])
            parents[left] = right

    grouped: dict[_RefKey, list[GuidanceCandidate]] = defaultdict(list)
    for candidate in response.candidates:
        raw = _key(candidate.ref)
        canonical = (
            _key(candidate.canonical_identity.ref)
            if candidate.canonical_identity is not None
            else aliases.get(raw, raw)
        )
        if candidate.canonical_identity is not None and candidate.ref not in (
            candidate.canonical_identity.ref,
            *candidate.canonical_identity.source_refs,
        ):
            invalid_aliases.add(canonical)
        grouped[canonical].append(candidate)

    contract_coverages: dict[_ContractKey, set[_RefKey]] = defaultdict(set)
    for key, candidates in grouped.items():
        if any(candidate.benefit_kind == "FIXED" for candidate in candidates):
            contract_coverages[contract(key[:2])].add(key)

    omissions: list[dict[str, Any]] = []
    accepted: dict[str, list[tuple[_RefKey, GuidanceCandidate]]] = defaultdict(list)

    def omit(key: _RefKey, candidate: GuidanceCandidate, reason: str) -> None:
        omissions.append(
            {
                "ref": _reference(key),
                "case_key": _case_key(candidate),
                "currency": candidate.estimate.currency,
                "benefit_kind": candidate.benefit_kind,
                "reason_code": reason,
            }
        )

    for key, candidates in sorted(grouped.items()):
        candidate = candidates[0]
        reason = _reason(candidate, response)
        if key in invalid_aliases:
            reason = "CANONICAL_IDENTITY_CONFLICT"
        elif any(_signature(other) != _signature(candidate) for other in candidates[1:]):
            reason = "CANONICAL_DUPLICATE_CONFLICT"
        elif candidate.benefit_kind == "FIXED":
            if len(contract_coverages[contract(key[:2])]) > 1:
                reason = "SAME_CONTRACT_COMBINATION_UNRESOLVED"
            elif key[0] != "OPERATIONAL_RIDER":
                reason = "CANONICAL_CONTRACT_UNRESOLVED"
        if reason is not None:
            omit(key, candidate, reason)
        else:
            assert candidate.estimate.currency is not None
            accepted[candidate.estimate.currency].append((key, candidate))

    for values in accepted.values():
        if len(values) < 2:
            for key, candidate in values:
                omit(key, candidate, "INSUFFICIENT_COMPATIBLE_ADDENDS")

    subtotals = []
    for currency, values in sorted(accepted.items()):
        if len(values) < 2:
            continue
        items: list[dict[str, Any]] = []
        scopes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for key, candidate in values:
            trace = candidate.estimate.trace
            assert trace is not None
            component = {"ref": _reference(key), "case_key": _case_key(candidate)}
            items.append(
                {
                    **component,
                    "amount": candidate.estimate.amount,
                    "trace_reference": {
                        "publication_id": str(trace.publication_id),
                        "source_revision": trace.source_revision,
                        "source_digest_sha256": trace.source_digest_sha256,
                        "formula_digest_sha256": trace.formula_digest_sha256,
                        "runtime_revision": trace.runtime_revision,
                    },
                }
            )
            for code in sorted(
                {
                    COMBINATION_ASSUMPTION,
                    *candidate.assumptions,
                    *candidate.estimate.assumptions,
                    *(candidate.cases[0].assumptions if candidate.cases else ()),
                }
            ):
                scopes[code].append(component)
        # Input Money has at most 80 characters and the response has at most
        # 1000 candidates. 164 digits also preserve sums of mixed-scale values.
        arithmetic = Context(prec=164)
        for signal in arithmetic.traps:
            arithmetic.traps[signal] = True
        with localcontext(arithmetic):
            total = sum((Decimal(item["amount"]) for item in items), Decimal(0))
        amount = decimal_text(total)
        if amount is None or len(amount) > 80:
            for key, candidate in values:
                omit(key, candidate, "SUBTOTAL_AMOUNT_LIMIT_EXCEEDED")
            continue
        subtotal = {
            "revision": SUBTOTAL_REVISION,
            "currency": currency,
            "amount": amount,
            "basis": "ASSUMED_COMBINATION",
            "conditional": True,
            "partial": any(
                item["benefit_kind"] == "FIXED" and item["currency"] in (None, currency)
                for item in omissions
            ),
            "items": items,
            "scoped_assumptions": [
                {"code": code, "applies_to": scope} for code, scope in sorted(scopes.items())
            ],
        }
        payload = {
            "medical_event_id": str(response.medical_event_id),
            "family_member_id": str(response.family_member_id),
            "event_version": response.event_version,
            "event_date": str(response.event_date),
            "versions": response.versions.model_dump(mode="json"),
            "subtotal": subtotal,
        }
        subtotal["subtotal_key"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        subtotals.append(subtotal)
    return {"fixed_subtotals": subtotals, "subtotal_omissions": omissions}
