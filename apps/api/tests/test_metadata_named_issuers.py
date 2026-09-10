"""Exact, revision-bound issuer vocabulary still needs original header evidence."""

from copy import deepcopy

import pytest
from familycare_api.insurance_documents import metadata_validation as api
from familycare_worker import document_metadata as worker

from apps.api.tests.test_metadata_header_preludes import _source
from apps.api.tests.test_metadata_header_regions import _proposal
from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words


@pytest.fixture(autouse=True)
def synthetic_vocabulary(monkeypatch):
    for module in (worker, api):
        monkeypatch.setattr(
            module,
            "METADATA_INSURER_CAPTIONS_V11",
            frozenset({"합성생명", "Sample Shield"}),
            raising=False,
        )


@pytest.mark.parametrize("caption", ["합성생명", "Sample Shield"])
def test_exact_known_caption_retains_literal_issuer_and_source(caption, monkeypatch):
    source = _source(prefix=(caption,))
    original = deepcopy(source.to_dict())
    components = _proposal(source, monkeypatch)["components"]
    assert len(components) == 1
    component = components[0]
    assert ("insurer", caption) in {(fact["field"], fact["value"]) for fact in component["facts"]}
    assert api.validate_component_metadata(component, original, revision="document-metadata-v11")
    assert source.to_dict() == original


@pytest.mark.parametrize(
    "caption", ["합성생명금융서비스", "다른합성생명", "sample shield", "Sample Shield agency"]
)
def test_vocabulary_does_not_infer_substrings_case_or_agency_identity(caption, monkeypatch):
    assert _proposal(_source(prefix=(caption,)), monkeypatch)["components"] == []


@pytest.mark.parametrize(
    "fault", ["reference", "other_column_title", "unlocated", "intervening_body"]
)
def test_known_company_does_not_bypass_source_header_boundaries(fault, monkeypatch):
    components = _proposal(_source(prefix=("합성생명",), fault=fault), monkeypatch)["components"]
    assert not any(fact["field"] == "insurer" for c in components for fact in c["facts"])


@pytest.mark.parametrize("revision", ["document-metadata-v9", "document-metadata-v10"])
def test_new_vocabulary_does_not_change_historical_proposals(revision, monkeypatch):
    components = _proposal(_source(prefix=("합성생명",)), monkeypatch, revision)["components"]
    assert not any(fact["field"] == "insurer" for c in components for fact in c["facts"])


def test_api_rechecks_vocabulary_without_trusting_producer_fact(monkeypatch):
    source = _source(prefix=("합성생명",))
    component = _proposal(source, monkeypatch)["components"][0]
    monkeypatch.setattr(api, "METADATA_INSURER_CAPTIONS_V11", frozenset())
    assert (
        api.validate_component_metadata(
            component, source.to_dict(), revision="document-metadata-v11"
        )
        is None
    )


def test_exact_caption_also_works_without_a_column_recovery(monkeypatch):
    source = _build(_extraction(_page(1, _words(["합성생명", "보험 약관"]))))
    assert not any("LINE_COLUMN_CONTEXT_UNRESOLVED" in n.issue_codes for n in source.nodes)
    component = _proposal(source, monkeypatch)["components"][0]
    assert ("insurer", "합성생명") in {(f["field"], f["value"]) for f in component["facts"]}
    assert api.validate_component_metadata(
        component, source.to_dict(), revision="document-metadata-v11"
    )
