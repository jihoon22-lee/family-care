"""Frozen daily arithmetic reaches the product through retained certificate evidence."""

import os
from copy import deepcopy
from dataclasses import replace
from decimal import ROUND_HALF_UP, Decimal

import psycopg
import pytest
from familycare_api.decisions.calculation_repository import CalculationRepository
from familycare_api.decisions.calculation_schemas import (
    ReceiptLineCreateRequest,
    ReceiptLineUpdateRequest,
)
from familycare_api.decisions.calculation_service import CalculationService
from familycare_api.guidance.amount_source import read_operational_amount_source
from familycare_api.terms_knowledge.source_meaning import observe_statement
from psycopg.rows import dict_row

from apps.api.tests import fixed_guidance_review_fixture as fixture
from apps.api.tests.fixed_guidance_review_fixture import seed_fixed_review_case
from apps.api.tests.test_terms_change_integration import _psycopg_url
from scripts.claim_guidance_benchmark import DEFAULT_CASES, load_cases

pytestmark = pytest.mark.integration


def _daily_case(suffix=""):
    case = next(case for case in load_cases(DEFAULT_CASES) if case.case_id == "synthetic-dev-daily")
    return replace(case, case_id=case.case_id + suffix) if suffix else case


def _daily_candidate(sample, guidance):
    return next(
        candidate
        for candidate in guidance.candidates
        if sample.coverage_keys[candidate.ref.coverage_id] == "synthetic-dev-daily-daily"
    )


def test_frozen_daily_amount_uses_published_certificate_fields(monkeypatch):
    case = _daily_case()
    sample = seed_fixed_review_case(os.environ["FAMILYCARE_TEST_DATABASE_URL"], case)
    guidance = sample.original.local_guidance
    assert guidance is not None
    candidate = _daily_candidate(sample, guidance)
    assert candidate.estimate.amount == "33"
    assert candidate.estimate.kind == "POINT" and candidate.estimate.currency == "KRW"
    with psycopg.connect(_psycopg_url(sample.database_url), row_factory=dict_row) as connection:
        proof = read_operational_amount_source(connection, sample.scope, candidate.ref.coverage_id)
        assert proof.amount == Decimal("11") and proof.currency == "KRW"
        assert proof.amount_authority == proof.currency_authority == "PROGRAM_VERIFIED"
        assert proof.amount_evidence and proof.currency_evidence and proof.publication_ids
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM analysis_candidate_versions WHERE household_space_id=%s "
                "AND status='USER_CONFIRMED'",
                (sample.scope.household_space_id,),
            ).fetchone()["n"]
            == 0
        )
    reopened = seed_fixed_review_case(sample.database_url, case)
    assert reopened.original == sample.original and reopened.coverage_keys == sample.coverage_keys
    with monkeypatch.context() as patch:
        patch.setattr(fixture, "REVISION", "fixed-review-database-fixture-v2")
        with pytest.raises(ValueError, match="FIXED_REVIEW_FIXTURE_INCOMPLETE_OR_CHANGED"):
            seed_fixed_review_case(sample.database_url, case)


def test_missing_certificate_field_evidence_cannot_reuse_the_ledger_number():
    sample = seed_fixed_review_case(
        os.environ["FAMILYCARE_TEST_DATABASE_URL"], _daily_case("-missing-amount-evidence")
    )
    candidate = _daily_candidate(sample, sample.original.local_guidance)
    assert candidate.estimate.amount == "33"
    with psycopg.connect(_psycopg_url(sample.database_url), row_factory=dict_row) as connection:
        proof = read_operational_amount_source(connection, sample.scope, candidate.ref.coverage_id)
        connection.execute(
            "DELETE FROM analysis_candidate_evidence WHERE candidate_version_id=ANY(%s) "
            "AND field_id='sum_assured'",
            (list(proof.publication_ids),),
        )
    result = sample.service.analyze_medical_event(sample.event.id)
    changed = _daily_candidate(sample, result.local_guidance)
    assert changed.estimate.amount is None
    assert "Rider.insured_amount" in changed.estimate.missing_inputs


def test_different_terms_currency_remains_a_real_mismatch(monkeypatch):
    source_body = fixture._source_body

    def different_currency(raw):
        return source_body(raw).replace("insured amount in KRW;", "insured amount in USD;")

    monkeypatch.setattr(fixture, "_source_body", different_currency)
    sample = seed_fixed_review_case(
        os.environ["FAMILYCARE_TEST_DATABASE_URL"], _daily_case("-currency-mismatch")
    )
    candidate = _daily_candidate(sample, sample.original.local_guidance)
    assert candidate.estimate.amount is None
    assert candidate.estimate.reason_code == "CALCULATION_CURRENCY_MISMATCH"


