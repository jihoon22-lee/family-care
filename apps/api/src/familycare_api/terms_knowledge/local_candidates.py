"""Propose bounded local graphs from whole original statements and explicit links.

These candidates carry no authority: publication must independently verify them.
A run covers all original regions even when its individual graphs cover one root.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.terms_knowledge.core import SemanticKnowledgeError, parse_knowledge
from familycare_api.terms_knowledge.source_layout import SemanticSourceRegion
from familycare_api.terms_knowledge.source_meaning import observe_statement
from familycare_api.terms_knowledge.source_tables import observe_classification_table
from familycare_api.terms_knowledge.source_verification import (
    _OVERRIDE,
    SourceSnapshot,
    _reference,
    verify_and_compile,
)

LOCAL_PLANNER_REVISION = "terms-local-candidates-v1"
_EXCLUDED = {"LOCAL_EXAMPLE_EXCLUDED", "SEMANTIC_TERMS_TITLE_CONTEXT"}


@dataclass(frozen=True, slots=True, repr=False)
class LocalSemanticCandidates:
    graphs: tuple[dict[str, Any], ...]
    consumed_region_ids: tuple[str, ...]
    unresolved_region_ids: tuple[str, ...]


@dataclass(slots=True, repr=False)
class _RegionPlan:
    region: SemanticSourceRegion
    identity: tuple[str, str, str, int]
    nodes: list[dict[str, Any]] = field(default_factory=list)
    citations: dict[str, dict[str, Any]] = field(default_factory=dict)
    links: list[tuple[str, str, str]] = field(default_factory=list)
    edges: list[dict[str, str]] = field(default_factory=list)
    dependencies: set[str] = field(default_factory=set)
    unresolved: bool = False
    excluded: bool = False

    @property
    def anchor(self) -> str:
        return str(self.nodes[0]["node_id"])


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def _citation(snapshot: SourceSnapshot, span: ClauseSourceSpan) -> dict[str, Any]:
    address = [span.node_id, span.page_number, span.start, span.end, span.source_layer, span.bbox]
    return {
        "citation_id": str(uuid5(NAMESPACE_URL, _digest([snapshot.source.model_dump(), address]))),
        "source_id": snapshot.source.source_id,
        "node_id": span.node_id,
        "page_number": span.page_number,
        "start": span.start,
        "end": span.end,
        "text": span.text,
        "source_layer": span.source_layer,
        "bbox": list(span.bbox),
    }


def _node(
    snapshot: SourceSnapshot,
    plan: _RegionPlan,
    span: ClauseSourceSpan,
    ordinal: int | str,
    payload: dict[str, Any] | None,
    reason: str = "LOCAL_SOURCE_MEANING_UNSUPPORTED",
) -> dict[str, Any]:
    citation = _citation(snapshot, span)
    plan.citations[citation["citation_id"]] = citation
    return {
        "node_id": "local-" + _digest([plan.identity, ordinal]),
        "source_id": snapshot.source.source_id,
        "statement": span.text if 1 <= len(span.text) <= 4096 else reason,
        "region_ids": [plan.region.region_id],
        "citation_ids": [citation["citation_id"]],
        "payload": payload
        or {
            "kind": "information",
            "effect": "unsupported_condition",
            "reason_code": reason,
        },
    }


def _edge(source: str, target: str, relation: str = "DEPENDS_ON") -> dict[str, str]:
    return {"from_node_id": source, "to_node_id": target, "relation": relation}


def _shape(node: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(
        node["payload"].get(k)
        for k in (
            "kind",
            "effect",
            "measure",
            "mode",
            "field",
            "rule_kind",
            "term",
        )
    )


def _region_plan(
    snapshot: SourceSnapshot, region: SemanticSourceRegion, occurrence: int
) -> _RegionPlan:
    plan = _RegionPlan(region, (snapshot.source.source_id, region.kind, region.label, occurrence))
    plan.excluded = bool(
        region.complete
        and region.kind == "unresolved"
        and region.reason_codes
        and set(region.reason_codes) <= _EXCLUDED
    )
    if plan.excluded:
        return plan
    plan.unresolved = not region.complete or region.kind == "unresolved"
    table = observe_classification_table(region)
    if table is not None:
        bundled = _node(
            snapshot, plan, table.spans[0], region.body_spans.index(table.spans[0]), table.payload
        )
        bundled["statement"] = table.statement
        bundled["citation_ids"] = []
        for span in table.spans:
            citation = _citation(snapshot, span)
            plan.citations[citation["citation_id"]] = citation
            bundled["citation_ids"].append(citation["citation_id"])
        plan.nodes.append(bundled)
    for ordinal, span in enumerate(region.body_spans):
        if table is not None and span in table.spans:
            continue
        reference, override = _reference(span.text), _OVERRIDE.fullmatch(span.text.strip())
        if reference or override:
            citation = _citation(snapshot, span)
            plan.citations[citation["citation_id"]] = citation
            if reference:
                plan.links.append((reference, "DEPENDS_ON", citation["citation_id"]))
            elif override is not None and override["source"] == region.label:
                plan.links.append((override["target"], "OVERRIDES", citation["citation_id"]))
            else:
                plan.nodes.append(_node(snapshot, plan, span, ordinal, None))
                plan.unresolved = True
            continue
        meaning = observe_statement(span.text)
        plan.nodes.append(_node(snapshot, plan, span, ordinal, meaning))
        plan.unresolved |= meaning is None or len(span.text) > 4096
    if not plan.nodes:
        witness = next(iter(region.body_spans), region.heading)
        if witness is not None:
            plan.nodes.append(_node(snapshot, plan, witness, "empty", None))
        plan.unresolved = True
    # Root selection is semantic; IDs remain tied to the original body ordinal.
    plan.nodes.sort(key=lambda n: n["payload"]["kind"] != "calculation")
    if plan.nodes:
        plan.edges.extend(_edge(plan.anchor, n["node_id"]) for n in plan.nodes[1:])
    return plan


def _link_regions(plans: dict[str, _RegionPlan]) -> None:
    labels: dict[str, list[_RegionPlan]] = defaultdict(list)
    for plan in plans.values():
        labels[plan.region.label].append(plan)
    for plan in plans.values():
        if not plan.nodes:
            continue
        for label, relation, citation in plan.links:
            targets = labels[label]
            if len(targets) != 1 or not targets[0].nodes:
                plan.nodes[0]["citation_ids"].append(citation)
                plan.edges.append(_edge(plan.anchor, "missing-" + _digest(label), relation))
                plan.unresolved = True
                continue
            target = targets[0]
            plan.dependencies.add(target.region.region_id)
            if relation == "DEPENDS_ON":
                plan.nodes[0]["citation_ids"].append(citation)
                plan.edges.append(_edge(plan.anchor, target.anchor))
                continue
            left = {_shape(n): n for n in plan.nodes}
            right = {_shape(n): n for n in target.nodes}
            if (
                len(left) != len(plan.nodes)
                or len(right) != len(target.nodes)
                or left.keys() != right.keys()
            ):
                plan.unresolved = True
                # Keep the complete conflicting closure; the verifier/compiler refuse it.
                plan.nodes[0]["citation_ids"].append(citation)
                plan.edges.append(_edge(plan.anchor, target.anchor, relation))
                continue
            for shape, node in left.items():
                node["citation_ids"].append(citation)
                plan.edges.append(_edge(node["node_id"], right[shape]["node_id"], relation))
        # A repeated original reference need not duplicate a semantic relation.
        plan.edges = list({tuple(e.values()): e for e in plan.edges}.values())


def _closure(plans: dict[str, _RegionPlan], root: str) -> set[str]:
    seen: set[str] = set()
    pending = [root]
    while pending:
        key = pending.pop()
        if key not in seen:
            seen.add(key)
            pending.extend(plans[key].dependencies)
    return seen


def _graph(snapshot: SourceSnapshot, plans: list[_RegionPlan], root: str) -> dict[str, Any]:
    return {
        "schema_version": "1",
        "schema_revision": "terms-semantic-v1",
        "prompt_revision": LOCAL_PLANNER_REVISION,
        "model_revision": LOCAL_PLANNER_REVISION,
        "sources": [snapshot.source.model_dump(mode="json")],
        "nodes": [node for p in plans for node in p.nodes],
        "citations": list({key: c for p in plans for key, c in p.citations.items()}.values()),
        "edges": [edge for p in plans for edge in p.edges],
        "roots": [root],
        "processing": {
            "expected_region_ids": list(snapshot.layout.expected_region_ids),
            "consumed_region_ids": list(snapshot.layout.expected_region_ids),
            "unresolved_region_ids": [],
        },
    }


def propose_local_candidates(snapshot: SourceSnapshot) -> LocalSemanticCandidates:
    """Plan every region, preserving full closures or explicit non-executable failure."""
    expected = snapshot.layout.expected_region_ids
    ids = [r.region_id for r in snapshot.layout.regions]
    if (
        len(ids) != len(set(ids))
        or len(expected) != len(set(expected))
        or set(ids) != set(expected)
        or len(expected) > 4096
    ):
        raise ValueError("LOCAL_SOURCE_REGION_IDENTITY_INVALID")
    occurrences: Counter[tuple[str, str]] = Counter()
    plans: dict[str, _RegionPlan] = {}
    for region in snapshot.layout.regions:
        label_key = region.kind, region.label
        plans[region.region_id] = _region_plan(snapshot, region, occurrences[label_key])
        occurrences[label_key] += 1
    _link_regions(plans)
    roots = [key for key, p in plans.items() if p.region.kind == "article" and p.nodes]
    reached = set().union(*(_closure(plans, root) for root in roots))
    # Preserve standalone appendix/footnote observations as their own complete graphs.
    roots.extend(key for key, p in plans.items() if key not in reached and p.nodes)
    unresolved = {key for key, p in plans.items() if p.unresolved}
    graphs: list[dict[str, Any]] = []
    represented: set[str] = {key for key, p in plans.items() if p.excluded}
    for key in roots:
        closure = _closure(plans, key)
        graph = _graph(snapshot, [p for k, p in plans.items() if k in closure], plans[key].anchor)
        try:
            parse_knowledge(graph)
        except SemanticKnowledgeError:
            unresolved.add(key)
            # Explicitly replace the whole oversized/invalid closure with a diagnostic.
            # No prefix of its formulas is published as if it were complete.
            fallback = _RegionPlan(plans[key].region, plans[key].identity)
            witness = next(iter(fallback.region.body_spans), fallback.region.heading)
            if witness is None or not 1 <= len(witness.text) <= 8192:
                continue
            fallback.nodes.append(
                _node(snapshot, fallback, witness, "limit", None, "LOCAL_GRAPH_LIMIT_EXCEEDED")
            )
            graph = _graph(snapshot, [fallback], fallback.anchor)
            try:
                parse_knowledge(graph)
            except SemanticKnowledgeError:
                continue
        else:
            represented.update(closure)
            result = verify_and_compile(
                graph, sources={snapshot.source.source_id: snapshot}
            ).compilation.roots[0]
            if result.diagnostics:
                unresolved.add(key)
        graphs.append(graph)
    unresolved.update(set(expected) - represented)
    # Missing/unknown/cyclic dependencies affect their ancestors, never unrelated roots.
    changed = True
    while changed:
        prior = len(unresolved)
        unresolved.update(key for key, p in plans.items() if p.dependencies & unresolved)
        changed = prior != len(unresolved)
    consumed_ids = tuple(key for key in expected if key not in unresolved)
    unresolved_ids = tuple(key for key in expected if key in unresolved)
    for graph in graphs:
        graph["processing"]["consumed_region_ids"] = list(consumed_ids)
        graph["processing"]["unresolved_region_ids"] = list(unresolved_ids)
    return LocalSemanticCandidates(tuple(graphs), consumed_ids, unresolved_ids)
