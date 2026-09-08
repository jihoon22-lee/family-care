"""Navigation pages do not turn a later operative section into an example."""

from copy import deepcopy
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal

from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_metadata import _structure

BODY = "제1조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."
REFERENCE_INSTRUCTION = "상품설명서 및 보험증권을 참고하여 합성 보장을 확인하십시오."


@pytest.mark.parametrize("native", [False, True])
def test_document_reference_instruction_does_not_start_a_reference_document(native: bool) -> None:
    if native:
        from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
        from workers.analyzer.tests.test_document_text_lines import _words

        source = _build(
            _extraction(
                _page(1, _words([REFERENCE_INSTRUCTION])),
                _page(2, _words(BODY.splitlines())),
            )
        )
    else:
        source = _structure(REFERENCE_INSTRUCTION, BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    terms = [component for component in proposal["components"] if component["role"] == "terms"]
    assert len(terms) == 1
    assert terms[0]["page_start"] == terms[0]["page_end"] == 2
    assert validate_component_metadata(terms[0], source.to_dict())
    _legacy_identity(terms[0], source.to_dict(), revision="document-metadata-v5")
    assert (
        validate_component_metadata(terms[0], source.to_dict(), revision="document-metadata-v5")
        is None
    )


def test_document_reference_instruction_does_not_end_an_existing_example() -> None:
    source = _structure("상품설명서(요약)", REFERENCE_INSTRUCTION, BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert not any(component["role"] == "terms" for component in proposal["components"])


@pytest.mark.parametrize("title", ["【목차】", "차례", "Table of contents"])
def test_navigation_context_ends_before_an_independently_verified_body(title: str) -> None:
    source = _structure(title + "\n제1조 가상 지급 조건 .... 2", BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert len(proposal["components"]) == 1
    component = proposal["components"][0]
    assert component["page_start"] == component["page_end"] == 2
    assert validate_component_metadata(component, source.to_dict())
    _legacy_identity(component, source.to_dict(), revision="document-metadata-v3")
    assert (
        validate_component_metadata(component, source.to_dict(), revision="document-metadata-v3")
        is None
    )


@pytest.mark.parametrize("prefix", ["상품설명서(요약)", "다음은 약관을 설명하기 위한 예시입니다."])
def test_navigation_does_not_erase_an_earlier_explanatory_document_boundary(prefix: str) -> None:
    source = _structure(prefix, "【목차】\n제1조 가상 지급 조건 .... 3", BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert not any(component["role"] == "terms" for component in proposal["components"])


@pytest.mark.parametrize("entry", ["상품설명서 ........ 9", "Example clauses ........ 9"])
def test_navigation_entry_is_not_a_new_explanatory_document(entry: str) -> None:
    source = _structure("목차\n" + entry + "\n보험약관 ........ 2", BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    terms = [component for component in proposal["components"] if component["role"] == "terms"]
    assert len(terms) == 1
    assert terms[0]["page_start"] == terms[0]["page_end"] == 2
    assert validate_component_metadata(terms[0], source.to_dict())
    _legacy_identity(terms[0], source.to_dict(), revision="document-metadata-v4")
    assert (
        validate_component_metadata(terms[0], source.to_dict(), revision="document-metadata-v4")
        is None
    )


@pytest.mark.parametrize("prefix", ["", "다음은 약관을 설명하기 위한 예시입니다.\n"])
def test_unproven_navigation_entries_cannot_end_an_example_document(prefix: str) -> None:
    first = prefix + ("목차\n" if prefix else "") + "상품설명서 ........ 9"
    source = _structure(first, BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert not any(component["role"] == "terms" for component in proposal["components"])


def test_native_navigation_is_replayed_without_discarding_hidden_raw_text() -> None:
    from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
    from workers.analyzer.tests.test_document_text_lines import _words

    source = _build(
        _extraction(
            _page(1, _words(["목차", "상품설명서 .... 9", "보험약관 .... 2"])),
            _page(2, _words(BODY.splitlines())),
        )
    )
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    component = next(c for c in proposal["components"] if c["role"] == "terms")
    assert component["page_start"] == component["page_end"] == 2
    assert validate_component_metadata(component, source.to_dict())
    forged_source = deepcopy(source.to_dict())
    raw = next(n for n in forged_source["nodes"] if n["page_number"] == 1 and n["kind"] == "BLOCK")
    raw["text"] += "\n다음은 약관을 설명하기 위한 예시입니다."
    assert validate_component_metadata(component, forged_source) is None


def test_cached_navigation_context_cannot_change_a_legacy_revision() -> None:
    from familycare_api.insurance_documents.metadata_validation import MetadataSourceContext

    source = _structure("목차\n상품설명서 .... 9\n보험약관 .... 2", BODY)
    projection = source.to_dict()
    component = metadata_proposal(source, UUID(int=905), "c" * 64)["components"][0]
    context = MetadataSourceContext(
        projection["lineage"],
        lambda number: {
            "lineage": projection["lineage"],
            "nodes": [node for node in projection["nodes"] if node["page_number"] == number],
        },
        revision="document-metadata-v6",
    )
    assert validate_component_metadata(component, projection, source_context=context)
    _legacy_identity(component, projection, revision="document-metadata-v4")
    assert (
        validate_component_metadata(
            component,
            projection,
            revision="document-metadata-v4",
            source_context=context,
        )
        is None
    )


@pytest.mark.parametrize("suffix", ["상품설명서(요약)", "다음은 약관을 설명하기 위한 예시입니다."])
def test_instruction_does_not_hide_another_reference_boundary(suffix: str) -> None:
    source = _structure(REFERENCE_INSTRUCTION + "\n" + suffix, BODY)
    proposal = metadata_proposal(source, UUID(int=905), "c" * 64)
    assert not any(c["role"] == "terms" for c in proposal["components"])


@pytest.mark.parametrize("corruption", ["hidden_text", "line_text", "cross_page", "partial_span"])
def test_unproven_native_instruction_cannot_hide_reference_context(corruption: str) -> None:
    from dataclasses import replace

    from familycare_api.insurance_documents.terms_body_validation import reference_context_present
    from familycare_worker.terms_body import reference_context_present as worker_reference

    from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
    from workers.analyzer.tests.test_document_text_lines import _words

    source = _build(
        _extraction(_page(1, _words([REFERENCE_INSTRUCTION])), _page(2, _words(BODY.splitlines())))
    )
    component = next(
        c
        for c in metadata_proposal(source, UUID(int=905), "c" * 64)["components"]
        if c["role"] == "terms"
    )
    nodes = list(source.nodes)
    raw_index = next(i for i, n in enumerate(nodes) if n.page_number == 1 and n.kind == "BLOCK")
    line_index = next(
        i for i, n in enumerate(nodes) if n.page_number == 1 and n.kind == "TEXT_LINE"
    )
    if corruption == "hidden_text":
        nodes[raw_index] = replace(nodes[raw_index], text=nodes[raw_index].text + " 예시 조항")
    elif corruption == "line_text":
        nodes[line_index] = replace(
            nodes[line_index], text=nodes[line_index].text.replace("합성", "가상")
        )
    elif corruption == "cross_page":
        nodes[line_index] = replace(nodes[line_index], page_number=2)
    else:
        line = nodes[line_index]
        nodes[line_index] = replace(
            line,
            source_spans=(replace(line.source_spans[0], block_start=1), *line.source_spans[1:]),
        )
    forged = replace(source, nodes=tuple(nodes))
    page_nodes = [n for n in forged.nodes if n.page_number == 1]
    assert worker_reference(page_nodes, persistent_only=True, navigation_instructions=True)
    projection = forged.to_dict()
    assert reference_context_present(
        [n for n in projection["nodes"] if n["page_number"] == 1],
        persistent_only=True,
        navigation_instructions=True,
    )
    assert validate_component_metadata(component, projection) is None


@pytest.mark.parametrize("separator", ["\n", "\r\n", "\u2028"])
def test_document_heading_and_instruction_on_separate_lines_remain_restricted(
    separator: str,
) -> None:
    from familycare_api.insurance_documents.terms_body_validation import reference_context_present
    from familycare_worker.terms_body import reference_context_present as worker_reference

    source = _structure("상품설명서" + separator + "보험증권을 참고하여 합성 보장을 확인하십시오.")
    assert worker_reference(source.nodes, persistent_only=True, navigation_instructions=True)
    assert reference_context_present(
        source.to_dict()["nodes"], persistent_only=True, navigation_instructions=True
    )
