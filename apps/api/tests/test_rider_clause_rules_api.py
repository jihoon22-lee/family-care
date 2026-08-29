"""HTTP contracts for Rider-Clause links, CoverageRule publication, and review."""

from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from familycare_api.clauses import router as clause_router_module
from familycare_api.clauses.dsl import RULE_SCHEMA_VERSION
from familycare_api.clauses.links import RiderClauseLink
from familycare_api.clauses.rules import CoverageRuleVersion, CoverageRuleVersionCollection
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.errors import ApiBoundaryError, install_error_handlers
from familycare_api.identity.context import AuthContext
from familycare_api.main import create_app
from familycare_api.policies.candidate_models import (
    CandidateConfirmationRequest,
    CandidateCorrectionRequest,
    CandidateRejectionRequest,
    PolicyReviewItem,
)
from familycare_api.policies.candidate_router import (
    get_candidate_review_service,
)
from familycare_api.policies.candidate_router import (
    router as candidate_router,
)
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

SCOPE_A = HouseholdScope(UUID("00000000-0000-4000-8000-000000000101"))
SCOPE_B = HouseholdScope(UUID("00000000-0000-4000-8000-000000000102"))
RIDER_ID = UUID("00000000-0000-4000-8000-000000000201")
LINK_ID = UUID("00000000-0000-4000-8000-000000000202")
EDITION_ID = UUID("00000000-0000-4000-8000-000000000203")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000000204")
LINK_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000205")
RULE_ID = UUID("00000000-0000-4000-8000-000000000301")
RULE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000302")
PUBLISHED_VERSION_ID = UUID("00000000-0000-4000-8000-000000000303")
RULE_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000304")
POLICY_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000401")
TERMS_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000402")
POLICY_DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000403")
TERMS_DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000404")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000405")
REVIEW_ID = UUID("00000000-0000-4000-8000-000000000501")
RULE_REVIEW_ID = UUID("00000000-0000-4000-8000-000000000502")
CHILD_CANDIDATE_ID = UUID("00000000-0000-4000-8000-000000000503")
UNKNOWN_ID = UUID("00000000-0000-4000-8000-000000000599")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000000601")
SESSION_ID = UUID("00000000-0000-4000-8000-000000000602")

NOW = datetime(2026, 1, 1, tzinfo=UTC)
PRIVATE_MARKERS = (
    "synthetic-private-source-path",
    "synthetic raw Clause body",
    "synthetic-private-password",
)
FORBIDDEN_RESPONSE_FIELDS = {
    "absolute_path",
    "document_text",
    "expression_json",
    "household_space_id",
    "password",
    "raw_pdf",
    "source_path",
}


class _LinkNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "CLAUSE_NOT_FOUND"
    public_message = "rider clause link not found"


class _RuleNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "CLAUSE_NOT_FOUND"
    public_message = "coverage rule not found"


class _ReviewNotFound(ApiBoundaryError):
    status_code = 404
    error_code = "REVIEW_ITEM_NOT_FOUND"
    public_message = "review item not found"


class _VersionConflict(ApiBoundaryError):
    status_code = 409
    error_code = "VERSION_CONFLICT"
    public_message = "version conflict"


class _InvalidCorrection(ApiBoundaryError):
    status_code = 422
    error_code = "INVALID_CANDIDATE_CORRECTION"
    public_message = "candidate correction is invalid"


def _evidence(
    evidence_id: UUID,
    document_version_id: UUID,
    *,
    page: int,
    hash_character: str,
) -> EvidenceRef:
    return EvidenceRef(
        evidence_id=evidence_id,
        document_version_id=document_version_id,
        extraction_id=EXTRACTION_ID,
        content_sha256=hash_character * 64,
        physical_page=page,
        bbox=(Decimal("1"), Decimal("2"), Decimal("30"), Decimal("40")),
        review_state="USER_CONFIRMED",
    )


