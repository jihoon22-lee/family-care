"""HTTP contracts for the household-scoped Clause catalog and search routes."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from familycare_api.clauses.domain import (
    Clause,
    ClauseSearchFilters,
    ClauseSearchHit,
    TermsEdition,
)
from familycare_api.clauses.errors import TermsEditionNotFound
from familycare_api.clauses.normalization import NORMALIZATION_VERSION
from familycare_api.clauses.router import (
    get_clause_catalog_service,
    get_clause_search_service,
    router,
)
from familycare_api.clauses.schemas import ClauseSearchQuery
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.errors import install_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

SCOPE_A = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
SCOPE_B = HouseholdScope(UUID("00000000-0000-4000-8000-000000000102"))
EDITION_ID = UUID("00000000-0000-4000-8000-000000000201")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000202")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000000301")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000401")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000402")
UNKNOWN_ID = UUID("00000000-0000-4000-8000-000000000499")
PRIVATE_QUERY = "synthetic-query-must-not-echo"
PRIVATE_BODY = "synthetic full normalized Clause body must not be returned"


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=EVIDENCE_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256="a" * 64,
        physical_page=7,
        bbox=None,
        review_state="USER_CONFIRMED",
    )


def _edition() -> TermsEdition:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return TermsEdition(
        id=EDITION_ID,
        household_space_id=SCOPE_A.household_space_id,
        document_version_id=DOCUMENT_VERSION_ID,
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


def _clause() -> Clause:
    return Clause(
        id=CLAUSE_ID,
        household_space_id=SCOPE_A.household_space_id,
        terms_edition_id=EDITION_ID,
        parent_clause_id=None,
        clause_type="article",
        label="제7조 (합성 입원 보장)",
        normalized_title="합성 입원 보장",
        normalized_text=(
            "합성 입원 보장은 표본 조건을 따릅니다. " + ("합성 설명 " * 80) + PRIVATE_BODY
        ),
        physical_page_start=7,
        physical_page_end=8,
        normalization_version=NORMALIZATION_VERSION,
        version=1,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
        deleted_at=None,
        evidence=(_evidence(),),
    )


def _hit() -> ClauseSearchHit:
    return ClauseSearchHit(
        clause_id=CLAUSE_ID,
        label="제7조 (합성 입원 보장)",
        excerpt="합성 입원 보장은 표본 조건을 따릅니다.",
        terms_edition_id=EDITION_ID,
        physical_page_start=7,
        physical_page_end=8,
        evidence=(_evidence(),),
        relevance=Decimal("0.875"),
        normalization_version=NORMALIZATION_VERSION,
    )


class _FakeCatalogService:
    def __init__(self) -> None:
        self.edition = _edition()
        self.clause = _clause()
        self.list_scopes: list[HouseholdScope] = []
        self.hierarchy_calls: list[tuple[HouseholdScope, UUID]] = []

    def list_terms_editions(self, scope: HouseholdScope) -> tuple[TermsEdition, ...]:
        self.list_scopes.append(scope)
        return (self.edition,) if scope == SCOPE_A else ()

    def get_clause_hierarchy(
        self,
        scope: HouseholdScope,
        terms_edition_id: UUID,
    ) -> tuple[Clause, ...]:
        self.hierarchy_calls.append((scope, terms_edition_id))
        if scope != SCOPE_A or terms_edition_id != EDITION_ID:
            raise TermsEditionNotFound
        return (self.clause,)


class _FakeSearchService:
    def __init__(self) -> None:
        self.calls: list[tuple[HouseholdScope, str, ClauseSearchFilters, int]] = []

    def search(
        self,
        scope: HouseholdScope,
        query: str,
        filters: ClauseSearchFilters,
        *,
        limit: int = 20,
    ) -> tuple[ClauseSearchHit, ...]:
        self.calls.append((scope, query, filters, limit))
        return (_hit(),) if scope == SCOPE_A else ()


@pytest.fixture()
def services() -> tuple[_FakeCatalogService, _FakeSearchService]:
    return _FakeCatalogService(), _FakeSearchService()


@pytest.fixture()
def app(services: tuple[_FakeCatalogService, _FakeSearchService]) -> FastAPI:
    catalog, search = services
    application = FastAPI()
    install_error_handlers(application)
    application.include_router(router)

    application.dependency_overrides[resolve_household_scope] = lambda: SCOPE_A
    application.dependency_overrides[get_clause_catalog_service] = lambda: catalog
    application.dependency_overrides[get_clause_search_service] = lambda: search
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _assert_no_store(response: Any) -> None:
    assert response.headers.get("cache-control", "").lower() == "no-store"


def test_terms_editions_list_is_scoped_and_no_store(
    client: TestClient,
    services: tuple[_FakeCatalogService, _FakeSearchService],
) -> None:
    catalog, _ = services

    response = client.get(
        "/api/v1/terms-editions",
        params={"household_space_id": str(SCOPE_B.household_space_id)},
    )

    assert response.status_code == 200
    _assert_no_store(response)
    assert response.json() == [
        {
            "id": str(EDITION_ID),
            "document_version_id": str(DOCUMENT_VERSION_ID),
            "insurer_display": "Synthetic Mutual",
            "insurer_key": "synthetic-mutual",
            "product_display": "Synthetic Care Plan",
            "product_key": "synthetic-care-plan",
            "applicability_start": "2026-01-01",
            "applicability_end": "2026-12-31",
            "content_sha256": "b" * 64,
            "normalization_version": NORMALIZATION_VERSION,
            "version": 1,
        }
    ]
    assert catalog.list_scopes == [SCOPE_A]
    assert "household_space_id" not in response.json()[0]


def test_clause_hierarchy_returns_bounded_nodes_and_exact_physical_evidence_page(
    client: TestClient,
    services: tuple[_FakeCatalogService, _FakeSearchService],
) -> None:
    catalog, _ = services

    response = client.get(f"/api/v1/terms-editions/{EDITION_ID}/clauses")

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    node = body["clauses"][0]
    assert body["terms_edition_id"] == str(EDITION_ID)
    assert node["clause_id"] == str(CLAUSE_ID)
    assert node["parent_clause_id"] is None
    assert node["clause_type"] == "article"
    assert node["label"] == "제7조 (합성 입원 보장)"
    assert node["excerpt"].startswith("합성 입원 보장은 표본 조건을 따릅니다")
    assert len(node["excerpt"]) <= 320
    assert node["physical_page_start"] == 7
    assert node["physical_page_end"] == 8
    assert node["evidence"] == [
        {
            "evidence_id": str(EVIDENCE_ID),
            "document_version_id": str(DOCUMENT_VERSION_ID),
            "content_sha256": "a" * 64,
            "page_number": 7,
            "bbox": None,
        }
    ]
    assert node["normalization_version"] == NORMALIZATION_VERSION
    assert catalog.hierarchy_calls == [(SCOPE_A, EDITION_ID)]
    assert PRIVATE_BODY not in response.text
    assert "physical_page" not in response.json()["clauses"][0]["evidence"][0]


def test_clause_search_uses_json_body_filters_server_scope_and_bounded_hit(
    client: TestClient,
    services: tuple[_FakeCatalogService, _FakeSearchService],
) -> None:
    _, search = services

    url_query = client.post(
        "/api/v1/clauses/search",
        params={"q": PRIVATE_QUERY},
        json={
            "q": "  입원,\n 의료비! ",
            "terms_edition_id": str(EDITION_ID),
            "effective_on": "2026-06-01",
            "insurer_key": "synthetic-mutual",
            "product_key": "synthetic-care-plan",
            "limit": 7,
        },
    )

    assert url_query.status_code == 422
    _assert_no_store(url_query)
    assert url_query.json() == {
        "error_code": "INVALID_REQUEST",
        "message": "search request is invalid",
    }
    assert PRIVATE_QUERY not in url_query.text

    response = client.post(
        "/api/v1/clauses/search",
        json={
            "q": "  입원,\n 의료비! ",
            "terms_edition_id": str(EDITION_ID),
            "effective_on": "2026-06-01",
            "insurer_key": "synthetic-mutual",
            "product_key": "synthetic-care-plan",
            "limit": 7,
        },
    )

    assert response.status_code == 200
    _assert_no_store(response)
    assert response.json() == {
        "schema_version": "1",
        "normalization_version": NORMALIZATION_VERSION,
        "query_matched_count": 1,
        "hits": [
            {
                "clause_id": str(CLAUSE_ID),
                "label": "제7조 (합성 입원 보장)",
                "excerpt": "합성 입원 보장은 표본 조건을 따릅니다.",
                "terms_edition_id": str(EDITION_ID),
                "physical_page_start": 7,
                "physical_page_end": 8,
                "evidence": [
                    {
                        "evidence_id": str(EVIDENCE_ID),
                        "document_version_id": str(DOCUMENT_VERSION_ID),
                        "content_sha256": "a" * 64,
                        "page_number": 7,
                        "bbox": None,
                    }
                ],
                "normalization_version": NORMALIZATION_VERSION,
                "relevance": 0.875,
            }
        ],
    }
    assert search.calls == [
        (
            SCOPE_A,
            "  입원,\n 의료비! ",
            ClauseSearchFilters(
                terms_edition_id=EDITION_ID,
                effective_on=date(2026, 6, 1),
                insurer_key="synthetic-mutual",
                product_key="synthetic-care-plan",
            ),
            7,
        )
    ]
    assert PRIVATE_QUERY not in response.text
    assert PRIVATE_BODY not in response.text
    assert "normalized_text" not in response.json()["hits"][0]


def test_search_query_is_strict_bounded_and_invalid_values_are_value_free(
    client: TestClient,
) -> None:
    extra = client.post(
        "/api/v1/clauses/search",
        json={"q": "입원", "unexpected": "synthetic"},
    )
    assert extra.status_code == 422
    _assert_no_store(extra)
    assert extra.json()["error_code"] == "INVALID_REQUEST"
    assert "unexpected" in extra.json().get("fields", [])

    oversized = client.post(
        "/api/v1/clauses/search",
        json={"q": PRIVATE_QUERY + ("가" * 200)},
    )
    assert oversized.status_code == 422
    _assert_no_store(oversized)
    assert oversized.json()["error_code"] == "INVALID_REQUEST"
    assert PRIVATE_QUERY not in oversized.text


def test_strict_query_model_rejects_extra_fields_and_out_of_range_limits() -> None:
    with pytest.raises(ValidationError):
        ClauseSearchQuery.model_validate(
            {"q": "입원", "household_space_id": str(SCOPE_A.household_space_id)}
        )
    with pytest.raises(ValidationError):
        ClauseSearchQuery.model_validate({"q": "입원", "limit": 51})
    with pytest.raises(ValidationError):
        ClauseSearchQuery.model_validate({"q": ""})


def test_path_id_is_strict_uuid_and_unknown_edition_is_sanitized(
    client: TestClient,
    services: tuple[_FakeCatalogService, _FakeSearchService],
) -> None:
    catalog, _ = services

    malformed = client.get("/api/v1/terms-editions/not-a-uuid/clauses")
    assert malformed.status_code == 422
    _assert_no_store(malformed)
    assert "not-a-uuid" not in malformed.text

    catalog.hierarchy_calls.clear()
    missing = client.get(f"/api/v1/terms-editions/{UNKNOWN_ID}/clauses")
    assert missing.status_code == 404
    _assert_no_store(missing)
    assert missing.json() == {
        "error_code": "TERMS_EDITION_NOT_FOUND",
        "message": "terms edition not found",
    }
