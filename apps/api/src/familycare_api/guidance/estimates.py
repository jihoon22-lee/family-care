"""Calculate documented estimates with source-neutral inputs and immutable traces."""

import hashlib
import json
from collections.abc import Mapping
from decimal import Decimal
from typing import Literal, cast

from familycare_api.clauses.dsl import CompiledCalculation, validate_rule_document
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_domain import KnowledgeFactContext
from familycare_api.guidance.calculation_runtime import (
    CalculationInput,
    CalculationSourceRef,
    CalculationUnit,
    evaluate_calculation,
)
from familycare_api.guidance.calculation_source import (
    CalculationSourceError,
    bind_calculation_source,
)
from familycare_api.guidance.domain import GuidanceCoverageInput
from familycare_api.guidance.event_facts import EventFactRead
from familycare_api.guidance.expense_projection import RECEIPT_FIELDS
from familycare_api.guidance.models import GuidanceEstimate
from familycare_api.guidance.trace_projection import (
    calculation_trace,
    decimal_text,
    runtime_reference,
)

_LABELS = {
    "Rider.insured_amount": "가입금액",
    "MedicalEvent.admission_days": "입원 일수",
    "MedicalEvent.reduction_applies": "약관의 감액 조건 해당 여부",
    "Receipt.confirmed_amount": "보장대상 확인 비용",
    "Receipt.covered_amount": "보장대상 비용",
    "ClaimHistory.counted_occurrence": "이전 지급 횟수",
}
_UNITS: dict[str, CalculationUnit] = {
    "Rider.insured_amount": "MONEY",
    "MedicalEvent.admission_days": "DAYS",
    "MedicalEvent.reduction_applies": "BOOLEAN",
    "Receipt.confirmed_amount": "MONEY",
    "Receipt.covered_amount": "MONEY",
    "ClaimHistory.counted_occurrence": "COUNT",
    "ClaimHistory.remaining_occurrences": "COUNT",
}


def formula_text(node: CompiledCalculation) -> str:
    values = [
        formula_text(item)
        if isinstance(item, CompiledCalculation)
        else _LABELS.get(item, item)
        if isinstance(item, str)
        else cast(str, decimal_text(item))
        if isinstance(item, Decimal)
        else "?"
        for item in node.operands
    ]
    symbols = {"add": " + ", "subtract": " - ", "multiply": " × "}
    if node.operator in symbols:
        return "(" + symbols[node.operator].join(values) + ")"
    if node.operator == "round":
        return f"반올림[{node.rounding}]({', '.join(values)})"
    if node.operator == "if":
        return f"조건[{values[0]}](참: {values[1]}, 거짓: {values[2]})"
    return f"{'최솟값' if node.operator == 'min' else '최댓값'}({', '.join(values)})"


def _number(value: object) -> Decimal | None:
    return Decimal(value) if type(value) is int else value if isinstance(value, Decimal) else None


def calculation_currency(coverage: GuidanceCoverageInput) -> str | None:
    """Choose a source denomination without manufacturing contract currency proof."""
    publication = coverage.calculation
    if (
        coverage.canonical_identity is not None
        and "currency" in coverage.canonical_identity.field_conflicts
    ):
        return None
    if publication is None or publication.source_currency is None:
        return coverage.currency
    try:
        document = validate_rule_document(
            publication.calculation_document, tuple(c.citation_key for c in publication.citations)
        )
    except ValueError:
        return coverage.currency
    fields = set(document.referenced_fields)
    if not fields & {"Rider.insured_amount", *RECEIPT_FIELDS}:
        return publication.source_currency
    if (
        coverage.currency is None
        and publication.source_kind == "SEMANTIC_NODE"
        and publication.calculation_kind == "INDEMNITY"
        and "Rider.insured_amount" not in fields
        and fields & RECEIPT_FIELDS
        and publication.citations
        and all(c.lineage_valid for c in publication.citations)
    ):
        # The caller selects only registered costs in this currency. A missing or
        # differently denominated receipt remains unavailable, never converted.
        return publication.source_currency
    return coverage.currency


def calculation_inputs(
    fields: tuple[str, ...],
    event: MedicalEvent,
    facts: KnowledgeFactContext,
    coverage: GuidanceCoverageInput,
    event_read: EventFactRead,
    supporting_sources: Mapping[str, tuple[CalculationSourceRef, ...]],
) -> dict[str, CalculationInput]:
    result = {}
    source_currency = calculation_currency(coverage)
    for path in fields:
        unit = _UNITS.get(path, "UNKNOWN")
        currency = source_currency if unit == "MONEY" else None
        value: Decimal | bool | None = None
        provenance = "UNCONFIRMED"
        stale = False
        refs: tuple[CalculationSourceRef, ...] = ()
        if path == "Rider.insured_amount":
            value = coverage.insured_amount
            proof = coverage.contract_amount
            if proof is not None:
                refs = tuple(runtime_reference(ref) for ref in proof.source_refs)
                provenance = proof.amount_authority
                if (
                    coverage.certificate_amount_decision != "MATCH"
                    or coverage.certificate_amount_evidence_state != "DIRECT"
                    or proof.amount is None
                    or Decimal(proof.amount) != value
                    or proof.currency != coverage.currency
                    or proof.currency_authority
                    not in {
                        "PROGRAM_VERIFIED",
                        "USER_CONFIRMED",
                        "DOCUMENT_REVIEWED",
                    }
                ):
                    provenance = "CONFLICTING"
        else:
            fact = facts.get(path)
            if path == "ClaimHistory.counted_occurrence":
                fact = coverage.claim_history_counted_occurrence or fact
            if fact is not None:
                value = (
                    fact.value
                    if unit == "BOOLEAN" and type(fact.value) is bool
                    else _number(fact.value)
                )
                provenance, stale = fact.provenance, fact.stale
                refs = supporting_sources.get(path, ())
                if path.startswith("MedicalEvent."):
                    local = path in event_read.local_fact_paths
                    digest = hashlib.sha256(
                        (
                            event.situation
                            if local
                            else json.dumps(
                                {"field": path, "value": fact.value, "provenance": fact.provenance},
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            )
                        ).encode()
                    ).hexdigest()
                    refs = (
                        CalculationSourceRef(
                            "EVENT_TEXT" if local else "EVENT_FACT", event.id, event.version, digest
                        ),
                    )
        result[path] = CalculationInput(value, unit, currency, provenance, refs, stale)
    return result


