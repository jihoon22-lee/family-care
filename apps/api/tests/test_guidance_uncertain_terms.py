"""An existing approved rule keeps relevance when only its application date is missing."""

from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from familycare_api.clauses.terms_change_selection import (
    SelectedTermsEdition,
    TermsEventSelection,
    TermsSelectionScope,
)
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import FactValue
from familycare_api.decisions.terms import RulesForEvent
from familycare_api.guidance.amount_source import OperationalAmountSource
from familycare_api.guidance.domain import GuidanceContext
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.models import GuidanceVersions
from familycare_api.guidance.repository import read_operational_guidance

from apps.api.tests.test_decision_engine import (
    EVIDENCE_ID,
    MEMBER_ID,
    POLICY_ID,
    RIDER_A,
    SCOPE_ID,
    calculation_rule,
    event,
    evidence_ref,
    rule,
    snapshot,
)

EDITION = UUID(int=7001)
CLAUSE = UUID(int=7002)
ASSESSMENT = UUID(int=7003)


def _proof(role):
    return {
        "role": role,
        "facts": [
            {"field": "insurer", "value": "Sample Assurance"},
            {"field": "product_code", "value": "SYNTHETIC-A"},
        ],
        "conflicting_fields": [],
        "unresolved_fields": [],
    }


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class _Connection:
    """Only source queries; writes or unexpected reads fail this adapter fixture."""

    def __init__(self, assessments):
        self.assessments = assessments
        self.queries = []

    def transaction(self):
        return nullcontext()

    def execute(self, query, params=None):
        self.queries.append(query)
        if "FROM claim_history" in query:
            return _Rows([{"count": 0}])
        if "SELECT p.product_display" in query:
            return _Rows(
                [
                    {
                        "product_display": "Sample Policy",
                        "policy_evidence": EVIDENCE_ID,
                        "rider_evidence": EVIDENCE_ID,
                        "party_evidence": [EVIDENCE_ID],
                        "policy_version": 1,
                        "rider_version": 1,
                        "policy_status": "active",
                        "rider_status": "active",
                    }
                ]
            )
        if "current_policy_terms_applicability" in query:
            return _Rows(self.assessments)
        raise AssertionError("unexpected source query")


def _read(monkeypatch, *, status="UNKNOWN", fault=None, relevant=True, required=True):
    import familycare_api.guidance.repository as module

    scope = HouseholdScope(SCOPE_ID)
    current_event = replace(
        event(),
        facts={"MedicalEvent.admission": FactValue(relevant, "user", ())},
        situation="Synthetic event",
    )
    source_rules = (
        rule(
            field="MedicalEvent.admission",
            value=True,
            required=required,
            evidence=(evidence_ref(UUID(int=7300)),),
        ),
        calculation_rule(),
    )
    if fault == "invalid_calculation_source":
        source_rules = (
            source_rules[0],
            replace(source_rules[1], result_reason_code="SYNTHETIC_MISMATCH"),
        )
    selected_edition = SelectedTermsEdition(
        EDITION, status, (), ("BASE_TERMS_SOURCE_UNRESOLVED",) if status == "UNKNOWN" else ()
    )
    selection = TermsEventSelection(
        TermsSelectionScope(SCOPE_ID, POLICY_ID, MEMBER_ID, RIDER_A, CLAUSE),
        current_event.event_date,
        (selected_edition,),
        (),
        (),
        base_assessment_ids=(ASSESSMENT,),
    )
    assessments = [
        {
            "rule_version_id": item.id,
            "clause_id": CLAUSE,
            "terms_edition_id": EDITION,
            "assessment_id": ASSESSMENT,
            "status": "UNKNOWN",
            "selection_state": "UNRESOLVED",
            "reason_codes": ["INSUFFICIENT_APPLICABILITY_EVIDENCE"],
            "evidence_fields": [
                [side, field]
                for side in ("policy", "terms")
                for field in ("insurer", "product_code")
            ],
            "policy_proof": _proof("policy"),
            "terms_proof": _proof("terms"),
            "gate": "LEGACY",
        }
        for item in source_rules
    ]
    if fault in {"stale", "excluded_component", "unapproved_link"}:
        assessments = []
    elif fault == "user_override":
        for assessment in assessments:
            assessment["reason_codes"] = ["USER_DOCUMENT_DECISION_EXISTS"]
            assessment["gate"] = "BLOCKED"
    elif fault == "wrong_product":
        for assessment in assessments:
            assessment["terms_proof"]["facts"][1]["value"] = "SYNTHETIC-B"
    elif fault == "change_uncertain":
        selection = replace(selection, uncertain_relation_ids=(UUID(int=7100),))
    selected = RulesForEvent(
        source_rules,
        (selection,),
        tuple((item.id, status) for item in source_rules),
    )
    repository = SimpleNamespace(
        database_url="unused-synthetic",
        _policy_snapshots=lambda *args: (snapshot(RIDER_A),),
        _rule_versions=lambda *args, **kwargs: selected,
        _evidence_many=lambda *args: (
            (evidence_ref(),)
            if fault == "invalid_evidence"
            else (evidence_ref(), evidence_ref(UUID(int=7300)))
        ),
    )
    subjects = GuidanceContext(
        SCOPE_ID, MEMBER_ID, (), versions=GuidanceVersions(engine="local-guidance-v2")
    )
    monkeypatch.setattr(module, "read_subject_guidance", lambda *args: subjects)
    monkeypatch.setattr(module.CanonicalLinkRepository, "read_in_transaction", lambda *args: ())
    monkeypatch.setattr(module.claim_aliases, "read_claim_coverage_aliases", lambda *a, **k: ())
    monkeypatch.setattr(module, "_event_statuses", lambda *args: ((), []))
    monkeypatch.setattr(
        module,
        "SemanticGuidanceReader",
        lambda *args: SimpleNamespace(for_rider=lambda *a: SimpleNamespace(roots=(), versions=())),
    )
    amount = OperationalAmountSource(
        RIDER_A,
        1,
        Decimal("100"),
        "KRW",
        "MATCH",
        "MATCH",
        "PROGRAM_VERIFIED",
        "PROGRAM_VERIFIED",
        (evidence_ref(),),
        (evidence_ref(),),
        (UUID(int=7200),),
        "a" * 64,
        (),
    )
    monkeypatch.setattr(module, "read_operational_amount_source", lambda *args: amount)
    connection = _Connection(assessments)
    context = read_operational_guidance(connection, scope, current_event, repository)
    result = LocalGuidanceEngine().evaluate(scope, current_event, context)
    return context, result, connection


