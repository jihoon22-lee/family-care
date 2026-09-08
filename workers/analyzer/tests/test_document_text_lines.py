"""Native word geometry is retained while complete enrollment lines are consumed."""

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from familycare_worker.ai.policy_ranges import build_policy_envelopes
from familycare_worker.ai.range_grounding import ground_range_candidate
from familycare_worker.ai.schemas import CandidateField, PolicyCandidate
from familycare_worker.document_structure import plan_structure_chunks
from familycare_worker.policy_source_association import LocalMember, associate_policy_sources

from workers.analyzer.tests.test_document_structure import _build, _extraction, _page


def _words(lines: list[str]) -> list[dict[str, Any]]:
    blocks = []
    for index, line in enumerate(lines):
        left = 10.0
        for word in line.split():
            width = len(word) * 5.0
            blocks.append(
                {
                    "text": word,
                    "reading_order": len(blocks),
                    "bbox": [left, 20.0 + index * 15, left + width, 30.0 + index * 15],
                }
            )
            left += width + 3
    return blocks


def test_word_lines_preserve_original_blocks_and_map_every_character() -> None:
    blocks = _words(["Policy certificate", "Sample Rider sum assured: 317 KRW"])
    source = _extraction(_page(1, blocks))
    structure = _build(source)
    assert structure.to_dict()["source_extraction"] == source
    raw = {node.node_id: node for node in structure.nodes if node.kind == "BLOCK"}
    assert [node.text for node in raw.values()] == [block["text"] for block in blocks]
    lines = [node for node in structure.nodes if node.kind == "TEXT_LINE"]
    assert [line.text for line in lines] == [
        "Policy certificate",
        "Sample Rider sum assured: 317 KRW",
    ]
    for line in lines:
        for part in line.source_spans:
            block = raw[part.block_node_id]
            assert (
                line.text[part.line_start : part.line_end]
                == block.text[part.block_start : part.block_end]
            )
            assert not block.schedulable
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=20
    )
    assert {chunk.node_id for chunk in plan.chunks} == {line.node_id for line in lines}
    assert structure.pages[0].role == "policy"


def test_lines_do_not_join_separate_columns_or_adjacent_rows() -> None:
    blocks = _words(["Sample Rider", "Another Rider sum assured: 619 KRW"])
    blocks.append({"text": "317", "reading_order": len(blocks), "bbox": [400, 20, 420, 30]})
    blocks.append({"text": "KRW", "reading_order": len(blocks), "bbox": [423, 20, 440, 30]})
    structure = _build(_extraction(_page(1, blocks)))
    lines = [node.text for node in structure.nodes if node.kind == "TEXT_LINE"]
    assert "Sample Rider" in lines
    assert not any("Sample Rider" in line and "317" in line for line in lines)
    assert not any("Sample Rider" in line and "Another Rider" in line for line in lines)
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=20
    )
    assert any(item.reason_code == "LINE_COLUMN_CONTEXT_UNRESOLVED" for item in plan.unprocessed)


def test_a_distant_unenrolled_column_cannot_be_dropped_from_an_enrollment_line() -> None:
    blocks = _words(["Policy certificate", "Sample Rider sum assured: 317 KRW"])
    blocks.append({"text": "미가입", "reading_order": len(blocks), "bbox": [400, 35, 430, 45]})
    structure = _build(_extraction(_page(1, blocks)))
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=20
    )
    assert not any("Sample Rider" in chunk.text for chunk in plan.chunks)
    assert any(item.reason_code == "LINE_COLUMN_CONTEXT_UNRESOLVED" for item in plan.unprocessed)


def test_a_line_exceeding_the_content_budget_is_kept_as_an_explicit_omission() -> None:
    structure = _build(_extraction(_page(1, _words(["Sample Rider sum assured: 317 KRW"]))))
    plan = plan_structure_chunks(
        structure, max_content_chars=10, max_context_chars=0, max_chunks=20
    )
    assert not plan.complete and not plan.chunks
    assert {item.reason_code for item in plan.unprocessed} == {"LINE_EXCEEDS_CONTENT_BUDGET"}