def _link() -> RiderClauseLink:
    return RiderClauseLink(
        id=LINK_ID,
        household_space_id=SCOPE_A.household_space_id,
        rider_id=RIDER_ID,
        terms_edition_id=EDITION_ID,
        clause_id=CLAUSE_ID,
        candidate_version_id=LINK_CANDIDATE_ID,
        review_state="AI_VERIFIED",
        applicability_reason_code="APPLICABLE",
        version=1,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        evidence=(
            _evidence(
                POLICY_EVIDENCE_ID,
                POLICY_DOCUMENT_VERSION_ID,
                page=1,
                hash_character="a",
            ),
            _evidence(
                TERMS_EVIDENCE_ID,
                TERMS_DOCUMENT_VERSION_ID,
                page=2,
                hash_character="b",
            ),
        ),
    )


def _rule_version() -> CoverageRuleVersion:
    evidence_ids = [str(POLICY_EVIDENCE_ID), str(TERMS_EVIDENCE_ID)]
    document: dict[str, object] = {
        "schema_version": RULE_SCHEMA_VERSION,
        "rule_kind": "temporal",
        "required": True,
        "input_field_paths": ["MedicalEvent.event_date"],
        "expression": {
            "op": "date_between",
            "field": "MedicalEvent.event_date",
            "value": {"start": "2026-01-01", "end": "2026-12-31"},
            "unit": "date",
        },
        "result_reason_code": "SYNTHETIC_TEMPORAL_MATCH",
        "evidence_ids": evidence_ids,
    }
    return CoverageRuleVersion(
        id=RULE_VERSION_ID,
        coverage_rule_id=RULE_ID,
        candidate_version_id=RULE_CANDIDATE_ID,
        version_number=1,
        schema_version=RULE_SCHEMA_VERSION,
        rule_kind="temporal",
        required=True,
        input_field_paths=("MedicalEvent.event_date",),
        rule_document=document,
        result_reason_code="SYNTHETIC_TEMPORAL_MATCH",
        review_state="AI_VERIFIED",
        executable=False,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=None,
        evidence=(
            _evidence(
                POLICY_EVIDENCE_ID,
                POLICY_DOCUMENT_VERSION_ID,
                page=1,
                hash_character="a",
            ),
            _evidence(
                TERMS_EVIDENCE_ID,
                TERMS_DOCUMENT_VERSION_ID,
                page=2,
                hash_character="b",
            ),
        ),
    )


class _FakeLinkService:
    def __init__(self) -> None:
        self.current = _link()
        self.list_calls: list[tuple[HouseholdScope, UUID]] = []
        self.confirm_calls: list[tuple[HouseholdScope, UUID, int]] = []
        self.reject_calls: list[tuple[HouseholdScope, UUID, int, str]] = []

    def list_rider_clause_links(
        self,
        scope: HouseholdScope,
        rider_id: UUID,
    ) -> tuple[RiderClauseLink, ...]:
        self.list_calls.append((scope, rider_id))
        if scope != SCOPE_A or rider_id != RIDER_ID:
            raise _LinkNotFound
        return (self.current,)

    def confirm_rider_clause_link(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
    ) -> RiderClauseLink:
        self.confirm_calls.append((scope, link_id, expected_version))
        if scope != SCOPE_A or link_id != self.current.id:
            raise _LinkNotFound
        if expected_version != self.current.version:
            raise _VersionConflict
        self.current = replace(
            self.current,
            review_state="USER_CONFIRMED",
            applicability_reason_code="APPLICABLE",
            version=expected_version + 1,
        )
        return self.current

    def reject_rider_clause_link(
        self,
        scope: HouseholdScope,
        link_id: UUID,
        *,
        expected_version: int,
        reason_code: str,
    ) -> RiderClauseLink:
        self.reject_calls.append((scope, link_id, expected_version, reason_code))
        if scope != SCOPE_A or link_id != self.current.id:
            raise _LinkNotFound
        if expected_version != self.current.version:
            raise _VersionConflict
        self.current = replace(
            self.current,
            review_state="rejected",
            applicability_reason_code=reason_code,
            version=expected_version + 1,
        )
        return self.current


