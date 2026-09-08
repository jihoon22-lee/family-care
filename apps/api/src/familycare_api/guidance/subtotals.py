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
from dataclasses import dataclass
from decimal import Context, Decimal, localcontext
from typing import Any

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.guidance.models import (
    GuidanceCandidate,
    GuidanceEstimate,
    GuidanceScenario,
    GuidanceSourceReference,
    LocalGuidanceResponse,
)
from familycare_api.guidance.trace_projection import decimal_text

SUBTOTAL_REVISION = "fixed-subtotals-v2"
COMBINATION_ASSUMPTION = "INDEPENDENT_FIXED_PAYMENTS_ASSUMED"
MAX_SCENARIO_CONTEXTS = 32
MAX_SCENARIO_SELECTIONS = 1000
MAX_SCENARIO_RECORDS = 2048
type _RefKey = tuple[str, str, str]
type _ContractKey = tuple[str, str]


@dataclass(frozen=True, slots=True, repr=False)
class _Selection:
    candidate: GuidanceCandidate
    scenario: GuidanceScenario | None = None
    failure: str | None = None

    @property
    def estimate(self) -> GuidanceEstimate:
        return self.candidate.estimate if self.scenario is None else self.scenario.estimate

    @property
    def scenario_key(self) -> str | None:
        return None if self.scenario is None else self.scenario.scenario_key


def _key(ref: CanonicalCoverageRef) -> _RefKey:
    return ref.kind, str(ref.contract_id), str(ref.coverage_id)


def _reference(key: _RefKey) -> dict[str, str]:
    return dict(zip(("kind", "contract_id", "coverage_id"), key, strict=True))


def _case_key(candidate: GuidanceCandidate) -> str | None:
    return candidate.cases[0].case_key if len(candidate.cases) == 1 else None


def _scenario_ref(
    ref: GuidanceSourceReference, scenario: GuidanceScenario, response: LocalGuidanceResponse
) -> bool:
    return (
        ref.source_kind == "EVENT_SCENARIO"
        and ref.source_id == str(response.medical_event_id)
        and type(ref.version) is int
        and ref.version == response.event_version
        and ref.digest_sha256 == scenario.scenario_key
    )


def _scenario_reason(selection: _Selection, response: LocalGuidanceResponse) -> str | None:
    candidate, scenario = selection.candidate, selection.scenario
    assert scenario is not None
    conditions = (
        *candidate.conditions,
        *(candidate.cases[0].conditions if candidate.cases else ()),
    )
    codes = (*candidate.reason_codes, *(candidate.cases[0].reason_codes if candidate.cases else ()))
    if "SEMANTIC_KNOWLEDGE_PARTIAL" in codes:
        return "SCENARIO_KNOWLEDGE_INCOMPLETE"
    if any(
        condition.result == "NO_MATCH" or condition.required and condition.result != "MATCH"
        for condition in conditions
    ):
        return "EVENT_CONDITIONS_UNRESOLVED"
    if (
        scenario.estimate.basis != "USER_SCENARIO"
        or "PLANNED_CARE_ASSUMED" not in scenario.estimate.assumptions
    ):
        return "ESTIMATE_CONTEXT_UNSUPPORTED"
    if (
        not scenario.hypotheses
        or len({h.field_path for h in scenario.hypotheses}) != len(scenario.hypotheses)
        or any(
            h.provenance != "SCENARIO_ASSUMPTION"
            or not h.spans
            or not h.source_refs
            or any(not _scenario_ref(ref, scenario, response) for ref in h.source_refs)
            for h in scenario.hypotheses
        )
    ):
        return "CALCULATION_CONTEXT_MISMATCH"
    hypotheses = {hypothesis.field_path: hypothesis for hypothesis in scenario.hypotheses}
    admission, days = (
        hypotheses.get("MedicalEvent.admission"),
        hypotheses.get("MedicalEvent.admission_days"),
    )
    # This is the exact planned-care context currently emitted by scenarios.py
    # and accepted by the arithmetic runtime, not a general assumption language.
    if (
        len(hypotheses) != 2
        or admission is None
        or admission.value is not True
        or days is None
        or type(days.value) is not int
        or not 1 <= days.value <= 36500
    ):
        return "CALCULATION_CONTEXT_MISMATCH"
    return None