def test_whole_krw_companion_preserves_frozen_arithmetic_and_original_units():
    checked = set()
    for case in load_cases(DEFAULT_CASES):
        for raw in case.scenario_parameters["coverages"]:
            spec = raw.get("calculation", {})
            if not spec:
                continue
            assert spec["unit"] == "synthetic-credit"
            for line in fixture._source_body(raw).splitlines():
                current = observe_statement(line)
                previous = observe_statement(line.replace("KRW", "TST"))
                assert (
                    current == previous
                    or current is not None
                    and previous
                    == {
                        **current,
                        "currency": "TST",
                    }
                )
            amount = fixture.exact_companion_amount(case.scenario_parameters, raw)
            if amount is not None:
                assert amount == amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if "expected_amount" in spec:
                    assert amount == Decimal(spec["expected_amount"])
                checked.add(spec["kind"])
    assert checked == {"fixed", "daily", "ratio"}


@pytest.mark.parametrize(("condition", "expected"), [(True, "30"), (False, "60"), (None, None)])
def test_source_bound_ratio_keeps_true_false_and_missing_condition_distinct(condition, expected):
    original = next(
        case for case in load_cases(DEFAULT_CASES) if case.case_id == "synthetic-holdout-ratio"
    )
    parameters = deepcopy(original.scenario_parameters)
    if condition is None:
        parameters["event_facts"].pop("event.reduction_applies")
    else:
        parameters["event_facts"]["event.reduction_applies"] = condition
    case = (
        original
        if condition is True
        else replace(
            original,
            case_id=original.case_id
            + ("-condition-missing" if condition is None else "-condition-false"),
            scenario_parameters=parameters,
        )
    )
    # The frozen true-condition oracle remains 30; false/missing are independent
    # counterfactual event fixtures, never edits to the benchmark source or label.
    assert original.scenario_parameters["event_facts"]["event.reduction_applies"] is True
    assert original.scenario_parameters["coverages"][0]["calculation"]["expected_amount"] == "30"
    sample = seed_fixed_review_case(os.environ["FAMILYCARE_TEST_DATABASE_URL"], case)
    guidance = sample.original.local_guidance
    assert guidance is not None
    candidate = next(
        item
        for item in guidance.candidates
        if sample.coverage_keys[item.ref.coverage_id] == "synthetic-holdout-ratio-ratio"
    )
    assert candidate.group == "PRIMARY" and candidate.benefit_kind == "FIXED"
    with psycopg.connect(_psycopg_url(sample.database_url), row_factory=dict_row) as connection:
        proof = read_operational_amount_source(connection, sample.scope, candidate.ref.coverage_id)
    assert proof.amount == Decimal("240") and proof.currency == "KRW"
    assert proof.amount_authority == proof.currency_authority == "PROGRAM_VERIFIED"
    assert proof.amount_evidence and proof.currency_evidence and proof.publication_ids
    assert candidate.estimate.amount == expected
    if condition is None:
        assert "MedicalEvent.reduction_applies" not in sample.event.facts
        assert candidate.estimate.kind != "POINT"
        assert "MedicalEvent.reduction_applies" in candidate.estimate.missing_inputs
    else:
        fact = sample.event.facts["MedicalEvent.reduction_applies"]
        assert fact.value is condition and fact.confirmation == "user"
        assert all(
            item["field_id"] != "reduction_applies" for item in sample.event.structured_facts
        )
        assert candidate.estimate.kind == "POINT" and candidate.estimate.currency == "KRW"
        trace = candidate.estimate.trace
        assert trace is not None and trace.status == "COMPLETE"
        assert any(ref.source_kind == "SEMANTIC_PUBLICATION" for ref in trace.source_refs)
        insured = [
            operand
            for step in trace.steps
            for operand in step.operands
            if operand.field_path == "Rider.insured_amount"
        ]
        assert insured and all(
            operand.value == "240"
            and operand.provenance == "PROGRAM_VERIFIED"
            and operand.source_refs
            for operand in insured
        )