class _FakeRuleService:
    def __init__(self) -> None:
        self.current = _rule_version()
        self.list_calls: list[tuple[HouseholdScope, UUID]] = []
        self.publish_calls: list[tuple[HouseholdScope, UUID, UUID, int]] = []

    def list_rule_versions(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
    ) -> CoverageRuleVersionCollection:
        self.list_calls.append((scope, rule_id))
        if scope != SCOPE_A or rule_id != RULE_ID:
            raise _RuleNotFound
        return CoverageRuleVersionCollection(
            rule_id=rule_id,
            expected_version=7,
            versions=(self.current,),
        )

    def publish_coverage_rule(
        self,
        scope: HouseholdScope,
        rule_id: UUID,
        version_id: UUID,
        *,
        expected_version: int,
    ) -> CoverageRuleVersion:
        self.publish_calls.append((scope, rule_id, version_id, expected_version))
        if scope != SCOPE_A or rule_id != RULE_ID or version_id != self.current.id:
            raise _RuleNotFound
        if expected_version != 1:
            raise _VersionConflict
        self.current = replace(
            self.current,
            id=PUBLISHED_VERSION_ID,
            version_number=self.current.version_number + 1,
            executable=True,
            published_at=NOW,
        )
        return self.current


def _review_item(
    *,
    review_item_id: UUID,
    candidate_kind: str,
    aggregate_id: UUID,
    candidate_version_id: UUID,
    field_id: str,
    value: object,
) -> PolicyReviewItem:
    return PolicyReviewItem.model_validate(
        {
            "review_item_id": str(review_item_id),
            "candidate_version_id": str(candidate_version_id),
            "aggregate_id": str(aggregate_id),
            "candidate_kind": candidate_kind,
            "status": "NEEDS_REVIEW",
            "fields": [
                {
                    "field_id": field_id,
                    "value": value,
                    "evidence_ids": [str(TERMS_EVIDENCE_ID)],
                }
            ],
            "evidence": [
                {
                    "evidence_id": str(TERMS_EVIDENCE_ID),
                    "document_version_id": str(TERMS_DOCUMENT_VERSION_ID),
                    "document_label": "Sample Terms",
                    "page": 2,
                    "bbox": [1, 2, 30, 40],
                    "bounded_excerpt": "Synthetic Evidence excerpt.",
                }
            ],
            "issues": [],
            "expected_version": 1,
        }
    )


class _FakeCandidateReviewService:
    def __init__(self) -> None:
        self.items = {
            REVIEW_ID: _review_item(
                review_item_id=REVIEW_ID,
                candidate_kind="rider_clause",
                aggregate_id=LINK_ID,
                candidate_version_id=LINK_CANDIDATE_ID,
                field_id="clause_id",
                value=str(CLAUSE_ID),
            ),
            RULE_REVIEW_ID: _review_item(
                review_item_id=RULE_REVIEW_ID,
                candidate_kind="coverage_rule",
                aggregate_id=RULE_ID,
                candidate_version_id=RULE_CANDIDATE_ID,
                field_id="rule_operator",
                value="date_between",
            ),
        }
        self.parent_snapshots = {
            key: deepcopy(item.model_dump(mode="json")) for key, item in self.items.items()
        }
        self.domains: list[str] = []
        self.correction_calls: list[tuple[HouseholdScope, UUID, str]] = []

    def list_review_items(
        self,
        *,
        scope: HouseholdScope,
        status: str = "NEEDS_REVIEW",
        domain: str = "policy",
    ) -> list[PolicyReviewItem]:
        self.domains.append(domain)
        if scope != SCOPE_A:
            return []
        return [
            item
            for item in self.items.values()
            if item.status == status and item.candidate_kind == domain
        ]

    def get_review_item(
        self,
        *,
        scope: HouseholdScope,
        review_item_id: UUID,
    ) -> PolicyReviewItem:
        item = self.items.get(review_item_id)
        if scope != SCOPE_A or item is None:
            raise _ReviewNotFound
        return item

    def correct_field(
        self,
        *,
        scope: HouseholdScope,
        request: CandidateCorrectionRequest,
        review_item_id: UUID,
        actor_id: UUID | None,
    ) -> PolicyReviewItem:
        del actor_id
        self.correction_calls.append((scope, review_item_id, request.field_id))
        item = self.items.get(review_item_id)
        if scope != SCOPE_A or item is None:
            raise _ReviewNotFound
        if request.expected_version != item.expected_version:
            raise _VersionConflict
        if request.evidence_id != TERMS_EVIDENCE_ID:
            raise _InvalidCorrection
        if request.field_id != item.fields[0].field_id:
            raise _InvalidCorrection
        fields = [field.model_dump(mode="json") for field in item.fields]
        fields[0]["value"] = request.value
        fields[0]["evidence_ids"] = [str(request.evidence_id)]
        child = PolicyReviewItem.model_validate(
            {
                **item.model_dump(mode="json"),
                "candidate_version_id": str(CHILD_CANDIDATE_ID),
                "fields": fields,
                "expected_version": item.expected_version + 1,
            }
        )
        self.items[review_item_id] = child
        return child


