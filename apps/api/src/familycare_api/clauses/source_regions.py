"""Locate complete, single-page clauses in retained source regions.

This is a source-addressing helper, not enrollment, applicability or rule authority.
The caller owns the retained projection's household, document, generation and
currentness checks. ``component_page_end`` must come from an independently
verified component boundary; a projected page list or a label cannot prove it.
The source digest binds this observation to raw spans and their retained
addresses. It is not a stable identity across different extraction generations.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from familycare_api.insurance_documents.terms_body_validation import (
    _DOCUMENT_REFERENCE,
    _REFERENCE,
    _external_reference_top,
    _layout_box,
    _local_nodes,
    _preceding_reference,
    _reference_text,
    _regions,
)

_ARTICLE_PREFIX = re.compile(
    r"^(?:제\s*(?P<ko>[1-9][0-9]{0,3})\s*조|Article\s+(?P<en>[1-9][0-9]{0,3}))"
    r"(?P<tail>.*)$",
    re.IGNORECASE,
)
_OTHER_HEADING = re.compile(
    r"^(?:제\s*[1-9][0-9]{0,3}\s*(?:장|절|관)|별표\s*[1-9][0-9]{0,3}"
    r"|(?:Chapter|Section|Appendix)\s+[A-Za-z0-9]+)(?:\s|[(（]|$)",
    re.IGNORECASE,
)
_TITLE = re.compile(r"[(（][^()（）\n]{1,160}[)）]")
_KOREAN_SENTENCE = re.compile(
    r"(?:합니다|한다|됩니다|된다|있습니다|있다|없습니다|없다|따릅니다|따른다|참조|준용)[.!?。]?$"
)
_ENGLISH_SENTENCE = re.compile(
    r"\b(?:apply|applies|refer|refers|govern|governs|shall|must|includes?|means|is|are)\b",
    re.IGNORECASE,
)
_HEX = re.compile(r"[0-9a-f]{64}")
_MAX_NODES = 4096
_MAX_TEXT = 262144
_MAX_LINES = 4096
_MAX_REGIONS = 64

Box = tuple[float, float, float, float]


@dataclass(frozen=True, slots=True, repr=False)
class ClauseSourceSpan:
    node_id: str
    page_number: int
    start: int
    end: int
    text: str
    source_layer: str
    bbox: Box


@dataclass(frozen=True, slots=True, repr=False)
class ClauseSourceRegion:
    label: str
    heading: ClauseSourceSpan
    body: tuple[ClauseSourceSpan, ...]
    body_text: str
    content_sha256: str
    source_sha256: str
    complete: bool
    boundary_kind: Literal["NEXT_HEADING", "COMPONENT_END", "UNRESOLVED"]
    boundary: ClauseSourceSpan | None
    reason_codes: tuple[str, ...]
    table_context: tuple[ClauseSourceSpan, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ClauseSourceObservation:
    regions: tuple[ClauseSourceRegion, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class ClauseSourceResolution:
    status: Literal["MATCH", "UNKNOWN"]
    region: ClauseSourceRegion | None = None
    reason_codes: tuple[str, ...] = ()


def _normative_reference(text: str) -> bool:
    # The old classification heading recognizer also accepts free-form titles.
    # A sentence starting with an article reference is not a heading boundary.
    return bool(
        _KOREAN_SENTENCE.search(text)
        or (text.endswith((".", "!", "?")) and _ENGLISH_SENTENCE.search(text))
    )


def _heading(text: str) -> tuple[str | None, bool]:
    """Return a supported label, or flag an explicit unsupported boundary."""
    value = text.strip()
    matched = _ARTICLE_PREFIX.fullmatch(value)
    if matched is None:
        return None, bool(_OTHER_HEADING.match(value) and not _normative_reference(value))
    tail = matched["tail"].strip()
    if not tail or _TITLE.fullmatch(tail):
        label = f"제{int(matched['ko'])}조" if matched["ko"] else f"Article {int(matched['en'])}"
        return label, False
    return None, not _normative_reference(value)


def _span(node: dict[str, Any], start: int, end: int) -> ClauseSourceSpan:
    box = _layout_box(node)
    return ClauseSourceSpan(
        node["node_id"],
        node["page_number"],
        start,
        end,
        node["text"][start:end],
        node["source_layer"],
        (box[0], box[1], box[2], box[3]),
    )


def _body_text(body: list[ClauseSourceSpan], by_id: dict[str, dict[str, Any]]) -> str:
    text = ""
    previous = None
    for span in body:
        if previous is not None:
            # Preserve original whitespace between spans in the same source
            # node. Different retained nodes have an explicit line separator.
            text += (
                by_id[span.node_id]["text"][previous.end : span.start]
                if previous.node_id == span.node_id
                else "\n"
            )
        text += span.text
        previous = span
    return text


def _region(
    label: str,
    heading: ClauseSourceSpan,
    body: list[ClauseSourceSpan],
    by_id: dict[str, dict[str, Any]],
    content_sha256: str,
    boundary_kind: Literal["NEXT_HEADING", "COMPONENT_END", "UNRESOLVED"],
    boundary: ClauseSourceSpan | None = None,
) -> ClauseSourceRegion:
    complete = bool(body) and boundary_kind != "UNRESOLVED"
    text = _body_text(body, by_id)
    referenced_nodes = {span.node_id for span in (heading, *body)}
    context_ids = {
        identifier
        for node_id in referenced_nodes
        for identifier in by_id[node_id].get("context_node_ids", [])
        if identifier not in referenced_nodes
    }
    contexts = tuple(
        _span(by_id[identifier], 0, len(by_id[identifier]["text"]))
        for identifier in sorted(context_ids)
    )
    payload = {
        "revision": "clause-source-region-v1",
        "content_sha256": content_sha256,
        "heading": asdict(heading),
        "body": [asdict(span) for span in body],
        "body_text": text,
        "table_context": [asdict(span) for span in contexts],
        "boundary_kind": boundary_kind,
        "boundary": asdict(boundary) if boundary is not None else None,
    }
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return ClauseSourceRegion(
        label,
        heading,
        tuple(body),
        text,
        content_sha256,
        digest,
        complete,
        boundary_kind,
        boundary,
        () if complete else ("CLAUSE_SOURCE_BOUNDARY_UNRESOLVED",),
        contexts,
    )


def _collect_region(
    nodes: list[dict[str, Any]],
    by_id: dict[str, dict[str, Any]],
    content_sha256: str,
    *,
    page_verified: bool,
    end_verified: bool,
    reference_top: float | None,
) -> list[ClauseSourceRegion]:
    result: list[ClauseSourceRegion] = []
    current: tuple[str, ClauseSourceSpan] | None = None
    body: list[ClauseSourceSpan] = []
    truncated = False
    last_number = 0

    def finish(
        kind: Literal["NEXT_HEADING", "COMPONENT_END", "UNRESOLVED"],
        boundary: ClauseSourceSpan | None = None,
    ) -> None:
        if current is not None:
            result.append(
                _region(
                    *current,
                    body,
                    by_id,
                    content_sha256,
                    kind if page_verified else "UNRESOLVED",
                    boundary,
                )
            )
            if len(result) > _MAX_REGIONS:
                raise ValueError

    for node in nodes:
        if reference_top is not None and _layout_box(node)[3] > reference_top:
            truncated = True
            break
        offset = 0
        for line in node["text"].splitlines(keepends=True):
            raw = line.rstrip("\r\n")
            start, end = offset, offset + len(raw)
            offset += len(line)
            if not raw.strip():
                continue
            if _REFERENCE.search(_reference_text(raw)):
                truncated = True
                break
            if node["kind"] == "TABLE_ROW" and node.get("row_role") == "header":
                if current is not None:
                    body.append(_span(node, start, end))
                continue
            label, unsupported = _heading(raw)
            if unsupported:
                finish("UNRESOLVED", _span(node, start, end))
                current, body = None, []
                # Subsequent supported headings can start new regions, but the
                # unsupported section's body cannot attach to an earlier clause.
                continue
            if label is not None:
                number = int(re.sub(r"[^0-9]", "", label))
                if number <= last_number:
                    # A repeated heading may continue the same clause. Neither
                    # fragment is independently complete without that proof.
                    return []
                last_number = number
                heading = _span(node, start, end)
                finish("NEXT_HEADING", heading)
                current, body = (label, heading), []
            elif current is not None:
                body.append(_span(node, start, end))
        if truncated:
            break
    finish("COMPONENT_END" if end_verified and not truncated else "UNRESOLVED")
    return result


def observe_clause_source_regions(
    structure: Mapping[str, Any],
    page_number: int,
    *,
    component_page_end: int | None = None,
) -> ClauseSourceObservation:
    """Observe one complete page projection without inferring a component end.

    ``component_page_end`` is a caller-verified component contract, never an IR
    proposal or the last projected page. It only closes an intact single region
    with a full ``pages``/``unresolved`` manifest and no omitted page ranges,
    layout barriers or competing column flows. Repository projections containing
    only lineage/nodes retain partial observations; they cannot prove that no
    body node was omitted, even when the next heading is visible.
    Cross-page clauses and unsupported headings remain incomplete. MATCH during
    resolution means one complete retained region, not that its legal text applies.
    """
    try:
        return _observe(structure, page_number, component_page_end)
    except KeyError, TypeError, ValueError, IndexError, OverflowError:
        return ClauseSourceObservation((), ("CLAUSE_SOURCE_INPUT_INVALID",))


def _observe(
    structure: Mapping[str, Any], page_number: int, component_page_end: int | None
) -> ClauseSourceObservation:
    if (
        type(page_number) is not int
        or not 1 <= page_number <= 500
        or (
            component_page_end is not None
            and (
                type(component_page_end) is not int or not page_number <= component_page_end <= 500
            )
        )
    ):
        raise ValueError
    digest = structure["lineage"]["content_sha256"]
    if not isinstance(digest, str) or _HEX.fullmatch(digest) is None:
        raise ValueError
    pages = (
        [p for p in structure["pages"] if p["page_number"] == page_number]
        if "pages" in structure
        else None
    )
    if pages is not None and (len(pages) != 1 or pages[0]["active_layer"] not in ("native", "ocr")):
        raise ValueError
    nodes = [node for node in structure["nodes"] if node["page_number"] == page_number]
    if not nodes or len(nodes) > _MAX_NODES:
        raise ValueError
    by_id = {node["node_id"]: node for node in nodes}
    if (
        len(by_id) != len(nodes)
        or sum(len(node["text"]) for node in nodes) > _MAX_TEXT
        or sum(len(node["text"].splitlines()) for node in nodes) > _MAX_LINES
    ):
        raise ValueError
    if pages is not None:
        identifiers = pages[0]["node_ids"]
        if (
            len(set(identifiers)) != len(identifiers)
            or set(identifiers) != set(by_id)
            or any(node["source_layer"] != pages[0]["active_layer"] for node in nodes)
        ):
            raise ValueError
    unresolved = [r for r in structure.get("unresolved", ()) if r["page_number"] == page_number]
    if any(r.get("node_id") is None for r in unresolved):
        return ClauseSourceObservation((), ("CLAUSE_SOURCE_BOUNDARY_UNRESOLVED",))
    selected, barriers = _local_nodes(nodes, by_id)
    regions = _regions(selected, barriers)
    page_verified = pages is not None and "unresolved" in structure
    article_tops = [
        _layout_box(node)[1]
        for node in selected
        if any(_heading(line)[0] is not None for line in node["text"].splitlines())
    ]
    if article_tops and any(
        _DOCUMENT_REFERENCE.search(_reference_text(node["text"].partition("\n")[0]))
        and _layout_box(node)[1] <= min(article_tops)
        for node in selected
    ):
        return ClauseSourceObservation((), ("CLAUSE_SOURCE_REFERENCE_CONTEXT",))
    end_verified = (
        component_page_end == page_number
        and page_verified
        and not barriers
        and not unresolved
        and len(regions) == 1
        and {n["node_id"] for n in regions[0]} == {n["node_id"] for n in selected}
    )
    observed: list[ClauseSourceRegion] = []
    for region in regions:
        if _preceding_reference(region, nodes):
            continue
        observed.extend(
            _collect_region(
                region,
                by_id,
                digest,
                page_verified=page_verified,
                end_verified=end_verified,
                reference_top=_external_reference_top(region, [*selected, *barriers]),
            )
        )
        if len(observed) > _MAX_REGIONS:
            raise ValueError
    reasons = (
        ("CLAUSE_SOURCE_PAGE_MANIFEST_UNVERIFIED",)
        if not page_verified
        else ()
        if observed
        else ("CLAUSE_SOURCE_NOT_FOUND",)
    )
    return ClauseSourceObservation(tuple(observed), reasons)


def resolve_clause_source_region(
    observation: ClauseSourceObservation,
    *,
    label: str,
    heading_bbox: Box | None = None,
    body_text: str | None = None,
) -> ClauseSourceResolution:
    """Require one location satisfying every supplied raw-text/geometry constraint.

    Label parsing only discovers candidates; it never establishes physical
    identity. A broad containing box is not an exact heading location. Partial,
    missing and ambiguous candidates all remain UNKNOWN rather than NO_MATCH.
    """
    if (
        not isinstance(observation, ClauseSourceObservation)
        or not isinstance(label, str)
        or not 1 <= len(label) <= 240
    ):
        return ClauseSourceResolution("UNKNOWN", reason_codes=("CLAUSE_SOURCE_INPUT_INVALID",))
    parsed, unsupported = _heading(label)
    if parsed is None or unsupported:
        return ClauseSourceResolution("UNKNOWN", reason_codes=("CLAUSE_SOURCE_INPUT_INVALID",))
    candidates = [
        region
        for region in observation.regions
        if region.label == parsed
        and (heading_bbox is None or region.heading.bbox == heading_bbox)
        and (body_text is None or region.body_text == body_text)
    ]
    if not candidates:
        return ClauseSourceResolution(
            "UNKNOWN", reason_codes=observation.reason_codes or ("CLAUSE_SOURCE_NOT_FOUND",)
        )
    if len(candidates) != 1:
        return ClauseSourceResolution("UNKNOWN", reason_codes=("CLAUSE_SOURCE_AMBIGUOUS",))
    if not candidates[0].complete:
        return ClauseSourceResolution(
            "UNKNOWN",
            reason_codes=tuple(
                dict.fromkeys((*observation.reason_codes, *candidates[0].reason_codes))
            ),
        )
    return ClauseSourceResolution("MATCH", candidates[0])
