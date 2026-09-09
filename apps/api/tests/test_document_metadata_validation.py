"""API independently checks protected proposal spans against retained local IR."""

import hashlib
import json
from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_structure import StructureCell

from workers.analyzer.tests.test_document_metadata import (
    _structure,
    _table_cover,
    _table_cover_with_overlapping_suffix,
)


def _inputs(
    text: str = "보험약관\n보험사: Sample Assurance\n상품코드: 001-SAMPLE\n판본일: 2020.01.01",
) -> tuple[dict[str, Any], dict[str, Any]]:
    structure = _structure(text)
    proposal = metadata_proposal(structure, UUID(int=205), "c" * 64)
    source = structure.to_dict()
    return deepcopy(proposal["components"][0]), {
        "lineage": source["lineage"],
        "nodes": source["nodes"],
    }


def _legacy_identity(
    component: dict[str, Any], source: dict[str, Any], *, revision: str = "document-metadata-v1"
) -> None:
    if revision in {"document-metadata-v1", "document-metadata-v2"}:
        component.pop("range_evidence", None)
    lineage = source["lineage"]
    component["identity"] = hashlib.sha256(
        json.dumps(
            [
                revision,
                lineage["document_version_id"],
                lineage["extraction_id"],
                lineage["source_payload_sha256"],
                lineage["ocr_revision"],
                component["role"],
                component["page_start"],
                component["page_end"],
            ],
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def test_old_revision_retains_its_original_validation_semantics() -> None:
    component, source = _inputs()
    _legacy_identity(component, source)
    assert validate_component_metadata(component, source, revision="document-metadata-v1")
    assert validate_component_metadata(component, source) is None
    component, source = _inputs("무배당 Sample 가족보험 약관\n보험회사 Sample Assurance")
    _legacy_identity(component, source)
    assert validate_component_metadata(component, source, revision="document-metadata-v1") is None


def test_api_rechecks_original_label_value_and_date_semantics() -> None:
    component, source = _inputs()
    result = validate_component_metadata(component, source)
    assert result is not None and result.role == "terms"
    assert result.facts["insurer"] == ("Sample Assurance",)
    assert result.facts["product_code"] == ("001-SAMPLE",)
    assert result.facts["edition_date"] == ("2020-01-01",)
    assert "applicability_start" not in result.facts


def test_api_rechecks_unlabelled_cover_product_and_plain_label_separator() -> None:
    component, source = _inputs(
        "무배당 Sample 가족보험\n보험약관\n보험회사 Sample Assurance\n상품코드 SAMPLE-A"
    )
    checked = validate_component_metadata(component, source)
    assert checked is not None
    assert checked.facts["product_name"] == ("무배당 Sample 가족보험",)
    assert checked.facts["insurer"] == ("Sample Assurance",)
    source["nodes"][0]["text"] = source["nodes"][0]["text"].replace("가족보험", "보험 예시")
    assert validate_component_metadata(component, source) is None


def test_api_rechecks_the_table_cell_title_and_preceding_cells() -> None:
    structure = _table_cover()
    proposal = metadata_proposal(structure, UUID(int=205), "c" * 64)
    component = proposal["components"][0]
    source = structure.to_dict()
    assert validate_component_metadata(component, source) is not None
    # Geometry puts an unrelated table row ahead of the title; original storage order
    # alone must not make that later title an opening caption.
    row = next(node for node in source["nodes"] if len(node.get("cells", [])) == 2)
    row["cells"][0]["text"] = "청구 제출서류"
    row["cells"][0]["bbox"] = [10, 1, 110, 15]
    row["cells"][1]["bbox"] = [120, 1, 350, 15]
    row["text"] = "청구 제출서류\tSample Assurance"
    assert validate_component_metadata(component, source) is None


def _prefix_component(source: dict[str, Any]) -> dict[str, Any]:
    """Build the claimed prefix from exact retained source nodes, independently of analysis."""
    title = next(
        node
        for node in source["nodes"]
        if node["kind"] == "TABLE_ROW" and node["text"] == "보험약관"
    )
    insurer = next(
        node
        for node in source["nodes"]
        if node["kind"] == "TABLE_ROW" and node["text"] == "보험회사\tSample Assurance"
    )
    component = deepcopy(
        metadata_proposal(_table_cover(), UUID(int=205), "c" * 64)["components"][0]
    )
    for span in component["role_spans"]:
        span["node_id"] = title["node_id"]
    for fact in component["facts"]:
        for span in fact["spans"]:
            span["node_id"] = insurer["node_id"]
    component["unresolved_fields"] = ["edition_date", "product_code"]
    _legacy_identity(component, source, revision="document-metadata-v7")
    return component


def test_api_accepts_only_the_proven_prefix_before_a_known_late_overlap() -> None:
    source = _table_cover_with_overlapping_suffix().to_dict()
    component = _prefix_component(source)
    checked = validate_component_metadata(component, source)
    assert checked is not None and checked.role == "terms"
    assert checked.facts == {"insurer": ("Sample Assurance",)}
    assert checked.unresolved_fields == ("edition_date", "product_code")


@pytest.mark.parametrize("field", ["product_code", "edition_date"])
def test_api_rejects_real_but_quarantined_suffix_metadata(field: str) -> None:
    source = _table_cover_with_overlapping_suffix().to_dict()
    component = _prefix_component(source)
    text = "SYNTHETIC-UNTRUSTED" if field == "product_code" else "2024-01-01"
    node = next(
        node for node in source["nodes"] if node["kind"] == "TABLE_ROW" and text in node["text"]
    )
    start = node["text"].index(text)
    component["facts"].append(
        {
            "field": field,
            "value": text,
            "spans": [
                {
                    "node_id": node["node_id"],
                    "page_number": 1,
                    "start": start,
                    "end": start + len(text),
                    "text": text,
                    "anchor_start": 0,
                    "anchor_end": len(node["text"]),
                }
            ],
        }
    )
    assert validate_component_metadata(component, source) is None


@pytest.mark.parametrize(
    "options",
    [{"top": 1}, {"top": 25}, {"checklist": True}, {"missing_bbox": True}],
)
def test_api_refuses_prefix_claims_crossing_earlier_or_unbounded_layout(options) -> None:
    source = _table_cover_with_overlapping_suffix(**options).to_dict()
    assert validate_component_metadata(_prefix_component(source), source) is None


@pytest.mark.parametrize("change", ["header", "mixed_checklist"])
def test_api_table_title_cannot_bypass_header_or_mixed_checklist_context(change: str) -> None:
    structure = _table_cover()
    component = metadata_proposal(structure, UUID(int=205), "c" * 64)["components"][0]
    source = structure.to_dict()
    title = next(
        node for node in source["nodes"] if node["kind"] == "TABLE_ROW" and len(node["cells"]) == 1
    )
    if change == "header":
        title["row_role"] = "header"
    else:
        row = deepcopy(title)
        row.update(node_id="synthetic-mixed-checklist", row_index=0, text="청구 제출서류\t보험사")
        row["cells"] = [
            dict(row["cells"][0], row_index=0, text="청구 제출서류", bbox=[10, 1, 110, 15]),
            dict(
                row["cells"][0], row_index=0, column_index=1, text="보험사", bbox=[120, 1, 350, 15]
            ),
        ]
        source["nodes"].append(row)
    assert validate_component_metadata(component, source) is None


def test_api_checks_plain_label_context_even_with_complete_literal_spans() -> None:
    component, source = _inputs("보험약관\n상품명: Sample Policy")
    prefix = "보험사 제출서류\n"
    node = source["nodes"][0]
    node["text"] = prefix + node["text"]
    for span in [
        *component["role_spans"],
        *[span for fact in component["facts"] for span in fact["spans"]],
    ]:
        for key in ("start", "end", "anchor_start", "anchor_end"):
            span[key] += len(prefix)
    component["facts"].append(
        {
            "field": "insurer",
            "value": "제출서류",
            "spans": [
                {
                    "node_id": node["node_id"],
                    "page_number": 1,
                    "start": len("보험사 "),
                    "end": len(prefix) - 1,
                    "text": "제출서류",
                    "anchor_start": 0,
                    "anchor_end": len(prefix) - 1,
                }
            ],
        }
    )
    assert validate_component_metadata(component, source) is None


def test_api_retains_a_named_reference_field_without_claiming_application() -> None:
    component, source = _inputs("보험약관\n참조약관코드 SAMPLE-TERMS")
    checked = validate_component_metadata(component, source)
    assert checked is not None
    assert checked.facts["terms_reference"] == ("SAMPLE-TERMS",)
    assert checked.unresolved_fields == ()


@pytest.mark.parametrize(
    "change", ["node", "page", "offset", "text", "value", "label", "range", "role"]
)
def test_forged_source_metadata_cannot_be_published(change: str) -> None:
    component, source = _inputs()
    fact = component["facts"][0]
    span = fact["spans"][0]
    if change == "node":
        span["node_id"] = "synthetic-missing-node"
    elif change == "page":
        span["page_number"] = 2
    elif change == "offset":
        span["start"] += 1
    elif change == "text":
        span["text"] = "Sample Fabricated"
    elif change == "value":
        fact["value"] = "Sample Fabricated"
    elif change == "label":
        fact["field"] = "product_name"
    elif change == "range":
        component["page_end"] = 2
    else:
        component["role"] = "policy"
    assert validate_component_metadata(component, source) is None


def test_omitting_conflicting_scalar_value_does_not_make_metadata_unambiguous() -> None:
    component, source = _inputs("보험약관\n보험사: Sample A\n보험사: Sample B")
    component["facts"] = component["facts"][:1]
    component["conflicting_fields"] = []
    assert validate_component_metadata(component, source) is None


def test_raw_role_conflict_is_checked_even_when_proposal_only_quotes_one_title() -> None:
    component, source = _inputs()
    source["nodes"][0]["text"] += "\n보험증권"
    assert validate_component_metadata(component, source) is None


def test_minimized_provider_offset_cannot_be_applied_to_original_source() -> None:
    component, source = _inputs("보험약관\nSynthetic private preface\n보험사: Sample Assurance")
    span = component["facts"][0]["spans"][0]
    # The external window could be shorter after minimization; its offsets are not source offsets.
    span["start"] -= 10
    span["end"] -= 10
    assert validate_component_metadata(component, source) is None


def test_api_rejects_a_correctly_quoted_body_checklist_as_a_title() -> None:
    component, source = _inputs("보험증권\n보험사: Sample Assurance")
    prefix = "청구 제출서류\n"
    source["nodes"][0]["text"] = prefix + source["nodes"][0]["text"]
    for span in [
        *component["role_spans"],
        *[span for fact in component["facts"] for span in fact["spans"]],
    ]:
        for key in ("start", "end", "anchor_start", "anchor_end"):
            span[key] += len(prefix)
    assert validate_component_metadata(component, source) is None


def test_conflicts_are_preserved_as_metadata_without_claiming_applicability() -> None:
    component, source = _inputs("보험약관\n보험사: Sample A\n보험사: Sample B")
    result = validate_component_metadata(component, source)
    assert result is not None
    assert result.facts["insurer"] == ("Sample A", "Sample B")
    assert result.conflicting_fields == ("insurer",)


def test_adjacent_table_value_is_rechecked_against_cell_coordinates() -> None:
    structure = _structure("보험약관")
    title = replace(structure.nodes[0], bbox=(10, 1, 400, 9))
    row = replace(
        title,
        node_id="synthetic-table-row",
        kind="TABLE_ROW",
        bbox=(10, 20, 400, 40),
        text="상품코드\t001-SAMPLE",
        cells=(
            StructureCell(0, 0, "상품코드", None, "synthetic.cells.0"),
            StructureCell(0, 1, "001-SAMPLE", None, "synthetic.cells.1"),
        ),
    )
    structure = replace(structure, nodes=(title, row))
    component = metadata_proposal(structure, UUID(int=205), "c" * 64)["components"][0]
    source = structure.to_dict()
    assert validate_component_metadata(component, source) is not None
    source["nodes"][1]["cells"][1]["column_index"] = 2
    assert validate_component_metadata(component, source) is None


def test_repeated_explicit_page_metadata_validates_as_one_component() -> None:
    structure = _structure("보험약관\n상품코드: SAMPLE-001", "보험약관\n상품코드: SAMPLE-001")
    component = metadata_proposal(structure, UUID(int=205), "c" * 64)["components"][0]
    assert component["page_end"] == 2
    assert validate_component_metadata(component, structure.to_dict()) is not None


def test_api_streams_page_validation_and_rejects_wrong_lineage() -> None:
    structure = _structure("보험약관\n상품코드: SAMPLE-001", "보험약관\n상품코드: SAMPLE-001")
    component = metadata_proposal(structure, UUID(int=205), "c" * 64)["components"][0]
    source = structure.to_dict()
    loaded: list[int] = []

    def page_loader(number: int) -> Any:
        loaded.append(number)
        return {
            "lineage": source["lineage"],
            "nodes": [node for node in source["nodes"] if node["page_number"] == number],
        }

    header = {"lineage": source["lineage"], "nodes": []}
    assert validate_component_metadata(component, header, page_loader=page_loader) is not None
    assert loaded == [1, 2]
    assert (
        validate_component_metadata(
            component, header, page_loader=lambda _: {"lineage": {}, "nodes": []}
        )
        is None
    )


@pytest.mark.parametrize(
    "change", ["extra", "extra_fact", "duplicate_conflict", "bool_page", "identity"]
)
def test_invalid_contract_shape_is_rejected(change: str) -> None:
    component, source = _inputs("보험약관\n보험사: Sample A\n보험사: Sample B")
    if change == "extra":
        component["is_enrolled"] = True
    elif change == "extra_fact":
        component["facts"][0]["authority"] = "USER_CONFIRMED"
    elif change == "duplicate_conflict":
        component["conflicting_fields"] *= 2
    elif change == "bool_page":
        component["page_start"] = True
    else:
        component["identity"] = "0" * 64
    assert validate_component_metadata(component, source) is None
