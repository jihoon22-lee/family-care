"""Bind semantic candidates to independently loaded original regions before compile.

The repository supplies current household/edition/source snapshots. This pure
boundary verifies addresses, whole-statement meanings and explicit scope witnesses.
Proofs are returned with the exact canonical graph and consumed immediately; callers
must not reuse an ID set for a changed graph or treat this as a DB authorization API.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from familycare_api.clauses.source_regions import ClauseSourceSpan
from familycare_api.terms_knowledge.core import (
    CompilationResult,
    compile_knowledge,
    parse_knowledge,
)
from familycare_api.terms_knowledge.generated_contracts import (
    SemanticSource,
    TermsSemanticKnowledge,
)
from familycare_api.terms_knowledge.source_layout import SemanticSourceLayout, SemanticSourceRegion
from familycare_api.terms_knowledge.source_meaning import MEANING_REVISION, observe_statement
from familycare_api.terms_knowledge.source_tables import observe_classification_table

VERIFIER_REVISION = "terms-semantic-source-v1"
_REFERENCE = re.compile(
    r"Apply (?P<label>(?:Article|Appendix|Footnote) [1-9][0-9]{0,3})\."
    r"|(?P<ko>(?:제[1-9][0-9]{0,3}조|별표\s?[1-9][0-9]{0,3}|각주\s?[1-9][0-9]{0,3}))"
    r"(?:을|를) 적용합니다\."
)
_OVERRIDE = re.compile(
    r"(?P<source>(?:Article|Appendix|Footnote) [1-9][0-9]{0,3}) replaces "
    r"(?P<target>(?:Article|Appendix|Footnote) [1-9][0-9]{0,3})\."
)


@dataclass(frozen=True, slots=True, repr=False)
class SourceSnapshot:
    source: SemanticSource
    layout: SemanticSourceLayout


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedCompilation:
    canonical_graph_json: str
    compilation: CompilationResult
    verified_citation_ids: frozenset[str]
    verified_node_ids: frozenset[str]
    verified_edges: frozenset[tuple[str, str, str]]
    proof_sha256: str
    verifier_revision: str = VERIFIER_REVISION


def _address(span: ClauseSourceSpan) -> tuple[str, int, int, int, str, tuple[float, ...]]:
    return span.node_id, span.page_number, span.start, span.end, span.source_layer, span.bbox


def _reference(text: str) -> str | None:
    match = _REFERENCE.fullmatch(text.strip())
    return (match["label"] or match["ko"]) if match else None


def _closure(graph: TermsSemanticKnowledge, root: str) -> set[str]:
    seen: set[str] = set()
    pending = [root]
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        pending.extend(e.to_node_id for e in graph.edges if e.from_node_id == key)
    return seen


def _verified_citations(
    graph: TermsSemanticKnowledge, snapshots: Mapping[str, SourceSnapshot]
) -> tuple[set[str], dict[str, tuple[str, ClauseSourceSpan]]]:
    verified: set[str] = set()
    locations: dict[str, tuple[str, ClauseSourceSpan]] = {}
    for citation in graph.citations:
        snapshot = snapshots.get(citation.source_id)
        if snapshot is None:
            continue
        matches = [
            (region.region_id, span)
            for region in snapshot.layout.regions
            for span in region.spans
            if span.node_id == citation.node_id
            and span.page_number == citation.page_number
            and span.source_layer == citation.source_layer
            and tuple(citation.bbox) == span.bbox
            and span.start <= citation.start < citation.end <= span.end
            and span.text[citation.start - span.start : citation.end - span.start] == citation.text
        ]
        if len(matches) == 1:
            verified.add(citation.citation_id)
            locations[citation.citation_id] = matches[0]
    return verified, locations


def _node_proofs(
    graph: TermsSemanticKnowledge,
    citations: set[str],
    locations: dict[str, tuple[str, ClauseSourceSpan]],
    regions: dict[str, SemanticSourceRegion],
) -> tuple[set[str], dict[str, tuple[str, tuple[ClauseSourceSpan, ...]]]]:
    verified: set[str] = set()
    meanings: dict[str, tuple[str, tuple[ClauseSourceSpan, ...]]] = {}
    by_citation = {c.citation_id: c for c in graph.citations}
    for node in graph.nodes:
        if not set(node.citation_ids) <= citations or len(node.region_ids) != 1:
            continue
        region = regions.get(node.region_ids[0])
        if region is None or not region.complete or region.kind == "unresolved":
            continue
        table = observe_classification_table(region)
        if (
            table is not None
            and table.payload == node.payload.model_dump(mode="json")
            and table.statement == node.statement
        ):
            original_spans = []
            exact = True
            for citation_id in node.citation_ids:
                region_id, span = locations[citation_id]
                citation = by_citation[citation_id]
                if (
                    region_id != region.region_id
                    or span not in region.body_spans
                    or citation.start != span.start
                    or citation.end != span.end
                ):
                    exact = False
                    break
                original_spans.append(span)
            witnessed = [span for span in original_spans if span in table.spans]
            extras = [span for span in original_spans if span not in table.spans]
            if (
                exact
                and tuple(witnessed) == table.spans
                and all(
                    _reference(span.text) is not None
                    or _OVERRIDE.fullmatch(span.text.strip()) is not None
                    for span in extras
                )
            ):
                verified.add(node.node_id)
                meanings[node.node_id] = (region.region_id, table.spans)
                continue
        matching = []
        extras_valid = True
        for citation_id in node.citation_ids:
            region_id, span = locations[citation_id]
            citation = by_citation[citation_id]
            if (
                region_id != region.region_id
                or span not in region.body_spans
                or (citation.start != span.start or citation.end != span.end)
            ):
                extras_valid = False
                break
            payload = observe_statement(span.text)
            if payload == node.payload.model_dump(mode="json") and node.statement == span.text:
                matching.append(span)
            elif _reference(span.text) is None and _OVERRIDE.fullmatch(span.text.strip()) is None:
                extras_valid = False
        if extras_valid and len(matching) == 1:
            verified.add(node.node_id)
            meanings[node.node_id] = (region.region_id, (matching[0],))
    return verified, meanings


def _edge_proofs(
    graph: TermsSemanticKnowledge,
    verified_nodes: set[str],
    regions: dict[str, SemanticSourceRegion],
) -> set[tuple[str, str, str]]:
    nodes = {n.node_id: n for n in graph.nodes}
    citations = {c.citation_id: c for c in graph.citations}
    sources = {s.source_id: s for s in graph.sources}
    verified: set[tuple[str, str, str]] = set()
    for edge in graph.edges:
        if edge.from_node_id not in verified_nodes or edge.to_node_id not in verified_nodes:
            continue
        source, target = nodes[edge.from_node_id], nodes[edge.to_node_id]
        if sources[source.source_id].terms_edition_id != sources[target.source_id].terms_edition_id:
            continue
        left, right = regions[source.region_ids[0]], regions[target.region_ids[0]]
        statements = [citations[key].text for key in source.citation_ids]
        if edge.relation == "DEPENDS_ON":
            valid = left.region_id == right.region_id or any(
                _reference(text) == right.label for text in statements
            )
        else:
            valid = any(
                (match := _OVERRIDE.fullmatch(text.strip())) is not None
                and match["source"] == left.label
                and match["target"] == right.label
                for text in statements
            )
        if valid:
            verified.add((edge.from_node_id, edge.to_node_id, edge.relation))
    return verified


def _covered_roots(
    graph: TermsSemanticKnowledge,
    verified_nodes: set[str],
    meanings: dict[str, tuple[str, tuple[ClauseSourceSpan, ...]]],
    regions: dict[str, SemanticSourceRegion],
    region_editions: dict[str, str | None],
) -> set[str]:
    """Detect omitted exceptions and references from the original region contents."""
    nodes = {n.node_id: n for n in graph.nodes}
    covered = set()
    for root in graph.roots:
        closure = _closure(graph, root)
        region_ids = {region for key in closure if key in nodes for region in nodes[key].region_ids}
        represented = {
            (region_id, _address(span))
            for key, (region_id, spans) in meanings.items()
            if key in closure and key in verified_nodes
            for span in spans
        }
        valid = True
        for region_id in region_ids:
            region = regions.get(region_id)
            if region is None or not region.complete or region.kind == "unresolved":
                valid = False
                break
            for span in region.body_spans:
                if (region_id, _address(span)) in represented:
                    continue
                target = _reference(span.text)
                override = _OVERRIDE.fullmatch(span.text.strip())
                if target is None and override:
                    target = override["target"] if override["source"] == region.label else None
                matches = (
                    [
                        r.region_id
                        for r in regions.values()
                        if r.label == target
                        and region_editions[r.region_id] == region_editions[region_id]
                    ]
                    if target
                    else []
                )
                if len(matches) != 1 or matches[0] not in region_ids:
                    valid = False
                    break
            if not valid:
                break
        if valid:
            covered.add(root)
    return covered


def verify_and_compile(
    payload: Mapping[str, Any], *, sources: Mapping[str, SourceSnapshot]
) -> VerifiedCompilation:
    graph = parse_knowledge(payload)
    canonical = json.dumps(
        graph.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    snapshots = {
        source.source_id: sources[source.source_id]
        for source in graph.sources
        if source.source_id in sources
        and sources[source.source_id].source == source
        and source.terms_edition_id is not None
    }
    region_counts: dict[str, int] = {}
    for snapshot in snapshots.values():
        for region in snapshot.layout.regions:
            region_counts[region.region_id] = region_counts.get(region.region_id, 0) + 1
    snapshots = {
        key: snapshot
        for key, snapshot in snapshots.items()
        if all(region_counts[r.region_id] == 1 for r in snapshot.layout.regions)
    }
    regions = {r.region_id: r for snapshot in snapshots.values() for r in snapshot.layout.regions}
    region_editions = {
        r.region_id: snapshot.source.terms_edition_id
        for snapshot in snapshots.values()
        for r in snapshot.layout.regions
    }
    citation_ids, locations = _verified_citations(graph, snapshots)
    node_ids, meanings = _node_proofs(graph, citation_ids, locations, regions)
    edges = _edge_proofs(graph, node_ids, regions)
    covered = _covered_roots(graph, node_ids, meanings, regions, region_editions)
    node_ids -= set(graph.roots) - covered
    compilation = compile_knowledge(
        graph,
        verified_citation_ids=frozenset(citation_ids),
        verified_node_ids=frozenset(node_ids),
        verified_edges=frozenset(edges),
    )
    expected = {r for snapshot in sources.values() for r in snapshot.layout.expected_region_ids}
    represented = {
        (region_id, _address(span))
        for key, (region_id, spans) in meanings.items()
        if key in node_ids
        for span in spans
    }
    fully_represented = {
        region_id
        for region_id, region in regions.items()
        if region.complete
        and (
            (
                region.kind == "unresolved"
                and region.reason_codes
                and set(region.reason_codes)
                <= {"LOCAL_EXAMPLE_EXCLUDED", "SEMANTIC_TERMS_TITLE_CONTEXT"}
            )
            or all(
                (region_id, _address(span)) in represented
                or (
                    _reference(span.text) is not None
                    and sum(
                        r.label == _reference(span.text)
                        and region_editions[r.region_id] == region_editions[region_id]
                        for r in regions.values()
                    )
                    == 1
                )
                for span in region.body_spans
            )
        )
    }
    actual_complete = (
        expected <= fully_represented
        and len(snapshots) == len(sources) == len(graph.sources)
        and all(s.layout.complete for s in sources.values())
        and expected == set(graph.processing.expected_region_ids)
        and expected == set(graph.processing.consumed_region_ids)
        and not graph.processing.unresolved_region_ids
        and len(node_ids) == len(graph.nodes)
        and covered == set(graph.roots)
    )
    original_citations = {
        c.citation_id: c.text for c in graph.citations if c.citation_id in citation_ids
    }
    compilation = replace(
        compilation,
        processing_complete=actual_complete,
        roots=tuple(
            replace(
                root,
                explanations=tuple(
                    replace(
                        explanation,
                        citation_ids=tuple(
                            key for key in explanation.citation_ids if key in original_citations
                        ),
                        statement="\n".join(
                            original_citations[key]
                            for key in explanation.citation_ids
                            if key in original_citations
                        ),
                    )
                    for explanation in root.explanations
                    if any(key in original_citations for key in explanation.citation_ids)
                ),
            )
            for root in compilation.roots
        ),
    )
    proof = {
        "graph_sha256": compilation.graph_sha256,
        "verifier_revision": VERIFIER_REVISION,
        "meaning_revision": MEANING_REVISION,
        "citations": sorted(citation_ids),
        "nodes": sorted(node_ids),
        "edges": sorted(edges),
        "complete": actual_complete,
    }
    digest = hashlib.sha256(
        json.dumps(proof, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return VerifiedCompilation(
        canonical,
        compilation,
        frozenset(citation_ids),
        frozenset(node_ids),
        frozenset(edges),
        digest,
    )
