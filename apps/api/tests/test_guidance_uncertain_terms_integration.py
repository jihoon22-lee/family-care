"""Retained, approved operational knowledge can inform an uncertain application date."""

from copy import deepcopy
from datetime import date

import psycopg
import pytest
from familycare_api.claims.repository import ClaimRepository
from familycare_api.clauses.repository import CoverageRuleRepository, RiderClauseLinkRepository
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_worker.document_structure import build_document_structure
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests import test_component_rule_sources as rule_sources
from apps.api.tests import test_terms_applicability_consumers as policy_sources
from apps.api.tests.test_decision_integration import _service
from apps.api.tests.test_rider_clause_rules_integration import _psycopg_url
from apps.api.tests.test_rider_clause_rules_integration import database_url as database_url

pytestmark = pytest.mark.integration


def _approved_sources(url, monkeypatch):
    def with_shared_code(payload, **kwargs):
        payload = deepcopy(payload)
        for page in payload["pages"]:
            for block in page["blocks"]:
                if block["text"].startswith(("보험약관", "보험증권")):
                    block["text"] += "\n상품코드: SYNTHETIC-A"
        return build_document_structure(payload, **kwargs)

    # Extend only the synthetic construction inputs; production parsing/publication runs normally.
    monkeypatch.setattr(rule_sources, "build_document_structure", with_shared_code)
    monkeypatch.setattr(policy_sources, "build_document_structure", with_shared_code)
    seed, edition = rule_sources._bounded_rule_source(url)
    policy_sources._policy_metadata(url, seed)
    assert TermsApplicabilityProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assessment = connection.execute(
            "SELECT * FROM current_policy_terms_applicability WHERE terms_edition_id=%s",
            (edition["id"],),
        ).fetchone()
        assert assessment["status"] == "UNKNOWN"
        assert assessment["reason_codes"] == ["INSUFFICIENT_APPLICABILITY_EVIDENCE"]
        # The old date-compatible approval path remains available. The current raw policy
        # lacks a printed contract date, so event-specific assessment retains UNKNOWN.
        connection.execute(
            "UPDATE coverage_rule_versions SET rule_kind='eligibility',input_field_paths=%s,"
            "expression_json=%s,result_reason_code='SYNTHETIC_ADMISSION' WHERE id=%s",
            (
                Jsonb(["MedicalEvent.admission"]),
                Jsonb(
                    {
                        "schema_version": "coverage-rule-v1",
                        "rule_kind": "eligibility",
                        "required": True,
                        "input_field_paths": ["MedicalEvent.admission"],
                        "expression": {
                            "op": "equals",
                            "field": "MedicalEvent.admission",
                            "value": True,
                        },
                        "result_reason_code": "SYNTHETIC_ADMISSION",
                        "evidence_ids": [str(seed.policy_evidence_id), str(seed.terms_evidence_id)],
                    }
                ),
                seed.rule_version_id,
            ),
        )
    RiderClauseLinkRepository(url).confirm(seed.scope_a, seed.link_id, expected_version=1)
    CoverageRuleRepository(url).publish(
        seed.scope_a, seed.rule_id, seed.rule_version_id, expected_version=1
    )
    return seed, edition, assessment


@pytest.mark.parametrize("barrier", ["component_excluded", "policy_corrected"])
def test_current_uncertainty_retains_candidate_and_history_but_respects_later_barriers(
    database_url, monkeypatch, barrier
):
    seed, edition, assessment = _approved_sources(database_url, monkeypatch)
    service = _service(database_url, seed.scope_a)
    event = service.create_medical_event(
        family_member_id=assessment["family_member_id"],
        mode="post_treatment",
        situation="입원했습니다.",
        event_date=date(2025, 6, 15),
        facts={"MedicalEvent.admission_days": 1},
        confirmation={"MedicalEvent.admission_days": "user"},
    )
    result = service.analyze_medical_event(event.id)
    candidate = next(
        c for c in result.local_guidance.candidates if c.ref.coverage_id == seed.rider_id
    )
    assert candidate.group == "CONDITIONAL"
    assert candidate.condition_result == "UNKNOWN"
    assert "TERMS_APPLICABILITY_UNRESOLVED" in candidate.reason_codes
    assert candidate.estimate.amount is None
    assert candidate.relevance and candidate.conditions[0].evidence
    claims = ClaimRepository(database_url)
    claim = claims.create_guidance_claim_case(
        seed.scope_a,
        event.id,
        run_id=result.run_id,
        expected_event_version=event.version,
        coverage=candidate.ref,
    )
    saved_snapshot = claim["snapshot"]
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        if barrier == "component_excluded":
            connection.execute(
                "UPDATE insurance_document_components SET review_state='REJECTED',"
                "version=version+1 WHERE id=%s",
                (edition["source_component_id"],),
            )
        else:
            connection.execute(
                "UPDATE policy_contracts SET product_display='Synthetic Different Product',"
                "product_key='synthetic-different-product',version=version+1 WHERE id=%s",
                (assessment["policy_contract_id"],),
            )
    later = service.analyze_medical_event(event.id)
    assert not later.local_guidance.candidates
    assert service.get_decision_result(event.id, 1).run_id == later.run_id
    with psycopg.connect(_psycopg_url(database_url)) as connection:
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s",
            (result.run_id,),
        ).fetchone()[0] == result.local_guidance.model_dump(mode="json")
    assert claims.get_claim_case(seed.scope_a, claim["id"])["snapshot"] == saved_snapshot