def estimate_coverage(
    event: MedicalEvent,
    facts: KnowledgeFactContext,
    coverage: GuidanceCoverageInput,
    event_read: EventFactRead,
    *,
    conditions: Literal["MATCH", "UNKNOWN"],
    assumptions: list[str],
    supporting_sources: Mapping[str, tuple[CalculationSourceRef, ...]] | None = None,
    partial_costs: bool = False,
    scenario_inputs: Mapping[str, CalculationInput] | None = None,
) -> GuidanceEstimate:
    publication = coverage.calculation
    if publication is None:
        return GuidanceEstimate(kind="UNAVAILABLE", reason_code="CALCULATION_NOT_PUBLISHED")
    if not publication.citations or any(not item.lineage_valid for item in publication.citations):
        return GuidanceEstimate(kind="UNAVAILABLE", reason_code="CALCULATION_CITATION_INVALID")
    evidence = tuple(item.evidence for item in publication.citations)
    estimate_assumptions = tuple(
        dict.fromkeys([*assumptions, *(["CONDITIONS_REMAIN"] if conditions == "UNKNOWN" else [])])
    )
    try:
        validated = validate_rule_document(
            publication.calculation_document, tuple(c.citation_key for c in publication.citations)
        )
        calculation = validated.calculation
        if calculation is None or publication.calculation_kind != coverage.benefit_type:
            raise ValueError("CALCULATION_METADATA_MISMATCH")
        formula = formula_text(calculation)
        currency = calculation_currency(coverage)
        binding = bind_calculation_source(publication, currency=currency)
    except CalculationSourceError as error:
        if error.reason_code == "CALCULATION_CURRENCY_MISMATCH":
            return GuidanceEstimate(
                kind="FORMULA",
                currency=publication.source_currency,
                formula=formula,
                missing_inputs=("Rider.currency",),
                assumptions=estimate_assumptions,
                reason_code="CALCULATION_CURRENCY_MISMATCH",
                evidence=evidence,
            )
        if calculation is not None and error.reason_code in {
            "CALCULATION_UNIT_MISMATCH",
            "CALCULATION_SOURCE_UNIT_UNSUPPORTED",
        }:
            unresolved = calculation_inputs(
                calculation.referenced_fields,
                event,
                facts,
                coverage,
                event_read,
                supporting_sources or {},
            )
            return GuidanceEstimate(
                kind="FORMULA",
                currency=currency,
                formula=formula,
                missing_inputs=tuple(
                    path for path, item in unresolved.items() if item.value is None
                ),
                assumptions=estimate_assumptions,
                reason_code=error.reason_code,
                evidence=evidence,
            )
        return GuidanceEstimate(kind="UNAVAILABLE", reason_code=error.reason_code)
    except ValueError, ArithmeticError:
        return GuidanceEstimate(kind="UNAVAILABLE", reason_code="CALCULATION_UNSUPPORTED")
    inputs = calculation_inputs(
        calculation.referenced_fields, event, facts, coverage, event_read, supporting_sources or {}
    )
    if scenario_inputs:
        inputs.update({path: item for path, item in scenario_inputs.items() if path in inputs})
    evaluated = evaluate_calculation(
        binding.calculation,
        inputs,
        source=binding.source,
        unit_hints=binding.unit_hints,
        scenario_inputs=scenario_inputs,
    )
    trace = calculation_trace(evaluated, binding.formula_digest_sha256)
    if evaluated.amount is None or binding.status != "BOUND":
        missing = (
            *evaluated.missing_paths,
            *(("Rider.currency",) if binding.status != "BOUND" else ()),
        )
        return GuidanceEstimate(
            kind="FORMULA",
            currency=currency,
            formula=formula,
            missing_inputs=tuple(dict.fromkeys(missing)),
            assumptions=estimate_assumptions,
            reason_code="CALCULATION_SOURCE_UNSUPPORTED"
            if evaluated.status == "FAILED"
            else "CALCULATION_INPUT_NEEDED",
            evidence=evidence,
            trace=trace,
        )
    receipt_based = bool(set(calculation.referenced_fields) & RECEIPT_FIELDS)
    partial = receipt_based and partial_costs
    return GuidanceEstimate(
        kind="FORMULA" if partial else "POINT",
        currency=evaluated.currency,
        amount=None if partial else decimal_text(evaluated.amount),
        partial_amount=decimal_text(evaluated.amount) if partial else None,
        basis="CONFIRMED_COST_SUBSET"
        if partial
        else "REGISTERED_COSTS"
        if receipt_based
        else "DOCUMENT_FORMULA",
        formula=formula,
        missing_inputs=("Receipt.unresolved_costs",) if partial else (),
        assumptions=estimate_assumptions,
        reason_code="CONFIRMED_COST_SUBSET_ESTIMATE" if partial else "DOCUMENT_BASED_ESTIMATE",
        evidence=evidence,
        trace=trace,
    )
