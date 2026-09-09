"""Wholly synthetic resident identifiers stay private without nearby labels."""

from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from familycare_worker.ai.minimizer import (
    SourceWindowMinimizer,
    minimize_evidence,
    minimize_text,
)
from familycare_worker.ai.policy_ranges import RANGE_ENVELOPE_REVISION, build_policy_envelopes
from familycare_worker.ai.provider import EvidenceSlice
from familycare_worker.document_structure import plan_structure_chunks

from workers.analyzer.tests.test_document_structure import _build, _extraction, _page
from workers.analyzer.tests.test_document_text_lines import _words

_IDENTIFIERS = (
    "010203-1******",
    "010203-*******",
    "010203-1234567",
    "010203 - 1******",
    "010203\t-\t*******",
    "010203\n-\n1234567",
    "010203-12*****",
)


@pytest.mark.parametrize("identifier", _IDENTIFIERS)
def test_unlabelled_identifier_redaction_keeps_dates_amounts_and_evidence_identity(
    identifier: str,
) -> None:
    suffix = "; Contract date: 2026-01-01; Insured amount: 317000 KRW"
    source = "Sample " + identifier + suffix
    expected = "Sample [REDACTED]" + suffix
    assert minimize_text(source, sensitive_terms=()) == expected
    original = EvidenceSlice(
        evidence_id=UUID("00000000-0000-4000-8000-000000000301"),
        document_version_id=UUID("00000000-0000-4000-8000-000000000302"),
        page=1,
        text=source,
        bbox=(10, 20, 300, 40),
        document_kind="policy",
    )
    minimized = minimize_evidence((original,), sensitive_terms=())[0]
    assert minimized.text == expected
    assert (
        minimized.evidence_id,
        minimized.document_version_id,
        minimized.page,
        minimized.bbox,
        minimized.document_kind,
    ) == (
        original.evidence_id,
        original.document_version_id,
        original.page,
        original.bbox,
        original.document_kind,
    )
    assert original.text == source


@pytest.mark.parametrize("identifier", _IDENTIFIERS)
def test_identifier_is_redacted_at_every_original_source_window_boundary(identifier: str) -> None:
    prefix = "Sample prefix "
    suffix = " Insured amount: 317000 KRW"
    source = prefix + identifier + suffix
    redactor = SourceWindowMinimizer(source, sensitive_terms=())
    for position in range(1, len(identifier)):
        boundary = len(prefix) + position
        assert redactor.window(0, boundary) == prefix + "[REDACTED]"
        assert redactor.window(boundary, len(source)) == "[REDACTED]" + suffix
    assert redactor.window(len(prefix) + 2, len(prefix) + len(identifier) - 2) == "[REDACTED]"


@pytest.mark.parametrize("identifier", _IDENTIFIERS[:4])
def test_native_word_constructor_redacts_an_identifier_detached_from_its_label(
    identifier: str,
) -> None:
    source = _build(
        _extraction(
            _page(
                1,
                _words(
                    [
                        "Policy certificate",
                        "Insured:",
                        "Family Member A",
                        identifier,
                        "Contract date: 2026-01-01",
                        "Sample Rider insured amount: 317000 KRW",
                    ]
                ),
            )
        )
    )
    original = source.to_dict()
    identifier_node = next(node for node in source.nodes if node.text == identifier)
    assert identifier_node.kind == ("TEXT_LINE" if " " in identifier else "BLOCK")
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=32
    )
    packet = build_policy_envelopes(source, plan, sensitive_terms=("Family Member A",))
    assert packet.envelopes and not packet.unprocessed
    evidence = [item for envelope in packet.envelopes for item in envelope.evidence]
    detached = next(item for item in evidence if item.node_id == identifier_node.node_id)
    assert detached.text == "[REDACTED]"
    assert detached.start == 0 and detached.end == len(identifier_node.text)
    assert detached.evidence_id == uuid5(
        NAMESPACE_URL,
        f"{RANGE_ENVELOPE_REVISION}:{identifier_node.node_id}:0:{len(identifier_node.text)}",
    )
    assert detached.document_version_id == source.lineage.document_version_id
    assert detached.page == identifier_node.page_number and detached.bbox == identifier_node.bbox
    assert all("010203" not in item.text for item in evidence)
    assert any("2026-01-01" in item.text for item in evidence)
    assert any("317000 KRW" in item.text for item in evidence)
    assert source.to_dict() == original


@pytest.mark.parametrize(
    "text",
    (
        "Contract date: 2001-02-03",
        "Coverage start: 2001/02/03",
        "Coverage end: 2001.02.03",
        "Insured amount: 317000 KRW",
        "가입금액: 317,000 KRW; 한도: 1234567 KRW",
    ),
)
def test_identifier_shape_does_not_change_dates_or_insured_amounts(text: str) -> None:
    assert minimize_text(text, sensitive_terms=()) == text
    assert SourceWindowMinimizer(text, sensitive_terms=()).window(0, len(text)) == text