def _reason(selection: _Selection, response: LocalGuidanceResponse) -> str | None:
    candidate = selection.candidate
    if selection.failure is not None:
        return selection.failure
    if candidate.benefit_kind != "FIXED":
        return "NON_FIXED_BENEFIT"
    if len(candidate.cases) > 1 or candidate.case_relation == "MUTUALLY_EXCLUSIVE":
        return "MULTIPLE_PAYOUT_CASES"
    if selection.scenario is None and candidate.condition_result != "MATCH":
        return "EVENT_CONDITIONS_UNRESOLVED"
    if candidate.cases:
        case = candidate.cases[0]
        if (
            selection.scenario is None
            and case.condition_result != "MATCH"
            or case.benefit_kind != "FIXED"
            or case.estimate != candidate.estimate
        ):
            return "PAYOUT_CASE_UNRESOLVED"
    estimate = selection.estimate
    if selection.scenario is None:
        if estimate.basis != "DOCUMENT_FORMULA" or candidate.scenarios:
            return "ESTIMATE_CONTEXT_UNSUPPORTED"
    else:
        failure = _scenario_reason(selection, response)
        if failure is not None:
            return failure
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
        ref.source_kind in {"EVENT_FACT", "EVENT_TEXT", "EVENT_RECEIPT_SET"}
        and (
            ref.source_id != str(response.medical_event_id)
            or type(ref.version) is not int
            or ref.version != response.event_version
        )
        for ref in references
    ):
        return "CALCULATION_CONTEXT_MISMATCH"
    scenario = selection.scenario
    scenario_operands = tuple(
        operand
        for step in trace.steps
        for operand in step.operands
        if operand.provenance == "SCENARIO_ASSUMPTION"
    )
    if scenario is None:
        if scenario_operands or any(ref.source_kind == "EVENT_SCENARIO" for ref in references):
            return "CALCULATION_CONTEXT_MISMATCH"
    else:
        hypotheses = {h.field_path: h for h in scenario.hypotheses}
        if not scenario_operands or any(
            ref.source_kind == "EVENT_SCENARIO" and not _scenario_ref(ref, scenario, response)
            for ref in references
        ):
            return "CALCULATION_CONTEXT_MISMATCH"
        for step in trace.steps:
            for operand in step.operands:
                if operand.provenance != "SCENARIO_ASSUMPTION":
                    if any(ref.source_kind == "EVENT_SCENARIO" for ref in operand.source_refs):
                        return "CALCULATION_CONTEXT_MISMATCH"
                    continue
                hypothesis = hypotheses.get(operand.field_path or "")
                if (
                    hypothesis is None
                    or operand.field_path != "MedicalEvent.admission_days"
                    or type(hypothesis.value) is not int
                    or not 1 <= hypothesis.value <= 36500
                    or operand.unit != "DAYS"
                    or operand.currency is not None
                    or operand.value is None
                    or Decimal(operand.value) != Decimal(hypothesis.value)
                    or not operand.source_refs
                    or any(
                        not _scenario_ref(ref, scenario, response) for ref in operand.source_refs
                    )
                    or set(operand.source_refs) != set(hypothesis.source_refs)
                ):
                    return "CALCULATION_CONTEXT_MISMATCH"
    return None


def _signature(candidate: GuidanceCandidate) -> dict[str, Any]:
    # A known identical coverage may arrive via either canonical source. Select
    # neither competing calculation when their preserved results actually differ.
    return candidate.model_dump(
        mode="json", exclude={"ref", "canonical_identity", "contract_label", "coverage_label"}
    )


