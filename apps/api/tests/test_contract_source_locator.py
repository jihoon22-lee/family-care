"""Canonical contract identity uses only wholly synthetic immutable sources."""

import hashlib
import json
from copy import deepcopy
from typing import Any
from uuid import UUID, uuid5

import pytest
from familycare_api.policies.contract_source_locator import contract_source_locator

DOCUMENT = UUID("00000000-0000-4000-8000-000000000101")
MEMBER = UUID("00000000-0000-4000-8000-000000000102")
NUMBER = "synthetic-policy-001"


def _source(*, table: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    node = {
        "node_id": "contract-anchor",
        "kind": "TABLE_ROW" if table else "BLOCK",
        "source_layer": "native",
        "page_number": 1,
        "text": f"계약번호\t{NUMBER}" if table else f"제목\n계약번호: {NUMBER}\n끝",
    }
    if table:
        node.update(
            row_role="data",
            row_index=1,
            cells=[
                {"row_index": 1, "column_index": 0, "text": "계약번호"},
                {"row_index": 1, "column_index": 1, "text": NUMBER},
            ],
        )
    start = 0 if table else 3
    end = len(node["text"]) if table else len(node["text"]) - 2
    return (
        {
            "lineage": {"document_version_id": str(DOCUMENT), "content_sha256": "a" * 64},
            "nodes": [node],
        },
        {
            "state": "RESOLVED",
            "family_member_id": str(MEMBER),
            "contract_scope_id": str(uuid5(DOCUMENT, "local-contract-v1:" + NUMBER)),
            "anchor_refs": [
                {
                    "node_id": node["node_id"],
                    "page": 1,
                    "start": start,
                    "end": end,
                    "kind": "contract",
                }
            ],
        },
    )


def test_new_document_version_preserves_same_content_contract_identity() -> None:
    source, association = _source()
    expected = {
        "schema_version": "contract-source-v1",
        "content_sha256": "a" * 64,
        "family_member_id": str(MEMBER),
        "contract_number_sha256": hashlib.sha256(NUMBER.encode()).hexdigest(),
    }
    assert contract_source_locator(source, association) == expected
    other_document = UUID("00000000-0000-4000-8000-000000000103")
    source["lineage"]["document_version_id"] = str(other_document)
    source["lineage"]["extraction_id"] = "synthetic-new-extraction"
    association["contract_scope_id"] = str(uuid5(other_document, "local-contract-v1:" + NUMBER))
    assert contract_source_locator(source, association) == expected
    assert NUMBER not in json.dumps(expected)


@pytest.mark.parametrize("label", ["계약번호", "증권번호", "Policy number", "CONTRACT NUMBER"])
@pytest.mark.parametrize("kind", ["BLOCK", "TEXT_LINE", "TABLE_ROW"])
def test_supported_labels_and_views_use_worker_normalization(label: str, kind: str) -> None:
    source, association = _source(table=kind == "TABLE_ROW")
    node = source["nodes"][0]
    node["kind"] = kind
    raw_number = " Ｓｙｎｔｈｅｔｉｃ－Ｐｏｌｉｃｙ－００１  "
    if kind == "TABLE_ROW":
        node["cells"][0]["text"] = label
        node["cells"][1]["text"] = raw_number
        node["text"] = label + "\t" + raw_number
    else:
        node["text"] = label + "：" + raw_number
    association["anchor_refs"][0].update(
        start=0, end=len(node["text"]) if kind == "TABLE_ROW" else len(node["text"].rstrip())
    )
    result = contract_source_locator(source, association)
    assert result is not None
    assert result["contract_number_sha256"] == hashlib.sha256(NUMBER.encode()).hexdigest()


@pytest.mark.parametrize("change", ["number", "member", "content"])
def test_distinct_contract_number_member_or_content_is_not_aliased(change: str) -> None:
    source, association = _source()
    original = contract_source_locator(source, association)
    if change == "number":
        source["nodes"][0]["text"] = source["nodes"][0]["text"].replace("001", "002")
        association["contract_scope_id"] = str(
            uuid5(DOCUMENT, "local-contract-v1:synthetic-policy-002")
        )
    elif change == "member":
        association["family_member_id"] = "00000000-0000-4000-8000-000000000104"
    else:
        source["lineage"]["content_sha256"] = "b" * 64
    changed = contract_source_locator(source, association)
    assert changed is not None and changed != original


@pytest.mark.parametrize(
    "variant",
    [
        "unresolved",
        "bad_scope",
        "missing_member",
        "multiple_anchors",
        "wrong_page",
        "cropped_start",
        "cropped_end",
        "outside_node",
        "missing_node",
        "duplicate_node",
        "unknown_kind",
        "bad_content",
        "ambiguous_line",
    ],
)
def test_invalid_association_or_anchor_cannot_create_a_locator(variant: str) -> None:
    source, association = _source()
    anchor = association["anchor_refs"][0]
    if variant == "unresolved":
        association["state"] = "UNRESOLVED"
    elif variant == "bad_scope":
        association["contract_scope_id"] = str(MEMBER)
    elif variant == "missing_member":
        association.pop("family_member_id")
    elif variant == "multiple_anchors":
        association["anchor_refs"].append(deepcopy(anchor))
    elif variant == "wrong_page":
        anchor["page"] = 2
    elif variant == "cropped_start":
        anchor["start"] += 1
    elif variant == "cropped_end":
        anchor["end"] -= 1
    elif variant == "outside_node":
        anchor["end"] += 50
    elif variant == "missing_node":
        anchor["node_id"] = "missing"
    elif variant == "duplicate_node":
        source["nodes"].append(deepcopy(source["nodes"][0]))
    elif variant == "unknown_kind":
        source["nodes"][0]["kind"] = "INVENTED"
    elif variant == "bad_content":
        source["lineage"]["content_sha256"] = "not-a-content-digest"
    else:
        source["nodes"][0]["issue_codes"] = ["LINE_COLUMN_CONTEXT_UNRESOLVED"]
    assert contract_source_locator(source, association) is None


@pytest.mark.parametrize(
    "variant",
    [
        "header",
        "row_span",
        "column_span",
        "sparse",
        "wrong_row",
        "duplicate_cell",
        "multiple_numbers",
        "partial_row",
    ],
)
def test_table_requires_one_unspanned_adjacent_contract_pair(variant: str) -> None:
    source, association = _source(table=True)
    node = source["nodes"][0]
    if variant == "header":
        node["row_role"] = "header"
    elif variant == "row_span":
        node["cells"][0]["row_span"] = 2
    elif variant == "column_span":
        node["cells"][1]["column_span"] = 2
    elif variant == "sparse":
        node["cells"][1]["column_index"] = 2
    elif variant == "wrong_row":
        node["cells"][1]["row_index"] = 2
    elif variant == "duplicate_cell":
        node["cells"].append(deepcopy(node["cells"][1]))
    elif variant == "multiple_numbers":
        node["cells"].extend(
            [
                {"row_index": 1, "column_index": 2, "text": "계약번호"},
                {"row_index": 1, "column_index": 3, "text": "synthetic-policy-002"},
            ]
        )
    else:
        association["anchor_refs"][0]["end"] -= 1
    assert contract_source_locator(source, association) is None


def test_validated_ocr_anchor_can_share_identity_without_asserting_enrollment() -> None:
    source, association = _source()
    expected = contract_source_locator(source, association)
    source["nodes"][0]["source_layer"] = "ocr"
    assert contract_source_locator(source, association) == expected


def test_noncontract_anchor_is_not_part_of_the_contract_identity() -> None:
    source, association = _source()
    expected = contract_source_locator(source, association)
    association["anchor_refs"].append(
        {"kind": "insured", "node_id": "other-node", "page": 1, "start": 0, "end": 10}
    )
    assert contract_source_locator(source, association) == expected
