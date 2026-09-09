"""Structure-preserving, minimized, bounded provider inputs from synthetic sources."""

import pytest
from familycare_worker.ai.policy_ranges import build_policy_envelopes
from familycare_worker.document_structure import plan_structure_chunks

from workers.analyzer.tests.test_document_structure import (
    _block,
    _build,
    _extraction,
    _page,
    _table,
)


def test_all_ranges_beyond_old_excerpt_limits_are_batched_without_per_chunk_calls() -> None:
    source = _build(
        _extraction(
            _page(
                1,
                [
                    _block(
                        "보험증권 가입금액 " + "Synthetic preface " * 40 + "Late Sample Rider 317"
                    ),
                    *[_block(f"Synthetic block {i}", i) for i in range(1, 100)],
                ],
            )
        )
    )
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=256
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=())
    primary = [key for envelope in result.envelopes for key in envelope.primary_chunk_ids]
    assert primary == [chunk.chunk_id for chunk in plan.chunks]
    assert len(primary) == len(set(primary)) == 100
    assert 1 < len(result.envelopes) <= 5
    text = "\n".join(item.text for envelope in result.envelopes for item in envelope.evidence)
    assert "Late Sample Rider 317" in text and "Synthetic block 99" in text
    assert not result.unprocessed
    for envelope in result.envelopes:
        assert len(envelope.evidence) <= 64
        assert sum(len(item.text) for item in envelope.evidence) <= 16_384
        assert len(envelope.primary_chunk_ids) <= 32
    assert "Late Sample Rider" not in repr(result)
    assert result == build_policy_envelopes(source, plan, sensitive_terms=())


def test_atomic_table_rows_keep_units_and_context_without_duplicate_primary_ranges() -> None:
    source = _build(
        _extraction(
            _page(
                1,
                [_block("보험증권 가입금액"), _block("단위: 만원", 1)],
                tables=[
                    _table(
                        [
                            ["담보", "가입금액"],
                            ["Sample Rider " * 30, "317"],
                            ["Other Rider", "71"],
                        ],
                        header_rows=[0],
                        unit_block_orders=[1],
                    )
                ],
            )
        )
    )
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=256
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=())
    assert not result.unprocessed
    assert len(result.envelopes) == 1
    evidence = result.envelopes[0].evidence
    assert len({item.evidence_id for item in evidence}) == len(evidence)
    assert any("317" in item.text and len(item.text) > 240 for item in evidence)
    assert any("만원" in item.text for item in evidence)
    assert len(result.envelopes[0].primary_chunk_ids) == len(plan.chunks)


def test_source_window_redaction_precedes_chunk_and_block_boundaries() -> None:
    text = "Synthetic " * 20 + "Family Member A" + " 가입금액: 317"
    source = _build(_extraction(_page(1, [_block("보험증권"), _block(text, 1)])))
    plan = plan_structure_chunks(
        source, max_content_chars=208, max_context_chars=4096, max_chunks=256
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=("Family Member A",))
    texts = [item.text for envelope in result.envelopes for item in envelope.evidence]
    assert all("Family" not in value and "Member A" not in value for value in texts)
    assert any("317" in value for value in texts)


@pytest.mark.parametrize(
    "suffix",
    [
        "(010203-1******)",
        "31세 (남성) (010203-1******)",
        "(010203-1******) / 31세 / 남성 / (2급) Sample Occupation (Synthetic role, duties)",
    ],
)
def test_compound_insured_identity_is_redacted_before_provider_ranges(suffix: str) -> None:
    source = _build(
        _extraction(
            _page(
                1,
                [_block("보험증권 가입금액")],
                tables=[
                    _table(
                        [
                            ["피보험자", f"Family Member A {suffix}"],
                            ["담보명", "Sample Rider"],
                            ["가입금액", "317 KRW"],
                        ]
                    )
                ],
            )
        )
    )
    plan = plan_structure_chunks(
        source, max_content_chars=208, max_context_chars=4096, max_chunks=256
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=("Family Member A",))
    assert result.envelopes and not result.unprocessed
    texts = [item.text for envelope in result.envelopes for item in envelope.evidence]
    assert all("Family Member A" not in text and "010203" not in text for text in texts)
    assert all("31세" not in text and "남성" not in text for text in texts)
    assert all("Sample Occupation" not in text and "2급" not in text for text in texts)
    assert any("Sample Rider" in text for text in texts)
    assert any("317" in text for text in texts)


def test_envelope_budget_retains_every_unprocessed_source_range() -> None:
    source = _build(_extraction(_page(1, [_block("보험증권 " + "X" * 40000)])))
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=256
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=(), maximum_envelopes=1)
    assert len(result.envelopes) == 1
    assert result.unprocessed
    processed = sum(item.end - item.start for item in result.envelopes[0].evidence if item.primary)
    assert processed + sum(item.end - item.start for item in result.unprocessed) == len(
        source.nodes[0].text
    )
    assert {item.reason_code for item in result.unprocessed} == {"ENVELOPE_BUDGET_EXHAUSTED"}


def test_unknown_role_cannot_become_policy_evidence_from_an_intake_hint() -> None:
    source = _build(_extraction(_page(1, [_block("Synthetic undecided document")])))
    plan = plan_structure_chunks(
        source, max_content_chars=4096, max_context_chars=4096, max_chunks=256
    )
    result = build_policy_envelopes(source, plan, sensitive_terms=())
    item = result.envelopes[0].evidence[0]
    assert item.source_role == "unknown"
    assert item.document_kind != "policy"
    assert item.to_provider_payload()["source_role"] == "unknown"
