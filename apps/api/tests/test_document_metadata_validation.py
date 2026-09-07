"""API independently checks protected proposal spans against retained local IR."""

from copy import deepcopy
from dataclasses import replace
from typing import Any
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_structure import StructureCell

from workers.analyzer.tests.test_document_metadata import _structure


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


def test_api_rechecks_original_label_value_and_date_semantics() -> None:
    component, source = _inputs()
    result = validate_component_metadata(component, source)
    assert result is not None and result.role == "terms"
    assert result.facts["insurer"] == ("Sample Assurance",)
    assert result.facts["product_code"] == ("001-SAMPLE",)
    assert result.facts["edition_date"] == ("2020-01-01",)
    assert "applicability_start" not in result.facts


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