def test_actual_native_pdf_reaches_local_insured_association_and_amount_grounding(
    tmp_path: Path,
) -> None:
    from familycare_worker.pdf.extractor import ExtractionSettings, PdfPlumberExtractor
    from familycare_worker.pdf.intake import open_source, validate_pdf
    from reportlab.pdfgen.canvas import Canvas

    path = tmp_path / "synthetic-native-policy.pdf"
    canvas = Canvas(str(path), invariant=1)
    for index, line in enumerate(
        [
            "Policy certificate",
            "Policy number: synthetic-policy-001",
            "Insured: Family Member A",
            "Sample Insurer Sample Plan",
            "Sample Rider sum assured: 317 KRW",
        ]
    ):
        canvas.drawString(72, 720 - index * 20, line)
    canvas.save()
    from workers.analyzer.tests.test_document_structure import DOCUMENT_ID

    with open_source(tmp_path, path.name) as opened:
        validated = validate_pdf(opened)
        extracted = PdfPlumberExtractor().extract(
            opened.fd,
            ExtractionSettings(
                document_version_id=str(DOCUMENT_ID),
                content_sha256=validated.content_sha256,
                extractor_config_hash="a" * 64,
                quality_rule_version="quality-v1",
                table_strategy="lines",
            ),
        )
    assert len(extracted["pages"][0]["blocks"]) > 10
    structure = _build(extracted)
    member = LocalMember(uuid4(), "Family Member A", "family-member-a", 1)
    associations = associate_policy_sources(
        structure, members=(member,), expected_member_id=member.id
    )
    rider_line = next(
        node
        for node in structure.nodes
        if node.kind == "TEXT_LINE" and node.text.startswith("Sample Rider")
    )
    assert associations[rider_line.node_id].state == "RESOLVED"
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=50
    )
    envelope = build_policy_envelopes(
        structure, plan, sensitive_terms=("Family Member A",)
    ).envelopes[0]
    assert all("Family Member A" not in item.text for item in envelope.evidence)
    evidence = next(item for item in envelope.evidence if item.node_id == rider_line.node_id)
    candidate = PolicyCandidate(
        candidate_id=uuid4(),
        candidate_kind="rider",
        status="AI_VERIFIED",
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(evidence.evidence_id,))
            for key, value in {
                "rider_name": "Sample Rider",
                "rider_key": "sample-rider",
                "sum_assured": 317,
                "currency": "KRW",
            }.items()
        ),
        issue_codes=(),
        provider_request_ids=(),
    )
    result = ground_range_candidate(
        candidate,
        envelope.evidence,
        local_nodes={node["node_id"]: node for node in structure.to_dict()["nodes"]},
    )
    assert result.status == "AI_VERIFIED"
    assert (
        next(field.value for field in result.fields if field.field_id == "benefit_type")
        == "unknown"
    )


@pytest.mark.parametrize("label", ["Insured:", "Insured"])
def test_unregistered_insured_value_is_minimized_before_any_provider_envelope(label: str) -> None:
    structure = _build(
        _extraction(
            _page(
                1,
                _words(
                    [
                        "Policy certificate",
                        f"{label} Unlisted Person",
                        "Sample Rider sum assured: 317 KRW",
                    ]
                ),
            )
        )
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=20
    )
    envelopes = build_policy_envelopes(structure, plan, sensitive_terms=()).envelopes
    assert envelopes
    assert all(
        "Unlisted Person" not in item.text for envelope in envelopes for item in envelope.evidence
    )


@pytest.mark.parametrize("status", ["미가입", "미선택", "not enrolled", "example only"])
def test_a_joined_terminal_status_remains_excluded_enrollment(status: str) -> None:
    structure = _build(
        _extraction(
            _page(1, _words(["Policy certificate", f"Sample Rider sum assured: 317 KRW {status}"]))
        )
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=4096, max_context_chars=4096, max_chunks=20
    )
    envelope = build_policy_envelopes(structure, plan, sensitive_terms=()).envelopes[0]
    evidence = next(item for item in envelope.evidence if "Sample Rider" in item.text)
    candidate = PolicyCandidate(
        candidate_id=uuid4(),
        candidate_kind="rider",
        status="AI_VERIFIED",
        fields=tuple(
            CandidateField(field_id=key, value=value, evidence_ids=(evidence.evidence_id,))
            for key, value in {
                "rider_name": "Sample Rider",
                "rider_key": "sample-rider",
                "sum_assured": 317,
                "currency": "KRW",
            }.items()
        ),
        issue_codes=(),
        provider_request_ids=(),
    )
    result = ground_range_candidate(
        candidate,
        envelope.evidence,
        local_nodes={node["node_id"]: node for node in structure.to_dict()["nodes"]},
    )
    assert "NOT_ENROLLED" in result.issue_codes and result.status == "NEEDS_REVIEW"
