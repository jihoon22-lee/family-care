"""V3 range claims require independent page-by-page contractual evidence."""

import hashlib
import json
from copy import deepcopy
from typing import Any

import pytest
from familycare_api.insurance_documents.metadata_validation import (
    MetadataSourceContext,
    validate_component_metadata,
)

from workers.analyzer.tests.test_document_metadata import _structure


def _inputs(*pages: str) -> tuple[dict[str, Any], dict[str, Any]]:
    source = _structure(*pages).to_dict()
    spans: list[dict[str, Any]] = []
    evidence = []
    for number, text in enumerate(pages, 1):
        node = next(
            n for n in source["nodes"] if n["page_number"] == number and n["kind"] == "BLOCK"
        )
        lines = text.splitlines()
        page_spans = []
        offset = 0
        for line in lines:
            page_spans.append(
                {
                    "node_id": node["node_id"],
                    "page_number": number,
                    "start": offset,
                    "end": offset + len(line),
                    "text": line,
                    "anchor_start": offset,
                    "anchor_end": offset + len(line),
                }
            )
            offset += len(line) + 1
        indices = list(range(len(spans), len(spans) + len(page_spans)))
        spans.extend(page_spans)
        evidence.append(
            {
                "page_number": number,
                "basis": "CONTRACTUAL_PROVISIONS",
                "previous_page": None if number == 1 else number - 1,
                "article_numbers": [int(lines[0].split("조")[0][1:])],
                "article_sequence_verified": True,
                "role_span_indices": indices,
            }
        )
    lineage = source["lineage"]
    component = {
        "identity": hashlib.sha256(
            json.dumps(
                [
                    "document-metadata-v3",
                    lineage["document_version_id"],
                    lineage["extraction_id"],
                    lineage["source_payload_sha256"],
                    lineage["ocr_revision"],
                    "terms",
                    1,
                    len(pages),
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest(),
        "role": "terms",
        "page_start": 1,
        "page_end": len(pages),
        "role_spans": spans,
        "facts": [],
        "conflicting_fields": [],
        "unresolved_fields": [],
        "authority": "CONTENT_CLASSIFICATION_ONLY",
        "range_evidence": evidence,
    }
    return component, source


FIRST = "제7조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
SECOND = (
    "제8조 (가상 제외 조건)\n회사는 계약자가 고의로 발생시킨 손해에 보험금을 지급하지 않습니다."
)


def test_body_ranges_need_no_repeated_title_or_metadata_labels() -> None:
    component, source = _inputs(FIRST, SECOND)
    result = validate_component_metadata(component, source, revision="document-metadata-v3")
    assert result is not None and result.role == "terms"
    assert result.facts == {}


@pytest.mark.parametrize(
    "change", ["missing_page", "forged_span", "wrong_previous", "invented_article"]
)
def test_v3_cannot_forge_or_omit_range_evidence(change: str) -> None:
    component, source = _inputs(FIRST, SECOND)
    if change == "missing_page":
        component["range_evidence"].pop()
    elif change == "forged_span":
        component["role_spans"][3]["text"] = "Invented synthetic statement"
    elif change == "wrong_previous":
        component["range_evidence"][1]["previous_page"] = None
    else:
        component["range_evidence"][1]["article_numbers"] = [999]
    assert validate_component_metadata(component, source, revision="document-metadata-v3") is None


def test_consecutive_physical_pages_do_not_override_a_new_document_boundary() -> None:
    component, source = _inputs(FIRST, SECOND)
    source = deepcopy(source)
    node = next(n for n in source["nodes"] if n["page_number"] == 2 and n["kind"] == "BLOCK")
    node["text"] = "상품설명서\n" + node["text"]
    # Keep exact source offsets valid so rejection must inspect the preceding context.
    for span in component["role_spans"][2:]:
        for field in ("start", "end", "anchor_start", "anchor_end"):
            span[field] += len("상품설명서\n")
    assert validate_component_metadata(component, source, revision="document-metadata-v3") is None


def test_article_restart_does_not_silently_merge_another_terms_document() -> None:
    component, source = _inputs(FIRST, SECOND.replace("제8조", "제1조"))
    assert validate_component_metadata(component, source, revision="document-metadata-v3") is None


@pytest.mark.parametrize("standalone_second_page", [False, True])
def test_api_rechecks_reference_context_before_a_claimed_body_range(
    standalone_second_page: bool,
) -> None:
    component, source = _inputs(FIRST, SECOND)
    first = next(node for node in source["nodes"] if node["page_number"] == 1)
    first["text"] += "\n다음은 약관을 설명하기 위한 예시입니다."
    if standalone_second_page:
        component["page_start"] = 2
        component["role_spans"] = component["role_spans"][2:]
        component["range_evidence"] = component["range_evidence"][1:]
        component["range_evidence"][0]["previous_page"] = None
        component["range_evidence"][0]["role_span_indices"] = [0, 1]
        lineage = source["lineage"]
        component["identity"] = hashlib.sha256(
            json.dumps(
                [
                    "document-metadata-v3",
                    lineage["document_version_id"],
                    lineage["extraction_id"],
                    lineage["source_payload_sha256"],
                    lineage["ocr_revision"],
                    "terms",
                    2,
                    2,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    assert validate_component_metadata(component, source, revision="document-metadata-v3") is None


def test_components_share_source_context_without_reloading_every_preceding_page() -> None:
    component, source = _inputs(FIRST, SECOND, SECOND.replace("제8조", "제9조"))
    calls: list[int] = []

    def load_page(number: int) -> dict[str, Any]:
        calls.append(number)
        return {
            "lineage": source["lineage"],
            "nodes": [node for node in source["nodes"] if node["page_number"] == number],
        }

    context = MetadataSourceContext(source["lineage"], load_page)
    for number in (2, 3):
        part = deepcopy(component)
        part["page_start"] = part["page_end"] = number
        part["role_spans"] = part["role_spans"][(number - 1) * 2 : number * 2]
        part["range_evidence"] = [part["range_evidence"][number - 1]]
        part["range_evidence"][0].update(previous_page=None, role_span_indices=[0, 1])
        lineage = source["lineage"]
        part["identity"] = hashlib.sha256(
            json.dumps(
                [
                    "document-metadata-v3",
                    lineage["document_version_id"],
                    lineage["extraction_id"],
                    lineage["source_payload_sha256"],
                    lineage["ocr_revision"],
                    "terms",
                    number,
                    number,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        assert validate_component_metadata(
            part,
            {"lineage": source["lineage"], "nodes": []},
            page_loader=load_page,
            source_context=context,
        )
    assert calls == [2, 1, 3]


@pytest.mark.parametrize(
    "change", ["boolean_index", "duplicate_index", "out_of_range", "boolean_page"]
)
def test_range_addresses_reject_noninteger_and_ambiguous_indices(change: str) -> None:
    component, source = _inputs(FIRST)
    evidence = component["range_evidence"][0]
    if change == "boolean_index":
        evidence["role_span_indices"] = [False, 1]
    elif change == "duplicate_index":
        evidence["role_span_indices"] = [0, 0, 1]
    elif change == "out_of_range":
        evidence["role_span_indices"] = [0, 99]
    else:
        evidence["page_number"] = True
    assert validate_component_metadata(component, source) is None
