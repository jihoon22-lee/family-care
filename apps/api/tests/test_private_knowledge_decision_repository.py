"""Scoped repository tests for private-knowledge decision snapshots."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.domain import MedicalEvent
from familycare_api.decisions.knowledge_engine import DeterministicKnowledgeDecisionEngine
from familycare_api.decisions.knowledge_repository import (
    PostgresKnowledgeDecisionRepository,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000007001")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000007002")
EVENT_ID = UUID("00000000-0000-4000-8000-000000007003")
KNOWLEDGE_RUN_ID = UUID("00000000-0000-4000-8000-000000007004")
RULE_RUN_ID = UUID("00000000-0000-4000-8000-000000007005")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000007006")
COVERAGE_ID = UUID("00000000-0000-4000-8000-000000007007")
RIDER_ID = UUID("00000000-0000-4000-8000-000000007008")
SECTION_ID = UUID("00000000-0000-4000-8000-000000007009")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000007010")
FACT_ID = UUID("00000000-0000-4000-8000-000000007011")
RULE_ID = UUID("00000000-0000-4000-8000-000000007012")
CALCULATION_ID = UUID("00000000-0000-4000-8000-000000007013")


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    def fetchone(self) -> dict[str, Any] | None:
        return self.rows[0] if self.rows else None

    def fetchall(self) -> list[dict[str, Any]]:
        return self.rows


class _Connection:
    def __init__(self, rows: dict[str, list[dict[str, Any]]]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, object]] = []

    def execute(self, query: str, parameters: object = None) -> _Cursor:
        self.calls.append((query, parameters))
        for marker, values in self.rows.items():
            if marker in query:
                return _Cursor(values)
        return _Cursor([])


def _event() -> MedicalEvent:
    return MedicalEvent(
        id=EVENT_ID,
        household_space_id=HOUSEHOLD_ID,
        family_member_id=MEMBER_ID,
        mode="post_treatment",
        situation="violet delta",
        event_date=date(2026, 6, 1),
        visit_date=date(2026, 6, 1),
    )


def _rows(*, with_publication: bool = True) -> dict[str, list[dict[str, Any]]]:
    citation = {
        "citation_key": "synthetic-citation-001",
        "terms_section_id": SECTION_ID,
        "source_clause_id": CLAUSE_ID,
        "fact_id": FACT_ID,
        "evidence_purpose": "ELIGIBILITY",
        "page_start": 2,
        "page_end": 2,
        "source_text_sha256": "a" * 64,
        "lineage_valid": True,
    }
    return {
        "private-knowledge:current-runs": [
            {
                "knowledge_import_run_id": KNOWLEDGE_RUN_ID,
                "rule_import_run_id": RULE_RUN_ID if with_publication else None,
                "rule_projection_digest_sha256": "b" * 64 if with_publication else None,
            }
        ],
        "private-knowledge:coverage-context": [
            {
                "knowledge_contract_id": CONTRACT_ID,
                "knowledge_coverage_id": COVERAGE_ID,
                "contract_label": "Sample Policy",
                "coverage_label": "Sample Benefit",
                "benefit_type": "FIXED",
                "insured_amount": Decimal("100"),
                "currency": "KRW",
                "contract_start": date(2025, 1, 1),
                "contract_end": None,
                "disposition": "PUBLISHED" if with_publication else None,
                "subject_binding_decision": "MATCH",
                "enrollment_decision": "MATCH",
                "component_classification": "BENEFIT_COVERAGE",
                "mapping_count": 1,
                "mapping_applicability": "APPLICABLE",
                "mapping_enrollment_decision": "MATCH",
                "document_identity_decision": "MATCH",
                "edition_applicability_decision": "MATCH",
                "section_mapping_decision": "MATCH",
                "overall_mapping_decision": "MATCH",
                "mapped_terms_section_id": SECTION_ID,
                "current_confirmation_decision": "MATCH",
                "current_confirmed_status": "active",
                "confirmation_digest_sha256": "c" * 64,
                "operational_binding_decision": "MATCH",
                "rider_id": RIDER_ID,
            }
        ],
        "private-knowledge:claim-history": [{"rider_id": RIDER_ID, "counted_occurrence": 1}],
        "private-knowledge:receipt-facts": [
            {
                "amount": Decimal("50"),
                "currency": "KRW",
                "coverage_category": "covered",
                "confirmation_level": "user",
            }
        ],
        "private-knowledge:status-intervals": [
            {
                "knowledge_contract_id": CONTRACT_ID,
                "effective_from": date(2026, 1, 1),
                "effective_through": date(2026, 12, 31),
                "decision": "MATCH",
                "confirmed_status": "active",
                "authority": "USER_CONFIRMED_EVENT_DATE",
                "interval_digest_sha256": "d" * 64,
            }
        ],
        "private-knowledge:normalizers": [
            {
                "normalizer_key": "synthetic-normalizer-001",
                "field_path": "MedicalEvent.classification",
                "normalized_tokens_json": ["violet", "delta"],
                "normalized_value_json": "sample_category",
                "priority": 100,
            }
        ],
        "private-knowledge:rules": [
            {
                "publication_id": RULE_ID,
                "knowledge_coverage_id": COVERAGE_ID,
                "rule_key": "synthetic-rule-001",
                "rule_kind": "eligibility",
                "required": True,
                "result_reason_code": "SYNTHETIC_MATCH",
                "rule_json": {
                    "schema_version": "coverage-rule-v1",
                    "rule_kind": "eligibility",
                    "required": True,
                    "input_field_paths": ["MedicalEvent.classification"],
                    "expression": {
                        "op": "equals",
                        "field": "MedicalEvent.classification",
                        "value": "sample_category",
                    },
                    "result_reason_code": "SYNTHETIC_MATCH",
                    "evidence_ids": ["synthetic-citation-001"],
                },
                **citation,
            }
        ],
        "private-knowledge:calculations": [
            {
                "publication_id": CALCULATION_ID,
                "knowledge_coverage_id": COVERAGE_ID,
                "calculation_key": "synthetic-calculation-001",
                "calculation_kind": "FIXED",
                "result_reason_code": "SYNTHETIC_AMOUNT",
                "calculation_json": {
                    "schema_version": "coverage-rule-v1",
                    "rule_kind": "fixed_amount",
                    "required": False,
                    "input_field_paths": ["Rider.insured_amount"],
                    "calculation": {
                        "op": "min",
                        "args": [
                            {"field": "Rider.insured_amount"},
                            {"value": 1},
                        ],
                    },
                    "result_reason_code": "SYNTHETIC_AMOUNT",
                    "evidence_ids": ["synthetic-citation-001"],
                },
                **{**citation, "evidence_purpose": "AMOUNT"},
            }
        ],
    }


def test_read_context_is_member_scoped_and_directly_evaluable() -> None:
    connection = _Connection(_rows())
    repository = PostgresKnowledgeDecisionRepository()

    snapshot = repository.read_context(
        connection,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
    )

    assert snapshot.reason_codes == ()
    assert snapshot.catalog_coverage.contract_count == 1
    assert snapshot.catalog_coverage.benefit_coverage_count == 1
    assert snapshot.catalog_coverage.published_coverage_count == 1
    assert snapshot.context is not None
    assert snapshot.context.family_member_id == MEMBER_ID
    assert snapshot.context.coverages[0].claim_history_counted_occurrence is not None
    assert snapshot.context.coverages[0].claim_history_counted_occurrence.value == 1
    assert snapshot.context.supporting_facts["Receipt.covered_amount"].value == Decimal("50")

    result = DeterministicKnowledgeDecisionEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
        snapshot.context,
        run_id=UUID("00000000-0000-4000-8000-000000007099"),
    )
    assert result.candidates[0].result == "MATCH"
    assert result.calculations[0].conditional_amount == Decimal("1")
    assert result.evaluations[0].citations[0].terms_section_id == SECTION_ID

    scoped_queries = "\n".join(query for query, _ in connection.calls)
    assert "subject.family_member_id" in scoped_queries
    assert "subject.binding_decision = 'MATCH'" in scoped_queries
    assert "household_space_id" in scoped_queries
    assert all(
        HOUSEHOLD_ID in parameters.values() and MEMBER_ID in parameters.values()
        for query, parameters in connection.calls
        if "coverage-context" in query and isinstance(parameters, dict)
    )


def test_catalog_without_current_publication_is_unavailable_not_empty_success() -> None:
    connection = _Connection(_rows(with_publication=False))

    snapshot = PostgresKnowledgeDecisionRepository().read_context(
        connection,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
    )

    assert snapshot.context is None
    assert snapshot.catalog_coverage.benefit_coverage_count == 1
    assert snapshot.catalog_coverage.published_coverage_count == 0
    assert snapshot.reason_codes == ("KNOWLEDGE_PUBLICATION_UNAVAILABLE",)


def test_persist_result_writes_each_private_stream_with_trace_digest() -> None:
    source = _Connection(_rows())
    repository = PostgresKnowledgeDecisionRepository()
    snapshot = repository.read_context(
        source,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
    )
    assert snapshot.context is not None
    result = DeterministicKnowledgeDecisionEngine().evaluate(
        HouseholdScope(HOUSEHOLD_ID),
        _event(),
        snapshot.context,
        run_id=UUID("00000000-0000-4000-8000-000000007099"),
    )
    target = _Connection({})

    repository.persist_result(
        target,  # type: ignore[arg-type]
        HouseholdScope(HOUSEHOLD_ID),
        result,
    )

    statements = "\n".join(query for query, _ in target.calls)
    assert "INSERT INTO private_knowledge_rule_evaluations" in statements
    assert "INSERT INTO private_knowledge_claim_candidates" in statements
    assert "INSERT INTO private_knowledge_benefit_calculations" in statements
    assert "INSERT INTO private_knowledge_calculation_steps" in statements
    calculation_call = next(
        parameters
        for query, parameters in target.calls
        if "INSERT INTO private_knowledge_benefit_calculations" in query
    )
    assert isinstance(calculation_call, tuple)
    assert isinstance(calculation_call[-1], str) and len(calculation_call[-1]) == 64