def _scenario_contexts(response: LocalGuidanceResponse) -> dict[str, list[_Selection]] | None:
    raw_count = sum(
        len(c.scenarios) + sum(len(case.scenarios) for case in c.cases) for c in response.candidates
    )
    if raw_count > MAX_SCENARIO_RECORDS:
        return None
    contexts: dict[str, list[_Selection]] = defaultdict(list)
    fingerprints: dict[str, set[str]] = defaultdict(set)
    selections = 0
    for candidate in response.candidates:
        by_key: dict[str, list[GuidanceScenario]] = defaultdict(list)
        for scenario in (
            *candidate.scenarios,
            *(s for case in candidate.cases for s in case.scenarios),
        ):
            by_key[scenario.scenario_key].append(scenario)
            fingerprints[scenario.scenario_key].add(
                json.dumps(
                    [h.model_dump(mode="json") for h in scenario.hypotheses],
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
        selections += len(by_key)
        if selections > MAX_SCENARIO_SELECTIONS or len(fingerprints) > MAX_SCENARIO_CONTEXTS:
            return None
        for key, scenarios in by_key.items():
            first = scenarios[0]
            failure = (
                "SCENARIO_ESTIMATE_CONFLICT" if any(s != first for s in scenarios[1:]) else None
            )
            contexts[key].append(_Selection(candidate, first, failure))
    for key, values in contexts.items():
        if len(fingerprints[key]) != 1:
            contexts[key] = [
                _Selection(value.candidate, value.scenario, "SCENARIO_HYPOTHESES_CONFLICT")
                for value in values
            ]
    return dict(contexts)


def fixed_subtotal_projection(response: LocalGuidanceResponse) -> dict[str, Any]:
    """Return proposed DTO dictionaries, without changing the input snapshot.

    Actual points and matching explicit plans have separate contexts. Multiple
    payouts within a contract and uncanonicalized private contracts remain omitted.
    At least two different canonical contracts must contribute in one currency.
    Scenario expansion is bounded before trace inspection; exhausting that budget
    preserves the actual subtotals and emits explicit scenario omission records.
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

    canonical_keys = {
        id(candidate): key for key, candidates in grouped.items() for candidate in candidates
    }
    fixed_by_currency: dict[str | None, set[_RefKey]] = defaultdict(set)
    for key, candidates in grouped.items():
        for candidate in candidates:
            if candidate.benefit_kind == "FIXED":
                fixed_by_currency[candidate.estimate.currency].add(key)

    def project(selections: list[_Selection], scenario_key: str | None) -> dict[str, Any]:
        selected_groups: dict[_RefKey, list[_Selection]] = defaultdict(list)
        for selection in selections:
            selected_groups[canonical_keys[id(selection.candidate)]].append(selection)
        omissions: list[dict[str, Any]] = []
        accepted: dict[str, list[tuple[_RefKey, _Selection]]] = defaultdict(list)

        def omit(key: _RefKey, selection: _Selection, reason: str) -> None:
            omissions.append(
                {
                    "ref": _reference(key),
                    "case_key": _case_key(selection.candidate),
                    "scenario_key": scenario_key,
                    "currency": selection.estimate.currency,
                    "benefit_kind": selection.candidate.benefit_kind,
                    "reason_code": reason,
                }
            )

        for key, choices in sorted(selected_groups.items()):
            selection = choices[0]
            candidate = selection.candidate
            reason = _reason(selection, response)
            if key in invalid_aliases:
                reason = "CANONICAL_IDENTITY_CONFLICT"
            elif any(
                _signature(other.candidate) != _signature(candidate)
                or other.scenario != selection.scenario
                for other in choices[1:]
            ):
                reason = "CANONICAL_DUPLICATE_CONFLICT"
            elif candidate.benefit_kind == "FIXED":
                if len(contract_coverages[contract(key[:2])]) > 1:
                    reason = "SAME_CONTRACT_COMBINATION_UNRESOLVED"
                elif key[0] != "OPERATIONAL_RIDER":
                    reason = "CANONICAL_CONTRACT_UNRESOLVED"
            if reason is not None:
                omit(key, selection, reason)
            else:
                assert selection.estimate.currency is not None
                accepted[selection.estimate.currency].append((key, selection))

        for values in accepted.values():
            if len(values) < 2:
                for key, selection in values:
                    omit(key, selection, "INSUFFICIENT_COMPATIBLE_ADDENDS")

        subtotals = []
        for currency, values in sorted(accepted.items()):
            if len(values) < 2:
                continue
            items: list[dict[str, Any]] = []
            scopes: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for key, selection in values:
                candidate, estimate = selection.candidate, selection.estimate
                trace = estimate.trace
                assert trace is not None
                component = {
                    "ref": _reference(key),
                    "case_key": _case_key(candidate),
                    "scenario_key": scenario_key,
                }
                items.append(
                    {
                        **component,
                        "amount": estimate.amount,
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
                        *estimate.assumptions,
                        *(candidate.cases[0].assumptions if candidate.cases else ()),
                    }
                ):
                    scopes[code].append(component)
            # Money <=80 characters and <=1000 input candidates: 164 digits
            # preserve mixed-scale Decimal sums without implicit rounding.
            arithmetic = Context(prec=164)
            for signal in arithmetic.traps:
                arithmetic.traps[signal] = True
            with localcontext(arithmetic):
                total = sum((Decimal(item["amount"]) for item in items), Decimal(0))
            amount = decimal_text(total)
            if amount is None or len(amount) > 80:
                for key, selection in values:
                    omit(key, selection, "SUBTOTAL_AMOUNT_LIMIT_EXCEEDED")
                continue
            scenario = values[0][1].scenario
            contributing = {key for key, _ in values}
            subtotal = {
                "revision": SUBTOTAL_REVISION,
                "currency": currency,
                "amount": amount,
                "basis": "ASSUMED_COMBINATION",
                "conditional": True,
                "scenario_key": scenario_key,
                "hypotheses": []
                if scenario is None
                else [h.model_dump(mode="json") for h in scenario.hypotheses],
                "partial": any(
                    item["benefit_kind"] == "FIXED" and item["currency"] in (None, currency)
                    for item in omissions
                )
                or scenario_key is not None
                and bool((fixed_by_currency[currency] | fixed_by_currency[None]) - contributing),
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

    result = project([_Selection(candidate) for candidate in response.candidates], None)
    contexts = _scenario_contexts(response)
    if contexts is None:
        for key, candidates in sorted(grouped.items()):
            planned_candidate = next(
                (c for c in candidates if c.scenarios or any(case.scenarios for case in c.cases)),
                None,
            )
            if planned_candidate is not None:
                result["subtotal_omissions"].append(
                    {
                        "ref": _reference(key),
                        "case_key": _case_key(planned_candidate),
                        "scenario_key": None,
                        "currency": planned_candidate.estimate.currency,
                        "benefit_kind": planned_candidate.benefit_kind,
                        "reason_code": "SCENARIO_SUBTOTAL_BUDGET_EXCEEDED",
                    }
                )
    else:
        for scenario_key, selections in sorted(contexts.items()):
            projected = project(selections, scenario_key)
            result["fixed_subtotals"].extend(projected["fixed_subtotals"])
            result["subtotal_omissions"].extend(projected["subtotal_omissions"])
    return result
