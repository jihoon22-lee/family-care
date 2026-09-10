"""Covered-person conditions can identify terms content without deciding a claim."""

import pytest
from familycare_api.insurance_documents.terms_body_validation import body_evidence
from familycare_worker.terms_body import observe_terms_body, role_witness

from workers.analyzer.tests.test_document_metadata import _structure


@pytest.mark.parametrize("subject", ["피보험자가", "보험대상자가"])
@pytest.mark.parametrize(
    ("ending", "kind"),
    [("지급합니다.", "PAYMENT"), ("지급하지 않습니다.", "EXCLUSION")],
)
def test_insured_condition_before_implicit_payer_has_original_role_evidence(subject, ending, kind):
    statement = f"① {subject} 합성 요건에 해당하는 경우에는 보험금을 {ending}"
    source = _structure("제7조 (가상 지급 조건)\n" + statement)
    original = source.to_dict()
    observed = observe_terms_body(1, source.nodes)
    assert observed.status == "SUPPORTED"
    assert observed.provisions[0].semantic_kinds == (kind,)
    assert role_witness(observed)
    result = body_evidence(1, original["nodes"], metadata_revision="document-metadata-v10")
    assert result is not None and result[0] == (7,)
    assert body_evidence(1, original["nodes"], metadata_revision="document-metadata-v9") is None
    assert source.to_dict() == original


@pytest.mark.parametrize(
    "statement",
    [
        "피보험자가 보험금을 지급합니다.",
        "피보험자가 합성 요건에 해당하는 경우에는 보험금을 지급받습니다.",
        "피보험자가 합성 요건에 해당하는 경우에는 보험금 지급을 문의합니다.",
        "예시: 피보험자가 합성 요건에 해당하는 경우에는 보험금을 지급합니다.",
        "피보험자가 합성 요건에 해당하는 경우에는 보험금을 지급한다고 설명합니다.",
    ],
)
def test_similar_words_without_the_complete_operative_statement_remain_unsupported(statement):
    source = _structure("제7조 (가상 지급 조건)\n" + statement)
    assert observe_terms_body(1, source.nodes).status != "SUPPORTED"
    assert (
        body_evidence(1, source.to_dict()["nodes"], metadata_revision="document-metadata-v10")
        is None
    )


def test_a_whole_brochure_or_example_context_cannot_be_promoted_by_conditional_wording():
    statement = "피보험자가 합성 요건에 해당하는 경우에는 보험금을 지급합니다."
    for heading in ("상품설명서", "예시"):
        source = _structure(heading + "\n제7조 (가상 지급 조건)\n" + statement)
        assert observe_terms_body(1, source.nodes).status != "SUPPORTED"
        assert (
            body_evidence(1, source.to_dict()["nodes"], metadata_revision="document-metadata-v10")
            is None
        )
