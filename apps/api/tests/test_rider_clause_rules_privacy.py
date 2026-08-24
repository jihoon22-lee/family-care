"""Privacy regressions for Rider-Clause and CoverageRule transport boundaries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, NoReturn
from uuid import UUID

import pytest
from familycare_api.clauses.errors import CoverageRuleInvalid, RiderClauseLinkInvalid
from familycare_api.clauses.links import RiderClauseLink
from familycare_api.clauses.router import (
    get_coverage_rule_service,
    get_rider_clause_link_service,
)
from familycare_api.clauses.rules import CoverageRuleVersion
from familycare_api.clauses.schemas import (
    CoverageRuleVersionResponse,
    RiderClauseLinkResponse,
)
from familycare_api.common.evidence import EvidenceRef
from familycare_api.common.scope import HouseholdScope, resolve_household_scope
from familycare_api.main import create_app
from fastapi.testclient import TestClient

SCOPE = HouseholdScope(UUID("00000000-0000-4000-8000-000000000601"))
LINK_ID = UUID("00000000-0000-4000-8000-000000000602")
RIDER_ID = UUID("00000000-0000-4000-8000-000000000603")
EDITION_ID = UUID("00000000-0000-4000-8000-000000000604")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000000605")
EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000606")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000607")
EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000608")
RULE_ID = UUID("00000000-0000-4000-8000-000000000609")
RULE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000610")
CANDIDATE_VERSION_ID = UUID("00000000-0000-4000-8000-000000000611")

PRIVATE_MARKERS = (
    "synthetic-private-rule-expression",
    "synthetic-private-clause-body",
    "/synthetic/private/rules.pdf",
    "synthetic-private-query",
    "synthetic-private-source",
    "synthetic-private-provider-id",
    "synthetic-private-household-id",
    "synthetic-private-secret",
)

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _evidence() -> EvidenceRef:
    return EvidenceRef(
        evidence_id=EVIDENCE_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256="a" * 64,
        physical_page=1,
        bbox=(Decimal("1"), Decimal("2"), Decimal("30"), Decimal("40")),
        review_state="USER_CONFIRMED",
    )


def _link() -> RiderClauseLink:
    return RiderClauseLink(
        id=LINK_ID,
        household_space_id=SCOPE.household_space_id,
        rider_id=RIDER_ID,
        terms_edition_id=EDITION_ID,
        clause_id=CLAUSE_ID,
        candidate_version_id=CANDIDATE_VERSION_ID,
        review_state="USER_CONFIRMED",
        applicability_reason_code="APPLICABLE",
        version=2,
        created_at=NOW,
        updated_at=NOW,
        deleted_at=None,
        evidence=(_evidence(),),
        rider_label="Sample Rider",
        clause_label="Sample Clause",
    )


def _rule_version() -> CoverageRuleVersion:
    evidence = _evidence()
    private_expression = {
        "op": "equals",
        "field": "MedicalEvent.classification",
        "value": PRIVATE_MARKERS[0],
    }
    return CoverageRuleVersion(
        id=RULE_VERSION_ID,
        coverage_rule_id=RULE_ID,
        candidate_version_id=CANDIDATE_VERSION_ID,
        version_number=1,
        schema_version="coverage-rule-v1",
        rule_kind="classification",
        required=True,
        input_field_paths=("MedicalEvent.classification",),
        rule_document={
            "schema_version": "coverage-rule-v1",
            "rule_kind": "classification",
            "required": True,
            "input_field_paths": ["MedicalEvent.classification"],
            "expression": private_expression,
            "result_reason_code": "SYNTHETIC_CLASSIFICATION_MATCH",
            "evidence_ids": [str(EVIDENCE_ID)],
        },
        result_reason_code="SYNTHETIC_CLASSIFICATION_MATCH",
        review_state="USER_CONFIRMED",
        executable=False,
        generator_version="synthetic-generator-v1",
        verifier_version="synthetic-verifier-v1",
        created_at=NOW,
        published_at=None,
        evidence=(evidence,),
    )


class _FailingLinkService:
    def list_rider_clause_links(self, *_: Any) -> NoReturn:
        raise RiderClauseLinkInvalid("LINK_EVIDENCE_INVALID")

    def confirm_rider_clause_link(self, *_: Any, **__: Any) -> NoReturn:
        raise RiderClauseLinkInvalid("LINK_EVIDENCE_INVALID")

    def reject_rider_clause_link(self, *_: Any, **__: Any) -> NoReturn:
        raise RiderClauseLinkInvalid("LINK_EVIDENCE_INVALID")


class _FailingRuleService:
    def list_rule_versions(self, *_: Any) -> NoReturn:
        raise CoverageRuleInvalid("RULE_DSL_INVALID")

    def publish_coverage_rule(self, *_: Any, **__: Any) -> NoReturn:
        raise CoverageRuleInvalid("RULE_DSL_INVALID")


@pytest.fixture()
def client() -> TestClient:
    app = create_app(enable_synthetic_ingestion=False)
    app.dependency_overrides[resolve_household_scope] = lambda: SCOPE
    app.dependency_overrides[get_rider_clause_link_service] = _FailingLinkService
    app.dependency_overrides[get_coverage_rule_service] = _FailingRuleService
    return TestClient(app)


def _serialized_error(response: Any, caplog: pytest.LogCaptureFixture) -> str:
    return f"{response.text}\n{caplog.text}".lower()


def test_public_rule_and_link_projections_omit_raw_rule_text_and_private_fields() -> None:
    link_payload = RiderClauseLinkResponse.from_domain(_link()).model_dump_json()
    rule_payload = CoverageRuleVersionResponse.from_domain(_rule_version()).model_dump_json()
    serialized = f"{link_payload}\n{rule_payload}".lower()

    assert PRIVATE_MARKERS[0] not in serialized
    assert "rule_document" not in serialized
    assert "household_space_id" not in serialized
    assert "provider_request_id" not in serialized
    assert "source_path" not in serialized
    assert "query" not in serialized
    assert "secret" not in serialized
    assert "clause_label" in serialized
    assert "input_field_paths" in serialized


def test_invalid_transition_body_does_not_echo_rule_text_paths_query_or_secrets(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    response = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/confirm",
        json={
            "expected_version": 1,
            "rule_document": PRIVATE_MARKERS[0],
            "clause_text": PRIVATE_MARKERS[1],
            "source_path": PRIVATE_MARKERS[2],
            "query": PRIVATE_MARKERS[3],
            "source": PRIVATE_MARKERS[4],
            "provider_request_id": PRIVATE_MARKERS[5],
            "household_space_id": PRIVATE_MARKERS[6],
            "secret": PRIVATE_MARKERS[7],
        },
    )

    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
    serialized = _serialized_error(response, caplog)
    assert all(marker.lower() not in serialized for marker in PRIVATE_MARKERS)


def test_link_and_rule_errors_are_fixed_and_value_free(
    client: TestClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    link_error = client.post(
        f"/api/v1/rider-clause-links/{LINK_ID}/confirm",
        json={"expected_version": 1},
    )
    rule_error = client.post(
        f"/api/v1/coverage-rules/{RULE_ID}/publish",
        json={"expected_version": 1, "version_id": str(RULE_VERSION_ID)},
    )

    assert link_error.status_code == 422
    assert link_error.json() == {
        "error_code": "EVIDENCE_INVALID",
        "message": "Rider clause link is invalid",
    }
    assert rule_error.status_code == 422
    assert rule_error.json() == {
        "error_code": "EVIDENCE_INVALID",
        "message": "coverage rule is invalid",
    }
    serialized = _serialized_error(link_error, caplog) + _serialized_error(rule_error, caplog)
    assert all(marker.lower() not in serialized for marker in PRIVATE_MARKERS)


def test_error_objects_do_not_store_private_rule_or_clause_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    errors = (
        RiderClauseLinkInvalid("CLAUSE_DOCUMENT_MISMATCH"),
        CoverageRuleInvalid("RULE_DSL_INVALID"),
    )
    serialized = "\n".join(f"{error!s} {error!r} {error.public_message}" for error in errors)
    serialized += caplog.text

    assert all(marker.lower() not in serialized.lower() for marker in PRIVATE_MARKERS)
    assert all("rule_document" not in serialized.lower() for _ in errors)
    assert all("household_space_id" not in serialized.lower() for _ in errors)
