"""Versioned Clause normalization and wholly synthetic corpus contracts."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from familycare_api.clauses import (
    NORMALIZATION_VERSION,
    Clause,
    ClauseSearchFilters,
    ClauseSearchHit,
    TermsEdition,
    bounded_excerpt,
    normalize_clause_text,
    normalize_search_query,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope

CORPUS_PATH = Path(__file__).resolve().parents[3] / "fixtures/synthetic/terms-search-corpus.json"


def test_clause_text_uses_nfc_and_collapses_whitespace_and_punctuation_boundaries() -> None:
    decomposed_korean = "\u1100\u1161"

    assert (
        normalize_clause_text(f"  {decomposed_korean}\t\n보장\u00a0·\u00a0범위 :\n  Sample  Care  ")
        == "가 보장 범위 Sample Care"
    )


def test_search_query_normalization_is_deterministic_for_synthetic_korean_terms() -> None:
    query = "\n  합성\u00a0입원 · 보장  "

    assert normalize_search_query(query) == "합성 입원 보장"
    assert normalize_search_query(query) == normalize_search_query(query)


def test_blank_and_overlong_search_queries_remain_bounded_by_the_service_contract() -> None:
    assert normalize_search_query(" \t\n") == ""

    overlong = "가" * 161
    assert normalize_search_query(overlong) == overlong
    assert len(normalize_search_query(overlong)) > 160


@pytest.mark.parametrize("value", [None, b"synthetic", 42, True, object()])
def test_text_normalizers_reject_non_string_values(value: Any) -> None:
    with pytest.raises(TypeError):
        normalize_clause_text(value)
    with pytest.raises(TypeError):
        normalize_search_query(value)
    with pytest.raises(TypeError):
        bounded_excerpt(value)


def test_bounded_excerpt_normalizes_then_truncates_without_raw_text_leakage() -> None:
    source = "  Synthetic clause text\twith a deterministic suffix.  "

    first = bounded_excerpt(source, max_chars=18)
    second = bounded_excerpt(source, max_chars=18)

    assert first == second == "Synthetic clause t"
    assert len(first) <= 18
    assert bounded_excerpt(source) == normalize_clause_text(source)


@pytest.mark.parametrize("max_chars", [0, -1, True, 1.5])
def test_bounded_excerpt_requires_a_positive_integer_bound(max_chars: Any) -> None:
    with pytest.raises(ValueError):
        bounded_excerpt("synthetic text", max_chars=max_chars)


def test_domain_projections_keep_scope_pages_versions_and_evidence_typed() -> None:
    household_id = UUID("00000000-0000-4000-8000-000000000001")
    edition_id = UUID("00000000-0000-4000-8000-000000000002")
    document_version_id = UUID("00000000-0000-4000-8000-000000000003")
    clause_id = UUID("00000000-0000-4000-8000-000000000004")
    evidence_id = UUID("00000000-0000-4000-8000-000000000005")
    extraction_id = UUID("00000000-0000-4000-8000-000000000006")
    now = datetime(2026, 1, 1, tzinfo=UTC)
    evidence = EvidenceRef(
        evidence_id=evidence_id,
        document_version_id=document_version_id,
        extraction_id=extraction_id,
        content_sha256="a" * 64,
        physical_page=2,
        bbox=None,
        review_state="AI_VERIFIED",
    )
    scope = HouseholdScope(household_id)
    edition = TermsEdition(
        id=edition_id,
        household_space_id=household_id,
        document_version_id=document_version_id,
        insurer_display="Synthetic Mutual",
        insurer_key="synthetic-mutual",
        product_display="Synthetic Care Plan",
        product_key="synthetic-care-plan",
        applicability_start=date(2026, 1, 1),
        applicability_end=date(2026, 12, 31),
        content_sha256="b" * 64,
        normalization_version=NORMALIZATION_VERSION,
        version=1,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )
    clause = Clause(
        id=clause_id,
        household_space_id=household_id,
        terms_edition_id=edition_id,
        parent_clause_id=None,
        clause_type="article",
        label="합성 입원 보장",
        normalized_title="합성 입원 보장",
        normalized_text="합성 입원 보장은 합성 조건을 따릅니다.",
        physical_page_start=2,
        physical_page_end=3,
        normalization_version=NORMALIZATION_VERSION,
        version=1,
        created_at=now,
        updated_at=now,
        deleted_at=None,
        evidence=(evidence,),
    )
    filters = ClauseSearchFilters(
        terms_edition_id=edition_id,
        effective_on=date(2026, 3, 1),
        insurer_key=edition.insurer_key,
        product_key=edition.product_key,
    )
    hit = ClauseSearchHit(
        clause_id=clause.id,
        label=clause.label,
        excerpt=bounded_excerpt(clause.normalized_text),
        terms_edition_id=edition.id,
        physical_page_start=clause.physical_page_start,
        physical_page_end=clause.physical_page_end,
        evidence=clause.evidence,
        relevance=Decimal("0.75"),
        normalization_version=NORMALIZATION_VERSION,
    )

    assert edition.in_scope(scope)
    assert clause.in_scope(scope)
    assert filters.terms_edition_id == edition.id
    assert hit.evidence == (evidence,)
    assert hit.physical_page_start == 2


def test_domain_projections_preserve_stale_normalization_versions_for_mismatch_reporting() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    edition = TermsEdition(
        id=UUID("00000000-0000-4000-8000-000000000012"),
        household_space_id=UUID("00000000-0000-4000-8000-000000000013"),
        document_version_id=UUID("00000000-0000-4000-8000-000000000014"),
        insurer_display="Synthetic Mutual",
        insurer_key="synthetic-mutual",
        product_display="Synthetic Care Plan",
        product_key="synthetic-care-plan",
        applicability_start=None,
        applicability_end=None,
        content_sha256="c" * 64,
        normalization_version="unicode-nfc-v0",
        version=1,
        created_at=now,
        updated_at=now,
        deleted_at=None,
    )

    assert edition.normalization_version == "unicode-nfc-v0"


def test_synthetic_corpus_has_versioned_korean_and_english_rows_only() -> None:
    corpus = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))

    assert corpus["normalization_version"] == NORMALIZATION_VERSION
    assert corpus["corpus_version"] == "synthetic-terms-search-v1"
    assert len(corpus["terms_editions"]) >= 2
    clause_text = " ".join(
        clause["normalized_text"]
        for edition in corpus["terms_editions"]
        for clause in edition["clauses"]
    )
    assert "합성" in clause_text
    assert "Synthetic" in clause_text
    assert all(
        edition["normalization_version"] == NORMALIZATION_VERSION
        for edition in corpus["terms_editions"]
    )
    assert all(
        clause["physical_page_start"] >= 1
        and clause["physical_page_end"] >= clause["physical_page_start"]
        for edition in corpus["terms_editions"]
        for clause in edition["clauses"]
    )
