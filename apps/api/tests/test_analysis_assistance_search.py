"""Pure contracts for deterministic, non-authoritative assistance search."""

from __future__ import annotations

from dataclasses import fields
from decimal import Decimal
from uuid import UUID

import pytest
from familycare_api.decisions.assistance import (
    AnalysisRecommendation,
    candidate_digest,
    normalize_search_tokens,
)


def _uuid(number: int) -> UUID:
    return UUID(f"00000000-0000-4000-8000-{number:012d}")


def _recommendation(*, rank: int = 1, excerpt: str = "Sample bounded excerpt") -> object:
    return AnalysisRecommendation(
        id=_uuid(1),
        private_claim_candidate_id=_uuid(2),
        knowledge_coverage_id=_uuid(3),
        terms_section_id=_uuid(4),
        knowledge_fact_id=_uuid(5),
        source_clause_id=_uuid(6),
        fact_citation_id=_uuid(7),
        rank=rank,
        score=Decimal("2"),
        contract_label="Sample Policy",
        coverage_label="Sample Coverage",
        clause_label="Sample Clause",
        excerpt=excerpt,
        page_start=2,
        page_end=2,
        citation_kind="FACT_CITATION",
        reason_code="TOKEN_OVERLAP",
    )


def test_normalization_uses_exact_nfkc_tokens_with_stable_deduplication() -> None:
    tokens = normalize_search_tokens(
        "  Ｓａｍｐｌｅ, sample! CATEGORY  ",
        ("Procedure category", "sample"),
    )

    assert tokens == ("category", "procedure", "sample")
    assert normalize_search_tokens(" - / . ", ()) == ()


def test_normalization_is_bounded_before_database_search() -> None:
    tokens = normalize_search_tokens(
        " ".join(f"token{index:03d}" for index in range(200)),
        (),
    )

    assert len(tokens) == 64
    assert all(2 <= len(token) <= 64 for token in tokens)


def test_recommendation_has_no_decision_or_payment_authority_fields() -> None:
    names = {item.name for item in fields(AnalysisRecommendation)}

    assert names.isdisjoint(
        {
            "eligibility",
            "eligibility_result",
            "payable_amount",
            "confirmed_amount",
            "conditional_amount",
            "claim_ready",
            "claim_start_ready",
        }
    )
    assert _recommendation().citation_kind == "FACT_CITATION"


@pytest.mark.parametrize(
    ("rank", "excerpt"),
    [
        (0, "Sample bounded excerpt"),
        (13, "Sample bounded excerpt"),
        (1, ""),
        (1, "x" * 241),
    ],
)
def test_recommendation_rejects_unbounded_rank_and_excerpt(rank: int, excerpt: str) -> None:
    with pytest.raises(ValueError):
        _recommendation(rank=rank, excerpt=excerpt)


def test_candidate_digest_is_order_independent_and_opaque() -> None:
    first = _recommendation()
    second = AnalysisRecommendation(
        id=_uuid(8),
        private_claim_candidate_id=_uuid(9),
        knowledge_coverage_id=_uuid(10),
        terms_section_id=_uuid(11),
        knowledge_fact_id=_uuid(12),
        source_clause_id=_uuid(13),
        fact_citation_id=_uuid(14),
        rank=2,
        score=Decimal("1"),
        contract_label="Sample Policy B",
        coverage_label="Sample Coverage B",
        clause_label="Sample Clause B",
        excerpt="Another bounded excerpt",
        page_start=4,
        page_end=4,
        citation_kind="FACT_CITATION",
        reason_code="TOKEN_OVERLAP",
    )

    digest = candidate_digest((first, second))

    assert digest == candidate_digest((second, first))
    assert len(digest) == 64
    assert digest != candidate_digest(())