@pytest.fixture()
def services() -> tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService]:
    return _FakeLinkService(), _FakeRuleService(), _FakeCandidateReviewService()


@pytest.fixture()
def app(
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> FastAPI:
    links, rules, review = services
    application = FastAPI()
    install_error_handlers(application)
    application.include_router(clause_router_module.router)
    application.include_router(candidate_router)

    def resolve_scope(request: Request) -> HouseholdScope:
        context = AuthContext(
            user_id=ACTOR_ID,
            household_space_id=SCOPE_A.household_space_id,
            session_id=SESSION_ID,
            needs_reauthentication=False,
        )
        request.state.auth_context = context
        return HouseholdScope(context.household_space_id)

    application.dependency_overrides[resolve_household_scope] = resolve_scope
    link_dependency = clause_router_module.get_rider_clause_link_service
    rule_dependency = clause_router_module.get_coverage_rule_service
    application.dependency_overrides[link_dependency] = lambda: links
    application.dependency_overrides[rule_dependency] = lambda: rules
    application.dependency_overrides[get_candidate_review_service] = lambda: review
    return application


@pytest.fixture()
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _assert_no_store(response: Any) -> None:
    assert response.headers.get("cache-control", "").lower() == "no-store"


def _assert_fixed_error(response: Any, status_code: int, error_code: str, message: str) -> None:
    assert response.status_code == status_code
    _assert_no_store(response)
    assert response.json() == {"error_code": error_code, "message": message}


def _assert_validation_error(response: Any, expected_fields: set[str]) -> None:
    assert response.status_code == 422
    _assert_no_store(response)
    body = response.json()
    assert body["error_code"] == "INVALID_REQUEST"
    assert body["message"] == "request validation failed"
    assert expected_fields <= set(body.get("fields", []))


def _assert_no_forbidden_fields(value: Any) -> None:
    if isinstance(value, dict):
        assert not FORBIDDEN_RESPONSE_FIELDS.intersection(str(key).lower() for key in value)
        for child in value.values():
            _assert_no_forbidden_fields(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_forbidden_fields(child)


def test_default_scope_is_fail_closed_with_stable_401_envelope() -> None:
    application = create_app(enable_synthetic_ingestion=False)

    with TestClient(application) as test_client:
        response = test_client.get(f"/api/v1/riders/{RIDER_ID}/clause-links")

    _assert_fixed_error(
        response,
        401,
        "AUTHENTICATION_REQUIRED",
        "authentication required",
    )


def test_get_rider_links_uses_injected_scope_and_bounded_evidence(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> None:
    links, _, _ = services
    response = client.get(
        f"/api/v1/riders/{RIDER_ID}/clause-links",
        params={"household_space_id": str(SCOPE_B.household_space_id)},
    )

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    assert body[0]["link_id"] == str(LINK_ID)
    assert body[0]["rider_id"] == str(RIDER_ID)
    assert body[0]["review_state"] == "AI_VERIFIED"
    assert body[0]["version"] == 1
    assert body[0]["applicability_reason_code"] == "APPLICABLE"
    assert len(body[0]["applicability_reason_code"]) <= 64
    assert len(body[0]["evidence"]) == 2
    assert all(item["page_number"] >= 1 for item in body[0]["evidence"])
    assert all(len(item["content_sha256"]) == 64 for item in body[0]["evidence"])
    assert links.list_calls == [(SCOPE_A, RIDER_ID)]
    _assert_no_forbidden_fields(body)


def test_link_transition_bodies_are_strict_and_confirm_uses_expected_version(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> None:
    links, _, _ = services
    confirmed = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/confirm",
        json={"expected_version": 1},
    )

    assert confirmed.status_code == 200
    _assert_no_store(confirmed)
    assert confirmed.json()["review_state"] == "USER_CONFIRMED"
    assert confirmed.json()["version"] == 2
    assert links.confirm_calls == [(SCOPE_A, LINK_ID, 1)]

    extra = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/confirm",
        json={"expected_version": 2, "household_space_id": str(SCOPE_B.household_space_id)},
    )
    _assert_validation_error(extra, {"household_space_id"})
    assert len(links.confirm_calls) == 1


def test_reject_requires_bounded_reason_code_and_expected_version(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> None:
    links, _, _ = services
    rejected = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/reject",
        json={"expected_version": 1, "reason_code": "WRONG_CLAUSE"},
    )

    assert rejected.status_code == 200
    _assert_no_store(rejected)
    assert rejected.json()["review_state"] == "rejected"
    assert rejected.json()["applicability_reason_code"] == "WRONG_CLAUSE"
    assert links.reject_calls == [(SCOPE_A, LINK_ID, 1, "WRONG_CLAUSE")]

    invalid = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/reject",
        json={"expected_version": 1, "reason_code": "free-form private explanation"},
    )
    _assert_validation_error(invalid, {"reason_code"})
    assert len(links.reject_calls) == 1


def test_link_not_found_and_stale_transition_use_fixed_404_and_409_envelopes(
    client: TestClient,
) -> None:
    missing = client.get(f"/api/v1/riders/{UNKNOWN_ID}/clause-links")
    _assert_fixed_error(missing, 404, "CLAUSE_NOT_FOUND", "rider clause link not found")

    stale = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/confirm",
        json={"expected_version": 99},
    )
    _assert_fixed_error(stale, 409, "VERSION_CONFLICT", "version conflict")


