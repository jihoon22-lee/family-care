#!/usr/bin/env python3
"""Execute local or historical policy against the frozen synthetic candidate cases."""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal
from time import perf_counter
from typing import Literal, cast
from uuid import UUID, uuid5

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import FactValue, MedicalEvent
from familycare_api.decisions.knowledge_domain import (
    KnowledgeCalculationPublication,
    KnowledgeCertificateEvidence,
    KnowledgeCitation,
    KnowledgeCoverageContext,
    KnowledgeDecisionContext,
    KnowledgeRulePublication,
    KnowledgeStatusInterval,
)
from familycare_api.decisions.knowledge_engine import DeterministicKnowledgeDecisionEngine

from scripts.claim_guidance_benchmark import (
    DEFAULT_CASES,
    BenchmarkCase,
    Prediction,
    load_cases,
    score_predictions,
)

_NAMESPACE = UUID("00000000-0000-4000-8000-000000009001")
_SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000009002"))
_MEMBER = UUID("00000000-0000-4000-8000-000000009003")
_FIELD_PATHS = {
    "event.kind": "MedicalEvent.classification",
    "event.performed": "MedicalEvent.performed",
    "event.planned": "MedicalEvent.planned",
    "event.confirmed": "MedicalEvent.diagnosis_confirmed",
    "event.admitted": "MedicalEvent.admission",
    "event.days": "MedicalEvent.admission_days",
    "event.eligible_cost": "Receipt.covered_amount",
    "event.other_cost": "Receipt.other_cost_status",
    "event.first_claim": "ClaimHistory.counted_occurrence",
    "event.remaining_uses": "ClaimHistory.remaining_occurrences",
}


def _id(value: str) -> UUID:
    return uuid5(_NAMESPACE, value)


