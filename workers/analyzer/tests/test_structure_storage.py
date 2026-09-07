"""Wholly synthetic lossless page storage and canonical streaming identity."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from familycare_worker.document_structure import (
    ChunkPlan,
    DocumentStructure,
    build_document_structure,
    plan_structure_chunks,
)
from familycare_worker.structure_storage import (
    StructureStorageError,
    iter_structure_pages,
    restore_structure_payload,
    structure_header,
    structure_identity,
)


def _fixture() -> tuple[DocumentStructure, ChunkPlan]:
    def page(number: int, **values: Any) -> dict[str, Any]:
        return {
            "page_number": number,
            "quality": {"classification": "TEXT_SUFFICIENT"},
            "blocks": [],
            "tables": [],
            **values,
        }

    def table(texts: list[str], **metadata: Any) -> dict[str, Any]:
        return {
            "bbox": [10, 100, 300, 120],
            "cells": [
                {
                    "row_index": 0,
                    "column_index": index,
                    "text": text,
                    "bbox": [10 + index * 100, 100, 100 + index * 100, 110],
                }
                for index, text in enumerate(texts)
            ],
            "metadata_json": metadata,
        }

    source = {
        "document_version_id": str(UUID(int=1)),
        "content_sha256": "a" * 64,
        "page_count": 4,
        "synthetic_metadata": {"unicode": "합성 😀", "numbers": [1.5, -0.0, 1e-8, 1e20]},
        "pages": [
            page(4, quality={"classification": "OCR_REQUIRED"}),
            page(
                3,
                tables=[
                    table(
                        ["Sample Rider", "100"],
                        continuation_of={
                            "page_number": 1,
                            "table_index": 0,
                        },
                    )
                ],
            ),
            page(
                1,
                blocks=[
                    {"reading_order": 0, "text": "Sample", "bbox": [10, 10, 40, 20]},
                    {"reading_order": 1, "text": "Policy", "bbox": [44, 10, 74, 20]},
                    {
                        "reading_order": 2,
                        "text": "policy certificate sum assured",
                        "bbox": [10, 30, 300, 40],
                    },
                ],
                tables=[table(["Rider", "Amount"], header_rows=[0])],
            ),
        ],
    }
    structure = build_document_structure(
        source,
        extraction_id=UUID(int=2),
        extraction_revision="synthetic-storage-v1",
        ocr_revision="synthetic-ocr-v1",
        ocr_pages=[
            {
                "page_number": 4,
                "status": "completed",
                "blocks": [
                    {"reading_order": 0, "text": "합성 OCR", "bbox": [10, 10, 100, 20]},
                ],
                "tables": [],
                "metadata": {"retained": True},
            },
            {"page_number": 1, "status": "failed", "blocks": [], "tables": []},
        ],
    )
    # Storage positions, not physical-page sorting, own all original array orders.
    structure = replace(
        structure,
        pages=tuple(reversed(structure.pages)),
        nodes=tuple(reversed(structure.nodes)),
        components=tuple(reversed(structure.components)),
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=100, max_context_chars=1000, max_chunks=100
    )
    return structure, plan


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _stored() -> tuple[DocumentStructure, dict[str, Any], list[dict[str, Any]]]:
    structure, plan = _fixture()
    _, digest, _, _ = structure_identity(structure, plan)
    return (
        structure,
        structure_header(structure, digest),
        [payload for _, payload in iter_structure_pages(structure)],
    )


def test_streaming_identity_exactly_matches_existing_canonical_json() -> None:
    structure, plan = _fixture()
    encoded, planned = _canonical(structure.to_dict()), _canonical(plan.to_dict())
    assert structure_identity(structure, plan) == (
        hashlib.sha256(encoded + b"\0" + planned).hexdigest(),
        hashlib.sha256(encoded).hexdigest(),
        len(encoded),
        len(planned),
    )


@pytest.mark.parametrize("part", ["structure", "plan"])
def test_streaming_limits_accept_exact_bytes_and_reject_the_next_byte(part: str) -> None:
    structure, plan = _fixture()
    result = structure_identity(structure, plan)
    limits = {"maximum_structure_bytes": result[2], "maximum_plan_bytes": result[3]}
    assert structure_identity(structure, plan, **limits) == result
    limits[f"maximum_{part}_bytes"] -= 1
    with pytest.raises(StructureStorageError, match="^DOCUMENT_STRUCTURE_STORAGE_INVALID$"):
        structure_identity(structure, plan, **limits)


def test_pages_reconstruct_every_source_value_order_node_and_cross_page_reference() -> None:
    structure, header, pages = _stored()
    assert any(node.source_spans for node in structure.nodes)
    assert any(node.context_node_ids for node in structure.nodes if node.page_number == 3)
    assert header["storage_layout"] == "page-v1"
    assert header["stored_page_count"] == 4
    assert all(header[key] == [] for key in ("pages", "nodes", "components", "source_ocr_pages"))
    assert header["source_extraction"]["pages"] == []
    missing = next(item for item in pages if item["page"]["page_number"] == 2)
    assert missing["source_native"] is missing["source_ocr"] is None
    assert missing["page"]["active_layer"] == "unavailable"
    before = deepcopy((header, pages))
    assert restore_structure_payload(header, list(reversed(pages))) == structure.to_dict()
    assert (header, pages) == before


def test_codec_does_not_call_whole_structure_or_plan_to_dict_or_json_dumps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structure, plan = _fixture()
    expected = structure.to_dict()

    def forbidden(*args: Any, **kwargs: Any) -> Any:
        pytest.fail("whole-document serialization must not be used")

    monkeypatch.setattr(DocumentStructure, "to_dict", forbidden)
    monkeypatch.setattr(ChunkPlan, "to_dict", forbidden)
    monkeypatch.setattr(json, "dumps", forbidden)
    _, digest, _, _ = structure_identity(structure, plan)
    header = structure_header(structure, digest)
    pages = list(iter_structure_pages(structure))
    assert [number for number, _ in pages] == [page.page_number for page in structure.pages]
    assert restore_structure_payload(header, [payload for _, payload in pages]) == expected


@pytest.mark.parametrize(
    "tamper",
    [
        "missing_page",
        "duplicate_page",
        "duplicate_page_position",
        "page_out_of_range",
        "node_position_gap",
        "duplicate_node_position",
        "component_position_gap",
        "source_position_gap",
        "source_wrong_page",
        "dropped_last_node",
        "wrong_digest",
    ],
)
def test_incomplete_or_conflicting_page_payloads_fail_without_echoing_source(tamper: str) -> None:
    _, header, pages = _stored()
    occupied = next(item for item in pages if len(item["nodes"]) > 1)
    native = next(item for item in pages if item["source_native"] is not None)
    if tamper == "missing_page":
        pages.pop()
    elif tamper == "duplicate_page":
        pages[0] = deepcopy(pages[1])
    elif tamper == "duplicate_page_position":
        pages[0]["page_position"] = pages[1]["page_position"]
    elif tamper == "page_out_of_range":
        pages[0]["page"]["page_number"] = 501
    elif tamper == "node_position_gap":
        occupied["node_positions"][0] = 999
    elif tamper == "duplicate_node_position":
        occupied["node_positions"][0] = occupied["node_positions"][1]
    elif tamper == "component_position_gap":
        pages[0]["component_positions"][0] = 999
    elif tamper == "source_position_gap":
        native["source_native"]["position"] = 999
    elif tamper == "source_wrong_page":
        native["source_native"]["page"]["page_number"] = 2
    elif tamper == "dropped_last_node":
        latest = max(position for item in pages for position in item["node_positions"])
        item = next(item for item in pages if latest in item["node_positions"])
        index = item["node_positions"].index(latest)
        item["node_positions"].pop(index)
        item["nodes"].pop(index)
    else:
        header["structure_digest_sha256"] = "f" * 64
    with pytest.raises(StructureStorageError, match="^DOCUMENT_STRUCTURE_STORAGE_INVALID$"):
        restore_structure_payload(header, pages)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), object()])
def test_non_json_values_are_rejected_with_a_generic_storage_error(invalid: Any) -> None:
    structure, plan = _fixture()
    structure = replace(
        structure, source_extraction={**structure.source_extraction, "synthetic_invalid": invalid}
    )
    with pytest.raises(StructureStorageError, match="^DOCUMENT_STRUCTURE_STORAGE_INVALID$"):
        structure_identity(structure, plan)
