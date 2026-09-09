"""Bind replayed semantic meaning to the original region of an enrolled Rider's Clause."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

from familycare_api.clauses.source_regions import ClauseSourceRegion, ClauseSourceSpan
from familycare_api.guidance.domain import (
    GuidanceCalculationInput,
    GuidanceCitation,
    GuidanceRuleInput,
)
from familycare_api.guidance.models import GuidanceSemanticEvidence
from familycare_api.terms_knowledge.repository import CurrentSemanticRoot
from familycare_api.terms_knowledge.source_verification import _OVERRIDE, _reference


@dataclass(frozen=True, slots=True, repr=False)
class BoundSemanticRoot:
    rules: tuple[GuidanceRuleInput, ...]
    calculation: GuidanceCalculationInput | None
    benefit_kind: Literal["FIXED", "INDEMNITY", "UNKNOWN"]
    original_anchor: tuple[object, ...]
    manifest_sha256: str
    complete: bool


def _within(citation: Mapping[str, Any], span: ClauseSourceSpan) -> bool:
    return (
        citation["node_id"] == span.node_id
        and citation["page_number"] == span.page_number
        and citation["source_layer"] == span.source_layer
        and span.start <= citation["start"] < citation["end"] <= span.end
        and citation["text"]
        == span.text[citation["start"] - span.start : citation["end"] - span.start]
        and tuple(citation["bbox"]) == span.bbox
    )


def bind_semantic_root(
    current: CurrentSemanticRoot,
    clause: ClauseSourceRegion,
    expected_source: Mapping[str, Any],
    *,
    review_job_id: UUID | None = None,
) -> BoundSemanticRoot | None:
    """Bind replayed authority; review callers supply a real persisted publication.

    This pure adapter does not grant publication authority. Review materialization
    replays the stored job/packet/proof before passing its actual publication ID.
    """
    root = current.root
    manifest = root.manifest
    nodes = {n["node_id"]: n for n in manifest["nodes"]}
    primary = nodes.get(root.root_node_id)
    if (
        not clause.complete
        or primary is None
        or root.root_node_id not in manifest["verified_node_ids"]
    ):
        return None
    source = next((s for s in manifest["sources"] if s["source_id"] == primary["source_id"]), None)
    if source is None or any(
        source[key] != str(expected_source[key])
        for key in ("document_version_id", "terms_edition_id", "generation_id", "content_sha256")
    ):
        return None
    if source["content_sha256"] != clause.content_sha256:
        return None
    raw = {c["citation_id"]: c for c in manifest["citations"]}
    # A root's own complete statement/reference citations must be in this Clause's body.
    # Dependencies may use an appendix/footnote only via the independently replayed graph.
    anchors = [raw[key] for key in primary["citation_ids"]]
    if not anchors or any(
        c["source_id"] != source["source_id"]
        or not any(_within(c, span) for span in (*clause.body, *clause.table_context))
        for c in anchors
    ):
        return None
    sources = {s["source_id"]: s for s in manifest["sources"]}
    verified = set(manifest["verified_citation_ids"])

    def citations(document: Mapping[str, Any]) -> tuple[GuidanceCitation, ...]:
        result = []
        for key in document["evidence_ids"]:
            c = raw[key]
            s = sources[c["source_id"]]
            result.append(
                GuidanceCitation(
                    key,
                    GuidanceSemanticEvidence(
                        citation_id=UUID(key),
                        publication_id=current.publication_id,
                        review_job_id=review_job_id,
                        document_version_id=UUID(s["document_version_id"]),
                        terms_edition_id=UUID(s["terms_edition_id"]),
                        generation_id=UUID(s["generation_id"]),
                        root_node_id=root.root_node_id,
                        source_node_id=c["node_id"],
                        page_start=c["page_number"],
                        page_end=c["page_number"],
                        start=c["start"],
                        end=c["end"],
                        source_layer=c["source_layer"],
                        bbox=tuple(c["bbox"]),
                        source_sha256=s["content_sha256"],
                        manifest_sha256=root.manifest_sha256,
                    ),
                    key in verified,
                )
            )
        return tuple(result)

    documents = list(root.rules)
    payloads = [n["payload"] for n in nodes.values() if n["payload"]["kind"] == "calculation"]
    modes = {p["mode"] for p in payloads}
    benefit: Literal["FIXED", "INDEMNITY", "UNKNOWN"] = (
        "FIXED" if modes and modes <= {"daily", "fixed", "insured_ratio"} else "UNKNOWN"
    )
    if primary["payload"].get("mode") == "daily":
        # The original daily basis establishes admission relevance, not code eligibility.
        documents.append(
            {
                "schema_version": "coverage-rule-v1",
                "rule_kind": "eligibility",
                "required": False,
                "input_field_paths": ["MedicalEvent.admission_days"],
                "expression": {
                    "op": "range",
                    "field": "MedicalEvent.admission_days",
                    "value": {"min": 1, "max": 36500},
                    "unit": "days",
                },
                "result_reason_code": "SEMANTIC_DAILY_RELEVANCE",
                "evidence_ids": list(primary["citation_ids"]),
            }
        )
    rules = tuple(
        GuidanceRuleInput(
            publication_id=current.publication_id,
            rule_key=f"{root.root_node_id}:{ordinal}",
            rule_kind=doc["rule_kind"],
            required=doc["required"],
            result_reason_code=doc["result_reason_code"],
            rule_document=doc,
            citations=citations(doc),
            source_kind="SEMANTIC_NODE",
            semantic_node_id=root.root_node_id,
            classification_scopes=root.classification_scopes,
        )
        for ordinal, doc in enumerate(documents)
    )
    calculation = None
    if root.calculation is not None and benefit != "UNKNOWN":
        calculation = GuidanceCalculationInput(
            publication_id=current.publication_id,
            calculation_key=root.root_node_id,
            calculation_kind=benefit,
            result_reason_code=root.calculation["result_reason_code"],
            calculation_document=root.calculation,
            citations=citations(root.calculation),
            source_kind="SEMANTIC_NODE",
            semantic_node_id=root.root_node_id,
            source_currency=root.calculation_currency,
        )
    # Different model/local IDs for the same source statement address share this key.
    anchor = tuple(
        sorted(
            (c["node_id"], c["page_number"], c["start"], c["end"])
            for c in anchors
            if _reference(c["text"]) is None and _OVERRIDE.fullmatch(c["text"].strip()) is None
        )
    )
    return BoundSemanticRoot(
        rules,
        calculation,
        benefit,
        (source["document_version_id"], source["generation_id"], anchor),
        root.manifest_sha256,
        not root.diagnostics,
    )