@pytest.mark.parametrize("required", [True, False])
def test_missing_application_date_keeps_true_source_and_conditional_candidate(
    monkeypatch, required
):
    context, result, connection = _read(monkeypatch, required=required)
    assert all(c.lineage_valid for rule in context.coverages[0].rules for c in rule.citations)
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL"
    assert candidate.condition_result == "UNKNOWN"
    assert "TERMS_APPLICABILITY_UNRESOLVED" in candidate.reason_codes
    assert any(c.reason_code == "TERMS_APPLICABILITY_UNRESOLVED" for c in candidate.conditions)
    assert candidate.relevance and candidate.estimate.evidence
    assert candidate.estimate.kind == "FORMULA" and candidate.estimate.formula
    assert candidate.estimate.amount is None
    assert candidate.estimate.reason_code == "TERMS_APPLICABILITY_UNRESOLVED"
    assert not result.fixed_subtotals
    assert all(query.lstrip().startswith("SELECT") for query in connection.queries)


def test_matching_application_retains_the_previous_amount(monkeypatch):
    _, result, _ = _read(monkeypatch, status="MATCH")
    assert len(result.candidates) == 1
    assert result.candidates[0].group == "PRIMARY"
    assert result.candidates[0].estimate.amount == "50"


@pytest.mark.parametrize(
    "fault",
    [
        "invalid_evidence",
        "wrong_product",
        "user_override",
        "stale",
        "excluded_component",
        "unapproved_link",
        "change_uncertain",
    ],
)
def test_uncertainty_cannot_bypass_source_identity_or_current_decisions(monkeypatch, fault):
    assert not _read(monkeypatch, fault=fault)[1].candidates


def test_decisive_wrong_edition_and_unrelated_event_still_have_no_candidate(monkeypatch):
    assert not _read(monkeypatch, status="NO_MATCH")[1].candidates
    assert not _read(monkeypatch, relevant=False)[1].candidates


def test_a_planned_scenario_cannot_hide_its_relevance_application_uncertainty(monkeypatch):
    from apps.api.tests.test_guidance_local_event_engine import context as semantic_context

    context, _, _ = _read(monkeypatch, required=False)
    source = context.coverages[0]
    # The formula has an independently applicable original source, while the only
    # positive admission witness is optional and still has unresolved application.
    context = replace(
        context,
        coverages=(replace(source, calculation=semantic_context().coverages[0].calculation),),
    )
    planned = replace(event(), facts={}, situation="5일 입원 예정입니다.")
    result = LocalGuidanceEngine().evaluate(HouseholdScope(SCOPE_ID), planned, context)
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL" and candidate.estimate.amount is None
    assert not candidate.scenarios
    assert not result.fixed_subtotals


def test_unknown_application_does_not_bypass_original_calculation_source_binding(monkeypatch):
    _, result, _ = _read(monkeypatch, fault="invalid_calculation_source")
    candidate = result.candidates[0]
    assert candidate.group == "CONDITIONAL"
    assert candidate.estimate.kind == "UNAVAILABLE"
    assert candidate.estimate.formula is None and candidate.estimate.amount is None
    assert candidate.estimate.reason_code.startswith("CALCULATION_SOURCE_")
