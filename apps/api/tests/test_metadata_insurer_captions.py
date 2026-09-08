"""Synthetic issuer captions are tied to a verified document source region."""

from copy import deepcopy
from dataclasses import replace
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal

from workers.analyzer.tests.test_document_metadata import _structure

BODY = "제1조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."


def _component(text: str):
    source = _structure(text)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert len(proposal["components"]) == 1
    return source, proposal["components"][0]


@pytest.mark.parametrize("role", ["보험증권", "보험약관", BODY])
@pytest.mark.parametrize(
    "issuer", ["Sample Assurance", "가상생명보험", "가상화재해상보험", "Sample Insurance Company"]
)
def test_unlabelled_issuer_caption_is_preserved_with_its_original_source(role, issuer) -> None:
    source, component = _component(f" {issuer} \nSample Policy\n{role}")
    facts = [fact for fact in component["facts"] if fact["field"] == "insurer"]
    assert len(facts) == 1 and facts[0]["value"] == issuer
    span = facts[0]["spans"][0]
    node = next(node for node in source.nodes if node.node_id == span["node_id"])
    assert node.text[span["start"] : span["end"]] == issuer
    assert validate_component_metadata(component, source.to_dict())
    forged = deepcopy(component)
    next(f for f in forged["facts"] if f["field"] == "insurer")["value"] = "Other Assurance"
    assert validate_component_metadata(forged, source.to_dict()) is None


@pytest.mark.parametrize("caption", ["무배당", "갱신형", "가상주식회사", "계약자 Sample Assurance"])
def test_cover_prelude_is_not_itself_insurer_evidence(caption: str) -> None:
    source = _structure(f"{caption}\n보험약관\n{BODY}")
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert all(
        fact["field"] != "insurer"
        for component in payload["components"]
        for fact in component["facts"]
    )


def test_competing_issuer_captions_preserve_the_conflict() -> None:
    source, component = _component("Sample Assurance\nOther Assurance\n보험약관")
    assert "insurer" in component["conflicting_fields"]
    assert {fact["value"] for fact in component["facts"] if fact["field"] == "insurer"} == {
        "Sample Assurance",
        "Other Assurance",
    }
    assert validate_component_metadata(component, source.to_dict())


def test_footer_caption_is_not_taken_back_into_the_opening_metadata() -> None:
    source, component = _component("보험약관\n" + BODY + "\nSample Assurance")
    assert not any(fact["field"] == "insurer" for fact in component["facts"])
    assert validate_component_metadata(component, source.to_dict())


def test_caption_in_another_column_is_not_bound_to_the_body() -> None:
    source = _structure(BODY)
    body = replace(source.nodes[0], bbox=(10, 60, 270, 140), reading_order=1)
    caption = replace(
        body,
        node_id="synthetic-other-column",
        text="Sample Assurance",
        bbox=(330, 10, 580, 30),
        reading_order=0,
    )
    source = replace(source, nodes=(caption, body))
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    component = payload["components"][0]
    assert not any(fact["field"] == "insurer" for fact in component["facts"])
    assert validate_component_metadata(component, source.to_dict())


def test_v3_proof_keeps_its_original_caption_semantics() -> None:
    from apps.api.tests.test_document_metadata_validation import _legacy_identity

    source, component = _component("Sample Assurance\nSample Policy\n보험약관")
    component["facts"] = [fact for fact in component["facts"] if fact["field"] != "insurer"]
    _legacy_identity(component, source.to_dict(), revision="document-metadata-v3")
    assert validate_component_metadata(component, source.to_dict(), revision="document-metadata-v3")
    _legacy_identity(component, source.to_dict(), revision="document-metadata-v4")
    assert validate_component_metadata(component, source.to_dict()) is None


@pytest.mark.parametrize("placement", ["footer_first", "distant_above"])
def test_extraction_order_does_not_join_a_spatially_separate_caption(placement: str) -> None:
    source = _structure("Sample Policy\n보험약관")
    role_box = (10, 20, 250, 70) if placement == "footer_first" else (10, 700, 250, 750)
    caption_box = (10, 700, 200, 720) if placement == "footer_first" else (10, 10, 200, 30)
    role = replace(source.nodes[0], bbox=role_box, reading_order=1)
    caption = replace(
        role,
        node_id="synthetic-separated-caption",
        text="Sample Assurance",
        bbox=caption_box,
        reading_order=0,
    )
    source = replace(source, nodes=(caption, role))
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    component = payload["components"][0]
    assert not any(fact["field"] == "insurer" for fact in component["facts"])
    assert validate_component_metadata(component, source.to_dict())


def test_separate_cells_do_not_share_the_retained_whole_table_region() -> None:
    from workers.analyzer.tests.test_terms_body import _table_nodes

    source = _structure("보험약관")
    _, row = _table_nodes()
    caption = replace(
        row,
        node_id="synthetic-caption-cell",
        text="Sample Assurance",
        bbox=(10, 10, 580, 100),
        context_node_ids=(),
        reading_order=0,
        cells=(replace(row.cells[0], text="Sample Assurance", bbox=(330, 10, 580, 30)),),
    )
    role = replace(
        caption,
        node_id="synthetic-role-cell",
        text="보험약관",
        reading_order=1,
        cells=(replace(caption.cells[0], text="보험약관", bbox=(10, 60, 270, 100)),),
    )
    source = replace(source, nodes=(caption, role))
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    component = payload["components"][0]
    assert not any(fact["field"] == "insurer" for fact in component["facts"])
    assert validate_component_metadata(component, source.to_dict())


@pytest.mark.parametrize("middle", ["Sample Policy", "서로 관계없는 합성 본문"])
def test_spatially_intervening_content_must_belong_to_the_caption_area(middle: str) -> None:
    source = _structure("보험약관")
    role = replace(source.nodes[0], bbox=(10, 60, 250, 80), reading_order=1)
    caption = replace(
        role,
        node_id="synthetic-adjacent-caption",
        text="Sample Assurance",
        bbox=(10, 10, 220, 30),
        reading_order=0,
    )
    between = replace(
        role,
        node_id="synthetic-intervening-content",
        text=middle,
        bbox=(10, 35, 230, 55),
        reading_order=2,
    )
    source = replace(source, nodes=(caption, role, between))
    payload = metadata_proposal(source, UUID(int=905), "c" * 64)
    component = payload["components"][0]
    assert any(fact["field"] == "insurer" for fact in component["facts"]) is (
        middle == "Sample Policy"
    )
    assert validate_component_metadata(component, source.to_dict())


def test_caption_and_explicit_insurer_disagreement_remain_visible() -> None:
    source, component = _component("Sample Assurance\n보험약관\n보험사: Other Assurance")
    assert "insurer" in component["conflicting_fields"]
    assert validate_component_metadata(component, source.to_dict())