def test_get_rule_versions_is_scoped_no_store_and_exposes_only_bounded_projection(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> None:
    _, rules, _ = services
    response = client.get(
        f"/api/v1/coverage-rules/{RULE_ID}/versions",
        params={"household_space_id": str(SCOPE_B.household_space_id)},
    )

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    assert body["rule_id"] == str(RULE_ID)
    assert body["expected_version"] == 7
    assert body["versions"][0]["version_id"] == str(RULE_VERSION_ID)
    assert body["versions"][0]["version_number"] == 1
    assert body["versions"][0]["review_state"] == "AI_VERIFIED"
    assert body["versions"][0]["executable"] is False
    assert len(body["versions"][0]["result_reason_code"]) <= 64
    assert all(item["page_number"] >= 1 for item in body["versions"][0]["evidence"])
    assert rules.list_calls == [(SCOPE_A, RULE_ID)]
    _assert_no_forbidden_fields(body)


def test_publish_selects_stored_version_and_rejects_dsl_or_household_body(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> None:
    _, rules, _ = services
    arbitrary = client.post(
        f"/api/v1/coverage-rules/{RULE_ID}/publish",
        json={
            "expected_version": 1,
            "version_id": str(RULE_VERSION_ID),
            "household_space_id": str(SCOPE_B.household_space_id),
            "expression": {"op": "python", "source": "synthetic"},
        },
    )
    _assert_validation_error(arbitrary, {"household_space_id", "expression"})
    assert rules.publish_calls == []

    published = client.post(
        f"/api/v1/coverage-rules/{RULE_ID}/publish",
        json={"expected_version": 1, "version_id": str(RULE_VERSION_ID)},
    )
    assert published.status_code == 200
    _assert_no_store(published)
    body = published.json()
    assert body["version_id"] == str(PUBLISHED_VERSION_ID)
    assert body["version_number"] == 2
    assert body["executable"] is True
    assert rules.publish_calls == [(SCOPE_A, RULE_ID, RULE_VERSION_ID, 1)]
    _assert_no_forbidden_fields(body)


def test_missing_rule_and_stale_publish_use_fixed_404_and_409_envelopes(
    client: TestClient,
) -> None:
    missing = client.get(f"/api/v1/coverage-rules/{UNKNOWN_ID}/versions")
    _assert_fixed_error(missing, 404, "CLAUSE_NOT_FOUND", "coverage rule not found")

    stale = client.post(
        f"/api/v1/coverage-rules/{RULE_ID}/publish",
        json={"expected_version": 2, "version_id": str(RULE_VERSION_ID)},
    )
    _assert_fixed_error(stale, 409, "VERSION_CONFLICT", "version conflict")


@pytest.mark.parametrize(
    ("domain", "review_id", "candidate_kind"),
    [
        ("rider_clause", REVIEW_ID, "rider_clause"),
        ("coverage_rule", RULE_REVIEW_ID, "coverage_rule"),
    ],
)
def test_generic_review_list_accepts_new_domains_and_is_scoped(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
    domain: str,
    review_id: UUID,
    candidate_kind: str,
) -> None:
    _, _, review = services
    response = client.get(
        "/api/v1/review-items",
        params={
            "domain": domain,
            "status": "NEEDS_REVIEW",
            "household_space_id": str(SCOPE_B.household_space_id),
        },
    )

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    assert [item["review_item_id"] for item in body] == [str(review_id)]
    assert body[0]["candidate_kind"] == candidate_kind
    assert review.domains[-1] == domain
    _assert_no_forbidden_fields(body)


def test_generic_review_get_missing_item_is_a_stable_404(
    client: TestClient,
) -> None:
    missing = client.get(f"/api/v1/review-items/{UNKNOWN_ID}")
    _assert_fixed_error(missing, 404, "REVIEW_ITEM_NOT_FOUND", "review item not found")


@pytest.mark.parametrize(
    ("review_id", "field_id", "value"),
    [
        (REVIEW_ID, "clause_id", str(CLAUSE_ID)),
        (RULE_REVIEW_ID, "rule_operator", "date_between"),
    ],
)
def test_typed_generic_review_patch_creates_child_candidate_version(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
    review_id: UUID,
    field_id: str,
    value: object,
) -> None:
    _, _, review = services
    parent = deepcopy(review.parent_snapshots[review_id])
    response = client.patch(
        f"/api/v1/review-items/{review_id}/fields/{field_id}",
        json={
            "expected_version": 1,
            "field_id": field_id,
            "value": value,
            "evidence_id": str(TERMS_EVIDENCE_ID),
        },
    )

    assert response.status_code == 200
    _assert_no_store(response)
    body = response.json()
    assert body["candidate_version_id"] == str(CHILD_CANDIDATE_ID)
    assert body["expected_version"] == 2
    assert (
        next(field for field in body["fields"] if field["field_id"] == field_id)["value"] == value
    )
    assert review.parent_snapshots[review_id] == parent
    assert review.correction_calls[-1] == (SCOPE_A, review_id, field_id)
    _assert_no_forbidden_fields(body)


def test_generic_review_patch_rejects_untyped_dsl_and_household_fields(
    client: TestClient,
    services: tuple[_FakeLinkService, _FakeRuleService, _FakeCandidateReviewService],
) -> None:
    _, _, review = services
    response = client.patch(
        f"/api/v1/review-items/{RULE_REVIEW_ID}/fields/rule_operator",
        json={
            "expected_version": 1,
            "field_id": "rule_operator",
            "value": {"python": PRIVATE_MARKERS[1]},
            "evidence_id": str(TERMS_EVIDENCE_ID),
            "household_space_id": str(SCOPE_B.household_space_id),
        },
    )

    _assert_validation_error(response, {"household_space_id"})
    assert review.correction_calls == []
    assert all(marker not in response.text for marker in PRIVATE_MARKERS)


def test_strict_transition_and_review_request_models_forbid_private_fields() -> None:
    with pytest.raises(ValidationError):
        CandidateConfirmationRequest.model_validate(
            {"expected_version": 1, "household_space_id": str(SCOPE_A.household_space_id)}
        )
    with pytest.raises(ValidationError):
        CandidateRejectionRequest.model_validate(
            {
                "expected_version": 1,
                "reason_code": "NOT_ENROLLED",
                "source_path": PRIVATE_MARKERS[0],
            }
        )
    with pytest.raises(ValidationError):
        CandidateCorrectionRequest.model_validate(
            {
                "expected_version": 1,
                "field_id": "rule_operator",
                "value": {"python": PRIVATE_MARKERS[1]},
                "evidence_id": str(TERMS_EVIDENCE_ID),
            }
        )
