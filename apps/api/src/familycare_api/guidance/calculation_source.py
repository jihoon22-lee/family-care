"""Bind published calculation dimensions without promoting arbitrary numbers to money.

The caller replays publication authority and supplies verified currency. This pure
adapter rechecks the data-only document and source identities, and binds original
AST addresses to dimensions; it never supplies event, receipt or insured amounts.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from familycare_api.clauses.dsl import CompiledCalculation, ValidatedRule, validate_rule_document
from familycare_api.guidance.calculation_runtime import (
    CalculationSource,
    CalculationSourceRef,
    CalculationUnit,
    CalculationUnitHint,
)
from familycare_api.guidance.domain import GuidanceCalculationInput, GuidanceCitation
from familycare_api.guidance.models import GuidanceEvidence, GuidanceSemanticEvidence

CALCULATION_SOURCE_REVISION = "guidance-calculation-source-v1"
_CURRENCY = re.compile(r"[A-Z]{3}")
_SHA = re.compile(r"[0-9a-f]{64}")
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}")
_FIELDS: dict[str, CalculationUnit] = {
    "Rider.insured_amount": "MONEY",
    "Receipt.covered_amount": "MONEY",
    "Receipt.confirmed_amount": "MONEY",
    "MedicalEvent.admission_days": "DAYS",
    "ClaimHistory.counted_occurrence": "COUNT",
    "ClaimHistory.remaining_occurrences": "COUNT",
}
type CalculationBasis = Literal[
    "FIXED_AMOUNT",
    "INSURED_AMOUNT",
    "INSURED_RATIO",
    "DAILY_INSURED_AMOUNT",
    "RECEIPT_AMOUNT",
    "NUMERIC_EXPRESSION",
]


class CalculationSourceError(ValueError):
    def __init__(self, reason_code: str = "CALCULATION_SOURCE_INVALID") -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True, repr=False)
class CalculationSourceBinding:
    source: CalculationSource
    calculation: CompiledCalculation
    unit_hints: Mapping[str, CalculationUnitHint]
    formula_digest_sha256: str
    basis: CalculationBasis
    payout_unit: CalculationUnit
    status: Literal["BOUND", "CURRENCY_UNRESOLVED"]
    reason_codes: tuple[str, ...] = ()


def _require(condition: bool, reason: str = "CALCULATION_SOURCE_INVALID") -> None:
    if not condition:
        raise CalculationSourceError(reason)


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _bounded_document(document: Mapping[str, object]) -> None:
    pending: list[tuple[object, int]] = [(document, 0)]
    remaining = 4096
    while pending:
        value, depth = pending.pop()
        remaining -= 1
        _require(remaining >= 0 and depth <= 40, "CALCULATION_SOURCE_BUDGET_EXCEEDED")
        if isinstance(value, Mapping):
            _require(len(value) <= 32 and all(type(k) is str for k in value))
            pending.extend((v, depth + 1) for v in value.values())
        elif isinstance(value, list | tuple):
            _require(len(value) <= 32)
            pending.extend((v, depth + 1) for v in value)
        elif isinstance(value, str):
            _require(len(value) <= 8192)
        elif isinstance(value, Decimal | int | float) and not isinstance(value, bool):
            _require(len(str(value)) <= 128, "CALCULATION_SOURCE_BUDGET_EXCEEDED")
        else:
            _require(value is None or isinstance(value, bool | UUID))


def _formula(node: object, *, remaining: list[int], depth: int = 0) -> object:
    remaining[0] -= 1
    _require(remaining[0] >= 0 and depth <= 16, "CALCULATION_SOURCE_BUDGET_EXCEEDED")
    if isinstance(node, CompiledCalculation):
        return {
            "op": node.operator,
            "args": [_formula(v, remaining=remaining, depth=depth + 1) for v in node.operands],
            **({"rounding": node.rounding} if node.rounding is not None else {}),
        }
    if isinstance(node, Decimal):
        return {"value": str(node)}
    _require(isinstance(node, str) and node in _FIELDS, "CALCULATION_SOURCE_UNIT_UNSUPPORTED")
    return {"field": node}


def _validated(
    publication: GuidanceCalculationInput,
) -> tuple[ValidatedRule, tuple[GuidanceCitation, ...]]:
    _require(isinstance(publication, GuidanceCalculationInput))
    _require(isinstance(publication.publication_id, UUID) and publication.publication_id.int != 0)
    _require(
        type(publication.calculation_key) is str and 1 <= len(publication.calculation_key) <= 256
    )
    _require(publication.calculation_kind in {"FIXED", "INDEMNITY"})
    _require(
        isinstance(publication.citations, tuple) and 1 <= len(publication.citations) <= 64,
        "CALCULATION_CITATION_INVALID",
    )
    origins = {
        citation.evidence.review_job_id
        for citation in publication.citations
        if isinstance(citation, GuidanceCitation)
        and isinstance(citation.evidence, GuidanceSemanticEvidence)
    }
    _require(len(origins) <= 1, "CALCULATION_SOURCE_REFERENCE_MISMATCH")
    keys = []
    for citation in publication.citations:
        _require(
            isinstance(citation, GuidanceCitation) and citation.lineage_valid is True,
            "CALCULATION_CITATION_INVALID",
        )
        _require(
            type(citation.citation_key) is str and 1 <= len(citation.citation_key) <= 256,
            "CALCULATION_CITATION_INVALID",
        )
        evidence = citation.evidence
        _require(
            isinstance(evidence, GuidanceEvidence | GuidanceSemanticEvidence),
            "CALCULATION_CITATION_INVALID",
        )
        _require(
            evidence.publication_id == publication.publication_id, "CALCULATION_CITATION_INVALID"
        )
        _require(
            isinstance(evidence.source_sha256, str)
            and _SHA.fullmatch(evidence.source_sha256) is not None,
            "CALCULATION_CITATION_INVALID",
        )
        if publication.source_kind == "SEMANTIC_NODE":
            _require(isinstance(evidence, GuidanceSemanticEvidence), "CALCULATION_CITATION_INVALID")
            assert isinstance(evidence, GuidanceSemanticEvidence)
            GuidanceSemanticEvidence.model_validate(
                evidence.model_dump(warnings=False), strict=True
            )
            _require(
                all(
                    identifier.int != 0
                    for identifier in (
                        evidence.citation_id,
                        evidence.document_version_id,
                        evidence.terms_edition_id,
                        evidence.generation_id,
                    )
                ),
                "CALCULATION_SOURCE_REFERENCE_MISMATCH",
            )
            _require(
                publication.semantic_node_id == publication.calculation_key == evidence.root_node_id
                and _IDENTIFIER.fullmatch(evidence.root_node_id) is not None
                and _IDENTIFIER.fullmatch(evidence.source_node_id) is not None
                and citation.citation_key == str(evidence.citation_id),
                "CALCULATION_SOURCE_REFERENCE_MISMATCH",
            )
        elif publication.source_kind in {"PRIVATE_RULE_PUBLICATION", "OPERATIONAL_RULE_VERSION"}:
            _require(
                isinstance(evidence, GuidanceEvidence) and publication.semantic_node_id is None,
                "CALCULATION_CITATION_INVALID",
            )
            assert isinstance(evidence, GuidanceEvidence)
            GuidanceEvidence.model_validate(evidence.model_dump(warnings=False), strict=True)
            expected_kind = (
                "TERMS_SECTION"
                if publication.source_kind == "PRIVATE_RULE_PUBLICATION"
                else "OPERATIONAL_EVIDENCE"
            )
            _require(
                evidence.kind == expected_kind and evidence.evidence_id.int != 0,
                "CALCULATION_CITATION_INVALID",
            )
            if evidence.kind == "OPERATIONAL_EVIDENCE":
                _require(
                    citation.citation_key == str(evidence.evidence_id),
                    "CALCULATION_SOURCE_REFERENCE_MISMATCH",
                )
        else:
            raise CalculationSourceError
        keys.append(citation.citation_key)
    _require(len(set(keys)) == len(keys), "CALCULATION_CITATION_INVALID")
    _bounded_document(publication.calculation_document)
    validated = validate_rule_document(publication.calculation_document, keys)
    _require(
        validated.calculation is not None
        and validated.rule_kind in {"fixed_amount", "rate_amount", "deductible", "limit"}
        and validated.result_reason_code == publication.result_reason_code,
    )
    used = {str(key) for key in validated.evidence_ids}
    return validated, tuple(c for c in publication.citations if c.citation_key in used)


def _source_refs(
    publication: GuidanceCalculationInput,
    citations: tuple[GuidanceCitation, ...],
    formula_digest: str,
) -> tuple[CalculationSourceRef, ...]:
    refs: dict[tuple[str, UUID | str, int | str | None], CalculationSourceRef] = {}

    def add(
        kind: str, identifier: UUID | str, *, version: str | None = None, digest: str | None = None
    ) -> None:
        ref = CalculationSourceRef(kind, identifier, version=version, digest_sha256=digest)
        identity = (kind, identifier, version)
        _require(
            identity not in refs or refs[identity] == ref, "CALCULATION_SOURCE_REFERENCE_MISMATCH"
        )
        refs[identity] = ref
        _require(len(refs) <= 64, "CALCULATION_SOURCE_BUDGET_EXCEEDED")

    if publication.source_kind != "SEMANTIC_NODE":
        add(publication.source_kind, publication.publication_id, digest=formula_digest)
        for citation in citations:
            evidence = citation.evidence
            assert isinstance(evidence, GuidanceEvidence)
            add(evidence.kind, evidence.evidence_id, digest=evidence.source_sha256)
    else:
        semantic = tuple(
            c.evidence for c in citations if isinstance(c.evidence, GuidanceSemanticEvidence)
        )
        _require(bool(semantic), "CALCULATION_CITATION_INVALID")
        manifests = {e.manifest_sha256 for e in semantic}
        _require(
            len(manifests) == 1 and len({e.terms_edition_id for e in semantic}) == 1,
            "CALCULATION_SOURCE_REFERENCE_MISMATCH",
        )
        manifest = next(iter(manifests))
        review_id = semantic[0].review_job_id
        add(
            "GUIDANCE_REVIEW_PUBLICATION" if review_id is not None else "SEMANTIC_PUBLICATION",
            publication.publication_id,
            digest=manifest,
        )
        if review_id is not None:
            add("GUIDANCE_REVIEW_JOB", review_id)
        assert publication.semantic_node_id is not None
        add(
            "SEMANTIC_ROOT",
            publication.semantic_node_id,
            version=str(publication.publication_id),
            digest=manifest,
        )
        documents: dict[UUID, tuple[UUID, str]] = {}
        generations: dict[UUID, UUID] = {}
        for evidence in semantic:
            address = (evidence.generation_id, evidence.source_sha256)
            _require(
                documents.get(evidence.document_version_id, address) == address,
                "CALCULATION_SOURCE_REFERENCE_MISMATCH",
            )
            _require(
                generations.get(evidence.generation_id, evidence.document_version_id)
                == evidence.document_version_id,
                "CALCULATION_SOURCE_REFERENCE_MISMATCH",
            )
            documents[evidence.document_version_id] = address
            generations[evidence.generation_id] = evidence.document_version_id
            add(
                "SEMANTIC_CITATION",
                evidence.citation_id,
                version=str(publication.publication_id),
                digest=manifest,
            )
            add("DOCUMENT_VERSION", evidence.document_version_id, digest=evidence.source_sha256)
            add("DOCUMENT_STRUCTURE_GENERATION", evidence.generation_id)
            add("TERMS_EDITION", evidence.terms_edition_id)
            add(
                "SEMANTIC_SOURCE_NODE",
                evidence.source_node_id,
                version=str(evidence.generation_id),
                digest=evidence.source_sha256,
            )
    return tuple(
        sorted(
            refs.values(),
            key=lambda ref: (ref.source_kind, str(ref.source_id), str(ref.version or "")),
        )
    )


def _fields(value: object) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset({value})
    return (
        frozenset(value.referenced_fields)
        if isinstance(value, CompiledCalculation)
        else frozenset()
    )


class _UnitBinder:
    def __init__(
        self,
        publication: GuidanceCalculationInput,
        document_kind: str,
        currency: str | None,
        refs: tuple[CalculationSourceRef, ...],
    ) -> None:
        self.publication, self.document_kind, self.currency, self.refs = (
            publication,
            document_kind,
            currency,
            refs,
        )
        self.hints: dict[str, CalculationUnitHint] = {}
        self.calls = 0
        self.daily = False
        self.ratio = False

    def hint(self, path: str, unit: CalculationUnit) -> None:
        if unit == "MONEY" and self.currency is None:
            self.hints.pop(path, None)
        else:
            self.hints[path] = CalculationUnitHint(
                unit, self.currency if unit == "MONEY" else None, self.refs
            )

    def infer(
        self, value: object, path: str, expected: CalculationUnit | None = None
    ) -> CalculationUnit:
        self.calls += 1
        _require(self.calls <= 4096, "CALCULATION_SOURCE_BUDGET_EXCEEDED")
        unit: CalculationUnit
        if isinstance(value, str):
            known_unit = _FIELDS.get(value)
            _require(known_unit is not None, "CALCULATION_SOURCE_UNIT_UNSUPPORTED")
            assert known_unit is not None
            unit = known_unit
            _require(expected is None or expected == unit, "CALCULATION_UNIT_MISMATCH")
        elif isinstance(value, Decimal):
            unit = expected or "NUMBER"
        elif isinstance(value, CompiledCalculation):
            units = [
                self.infer(operand, f"{path}/args/{index}")
                for index, operand in enumerate(value.operands)
            ]
            dimensioned = [u for u in units if u not in {"NUMBER", "RATIO"}]
            if value.operator == "multiply":
                if len(dimensioned) > 1:
                    money_index = units.index("MONEY") if "MONEY" in units else -1
                    days_index = units.index("DAYS") if "DAYS" in units else -1
                    _require(
                        len(units) == 2
                        and sorted(units) == ["DAYS", "MONEY"]
                        and self.document_kind == "rate_amount"
                        and self.publication.calculation_kind == "FIXED"
                        and value.operands[money_index] == "Rider.insured_amount"
                        and _fields(value.operands[days_index]) == {"MedicalEvent.admission_days"},
                        "CALCULATION_UNIT_MISMATCH",
                    )
                    unit = "MONEY"
                    self.daily = True
                else:
                    unit = dimensioned[0] if dimensioned else "NUMBER"
                if expected is not None:
                    _require(
                        unit == expected or unit == "NUMBER" and not _fields(value),
                        "CALCULATION_UNIT_MISMATCH",
                    )
                    unit = expected
                if (
                    unit == "MONEY"
                    and len(dimensioned) == 1
                    and self.document_kind == "rate_amount"
                ):
                    for index, operand in enumerate(value.operands):
                        if isinstance(operand, Decimal) and 0 <= operand <= 1:
                            self.hint(f"{path}/args/{index}", "RATIO")
                            self.ratio = True
            else:
                dimensions = set(dimensioned)
                _require(len(dimensions) <= 1, "CALCULATION_UNIT_MISMATCH")
                unit = next(iter(dimensions)) if dimensions else expected or "NUMBER"
                _require(expected is None or unit == expected, "CALCULATION_UNIT_MISMATCH")
                for index, (operand, operand_unit) in enumerate(
                    zip(value.operands, units, strict=True)
                ):
                    if operand_unit != unit:
                        _require(not _fields(operand), "CALCULATION_UNIT_MISMATCH")
                        self.infer(operand, f"{path}/args/{index}", unit)
        else:
            raise CalculationSourceError("CALCULATION_SOURCE_UNIT_UNSUPPORTED")
        self.hint(path, unit)
        return unit


def bind_calculation_source(
    publication: GuidanceCalculationInput, *, currency: str | None
) -> CalculationSourceBinding:
    """Bind units and source references, retaining the formula if currency is absent.

    Returned hints are valid only for this source and exact AST. The caller must
    retain CURRENCY_UNRESOLVED as a non-amount state and independently bind fields.
    """
    try:
        validated, citations = _validated(publication)
        for value in (currency, publication.source_currency):
            _require(value is None or type(value) is str and _CURRENCY.fullmatch(value) is not None)
        _require(
            currency is None
            or publication.source_currency is None
            or currency == publication.source_currency,
            "CALCULATION_CURRENCY_MISMATCH",
        )
        assert validated.calculation is not None
        calculation = validated.calculation
        formula_digest = _digest(
            {
                "schema_version": validated.schema_version,
                "rule_kind": validated.rule_kind,
                "calculation": _formula(calculation, remaining=[256]),
            }
        )
        refs = _source_refs(publication, citations, formula_digest)
        effective_currency = (
            currency
            if publication.source_kind != "SEMANTIC_NODE" or publication.source_currency is not None
            else None
        )
        binder = _UnitBinder(publication, validated.rule_kind, effective_currency, refs)
        payout_unit = binder.infer(
            calculation, "/calculation", "MONEY" if validated.rule_kind == "fixed_amount" else None
        )
        _require(payout_unit == "MONEY", "CALCULATION_SOURCE_UNIT_UNSUPPORTED")
        fields = _fields(calculation)
        basis: CalculationBasis = (
            "DAILY_INSURED_AMOUNT"
            if binder.daily
            else "RECEIPT_AMOUNT"
            if fields & {"Receipt.covered_amount", "Receipt.confirmed_amount"}
            else "INSURED_RATIO"
            if binder.ratio and "Rider.insured_amount" in fields
            else "INSURED_AMOUNT"
            if "Rider.insured_amount" in fields
            else "FIXED_AMOUNT"
            if not fields and validated.rule_kind == "fixed_amount"
            else "NUMERIC_EXPRESSION"
        )
        source_digest = _digest(
            {
                "revision": CALCULATION_SOURCE_REVISION,
                "publication_id": str(publication.publication_id),
                "source_kind": publication.source_kind,
                "calculation_key": publication.calculation_key,
                "semantic_node_id": publication.semantic_node_id,
                "calculation_kind": publication.calculation_kind,
                "formula_digest_sha256": formula_digest,
                "source_currency": publication.source_currency,
                "currency": currency,
                "required": validated.required,
                "result_reason_code": validated.result_reason_code,
                "input_field_paths": validated.input_field_paths,
                "evidence_ids": [str(key) for key in validated.evidence_ids],
                "citations": [c.evidence.model_dump(mode="json") for c in citations],
            }
        )
        return CalculationSourceBinding(
            CalculationSource(
                publication.publication_id, CALCULATION_SOURCE_REVISION, source_digest, refs
            ),
            calculation,
            MappingProxyType(dict(binder.hints)),
            formula_digest,
            basis,
            payout_unit,
            "BOUND" if effective_currency is not None else "CURRENCY_UNRESOLVED",
            () if effective_currency is not None else ("CALCULATION_CURRENCY_UNRESOLVED",),
        )
    except CalculationSourceError:
        raise
    except ValueError, TypeError, KeyError, ArithmeticError:
        raise CalculationSourceError from None
