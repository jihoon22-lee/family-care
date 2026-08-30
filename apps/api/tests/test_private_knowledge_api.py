"""Authenticated bounded HTTP projection for private knowledge snapshots."""

from __future__ import annotations

import json
from collections.abc import Iterator
from uuid import UUID

import pytest
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.errors import install_error_handlers
from familycare_api.private_knowledge.router import (
    get_private_knowledge_query_service,
    router,
)
from familycare_api.private_knowledge.schemas import (
    CurrentKnowledgeResponse,
    KnowledgeContractDetailResponse,
    KnowledgeContractPageResponse,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001961")
RUN_ID = UUID("00000000-0000-4000-8000-000000001962")
SUBJECT_ID = UUID("00000000-0000-4000-8000-000000001963")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000001964")
COVERAGE_ID = UUID("00000000-0000-4000-8000-000000001965")
ASSIGNMENT_ID = UUID("00000000-0000-4000-8000-000000001966")
SECTION_ID = UUID("00000000-0000-4000-8000-000000001967")
FACT_ID = UUID("00000000-0000-4000-8000-000000001968")


def _counts() -> dict[str, int]:
    return {
        "subjects": 1,
        "contracts": 1,
        "coverages": 1,
        "terms_assignments": 1,
        "terms_assignment_sources": 1,
        "terms_sections": 1,
        "source_clauses": 1,
        "semantic_reviews": 1,
        "facts": 1,
        "fact_citations": 1,
        "coverage_terms_mappings": 1,
        "document_bindings": 2,
    }


class _QueryService:
    def current(self) -> CurrentKnowledgeResponse:
        return CurrentKnowledgeResponse.model_validate(
            {
                "schema_version": "1",
                "run_id": RUN_ID,
                "counts": _counts(),
                "executable_fact_count": 0,
                "executable_mapping_count": 0,
                "unsafe_operational_binding_count": 0,
            }
        )

    def list_contracts(
        self,
        *,
        limit: int,
        after: UUID | None,
    ) -> KnowledgeContractPageResponse:
        assert limit == 50
        assert after is None
        return KnowledgeContractPageResponse.model_validate(
            {
                "schema_version": "1",
                "items": [
                    {
                        "id": CONTRACT_ID,
                        "subject_id": SUBJECT_ID,
                        "family_alias": "Family Member A",
                        "insurer_display": "Sample Insurer",
                        "product_display": "Sample Policy",
                        "contract_start": "2024-01-01",
                        "contract_end": None,
                        "certificate_decision": "MATCH",
                        "current_status": "unknown",
                        "coverage_count": 1,
                        "enrollment_match_count": 1,
                        "enrollment_no_match_count": 0,
                        "enrollment_unknown_count": 0,
                        "document_identity_decision": "MATCH",
                        "edition_applicability_decision": "MATCH",
                        "terms_overall_decision": "MATCH",
                    }
                ],
                "next_cursor": None,
            }
        )

    def get_contract(self, contract_id: UUID) -> KnowledgeContractDetailResponse:
        assert contract_id == CONTRACT_ID
        return KnowledgeContractDetailResponse.model_validate(
            {
                "schema_version": "1",
                "contract": self.list_contracts(limit=50, after=None).items[0],
                "coverages": [
                    {
                        "id": COVERAGE_ID,
                        "display_name": "Sample Hospital Benefit",
                        "component_role": "RIDER",
                        "component_classification": "BENEFIT_COVERAGE",
                        "enrollment_decision": "MATCH",
                        "benefit_type": "FIXED",
                        "insured_amount": "10000.0000",
                        "currency": "KRW",
                        "coverage_start": "2024-01-01",
                        "coverage_end": None,
                        "renewal_state": "NO",
                        "current_status": "unknown",
                    }
                ],
                "terms_assignments": [
                    {
                        "id": ASSIGNMENT_ID,
                        "document_identity_decision": "MATCH",
                        "edition_applicability_decision": "MATCH",
                        "overall_decision": "MATCH",
                        "reason_codes": ["SYNTHETIC_EXACT_MATCH"],
                        "selected_source_count": 1,
                    }
                ],
                "coverage_mappings": [
                    {
                        "coverage_id": COVERAGE_ID,
                        "terms_section_id": SECTION_ID,
                        "mapping_applicability": "APPLICABLE",
                        "enrollment_decision": "MATCH",
                        "document_identity_decision": "MATCH",
                        "edition_applicability_decision": "MATCH",
                        "section_mapping_decision": "MATCH",
                        "overall_decision": "MATCH",
                        "reason_codes": ["SYNTHETIC_SECTION_MATCH"],
                        "executable": False,
                    }
                ],
                "terms_sections": [
                    {
                        "id": SECTION_ID,
                        "heading": "Sample Benefit Section",
                        "page_start": 2,
                        "page_end": 2,
                        "review_state": "DIRECT_REVIEWED",
                        "section_summary": "Synthetic section summary.",
                        "confidence": "high",
                        "found_categories": ["payment_reason"],
                        "missing_categories": [],
                        "warnings": [],
                        "facts": [
                            {
                                "id": FACT_ID,
                                "fact_type": "PAYMENT_TRIGGER",
                                "statement": "Synthetic payment condition.",
                                "conditions": {
                                    "details_ko": ["Synthetic condition."],
                                    "decision_impact": "establishes_payment_trigger",
                                    "confidence": "high",
                                    "unresolved_reference": False,
                                },
                                "numeric_terms": [],
                                "review_state": "DIRECT_REVIEWED",
                                "executable": False,
                                "citations": [
                                    {
                                        "page_start": 2,
                                        "page_end": 2,
                                        "clause_label": "Synthetic clause 1",
                                        "clause_title": "Sample payment condition",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )


@pytest.fixture()
def client() -> Iterator[TestClient]:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.dependency_overrides[resolve_household_scope] = lambda: HouseholdScope(HOUSEHOLD_ID)
    app.dependency_overrides[get_private_knowledge_query_service] = _QueryService
    with TestClient(app) as test_client:
        yield test_client


def test_current_and_contract_list_are_bounded_authenticated_and_no_store(
    client: TestClient,
) -> None:
    current = client.get("/api/v1/private-knowledge/current")
    contracts = client.get("/api/v1/private-knowledge/current/contracts")

    assert current.status_code == 200
    assert contracts.status_code == 200
    assert current.headers["cache-control"] == "no-store"
    assert contracts.headers["cache-control"] == "no-store"
    assert current.json()["counts"] == _counts()
    assert len(contracts.json()["items"]) == 1
    assert contracts.json()["items"][0]["family_alias"] == "Family Member A"


def test_contract_detail_exposes_semantics_and_citations_without_source_material(
    client: TestClient,
) -> None:
    response = client.get(f"/api/v1/private-knowledge/current/contracts/{CONTRACT_ID}")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    fact = payload["terms_sections"][0]["facts"][0]
    assert fact["statement"] == "Synthetic payment condition."
    assert fact["executable"] is False
    assert fact["citations"] == [
        {
            "page_start": 2,
            "page_end": 2,
            "clause_label": "Synthetic clause 1",
            "clause_title": "Sample payment condition",
        }
    ]
    serialized = json.dumps(payload, sort_keys=True).lower()
    for forbidden in (
        "source_alias",
        "source_record",
        "source_text_sha256",
        "content_sha256",
        "document_version_id",
        "evidence_id",
        "policy_contract_id",
        "rider_id",
        "package_digest",
        "/mnt/",
    ):
        assert forbidden not in serialized
