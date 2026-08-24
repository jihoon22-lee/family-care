"""Real PostgreSQL FTS/trigram proof using a wholly synthetic Clause corpus."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from datetime import date
from typing import cast
from uuid import UUID

import psycopg
import pytest
from familycare_api.clauses.domain import ClauseSearchFilters, ClauseType, TermsEdition
from familycare_api.clauses.errors import ClauseEvidenceInvalid
from familycare_api.clauses.repository import (
    ClauseRepository,
    ClauseSearchRepository,
    TermsEditionRepository,
)
from familycare_api.clauses.search import ClauseSearchService
from familycare_api.clauses.service import ClauseCatalogService
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row

pytestmark = pytest.mark.integration


def _psycopg_url(database_url: str) -> str:
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


@dataclass(frozen=True)
class SourceSeed:
    scope: HouseholdScope
    document_version_id: UUID
    evidence_ids: tuple[UUID, ...]
    content_sha256: str


@pytest.fixture()
def database_url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required for PostgreSQL integration tests")
    with psycopg.connect(_psycopg_url(value), autocommit=True) as connection:
        connection.execute(
            """
            TRUNCATE TABLE
              clause_evidence, clause_search_synonyms, clauses, terms_editions,
              analysis_candidate_evidence, analysis_candidate_fields,
              analysis_candidate_versions, policy_status_snapshots, riders,
              policy_parties, policy_contracts, evidence, family_members,
              household_spaces, extraction_cells, extraction_tables,
              extraction_blocks, extraction_pages, extractions, analysis_jobs,
              document_versions, documents
            RESTART IDENTITY CASCADE
            """
        )
    return value


def _seed_source(database_url: str, *, suffix: str, hash_character: str) -> SourceSeed:
    content_sha256 = hash_character * 64
    with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
        household = connection.execute(
            """
            INSERT INTO household_spaces (space_key, display_name)
            VALUES (%s, %s)
            RETURNING id
            """,
            (f"synthetic-clause-household-{suffix}", f"Synthetic Household {suffix.upper()}"),
        ).fetchone()
        document = connection.execute(
            """
            INSERT INTO documents (source_key, document_kind, status)
            VALUES (%s, 'terms', 'ready')
            RETURNING id
            """,
            (f"synthetic/clause-terms-{suffix}.pdf",),
        ).fetchone()
        assert household and document
        document_version = connection.execute(
            """
            INSERT INTO document_versions (
              document_id, version_number, content_sha256, byte_size, page_count
            ) VALUES (%s, 1, %s, 512, 4)
            RETURNING id
            """,
            (document["id"], content_sha256),
        ).fetchone()
        assert document_version
        extraction = connection.execute(
            """
            INSERT INTO extractions (
              document_version_id, extractor_name, extractor_version,
              extractor_config_hash, quality_rule_version, status, succeeded_at
            ) VALUES (%s, 'synthetic', '1', %s, 'quality-v1', 'succeeded', clock_timestamp())
            RETURNING id
            """,
            (
                document_version["id"],
                hashlib.sha256(f"synthetic-config-{suffix}".encode()).hexdigest(),
            ),
        ).fetchone()
        assert extraction
        evidence_ids: list[UUID] = []
        for page in range(1, 5):
            connection.execute(
                """
                INSERT INTO extraction_pages (
                  extraction_id, page_number, width_points, height_points,
                  non_whitespace_chars, alphanumeric_ratio,
                  replacement_character_ratio, maximum_repeated_character_run,
                  classification
                ) VALUES (%s, %s, 612, 792, 80, 0.8, 0, 1, 'TEXT_SUFFICIENT')
                """,
                (extraction["id"], page),
            )
            evidence = connection.execute(
                """
                INSERT INTO evidence (
                  household_space_id, document_version_id, extraction_id,
                  content_sha256, physical_page, review_state
                ) VALUES (%s, %s, %s, %s, %s, 'USER_CONFIRMED')
                RETURNING id
                """,
                (
                    household["id"],
                    document_version["id"],
                    extraction["id"],
                    content_sha256,
                    page,
                ),
            ).fetchone()
            assert evidence
            evidence_ids.append(cast(UUID, evidence["id"]))
    return SourceSeed(
        scope=HouseholdScope(cast(UUID, household["id"])),
        document_version_id=cast(UUID, document_version["id"]),
        evidence_ids=tuple(evidence_ids),
        content_sha256=content_sha256,
    )


def _services(
    database_url: str,
) -> tuple[ClauseCatalogService, ClauseSearchService, ClauseRepository]:
    terms = TermsEditionRepository(database_url)
    clauses = ClauseRepository(database_url)
    return (
        ClauseCatalogService(terms, clauses),
        ClauseSearchService(ClauseSearchRepository(database_url)),
        clauses,
    )


def _edition(
    catalog: ClauseCatalogService,
    source: SourceSeed,
    *,
    insurer_key: str = "sample-insurer",
    product_key: str = "sample-policy",
    start: date | None = date(2025, 1, 1),
    end: date | None = date(2025, 12, 31),
) -> TermsEdition:
    return catalog.create_terms_edition(
        source.scope,
        source_evidence_id=source.evidence_ids[0],
        document_version_id=source.document_version_id,
        insurer_display="Sample Insurer",
        insurer_key=insurer_key,
        product_display="Sample Policy",
        product_key=product_key,
        applicability_start=start,
        applicability_end=end,
        content_sha256=source.content_sha256,
    )


def _clause(
    catalog: ClauseCatalogService,
    source: SourceSeed,
    edition: TermsEdition,
    *,
    title: str,
    text: str,
    page: int,
    clause_type: ClauseType = "article",
    parent_clause_id: UUID | None = None,
):
    return catalog.create_clause(
        source.scope,
        terms_edition_id=edition.id,
        parent_clause_id=parent_clause_id,
        clause_type=clause_type,
        label=f"제{page}조 (합성 조항)",
        title=title,
        text=text,
        physical_page_start=page,
        physical_page_end=page,
        evidence_ids=(source.evidence_ids[page - 1],),
    )


def test_postgres_search_is_scoped_bounded_and_evidence_backed(database_url: str) -> None:
    source_a = _seed_source(database_url, suffix="a", hash_character="a")
    source_b = _seed_source(database_url, suffix="b", hash_character="b")
    catalog, search, _ = _services(database_url)
    edition_a = _edition(catalog, source_a)
    edition_b = _edition(catalog, source_b)
    expected = _clause(
        catalog,
        source_a,
        edition_a,
        title="입원 의료비",
        text="합성 입원 의료비는 표본 의료기관에서 이어지는 관찰 비용입니다. "
        + ("합성 설명 " * 80)
        + "bounded-excerpt-end-marker",
        page=2,
    )
    _clause(
        catalog,
        source_b,
        edition_b,
        title="입원 의료비",
        text="다른 합성 가구의 입원 의료비 정의입니다.",
        page=2,
    )

    hits = search.search(
        source_a.scope,
        "  입원,\n의료비! ",
        ClauseSearchFilters(),
    )

    assert [hit.clause_id for hit in hits] == [expected.id]
    assert len(hits[0].excerpt) <= 320
    assert "bounded-excerpt-end-marker" not in hits[0].excerpt
    assert len(hits[0].evidence) == 1
    assert hits[0].evidence[0].evidence_id == source_a.evidence_ids[1]
    assert hits[0].evidence[0].physical_page == 2
    assert hits[0].evidence[0].content_sha256 == source_a.content_sha256


def test_filters_apply_inclusive_dates_keys_and_edition_scope(database_url: str) -> None:
    source = _seed_source(database_url, suffix="filters", hash_character="c")
    other = _seed_source(database_url, suffix="other", hash_character="d")
    catalog, search, _ = _services(database_url)
    edition = _edition(catalog, source)
    foreign_edition = _edition(catalog, other)
    clause = _clause(
        catalog,
        source,
        edition,
        title="수술 급여",
        text="합성 수술 급여는 표본 처치에 적용됩니다.",
        page=3,
    )

    for effective_on in (date(2025, 1, 1), date(2025, 12, 31)):
        hits = search.search(
            source.scope,
            "수술 급여",
            ClauseSearchFilters(
                terms_edition_id=edition.id,
                effective_on=effective_on,
                insurer_key="sample-insurer",
                product_key="sample-policy",
            ),
        )
        assert [hit.clause_id for hit in hits] == [clause.id]

    assert (
        search.search(
            source.scope,
            "수술 급여",
            ClauseSearchFilters(effective_on=date(2024, 12, 31)),
        )
        == ()
    )
    assert (
        search.search(
            source.scope,
            "수술 급여",
            ClauseSearchFilters(insurer_key="different-insurer"),
        )
        == ()
    )
    assert (
        search.search(
            source.scope,
            "수술 급여",
            ClauseSearchFilters(terms_edition_id=foreign_edition.id),
        )
        == ()
    )


def test_same_title_definitions_remain_separate_and_rank_deterministically(
    database_url: str,
) -> None:
    source = _seed_source(database_url, suffix="ranking", hash_character="e")
    catalog, search, _ = _services(database_url)
    edition = _edition(catalog, source, start=None, end=None)
    first = _clause(
        catalog,
        source,
        edition,
        title="통원 정의",
        text="통원 정의 첫 번째 합성 설명입니다.",
        page=2,
        clause_type="definition",
    )
    second = _clause(
        catalog,
        source,
        edition,
        title="통원 정의",
        text="통원 정의 두 번째 합성 설명입니다.",
        page=3,
        clause_type="definition",
    )

    hits = search.search(source.scope, "통원 정의", ClauseSearchFilters())

    assert [hit.clause_id for hit in hits] == [first.id, second.id]
    assert hits[0].excerpt != hits[1].excerpt
    assert hits[0].evidence[0].physical_page == 2
    assert hits[1].evidence[0].physical_page == 3


def test_hierarchy_and_soft_deleted_rows_stay_out_of_default_queries(database_url: str) -> None:
    source = _seed_source(database_url, suffix="hierarchy", hash_character="f")
    catalog, search, repository = _services(database_url)
    edition = _edition(catalog, source, start=None, end=None)
    parent = _clause(
        catalog,
        source,
        edition,
        title="보장 장",
        text="보장 장 합성 안내입니다.",
        page=1,
        clause_type="chapter",
    )
    child = _clause(
        catalog,
        source,
        edition,
        title="보장 항목",
        text="보장 항목 합성 안내입니다.",
        page=2,
        parent_clause_id=parent.id,
    )

    hierarchy = catalog.get_clause_hierarchy(source.scope, edition.id)
    assert [item.id for item in hierarchy] == [parent.id, child.id]
    assert hierarchy[1].parent_clause_id == parent.id

    repository.soft_delete(
        source.scope,
        child.id,
        expected_version=child.version,
    )
    assert search.search(source.scope, "보장 항목", ClauseSearchFilters()) == ()
    assert [item.id for item in catalog.get_clause_hierarchy(source.scope, edition.id)] == [
        parent.id
    ]


def test_clause_creation_rejects_cross_document_evidence_atomically(database_url: str) -> None:
    source = _seed_source(database_url, suffix="evidence", hash_character="1")
    wrong = _seed_source(database_url, suffix="wrong", hash_character="2")
    catalog, _, _ = _services(database_url)
    edition = _edition(catalog, source)
    valid = _clause(
        catalog,
        source,
        edition,
        title="기존 검색 조항",
        text="이 합성 조항은 뒤의 개별 파싱 실패 후에도 남습니다.",
        page=2,
    )

    with pytest.raises(ClauseEvidenceInvalid):
        catalog.create_clause(
            source.scope,
            terms_edition_id=edition.id,
            parent_clause_id=None,
            clause_type="article",
            label="제4조 (합성 오류)",
            title="증거 오류",
            text="잘못 연결된 합성 증거입니다.",
            physical_page_start=4,
            physical_page_end=4,
            evidence_ids=(wrong.evidence_ids[3],),
        )

    assert [clause.id for clause in catalog.get_clause_hierarchy(source.scope, edition.id)] == [
        valid.id
    ]
