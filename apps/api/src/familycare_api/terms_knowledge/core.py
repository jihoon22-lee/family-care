"""Bounded semantic candidates, dependency diagnostics and data-only compilation.

This module never verifies original source text or confers publication authority.
The caller must verify every field against retained originals before supplying
citation IDs, and bind the edition separately. Unsupported meaning is retained.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from familycare_api.clauses.dsl import RuleValidationError, validate_rule_document
from familycare_api.terms_knowledge.generated_contracts import (
    SemanticClassification,
    SemanticCodeDefinition,
    SemanticCondition,
    SemanticDailyCalculation,
    SemanticDeductible,
    SemanticFixedCalculation,
    SemanticFootnote,
    SemanticInformation,
    SemanticLimit,
    SemanticNode,
    SemanticRatioCalculation,
    TermsSemanticKnowledge,
)
from pydantic import ValidationError

COMPILER_REVISION = "terms-semantic-compiler-v1"
MAX_CLOSURE_DEPTH = 32


class SemanticKnowledgeError(ValueError):
    """A fixed, input-free failure for malformed graph identities or structure."""

    def __init__(self, code: str = "SEMANTIC_GRAPH_INVALID") -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SemanticDiagnostic:
    node_id: str
    code: str
    affected_scope: str


@dataclass(frozen=True, slots=True, repr=False)
class SemanticExplanation:
    node_id: str
    kind: str
    statement: str
    citation_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True, repr=False)
class CompiledSemanticRoot:
    root_node_id: str
    closure_node_ids: tuple[str, ...]
    citation_ids: tuple[str, ...]
    rules: tuple[dict[str, Any], ...]
    calculation: dict[str, Any] | None
    diagnostics: tuple[SemanticDiagnostic, ...]
    explanations: tuple[SemanticExplanation, ...]
    classification_scopes: tuple[dict[str, str], ...]
    manifest: dict[str, Any]
    manifest_sha256: str
    semantic_sha256: str
    calculation_currency: str | None

    @property
    def executable(self) -> bool:
        """Compiler usability only; the caller still owns publication authority."""
        return bool(self.rules or self.calculation)


@dataclass(frozen=True, slots=True, repr=False)
class CompilationResult:
    compiler_revision: str
    processing_complete: bool
    roots: tuple[CompiledSemanticRoot, ...]
    graph_sha256: str


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
        ).encode()
    ).hexdigest()


def _unique(values: list[str]) -> bool:
    return len(values) == len(set(values))


def parse_knowledge(payload: Mapping[str, object]) -> TermsSemanticKnowledge:
    """Validate transport and graph identities; do not validate source meaning."""
    try:
        graph = TermsSemanticKnowledge.model_validate_json(
            json.dumps(dict(payload), allow_nan=False), strict=True
        )
    except ValidationError, ValueError, TypeError, OverflowError, RecursionError:
        raise SemanticKnowledgeError() from None
    _validate_graph(graph)
    return graph


def _validate_graph(graph: TermsSemanticKnowledge) -> None:
    source_ids = [s.source_id for s in graph.sources]
    citation_ids = [c.citation_id for c in graph.citations]
    node_ids = [n.node_id for n in graph.nodes]
    processing = graph.processing
    expected = set(processing.expected_region_ids)
    if (
        not all(
            _unique(v)
            for v in [
                source_ids,
                citation_ids,
                node_ids,
                graph.roots,
                processing.expected_region_ids,
                processing.consumed_region_ids,
                processing.unresolved_region_ids,
            ]
        )
        or not set(graph.roots) <= set(node_ids)
        or not set(processing.consumed_region_ids) <= expected
        or not set(processing.unresolved_region_ids) <= expected
    ):
        raise SemanticKnowledgeError()
    citations = {c.citation_id: c for c in graph.citations}
    for citation in graph.citations:
        if (
            citation.source_id not in source_ids
            or citation.end <= citation.start
            or len(citation.text) != citation.end - citation.start
            or citation.bbox[2] < citation.bbox[0]
            or citation.bbox[3] < citation.bbox[1]
        ):
            raise SemanticKnowledgeError()
    for node in graph.nodes:
        if (
            node.source_id not in source_ids
            or not _unique(node.citation_ids)
            or not _unique(node.region_ids)
            or not set(node.region_ids) <= expected
            or any(
                key not in citations or citations[key].source_id != node.source_id
                for key in node.citation_ids
            )
        ):
            raise SemanticKnowledgeError()
    edge_ids = [(e.from_node_id, e.to_node_id, e.relation) for e in graph.edges]
    if len(edge_ids) != len(set(edge_ids)) or any(
        e.from_node_id not in node_ids for e in graph.edges
    ):
        raise SemanticKnowledgeError()


def _closure(
    graph: TermsSemanticKnowledge, root_id: str
) -> tuple[tuple[str, ...], list[SemanticDiagnostic]]:
    nodes = {n.node_id: n for n in graph.nodes}
    sources = {s.source_id: s for s in graph.sources}
    edges: dict[str, list[str]] = {}
    for edge in graph.edges:
        edges.setdefault(edge.from_node_id, []).append(edge.to_node_id)
    seen: set[str] = set()
    active: set[str] = set()
    diagnostics: list[SemanticDiagnostic] = []
    root_edition = sources[nodes[root_id].source_id].terms_edition_id

    def visit(key: str, depth: int) -> None:
        if key in active:
            diagnostics.append(SemanticDiagnostic(key, "DEPENDENCY_CYCLE", "dependency"))
            return
        if depth > MAX_CLOSURE_DEPTH:
            diagnostics.append(SemanticDiagnostic(key, "DEPENDENCY_DEPTH_EXCEEDED", "dependency"))
            return
        if key in seen:
            return
        if key not in nodes:
            diagnostics.append(SemanticDiagnostic(key, "DEPENDENCY_MISSING", "dependency"))
            return
        seen.add(key)
        edition = sources[nodes[key].source_id].terms_edition_id
        if edition is None or root_edition is None:
            diagnostics.append(SemanticDiagnostic(key, "EDITION_BINDING_UNVERIFIED", "dependency"))
        elif edition != root_edition:
            diagnostics.append(SemanticDiagnostic(key, "CROSS_EDITION_REFERENCE", "dependency"))
        active.add(key)
        for target in sorted(edges.get(key, [])):
            visit(target, depth + 1)
        active.remove(key)

    visit(root_id, 0)
    return tuple(sorted(seen)), diagnostics


def affected_roots(
    graph: TermsSemanticKnowledge, changed_node_ids: frozenset[str]
) -> tuple[str, ...]:
    """Return only roots whose transitive (including missing) references changed."""
    _validate_graph(graph)
    reverse: dict[str, set[str]] = {}
    for edge in graph.edges:
        reverse.setdefault(edge.to_node_id, set()).add(edge.from_node_id)
    seen = set(changed_node_ids)
    pending = list(changed_node_ids)
    while pending:
        for parent in reverse.get(pending.pop(), set()):
            if parent not in seen:
                seen.add(parent)
                pending.append(parent)
    return tuple(root for root in graph.roots if root in seen)


def _document(
    kind: str,
    evidence: tuple[str, ...],
    *,
    expression: dict[str, Any] | None = None,
    calculation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    fields: set[str] = set()

    def collect(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "field" and isinstance(value, str):
                    fields.add(value)
                else:
                    collect(value)
        elif isinstance(node, list):
            for value in node:
                collect(value)

    collect(expression or calculation)
    result: dict[str, Any] = {
        "schema_version": "coverage-rule-v1",
        "rule_kind": kind,
        "required": True,
        "input_field_paths": sorted(fields),
        "result_reason_code": "SEMANTIC_" + kind.upper(),
        "evidence_ids": list(evidence),
    }
    if expression is not None:
        result["expression"] = expression
    if calculation is not None:
        result["calculation"] = calculation
    validate_rule_document(result, evidence)
    return result


def _operand(value: str | int) -> dict[str, Any]:
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return {"value": int(number)}
    wire_value = float(number)
    if Decimal(str(wire_value)) != number:
        raise SemanticKnowledgeError("CALCULATION_PRECISION_UNSUPPORTED")
    return {"value": wire_value}


def _op(name: str, *args: dict[str, Any]) -> dict[str, Any]:
    return {"op": name, "args": list(args)}


def _calculation(nodes: list[SemanticNode], evidence: tuple[str, ...]) -> dict[str, Any] | None:
    formulas = [
        n.payload
        for n in nodes
        if isinstance(
            n.payload,
            SemanticDailyCalculation | SemanticFixedCalculation | SemanticRatioCalculation,
        )
    ]
    if not formulas:
        return None
    if len(formulas) != 1:
        raise SemanticKnowledgeError("CALCULATION_CONFLICT")
    formula = formulas[0]
    exclusions = [n.payload for n in nodes if isinstance(n.payload, SemanticFootnote)]
    limits = [n.payload for n in nodes if isinstance(n.payload, SemanticLimit)]
    deductibles = [n.payload for n in nodes if isinstance(n.payload, SemanticDeductible)]
    if (
        len(exclusions) > 1
        or len({p.measure for p in limits}) != len(limits)
        or len(deductibles) > 1
    ):
        raise SemanticKnowledgeError("CALCULATION_CONFLICT")
    if any(
        p.measure == "maximum_amount" and (p.currency != formula.currency or p.unit != "amount")
        for p in limits
    ):
        raise SemanticKnowledgeError("CALCULATION_CURRENCY_MISMATCH")
    if any(deduction.currency != formula.currency for deduction in deductibles):
        raise SemanticKnowledgeError("CALCULATION_CURRENCY_MISMATCH")
    for limit in limits:
        if limit.measure == "payable_days" and (
            limit.unit != "days"
            or limit.currency is not None
            or Decimal(limit.value) != Decimal(limit.value).to_integral_value()
        ):
            raise SemanticKnowledgeError("CALCULATION_UNIT_MISMATCH")
    if isinstance(formula, SemanticDailyCalculation):
        if not exclusions:
            # Absence of an exclusion is not a source assertion of zero days.
            raise SemanticKnowledgeError("DAILY_EXCLUSION_BASIS_MISSING")
        payable = _op(
            "max",
            _op("subtract", {"field": "MedicalEvent.admission_days"}, _operand(exclusions[0].days)),
            _operand(0),
        )
        for limit in limits:
            if limit.measure == "payable_days":
                payable = _op("min", payable, _operand(limit.value))
        expression = _op("multiply", {"field": "Rider.insured_amount"}, payable)
        kind = "rate_amount"
    else:
        if exclusions or any(p.measure == "payable_days" for p in limits):
            raise SemanticKnowledgeError("CALCULATION_UNIT_MISMATCH")
        if isinstance(formula, SemanticFixedCalculation):
            expression = _op("multiply", _operand(formula.amount), _operand(1))
            kind = "fixed_amount"
        else:
            if Decimal(formula.ratio) > 1:
                raise SemanticKnowledgeError("CALCULATION_RATE_INVALID")
            expression = _op("multiply", {"field": "Rider.insured_amount"}, _operand(formula.ratio))
            kind = "rate_amount"
    if deductibles:
        expression = _op(
            "max", _op("subtract", expression, _operand(deductibles[0].amount)), _operand(0)
        )
    for limit in limits:
        if limit.measure == "maximum_amount":
            expression = _op("min", expression, _operand(limit.value))
    expression = {"op": "round", "args": [expression], "rounding": formula.rounding}
    return _document(kind, evidence, calculation=expression)


def _active_nodes(
    graph: TermsSemanticKnowledge, closure: tuple[str, ...], diagnostics: list[SemanticDiagnostic]
) -> list[SemanticNode]:
    nodes = {n.node_id: n for n in graph.nodes}
    overridden: set[str] = set()
    replacements: dict[str, str] = {}
    for edge in graph.edges:
        if (
            edge.relation != "OVERRIDES"
            or edge.from_node_id not in closure
            or edge.to_node_id not in nodes
        ):
            continue
        source, target = nodes[edge.from_node_id], nodes[edge.to_node_id]
        source_shape = (
            source.payload.kind,
            getattr(source.payload, "effect", None),
            getattr(source.payload, "measure", None),
            getattr(source.payload, "mode", None),
            getattr(source.payload, "field", None),
            getattr(source.payload, "rule_kind", None),
            getattr(source.payload, "term", None),
        )
        target_shape = (
            target.payload.kind,
            getattr(target.payload, "effect", None),
            getattr(target.payload, "measure", None),
            getattr(target.payload, "mode", None),
            getattr(target.payload, "field", None),
            getattr(target.payload, "rule_kind", None),
            getattr(target.payload, "term", None),
        )
        if source_shape != target_shape or edge.to_node_id in replacements:
            diagnostics.append(
                SemanticDiagnostic(source.node_id, "OVERRIDE_CONFLICT", "dependency")
            )
        else:
            replacements[edge.to_node_id] = edge.from_node_id
            overridden.add(edge.to_node_id)
    return [nodes[key] for key in closure if key not in overridden]


def _condition_expression(payload: SemanticCondition) -> dict[str, Any] | None:
    """Keep semantic comparison direction, scalar types and field dimensions explicit."""
    boolean_fields = {
        "MedicalEvent.admission",
        "MedicalEvent.performed",
        "MedicalEvent.diagnosis_confirmed",
    }
    integer_units = {
        "MedicalEvent.admission_days": "days",
        "ClaimHistory.counted_occurrence": "occurrences",
    }
    ordinary_kinds = {"eligibility", "exclusion"}
    count_field = payload.field == "ClaimHistory.counted_occurrence"
    allowed_integer_kinds = ordinary_kinds | ({"frequency"} if count_field else set())
    valid = False
    if payload.operator == "equals":
        valid = payload.unit is None and (
            (
                payload.field in boolean_fields
                and type(payload.value) is bool
                and payload.rule_kind in ordinary_kinds
            )
            or (
                payload.field in integer_units
                and type(payload.value) is int
                and payload.rule_kind in allowed_integer_kinds
            )
        )
    elif payload.operator == "range":
        valid = (
            payload.field in integer_units
            and payload.unit == integer_units[payload.field]
            and isinstance(payload.value, list)
            and len(payload.value) == 2
            and all(type(value) is int for value in payload.value)
            and payload.rule_kind in allowed_integer_kinds
        )
    elif payload.operator == "days_since":
        valid = (
            payload.field == "PolicyContract.contract_start"
            and type(payload.value) is int
            and payload.unit == "days"
            and payload.rule_kind in {"eligibility", "temporal"}
        )
    elif payload.operator in {"count_before", "count_below"}:
        valid = (
            count_field
            and type(payload.value) is int
            and payload.unit == "occurrences"
            and payload.rule_kind in {"eligibility", "frequency"}
        )
    if not valid:
        return None
    expression: dict[str, Any] = {
        "op": "count_before" if payload.operator == "count_below" else payload.operator,
        "field": payload.field,
        "value": payload.value,
    }
    if payload.operator == "range" and isinstance(payload.value, list):
        expression["value"] = {"min": payload.value[0], "max": payload.value[1]}
    if payload.unit is not None:
        expression["unit"] = payload.unit
    # Existing count_before evaluates count >= threshold, despite its historical name.
    # Its tri-state NOT preserves missing history as UNKNOWN, never inventing zero.
    return {"op": "not", "args": [expression]} if payload.operator == "count_below" else expression


def _compile_root(
    graph: TermsSemanticKnowledge,
    root: str,
    verified: frozenset[str],
    verified_nodes: frozenset[str],
    verified_edges: frozenset[tuple[str, str, str]],
) -> CompiledSemanticRoot:
    closure, diagnostics = _closure(graph, root)
    nodes = {n.node_id: n for n in graph.nodes}
    active = _active_nodes(graph, closure, diagnostics)
    unavailable = set(graph.processing.expected_region_ids) - set(
        graph.processing.consumed_region_ids
    ) | set(graph.processing.unresolved_region_ids)
    evidence = tuple(sorted({key for node_id in closure for key in nodes[node_id].citation_ids}))
    for key in closure:
        node = nodes[key]
        if key not in verified_nodes:
            diagnostics.append(SemanticDiagnostic(key, "SEMANTIC_NODE_UNVERIFIED", "dependency"))
        if not set(node.citation_ids) <= verified:
            diagnostics.append(SemanticDiagnostic(key, "SOURCE_UNVERIFIED", "dependency"))
        if set(node.region_ids) & unavailable:
            diagnostics.append(
                SemanticDiagnostic(key, "SOURCE_PROCESSING_INCOMPLETE", "dependency")
            )
    for edge in graph.edges:
        if (
            edge.from_node_id in closure
            and (edge.from_node_id, edge.to_node_id, edge.relation) not in verified_edges
        ):
            diagnostics.append(
                SemanticDiagnostic(edge.from_node_id, "SEMANTIC_RELATION_UNVERIFIED", "dependency")
            )
    blocked_nodes = {
        d.node_id
        for d in diagnostics
        if d.code
        in {
            "CROSS_EDITION_REFERENCE",
            "EDITION_BINDING_UNVERIFIED",
            "SOURCE_UNVERIFIED",
            "SEMANTIC_NODE_UNVERIFIED",
            "SOURCE_PROCESSING_INCOMPLETE",
            "OVERRIDE_CONFLICT",
        }
    }
    proof_paths: dict[str, tuple[str, ...]] = {}
    if root not in blocked_nodes:
        proof_paths[root] = (root,)
        pending = [root]
        while pending:
            current = pending.pop()
            for edge in graph.edges:
                if (
                    edge.from_node_id == current
                    and edge.to_node_id in closure
                    and edge.to_node_id not in blocked_nodes
                    and edge.to_node_id not in proof_paths
                    and (edge.from_node_id, edge.to_node_id, edge.relation) in verified_edges
                ):
                    proof_paths[edge.to_node_id] = (*proof_paths[current], edge.to_node_id)
                    pending.append(edge.to_node_id)
    rules = []
    classifications = []
    dependency_failed = any(d.affected_scope == "dependency" for d in diagnostics)
    unsupported_calculation = False
    for node in active:
        payload = node.payload
        if isinstance(payload, SemanticInformation) and payload.effect != "explanation_only":
            diagnostics.append(
                SemanticDiagnostic(node.node_id, payload.reason_code, payload.effect)
            )
            unsupported_calculation |= payload.effect == "unsupported_calculation"
        node_closure, node_diagnostics = _closure(graph, node.node_id)
        node_unverified = any(
            key in blocked_nodes
            or not set(nodes[key].citation_ids) <= verified
            or set(nodes[key].region_ids) & unavailable
            for key in node_closure
        )
        node_edges_unverified = any(
            edge.from_node_id in node_closure
            and (edge.from_node_id, edge.to_node_id, edge.relation) not in verified_edges
            for edge in graph.edges
        )
        if (
            node.node_id not in proof_paths
            or node_diagnostics
            or node_unverified
            or node_edges_unverified
        ):
            continue
        expression: dict[str, Any] | None = None
        kind = "classification"
        if isinstance(payload, SemanticClassification | SemanticCodeDefinition):
            groups = [
                {"op": "in", "field": payload.field, "value": payload.codes[index : index + 16]}
                for index in range(0, len(payload.codes), 16)
            ]
            expression = groups[0] if len(groups) == 1 else {"op": "any", "args": groups}
            classifications.append(
                {
                    "node_id": node.node_id,
                    "field": payload.field,
                    "code_system": payload.code_system,
                    "code_version": payload.code_version,
                }
            )
        elif isinstance(payload, SemanticCondition):
            kind = payload.rule_kind
            expression = _condition_expression(payload)
            if expression is None:
                diagnostics.append(
                    SemanticDiagnostic(
                        node.node_id, "CONDITION_UNSUPPORTED", "unsupported_condition"
                    )
                )
        if expression is not None:
            try:
                rule_evidence = tuple(
                    sorted(
                        {
                            citation
                            for key in (*node_closure, *proof_paths[node.node_id])
                            for citation in nodes[key].citation_ids
                        }
                    )
                )
                rules.append(_document(kind, rule_evidence, expression=expression))
            except RuleValidationError:
                diagnostics.append(
                    SemanticDiagnostic(
                        node.node_id, "CONDITION_UNSUPPORTED", "unsupported_condition"
                    )
                )
    calculation = None
    if not dependency_failed and not unsupported_calculation:
        try:
            calculation = _calculation(active, evidence)
        except (RuleValidationError, SemanticKnowledgeError) as error:
            code = (
                error.code
                if isinstance(error, SemanticKnowledgeError)
                else "CALCULATION_DSL_UNSUPPORTED"
            )
            diagnostics.append(SemanticDiagnostic(root, code, "unsupported_calculation"))
    sources = {nodes[key].source_id for key in closure}
    relevant_regions = {region for key in closure for region in nodes[key].region_ids}
    closure_edges = sorted(
        [e for e in graph.edges if e.from_node_id in closure],
        key=lambda e: (e.from_node_id, e.to_node_id, e.relation),
    )
    semantic_digest = _digest(
        {
            "compiler_revision": COMPILER_REVISION,
            "schema_revision": graph.schema_revision,
            "nodes": [
                {"node_id": key, "payload": nodes[key].payload.model_dump(mode="json")}
                for key in closure
            ],
            "edges": [e.model_dump(mode="json") for e in closure_edges],
        }
    )
    manifest = {
        "semantic_sha256": semantic_digest,
        "compiler_revision": COMPILER_REVISION,
        "schema_revision": graph.schema_revision,
        "prompt_revision": graph.prompt_revision,
        "model_revision": graph.model_revision,
        "root_node_id": root,
        "sources": [s.model_dump(mode="json") for s in graph.sources if s.source_id in sources],
        "nodes": [nodes[key].model_dump(mode="json") for key in closure],
        "edges": [e.model_dump(mode="json") for e in graph.edges if e.from_node_id in closure],
        "citations": [
            c.model_dump(mode="json") for c in graph.citations if c.citation_id in evidence
        ],
        "verified_citation_ids": sorted(set(evidence) & verified),
        "verified_node_ids": sorted(set(closure) & verified_nodes),
        "verified_edges": sorted([list(key) for key in verified_edges if key[0] in closure]),
        "unavailable_region_ids": sorted(relevant_regions & unavailable),
        "classification_scopes": classifications,
    }
    return CompiledSemanticRoot(
        root,
        closure,
        evidence,
        tuple(rules),
        calculation,
        tuple(diagnostics),
        tuple(
            SemanticExplanation(
                key, nodes[key].payload.kind, nodes[key].statement, tuple(nodes[key].citation_ids)
            )
            for key in closure
        ),
        tuple(classifications),
        manifest,
        _digest(manifest),
        semantic_digest,
        next(
            (
                n.payload.currency
                for n in active
                if isinstance(
                    n.payload,
                    SemanticDailyCalculation | SemanticFixedCalculation | SemanticRatioCalculation,
                )
            ),
            None,
        ),
    )


def compile_knowledge(
    graph: TermsSemanticKnowledge,
    *,
    verified_citation_ids: frozenset[str] = frozenset(),
    verified_node_ids: frozenset[str] = frozenset(),
    verified_edges: frozenset[tuple[str, str, str]] = frozenset(),
) -> CompilationResult:
    """Compile independently grounded roots while retaining scoped failures."""
    # Reparse even model instances: nested lists can have been mutated by callers.
    graph = parse_knowledge(graph.model_dump(mode="json"))
    if not verified_citation_ids <= {c.citation_id for c in graph.citations}:
        raise SemanticKnowledgeError("VERIFIED_CITATION_UNKNOWN")
    if not verified_node_ids <= {n.node_id for n in graph.nodes} or not verified_edges <= {
        (e.from_node_id, e.to_node_id, e.relation) for e in graph.edges
    }:
        raise SemanticKnowledgeError("VERIFIED_SEMANTIC_CLAIM_UNKNOWN")
    processing = graph.processing
    complete = (
        set(processing.expected_region_ids) == set(processing.consumed_region_ids)
        and not processing.unresolved_region_ids
    )
    return CompilationResult(
        COMPILER_REVISION,
        complete,
        tuple(
            _compile_root(graph, root, verified_citation_ids, verified_node_ids, verified_edges)
            for root in graph.roots
        ),
        _digest(graph.model_dump(mode="json")),
    )
