"""Product captions retain bounded variant suffixes without changing source values."""

from copy import deepcopy
from uuid import UUID

import pytest
from familycare_api.insurance_documents.metadata_validation import validate_component_metadata
from familycare_worker.document_metadata import metadata_proposal

from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_metadata import _structure


@pytest.fixture(autouse=True)
def _historical_v10_producer(monkeypatch):
    """Keep the v10 source-flow acceptance independent of later producers."""
    from familycare_worker import document_metadata, document_metadata_repository

    monkeypatch.setattr(document_metadata, "REVISION", "document-metadata-v10")
    monkeypatch.setattr(document_metadata_repository, "REVISION", "document-metadata-v10")


@pytest.mark.parametrize(
    "caption",
    [
        "Sample Policy (renewable) (annual)",
        "가상 보 험 (표준형) (갱신형)",
        "Sample Policy（renewable）（annual）",
        "가상 보험 (표준형) 7종",
        "Sample Policy (annual) 8형",
    ],
)
def test_multiple_variant_suffixes_keep_original_caption_and_v9_meaning(caption):
    source = _structure(caption + "\n보험약관")
    original = deepcopy(source.to_dict())
    components = metadata_proposal(source, UUID(int=205), "c" * 64)["components"]
    assert len(components) == 1
    component = components[0]
    assert component["unresolved_fields"] == []
    assert [(fact["field"], fact["value"]) for fact in component["facts"]] == [
        ("product_name", caption)
    ]
    assert (
        validate_component_metadata(component, original, revision="document-metadata-v10")
        is not None
    )
    legacy = deepcopy(component)
    _legacy_identity(legacy, original, revision="document-metadata-v9")
    assert validate_component_metadata(legacy, original, revision="document-metadata-v9") is None
    assert source.to_dict() == original


@pytest.mark.parametrize(
    "caption",
    [
        "Example Policy (renewable) (annual)",
        "가상 보험 (설명용) (갱신형)",
        "Sample Policy (renewable) unrelated sentence",
        "Sample Policy (first) (second) (third) (fourth) (fifth)",
        "Sample Policy（renewable)（annual）",
    ],
)
def test_reference_unbounded_or_unbalanced_suffix_does_not_become_product_identity(caption):
    source = _structure(caption + "\n보험약관")
    proposal = metadata_proposal(source, UUID(int=205), "c" * 64)
    assert not any(
        fact["field"] == "product_name" for c in proposal["components"] for fact in c["facts"]
    )
