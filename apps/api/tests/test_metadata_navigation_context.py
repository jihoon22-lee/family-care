"""Navigation pages do not turn a later operative section into an example."""

from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal

from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_metadata import _structure

BODY = "제1조 (가상 지급 조건)\n회사는 보험수익자에게 보험금을 지급합니다."


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