def test_original_indemnity_uses_only_the_registered_covered_cost_and_never_a_fixed_subtotal():
    original = next(
        case for case in load_cases(DEFAULT_CASES) if case.case_id == "synthetic-dev-partial-cost"
    )
    parameters = deepcopy(original.scenario_parameters)
    renamed = {
        key: f"synthetic-indemnity-source-proof-{index}"
        for index, key in enumerate(original.candidate_pool)
    }
    for raw in parameters["coverages"]:
        raw["coverage_key"] = renamed[raw["coverage_key"]]
    case = replace(
        original,
        case_id=original.case_id + "-receipt-source",
        candidate_pool=tuple(renamed[key] for key in original.candidate_pool),
        expected_primary=tuple(renamed[key] for key in original.expected_primary),
        expected_conditional=tuple(renamed[key] for key in original.expected_conditional),
        scenario_parameters=parameters,
    )
    spec = case.scenario_parameters["coverages"][0]["calculation"]
    cost = case.scenario_parameters["event_facts"][spec["cost_field"]]
    assert cost == "75" and spec["deductible"] == "15" and spec["expected_amount"] == "60"
    sample = seed_fixed_review_case(os.environ["FAMILYCARE_TEST_DATABASE_URL"], case)

    def coverage(guidance):
        assert guidance is not None
        return next(
            item
            for item in guidance.candidates
            if sample.coverage_keys[item.ref.coverage_id]
            == renamed["synthetic-dev-partial-cost-cost"]
        )

    # The original confirmed eligible-cost input must reach the normal receipt
    # boundary before the fixture's first saved DecisionService answer.
    assert coverage(sample.original.local_guidance).estimate.amount == "60"
    receipts = CalculationService(sample.scope, CalculationRepository(sample.database_url))
    lines = receipts.list_receipt_lines(sample.event.id)
    assert len(lines) == 1
    line = lines[0]
    assert line.amount.amount == Decimal(cost) and line.amount.currency == "KRW"
    assert line.confirmation_level == "user" and line.coverage_category == "covered"

    def register_original_cost():
        return receipts.create_receipt_line(
            sample.event.id,
            ReceiptLineCreateRequest(
                category="outpatient",
                coverage_category="covered",
                amount=cost,
                currency="KRW",
                confirmation_level="user",
            ),
        )

    try:
        guidance = sample.service.analyze_medical_event(sample.event.id).local_guidance
        candidate = coverage(guidance)
        assert candidate.group == "PRIMARY" and candidate.benefit_kind == "INDEMNITY"
        assert candidate.cases and all(case.benefit_kind == "INDEMNITY" for case in candidate.cases)
        estimate = candidate.estimate
        assert estimate.kind == "POINT" and estimate.amount == "60" and estimate.currency == "KRW"
        assert estimate.basis == "REGISTERED_COSTS" and estimate.evidence
        assert estimate.trace is not None and estimate.trace.status == "COMPLETE"
        assert any(ref.source_kind == "SEMANTIC_PUBLICATION" for ref in estimate.trace.source_refs)
        operands = [
            operand
            for step in estimate.trace.steps
            for operand in step.operands
            if operand.field_path == "Receipt.covered_amount"
        ]
        assert operands and all(
            operand.value == "75"
            and operand.currency == "KRW"
            and operand.provenance == "USER_CONFIRMED"
            and any(
                ref.source_kind == "RECEIPT_LINE"
                and ref.source_id == str(line.line_id)
                and ref.version == line.version
                and ref.digest_sha256 is not None
                for ref in operand.source_refs
            )
            for operand in operands
        )
        assert guidance.expenses is not None
        assert guidance.expenses.currencies[0].covered.known_cost == "75"
        assert not guidance.fixed_subtotals
        assert any(
            omission.ref == candidate.ref
            and omission.benefit_kind == "INDEMNITY"
            and omission.reason_code == "NON_FIXED_BENEFIT"
            for omission in guidance.subtotal_omissions
        )

        receipts.delete_receipt_line(sample.event.id, line.line_id, expected_version=line.version)
        line = None
        missing = sample.service.analyze_medical_event(sample.event.id).local_guidance
        assert Decimal(str(sample.event.facts["Receipt.covered_amount"].value)) == Decimal(cost)
        assert coverage(missing).estimate.amount is None
        assert "Receipt.covered_amount" in coverage(missing).estimate.missing_inputs
        line = register_original_cost()

        line = receipts.update_receipt_line(
            sample.event.id,
            line.line_id,
            ReceiptLineUpdateRequest(
                expected_version=line.version, confirmation_level="ai_structured"
            ),
        )
        unconfirmed = sample.service.analyze_medical_event(sample.event.id).local_guidance
        assert coverage(unconfirmed).estimate.amount is None
        assert unconfirmed.expenses.currencies[0].unconfirmed.known_cost == "75"
        assert unconfirmed.expenses.currencies[0].covered.known_cost is None

        line = receipts.update_receipt_line(
            sample.event.id,
            line.line_id,
            ReceiptLineUpdateRequest(
                expected_version=line.version, confirmation_level="user", currency="USD"
            ),
        )
        mismatch = sample.service.analyze_medical_event(sample.event.id).local_guidance
        assert coverage(mismatch).estimate.amount is None
        assert mismatch.expenses.currencies[0].currency == "USD"
    finally:
        if line is None:
            register_original_cost()
        else:
            receipts.update_receipt_line(
                sample.event.id,
                line.line_id,
                ReceiptLineUpdateRequest(
                    expected_version=line.version, confirmation_level="user", currency="KRW"
                ),
            )
