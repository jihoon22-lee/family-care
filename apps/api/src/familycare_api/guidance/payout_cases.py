"""Keep each original payout case intact under one canonical coverage."""

import hashlib
import json
from dataclasses import replace
from decimal import Decimal
from typing import Literal

from familycare_api.guidance.domain import (
    GuidanceCalculationInput,
    GuidanceCoverageInput,
    GuidancePayoutCaseInput,
    GuidanceRuleInput,
)
from familycare_api.guidance.models import (
    GuidanceCandidate,
    GuidanceEstimate,
    GuidancePayoutCase,
    GuidanceSemanticEvidence,
)
from familycare_api.guidance.semantic_binding import BoundSemanticRoot

MAX_PAYOUT_CASES = 32


def source_payout_case(
    coverage: GuidanceCoverageInput,
    case: GuidancePayoutCaseInput | None = None,
    *,
    namespace: bool = False,
) -> GuidancePayoutCaseInput:
    """Identify a case without transplanting its source coverage's facts."""
    source_key = (
        case.case_key
        if case is not None
        else [
            str(coverage.calculation.publication_id) if coverage.calculation else None,
            [(str(rule.publication_id), rule.rule_key) for rule in coverage.rules],
        ]
    )
    key = (
        hashlib.sha256(
            json.dumps(
                [coverage.ref.model_dump(mode="json"), source_key],
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        if namespace or case is None
        else case.case_key
    )
    if case is not None:
        return replace(case, case_key=key, source_ref=coverage.ref)
    return GuidancePayoutCaseInput(
        case_key=key,
        rules=coverage.rules,
        calculation=coverage.calculation,
        benefit_type=coverage.benefit_type,
        knowledge_incomplete=coverage.knowledge_incomplete,
        source_ref=coverage.ref,
    )


def case_coverage(
    coverage: GuidanceCoverageInput, case: GuidancePayoutCaseInput
) -> GuidanceCoverageInput:
    kind_conflict = (
        coverage.benefit_type != "UNKNOWN"
        and case.benefit_type != "UNKNOWN"
        and coverage.benefit_type != case.benefit_type
    )
    return replace(
        coverage,
        rules=case.rules,
        calculation=case.calculation,
        cases=(),
        benefit_type=coverage.benefit_type if kind_conflict else case.benefit_type,
        knowledge_incomplete=case.knowledge_incomplete or kind_conflict,
    )


def semantic_payout_cases(
    roots: tuple[BoundSemanticRoot, ...],
    *,
    operational_rules: tuple[GuidanceRuleInput, ...] = (),
    operational_calculations: tuple[GuidanceCalculationInput, ...] = (),
) -> tuple[GuidancePayoutCaseInput, ...]:
    def rule_identity(rule: GuidanceRuleInput) -> str | None:
        if not rule.citations or any(
            not citation.lineage_valid
            or not isinstance(citation.evidence, GuidanceSemanticEvidence)
            for citation in rule.citations
        ):
            return None
        addresses = sorted(
            (
                str(e.document_version_id),
                str(e.generation_id),
                e.source_node_id,
                e.page_start,
                e.start,
                e.end,
                e.source_layer,
                e.bbox,
                e.source_sha256,
            )
            for citation in rule.citations
            if isinstance(e := citation.evidence, GuidanceSemanticEvidence)
        )
        return json.dumps(
            [
                addresses,
                rule.rule_kind,
                rule.required,
                rule.classification_scopes,
                {
                    key: value
                    for key, value in rule.rule_document.items()
                    if key not in {"evidence_ids", "result_reason_code"}
                },
            ],
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )

    # A standalone condition is already represented only when another payout
    # references both its actual original address and the same condition contract.
    included = {
        identity
        for root in roots
        if root.calculation is not None
        for rule in root.rules
        if (identity := rule_identity(rule)) is not None
    }
    semantic = tuple(
        GuidancePayoutCaseInput(
            case_key=hashlib.sha256(
                json.dumps(root.original_anchor, default=str, separators=(",", ":")).encode()
            ).hexdigest(),
            rules=root.rules,
            calculation=root.calculation,
            benefit_type=root.benefit_kind,
            knowledge_incomplete=not root.complete,
        )
        for root in roots
        if root.calculation is not None
        or not root.rules
        or not root.complete
        or not all(rule_identity(rule) in included for rule in root.rules)
    )
    if not semantic and len(operational_calculations) <= 1:
        return ()
    # A new semantic condition does not supersede an existing versioned payout.
    # The legacy source already groups these rules at Rider scope; preserve that
    # grouping within its own case, without importing them into other roots.
    return (
        *semantic,
        *(
            GuidancePayoutCaseInput(
                case_key=str(calculation.publication_id),
                rules=operational_rules,
                calculation=calculation,
                benefit_type=calculation.calculation_kind,
            )
            for calculation in operational_calculations
        ),
    )


def combine_payout_cases(
    results: tuple[tuple[GuidancePayoutCaseInput, GuidanceCandidate], ...],
    relation: Literal["MUTUALLY_EXCLUSIVE", "UNRESOLVED"],
) -> GuidanceCandidate:
    first = results[0][1]
    cases = tuple(
        GuidancePayoutCase(
            case_key=case.case_key,
            source_ref=case.source_ref,
            contract_amount=candidate.contract_amount,
            freshness=candidate.freshness,
            benefit_kind=candidate.benefit_kind,
            condition_result=candidate.condition_result,
            reason_codes=candidate.reason_codes,
            assumptions=candidate.assumptions,
            conditions=candidate.conditions,
            relevance=candidate.relevance,
            estimate=candidate.estimate,
            scenarios=candidate.scenarios,
            questions=candidate.questions,
        )
        for case, candidate in results
    )
    if len(cases) == 1:
        return first.model_copy(update={"cases": cases})
    estimate = GuidanceEstimate(kind="UNAVAILABLE", reason_code="MULTIPLE_PAYOUT_CASES")
    estimates = tuple(case.estimate for case in cases)
    currencies = {item.currency for item in estimates}
    assumptions = {item.assumptions for item in estimates}
    formulas = "; ".join(item.formula or "" for item in estimates)
    evidence = tuple(dict.fromkeys(e for item in estimates for e in item.evidence))
    if (
        relation == "MUTUALLY_EXCLUSIVE"
        and all(item.kind == "POINT" and item.amount is not None for item in estimates)
        and len(currencies) == len(assumptions) == 1
        and None not in currencies
        and len(formulas) <= 4000
        and len(evidence) <= 64
        and len(estimates[0].assumptions) < 32
    ):
        amounts = [Decimal(item.amount) for item in estimates if item.amount is not None]
        from familycare_api.guidance.trace_projection import decimal_text

        estimate = GuidanceEstimate(
            kind="RANGE",
            currency=estimates[0].currency,
            lower=decimal_text(min(amounts)),
            upper=decimal_text(max(amounts)),
            formula=formulas,
            evidence=evidence,
            basis="SOURCE_ALTERNATIVES",
            assumptions=tuple(dict.fromkeys((*estimates[0].assumptions, "SOURCE_CASE_APPLIES"))),
            reason_code="SOURCE_ALTERNATIVE_ESTIMATE",
        )
    return first.model_copy(
        update={
            "cases": cases,
            "case_relation": relation,
            "estimate": estimate,
            "condition_result": "UNKNOWN",
            "group": "CONDITIONAL",
            "contract_amount": first.contract_amount
            if all(candidate.contract_amount == first.contract_amount for _, candidate in results)
            else None,
            "freshness": first.freshness
            if all(candidate.freshness == first.freshness for _, candidate in results)
            else "STATUS_UNRESOLVED",
            "benefit_kind": first.benefit_kind
            if all(candidate.benefit_kind == first.benefit_kind for _, candidate in results)
            else "UNKNOWN",
            "assumptions": tuple(
                assumption
                for assumption in first.assumptions
                if all(assumption in candidate.assumptions for _, candidate in results)
            ),
            "scenarios": (),
            "conditions": (),
            "questions": tuple(dict.fromkeys(q for case in cases for q in case.questions))[:64],
            "reason_codes": tuple(dict.fromkeys((*first.reason_codes, "MULTIPLE_PAYOUT_CASES"))),
        }
    )