def _record(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("invalid synthetic scenario")
    return value


def _value(field: str, value: object) -> object:
    if field == "event.first_claim":
        if not isinstance(value, bool):
            raise ValueError("invalid synthetic first-claim fact")
        return 0 if value else 1
    return value


def _rule(key: str, field: str, value: object) -> KnowledgeRulePublication:
    path = _FIELD_PATHS[field]
    citation = KnowledgeCitation(
        citation_key=f"synthetic-citation-{key}",
        terms_section_id=_id(f"section:{key}"),
        source_clause_id=_id(f"clause:{key}"),
        fact_id=None,
        evidence_purpose="ELIGIBILITY",
        page_start=2,
        page_end=2,
        source_text_sha256="a" * 64,
    )
    return KnowledgeRulePublication(
        publication_id=_id(f"rule:{key}"),
        rule_key=key,
        rule_kind="eligibility",
        required=True,
        result_reason_code="SYNTHETIC_CONDITION_MATCH",
        rule_document={
            "schema_version": "coverage-rule-v1",
            "rule_kind": "eligibility",
            "required": True,
            "input_field_paths": [path],
            "expression": {"op": "equals", "field": path, "value": _value(field, value)},
            "result_reason_code": "SYNTHETIC_CONDITION_MATCH",
            "evidence_ids": [citation.citation_key],
        },
        citations=(citation,),
    )


def _coverage(case: BenchmarkCase, raw: Mapping[str, object]) -> KnowledgeCoverageContext:
    key = str(raw["coverage_key"])
    primary = _rule(f"{key}-relevance", str(raw["event_field"]), raw["event_value"])
    rules = [primary]
    if "required_field" in raw:
        rules.append(_rule(f"{key}-condition", str(raw["required_field"]), raw["required_value"]))
    status = str(raw["status"])
    intervals: tuple[KnowledgeStatusInterval, ...] = ()
    current: Literal["active", "terminated", "unknown"] = "unknown"
    if status in {"active", "active_at_event", "terminated"}:
        current = "active" if status == "active" else "terminated"
        intervals = (
            KnowledgeStatusInterval(
                effective_from=date(2026, 1, 1),
                effective_through=date(2026, 12, 31),
                decision="MATCH",
                confirmed_status="terminated" if status == "terminated" else "active",
                authority="REVIEWED_STATUS_DOCUMENT",
            ),
        )
    amount = Decimal("1")
    calculation = None
    spec = _record(raw["calculation"]) if "calculation" in raw else {}
    if spec.get("kind") == "fixed":
        amount = Decimal(str(spec["amount"]))
        citation = primary.citations[0]
        calculation = KnowledgeCalculationPublication(
            publication_id=_id(f"calculation:{key}"),
            calculation_key=key,
            calculation_kind="FIXED",
            result_reason_code="SYNTHETIC_FIXED_AMOUNT",
            calculation_document={
                "schema_version": "coverage-rule-v1",
                "rule_kind": "fixed_amount",
                "required": False,
                "input_field_paths": ["Rider.insured_amount"],
                "calculation": {
                    "op": "add",
                    "args": [{"field": "Rider.insured_amount"}, {"value": 0}],
                },
                "result_reason_code": "SYNTHETIC_FIXED_AMOUNT",
                "evidence_ids": [citation.citation_key],
            },
            citations=(citation,),
        )
    return KnowledgeCoverageContext(
        knowledge_contract_id=_id(str(raw.get("contract_identity", case.contract_group))),
        knowledge_coverage_id=_id(key),
        contract_label="Sample Policy",
        coverage_label=key,
        benefit_type=cast(Literal["FIXED", "INDEMNITY"], raw["benefit_type"]),
        insured_amount=amount,
        currency="TST",
        contract_start=date(2026, 1, 1),
        contract_end=date(2026, 12, 31),
        disposition="PUBLISHED",
        subject_binding_decision="MATCH" if raw["subject"] == "same_member" else "NO_MATCH",
        enrollment_decision="MATCH" if raw["enrollment"] == "confirmed" else "NO_MATCH",
        component_classification="BENEFIT_COVERAGE",
        mapping_applicability="APPLICABLE",
        mapping_enrollment_decision="MATCH",
        document_identity_decision="MATCH",
        edition_applicability_decision="MATCH",
        section_mapping_decision="MATCH",
        overall_mapping_decision="MATCH",
        current_confirmation_decision=(
            "NO_MATCH" if status == "conflicting" else None if status == "unknown" else "MATCH"
        ),
        current_confirmed_status=current,
        status_intervals=intervals,
        rules=tuple(rules),
        calculation=calculation,
        certificate_amount_decision="MATCH",
        certificate_amount_evidence_state="DIRECT",
        certificate_evidence=(KnowledgeCertificateEvidence("Sample Certificate", (1,)),),
    )


def case_inputs(case: BenchmarkCase) -> tuple[MedicalEvent, KnowledgeDecisionContext]:
    """Build inputs solely from scenario parameters; never consult expected labels."""
    parameters = case.scenario_parameters
    raw_facts = _record(parameters["event_facts"])
    raw_coverages = parameters["coverages"]
    if not isinstance(raw_coverages, list) or not case.case_id.startswith("synthetic-"):
        raise ValueError("only frozen synthetic scenarios are supported")
    event = MedicalEvent(
        id=_id(case.case_id),
        household_space_id=_SCOPE.household_space_id,
        family_member_id=_MEMBER,
        mode="post_treatment",
        situation="Synthetic structured benchmark event",
        event_date=date(2026, 6, 1),
        visit_date=date(2026, 6, 1),
        facts={
            _FIELD_PATHS[key]: FactValue(_value(key, value), "user", ())
            for key, value in raw_facts.items()
            if key in _FIELD_PATHS
        },
    )
    context = KnowledgeDecisionContext(
        household_space_id=_SCOPE.household_space_id,
        family_member_id=_MEMBER,
        knowledge_import_run_id=_id(f"catalog:{case.case_id}"),
        rule_import_run_id=_id(f"rules:{case.case_id}"),
        status_projection_digest_sha256="b" * 64,
        coverages=tuple(_coverage(case, _record(raw)) for raw in raw_coverages),
        normalizers=(),
        receipt_currency="TST" if "event.eligible_cost" in raw_facts else None,
    )
    return event, context


def predict_cases(
    cases: Sequence[BenchmarkCase],
    *,
    engine: Literal["local", "legacy"],
    latency_samples: list[float] | None = None,
) -> tuple[Prediction, ...]:
    predictions: list[Prediction] = []
    for case in cases:
        started = perf_counter()
        event, context = case_inputs(case)
        keys = {_id(key): key for key in case.candidate_pool}
        if engine == "local":
            from familycare_api.guidance.engine import LocalGuidanceEngine

            guidance = LocalGuidanceEngine().evaluate(_SCOPE, event, context)
            primary = tuple(
                keys[item.ref.coverage_id]
                for item in guidance.candidates
                if item.group == "PRIMARY"
            )
            conditional = tuple(
                keys[item.ref.coverage_id]
                for item in guidance.candidates
                if item.group == "CONDITIONAL"
            )
            held = not (primary or conditional) and guidance.outcome in {
                "KNOWLEDGE_PENDING",
                "INPUT_UNRESOLVED",
            }
        else:
            legacy = DeterministicKnowledgeDecisionEngine().evaluate(
                _SCOPE, event, context, run_id=_id(f"run:{case.case_id}")
            )
            primary = tuple(
                keys[item.knowledge_coverage_id]
                for item in legacy.candidates
                if item.result == "MATCH"
            )
            conditional = tuple(
                keys[item.knowledge_coverage_id]
                for item in legacy.candidates
                if item.result == "UNKNOWN"
            )
            held = False
        predictions.append(Prediction(case.case_id, primary, conditional, held=held))
        if latency_samples is not None:
            latency_samples.append((perf_counter() - started) * 1000)
    return tuple(predictions)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engine", choices=("local", "legacy"), default="local")
    parser.add_argument("--repetitions", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 1000:
        parser.error("repetitions must be between 1 and 1000")
    cases = load_cases(DEFAULT_CASES)
    samples: list[float] = []
    predictions: tuple[Prediction, ...] = ()
    for _ in range(args.repetitions):
        predictions = predict_cases(cases, engine=args.engine, latency_samples=samples)
    results = {
        split: score_predictions(cases, predictions, split=split) for split in ("dev", "holdout")
    }
    ordered = sorted(samples)
    report: dict[str, object] = {key: value.as_dict() for key, value in results.items()}
    report["latency"] = {
        "scope": "synthetic_context_construction_and_engine_only",
        "samples": len(ordered),
        "p50_ms": ordered[math.ceil(len(ordered) * 0.50) - 1],
        "p95_ms": ordered[math.ceil(len(ordered) * 0.95) - 1],
    }
    print(json.dumps(report, sort_keys=True))
    return 0 if all(result.passed for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
