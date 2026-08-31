"""Lossless normalized writes for one validated private-knowledge package."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg.types.json import Jsonb

from familycare_api.private_knowledge.models import CoverageRecord
from familycare_api.private_knowledge.package import (
    PrivateKnowledgePackage,
    contract_certificate_decision,
)
from familycare_api.private_knowledge.reconciliation import (
    KnowledgeEntityCounts,
    package_source_aliases,
)

StageCallback = Callable[[str], None]
SqlParameters = dict[str, object]


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _digest_text(value: str) -> str:
    return _sha256(value.encode("utf-8"))


def _source_key(kind: str, *parts: object) -> str:
    return f"{kind}:{_sha256(_canonical_json(list(parts)))}"


def _synthesized_source_record(value: dict[str, object]) -> tuple[Jsonb, str]:
    return Jsonb(value), _sha256(_canonical_json(value))


def _date(value: str | None) -> date | None:
    return date.fromisoformat(value) if value is not None else None


def _amount(value: int | float | None) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _certificate_amount_evidence(coverage: CoverageRecord) -> list[dict[str, object]]:
    review = coverage.certificate_review
    if review.amount_decision == "MATCH" and review.amount_evidence_locations:
        pages_by_document: dict[str, set[int]] = {}
        for location in review.amount_evidence_locations:
            pages_by_document.setdefault(location.document_alias, set()).add(location.physical_page)
        return [
            {
                "document_alias": document_alias,
                "evidence_pages": sorted(pages),
            }
            for document_alias, pages in sorted(pages_by_document.items())
        ]
    return [reference.model_dump(mode="json") for reference in coverage.source_refs]


def _review_state(value: str) -> str:
    if value == "SOL_DIRECT_GROUNDED":
        return "DIRECT_REVIEWED"
    if value == "NEEDS_REFERENCE_REVIEW":
        return "NEEDS_REVIEW"
    return "UNKNOWN"


_FACT_TYPES = {
    "payment_reason": "PAYMENT_TRIGGER",
    "definition": "DEFINITION",
    "exclusion": "EXCLUSION",
    "waiting_period": "WAITING_PERIOD",
    "reduction_period": "REDUCTION",
    "frequency_limit": "FREQUENCY",
    "amount_basis": "AMOUNT",
    "renewal": "RENEWAL",
    "claim_documents": "REQUIRED_DOCUMENT",
    "termination": "TERMINATION",
    "cross_reference": "CROSS_REFERENCE",
}


def _overall_decision(applicability: str, decisions: Sequence[str]) -> str:
    if applicability != "APPLICABLE":
        return "UNKNOWN"
    if "NO_MATCH" in decisions:
        return "NO_MATCH"
    if decisions and all(value == "MATCH" for value in decisions):
        return "MATCH"
    return "UNKNOWN"


def _execute_many(
    connection: psycopg.Connection[dict[str, Any]],
    statement: str,
    rows: list[SqlParameters],
) -> None:
    if not rows:
        return
    with connection.cursor() as cursor:
        cursor.executemany(statement, rows)


def insert_private_knowledge_snapshot(
    connection: psycopg.Connection[dict[str, Any]],
    *,
    run_id: UUID,
    household_space_id: UUID,
    package: PrivateKnowledgePackage,
    after_group: StageCallback,
) -> KnowledgeEntityCounts:
    """Insert all child entities for one run; the caller owns the transaction."""

    subject_ids = {alias: uuid4() for alias in package.subject_aliases}
    contract_ids = {record.value.canonical_policy_id: uuid4() for record in package.contracts}
    coverage_ids = {record.value.canonical_rider_id: uuid4() for record in package.coverages}
    assignment_ids = {record.value.canonical_policy_id: uuid4() for record in package.pairings}
    section_ids = {
        (record.value.terms_alias, record.value.section_id): uuid4() for record in package.sections
    }
    clause_ids = {
        (
            record.value.terms_alias,
            record.value.section_id,
            record.value.clause_index,
        ): uuid4()
        for record in package.clauses
    }
    review_ids = {
        (record.value.terms_alias, record.value.section_id): uuid4()
        for record in package.semantic_reviews
    }
    fact_ids = {
        (review.value.terms_alias, review.value.section_id, fact.fact_id): uuid4()
        for review in package.semantic_reviews
        for fact in review.value.facts
    }

    subject_rows: list[SqlParameters] = []
    for alias, subject_id in subject_ids.items():
        source_record, source_digest = _synthesized_source_record({"family_alias": alias})
        subject_rows.append(
            {
                "id": subject_id,
                "run": run_id,
                "household": household_space_id,
                "key": _source_key("subject", alias),
                "alias": alias,
                "alias_digest": _digest_text(alias),
                "source": source_record,
                "source_digest": source_digest,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_subjects (
          id, import_run_id, household_space_id, source_subject_key, family_alias,
          family_alias_digest_sha256, binding_decision, binding_conflict,
          binding_reason_code, source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(household)s, %(key)s, %(alias)s, %(alias_digest)s,
          'UNKNOWN', false, 'NO_EXACT_BINDING', %(source)s, %(source_digest)s
        )
        """,
        subject_rows,
    )
    after_group("subjects")

    contract_rows: list[SqlParameters] = []
    for contract_record in package.contracts:
        contract = contract_record.value
        contract_rows.append(
            {
                "id": contract_ids[contract.canonical_policy_id],
                "run": run_id,
                "household": household_space_id,
                "subject": subject_ids[contract.family_alias],
                "key": _source_key("contract", contract.canonical_policy_id),
                "insurer": contract.insurer,
                "product": contract.product_name,
                "start": _date(contract.contract_start),
                "end": _date(contract.contract_end),
                "certificate_decision": contract_certificate_decision(contract),
                "status_candidates": Jsonb(
                    [
                        {
                            "status": contract.candidate_current_status,
                            "authority": "candidate_only",
                        }
                    ]
                ),
                "evidence": Jsonb(
                    [member.model_dump(mode="json") for member in contract.source_members]
                ),
                "issues": Jsonb(contract.field_conflicts),
                "source": Jsonb(contract_record.source_record),
                "source_digest": contract_record.source_record_digest_sha256,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_contracts (
          id, import_run_id, household_space_id, subject_id, source_contract_key,
          insurer_display, product_display, contract_start, contract_end,
          certificate_decision, current_status, status_candidates_json,
          certificate_evidence_json, review_issues_json,
          operational_binding_decision, operational_binding_reason_code,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(household)s, %(subject)s, %(key)s, %(insurer)s, %(product)s,
          %(start)s, %(end)s, %(certificate_decision)s, 'unknown', %(status_candidates)s,
          %(evidence)s, %(issues)s, 'UNKNOWN', 'NO_EXACT_BINDING',
          %(source)s, %(source_digest)s
        )
        """,
        contract_rows,
    )
    after_group("contracts")

    mappings_by_coverage = {
        record.value.canonical_rider_id: record.value for record in package.mappings
    }
    coverage_rows: list[SqlParameters] = []
    for coverage_record in package.coverages:
        coverage = coverage_record.value
        mapping = mappings_by_coverage[coverage.canonical_rider_id]
        non_benefit = mapping.component_class == "NON_BENEFIT_CONTRACT_COMPONENT"
        renewal = (
            "NOT_APPLICABLE"
            if non_benefit
            else "YES"
            if coverage.renewable is True
            else "NO"
            if coverage.renewable is False
            else "UNKNOWN"
        )
        benefit_type = "NOT_APPLICABLE" if non_benefit else coverage.benefit_type.upper()
        coverage_rows.append(
            {
                "id": coverage_ids[coverage.canonical_rider_id],
                "run": run_id,
                "household": household_space_id,
                "contract": contract_ids[coverage.canonical_policy_id],
                "key": _source_key("coverage", coverage.canonical_rider_id),
                "name": coverage.name,
                "role": coverage.coverage_role.upper(),
                "classification": mapping.component_class,
                "enrollment": mapping.enrollment_decision,
                "benefit_type": benefit_type,
                "amount": _amount(coverage.sum_assured_krw),
                "currency": coverage.currency,
                "start": _date(coverage.coverage_start),
                "end": _date(coverage.coverage_end),
                "renewal": renewal,
                "evidence": Jsonb(_certificate_amount_evidence(coverage)),
                "issues": Jsonb(coverage.warnings),
                "source": Jsonb(coverage_record.source_record),
                "source_digest": coverage_record.source_record_digest_sha256,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_coverages (
          id, import_run_id, household_space_id,
          knowledge_contract_id, source_coverage_key,
          display_name, component_role, component_classification,
          enrollment_decision, benefit_type, insured_amount, currency,
          coverage_start, coverage_end, renewal_state, current_status,
          certificate_evidence_json, review_issues_json,
          operational_binding_decision, operational_binding_reason_code,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(household)s, %(contract)s, %(key)s, %(name)s, %(role)s,
          %(classification)s, %(enrollment)s, %(benefit_type)s, %(amount)s,
          %(currency)s, %(start)s, %(end)s, %(renewal)s, 'unknown',
          %(evidence)s, %(issues)s, 'UNKNOWN', 'NO_EXACT_BINDING',
          %(source)s, %(source_digest)s
        )
        """,
        coverage_rows,
    )
    after_group("coverages")

    assignment_rows: list[SqlParameters] = []
    assignment_source_rows: list[SqlParameters] = []
    for pairing_record in package.pairings:
        pairing = pairing_record.value
        assignment_id = assignment_ids[pairing.canonical_policy_id]
        assignment_rows.append(
            {
                "id": assignment_id,
                "run": run_id,
                "household": household_space_id,
                "contract": contract_ids[pairing.canonical_policy_id],
                "key": _source_key("assignment", pairing.canonical_policy_id),
                "identity": pairing.document_identity_decision,
                "edition": pairing.edition_applicability_decision,
                "overall": pairing.review_decision,
                "reasons": Jsonb(pairing.reason_codes),
                "source": Jsonb(pairing_record.source_record),
                "source_digest": pairing_record.source_record_digest_sha256,
            }
        )
        evidence_by_alias = {
            item.terms_alias: item.model_dump(mode="json") for item in pairing.selected_evidence
        }
        for ordinal, alias in enumerate(pairing.selected_terms_aliases, start=1):
            selected_evidence = evidence_by_alias.get(alias, {})
            source_value: dict[str, object] = {
                "source_alias": alias,
                "selection_ordinal": ordinal,
                "selected_evidence": selected_evidence,
            }
            source_record, source_digest = _synthesized_source_record(source_value)
            assignment_source_rows.append(
                {
                    "id": uuid4(),
                    "run": run_id,
                    "assignment": assignment_id,
                    "alias": alias,
                    "alias_digest": _digest_text(alias),
                    "ordinal": ordinal,
                    "evidence": Jsonb(selected_evidence),
                    "source": source_record,
                    "source_digest": source_digest,
                }
            )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_terms_assignments (
          id, import_run_id, household_space_id,
          knowledge_contract_id, source_assignment_key,
          document_identity_decision, edition_applicability_decision,
          overall_decision, reason_codes_json, operational_binding_decision,
          operational_binding_reason_code, source_record_json,
          source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(household)s, %(contract)s, %(key)s,
          %(identity)s, %(edition)s,
          %(overall)s, %(reasons)s, 'UNKNOWN', 'NO_EXACT_BINDING',
          %(source)s, %(source_digest)s
        )
        """,
        assignment_rows,
    )
    after_group("terms_assignments")
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_terms_assignment_sources (
          id, import_run_id, terms_assignment_id, source_alias,
          source_alias_digest_sha256, selection_ordinal, selected_evidence_json,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(assignment)s, %(alias)s, %(alias_digest)s,
          %(ordinal)s, %(evidence)s, %(source)s, %(source_digest)s
        )
        """,
        assignment_source_rows,
    )
    after_group("terms_assignment_sources")

    section_rows: list[SqlParameters] = []
    for section_record in package.sections:
        section = section_record.value
        section_rows.append(
            {
                "id": section_ids[(section.terms_alias, section.section_id)],
                "run": run_id,
                "key": _source_key("section", section.terms_alias, section.section_id),
                "alias": section.terms_alias,
                "alias_digest": _digest_text(section.terms_alias),
                "heading": section.title,
                "page": section.physical_page,
                "source": Jsonb(section_record.source_record),
                "source_digest": section_record.source_record_digest_sha256,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_terms_sections (
          id, import_run_id, source_section_key, terms_source_alias,
          terms_source_alias_digest_sha256, section_kind, heading,
          page_start, page_end, review_state, source_record_json,
          source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(key)s, %(alias)s, %(alias_digest)s,
          'BENEFIT_PROVISION', %(heading)s, %(page)s, %(page)s,
          'DIRECT_REVIEWED', %(source)s, %(source_digest)s
        )
        """,
        section_rows,
    )
    after_group("terms_sections")

    clause_rows: list[SqlParameters] = []
    for clause_record in package.clauses:
        clause = clause_record.value
        clause_identity = (
            clause.terms_alias,
            clause.section_id,
            clause.clause_index,
        )
        clause_rows.append(
            {
                "id": clause_ids[clause_identity],
                "run": run_id,
                "section": section_ids[(clause.terms_alias, clause.section_id)],
                "key": _source_key("clause", *clause_identity),
                "label": clause.label,
                "title": clause.title,
                "start": clause.physical_page_start,
                "end": clause.physical_page_end,
                "text_digest": clause.source_text_sha256,
                "source": Jsonb(clause_record.source_record),
                "source_digest": clause_record.source_record_digest_sha256,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_source_clauses (
          id, import_run_id, terms_section_id, source_clause_key,
          clause_label, title, page_start, page_end, source_text_sha256,
          review_state, source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(section)s, %(key)s, %(label)s, %(title)s,
          %(start)s, %(end)s, %(text_digest)s, 'DIRECT_REVIEWED',
          %(source)s, %(source_digest)s
        )
        """,
        clause_rows,
    )
    after_group("source_clauses")

    review_rows: list[SqlParameters] = []
    fact_rows: list[SqlParameters] = []
    citation_rows: list[SqlParameters] = []
    for semantic_record in package.semantic_reviews:
        semantic_review = semantic_record.value
        review_identity = (
            semantic_review.terms_alias,
            semantic_review.section_id,
        )
        review_id = review_ids[review_identity]
        section_id = section_ids[review_identity]
        review_rows.append(
            {
                "id": review_id,
                "run": run_id,
                "section": section_id,
                "key": _source_key("review", *review_identity),
                "summary": semantic_review.section_summary_ko,
                "status": semantic_review.analysis_status,
                "confidence": semantic_review.confidence,
                "found": Jsonb(semantic_review.found_categories),
                "missing": Jsonb(semantic_review.missing_categories),
                "warnings": Jsonb(semantic_review.warnings),
                "source_count": semantic_review.source_clause_count,
                "classified_count": semantic_review.classified_clause_count,
                "unclassified_count": semantic_review.unclassified_clause_count,
                "legacy": semantic_review.legacy_review_only,
                "source": Jsonb(semantic_record.source_record),
                "source_digest": semantic_record.source_record_digest_sha256,
            }
        )
        for fact in semantic_review.facts:
            fact_identity = (
                semantic_review.terms_alias,
                semantic_review.section_id,
                fact.fact_id,
            )
            fact_id = fact_ids[fact_identity]
            fact_source = fact.model_dump(mode="json")
            fact_rows.append(
                {
                    "id": fact_id,
                    "run": run_id,
                    "section": section_id,
                    "review": review_id,
                    "key": _source_key("fact", *fact_identity),
                    "type": _FACT_TYPES[fact.category],
                    "statement": fact.statement_ko,
                    "conditions": Jsonb(
                        {
                            "details_ko": fact.condition_details_ko,
                            "decision_impact": fact.decision_impact,
                            "confidence": fact.confidence,
                            "unresolved_reference": fact.unresolved_reference,
                        }
                    ),
                    "numeric_terms": Jsonb(fact.numeric_terms_ko),
                    "review_state": _review_state(fact.review_state),
                    "source": Jsonb(fact_source),
                    "source_digest": _sha256(_canonical_json(fact_source)),
                }
            )
            for ordinal, citation in enumerate(fact.citations, start=1):
                clause_identity = (
                    semantic_review.terms_alias,
                    semantic_review.section_id,
                    citation.clause_index,
                )
                citation_source = citation.model_dump(mode="json")
                citation_rows.append(
                    {
                        "id": uuid4(),
                        "run": run_id,
                        "fact": fact_id,
                        "clause": clause_ids[clause_identity],
                        "ordinal": ordinal,
                        "start": citation.physical_page_start,
                        "end": citation.physical_page_end,
                        "text_digest": citation.source_text_sha256,
                        "locator": Jsonb({"clause_index": citation.clause_index}),
                        "source": Jsonb(citation_source),
                        "source_digest": _sha256(_canonical_json(citation_source)),
                    }
                )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_semantic_reviews (
          id, import_run_id, terms_section_id, source_review_key,
          section_summary, analysis_status, confidence, review_state,
          found_categories_json, missing_categories_json, warnings_json,
          source_clause_count, classified_clause_count, unclassified_clause_count,
          legacy_review_only, source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(section)s, %(key)s, %(summary)s, %(status)s,
          %(confidence)s, 'DIRECT_REVIEWED', %(found)s, %(missing)s, %(warnings)s,
          %(source_count)s, %(classified_count)s, %(unclassified_count)s,
          %(legacy)s, %(source)s, %(source_digest)s
        )
        """,
        review_rows,
    )
    after_group("semantic_reviews")
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_facts (
          id, import_run_id, terms_section_id, semantic_review_id,
          source_fact_key, fact_type, statement, conditions_json,
          numeric_terms_json, review_state, executable, source_record_json,
          source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(section)s, %(review)s, %(key)s, %(type)s,
          %(statement)s, %(conditions)s, %(numeric_terms)s, %(review_state)s,
          false, %(source)s, %(source_digest)s
        )
        """,
        fact_rows,
    )
    after_group("facts")
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_fact_citations (
          id, import_run_id, fact_id, source_clause_id, citation_ordinal,
          page_start, page_end, source_text_sha256, locator_json,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(fact)s, %(clause)s, %(ordinal)s, %(start)s,
          %(end)s, %(text_digest)s, %(locator)s, %(source)s, %(source_digest)s
        )
        """,
        citation_rows,
    )
    after_group("fact_citations")

    mapping_rows: list[SqlParameters] = []
    for mapping_record in package.mappings:
        mapping_value = mapping_record.value
        applicability = (
            "APPLICABLE"
            if mapping_value.component_class == "BENEFIT_COVERAGE"
            else "NOT_APPLICABLE"
            if mapping_value.component_class == "NON_BENEFIT_CONTRACT_COMPONENT"
            else "UNKNOWN"
        )
        section_decision = (
            mapping_value.mapping_decision
            if mapping_value.mapping_decision != "NOT_APPLICABLE"
            else "UNKNOWN"
        )
        selected_section_uuid: UUID | None = None
        if (
            mapping_value.selected_terms_alias is not None
            and mapping_value.selected_section_id is not None
        ):
            selected_section_uuid = section_ids[
                (
                    mapping_value.selected_terms_alias,
                    mapping_value.selected_section_id,
                )
            ]
        overall = _overall_decision(
            applicability,
            (
                mapping_value.enrollment_decision,
                mapping_value.pairing_document_identity_decision,
                mapping_value.pairing_edition_applicability_decision,
                section_decision,
            ),
        )
        mapping_rows.append(
            {
                "id": uuid4(),
                "run": run_id,
                "coverage": coverage_ids[mapping_value.canonical_rider_id],
                "section": selected_section_uuid,
                "key": _source_key("mapping", mapping_value.canonical_rider_id),
                "applicability": applicability,
                "alias": mapping_value.selected_terms_alias,
                "alias_digest": (
                    _digest_text(mapping_value.selected_terms_alias)
                    if mapping_value.selected_terms_alias is not None
                    else None
                ),
                "enrollment": mapping_value.enrollment_decision,
                "identity": mapping_value.pairing_document_identity_decision,
                "edition": mapping_value.pairing_edition_applicability_decision,
                "section_decision": section_decision,
                "overall": overall,
                "reasons": Jsonb(mapping_value.reason_codes),
                "source": Jsonb(mapping_record.source_record),
                "source_digest": mapping_record.source_record_digest_sha256,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_coverage_terms_mappings (
          id, import_run_id, coverage_id, terms_section_id, source_mapping_key,
          mapping_applicability, selected_terms_source_alias,
          selected_terms_source_alias_digest_sha256, enrollment_decision,
          document_identity_decision, edition_applicability_decision,
          section_mapping_decision, overall_decision, reason_codes_json,
          executable, source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(coverage)s, %(section)s, %(key)s,
          %(applicability)s, %(alias)s, %(alias_digest)s, %(enrollment)s,
          %(identity)s, %(edition)s, %(section_decision)s, %(overall)s,
          %(reasons)s, false, %(source)s, %(source_digest)s
        )
        """,
        mapping_rows,
    )
    after_group("coverage_terms_mappings")

    binding_rows: list[SqlParameters] = []
    for alias in package_source_aliases(package):
        source_record, source_digest = _synthesized_source_record(
            {"source_alias": alias, "binding_basis": "PACKAGE_REFERENCE_ONLY"}
        )
        binding_rows.append(
            {
                "id": uuid4(),
                "run": run_id,
                "household": household_space_id,
                "alias": alias,
                "alias_digest": _digest_text(alias),
                "source": source_record,
                "source_digest": source_digest,
            }
        )
    _execute_many(
        connection,
        """
        INSERT INTO private_knowledge_document_bindings (
          id, import_run_id, household_space_id,
          source_alias, source_alias_digest_sha256,
          binding_decision, binding_conflict, binding_reason_code,
          content_digest_decision, page_count_decision, document_kind_decision,
          source_record_json, source_record_digest_sha256
        ) VALUES (
          %(id)s, %(run)s, %(household)s, %(alias)s, %(alias_digest)s,
          'UNKNOWN', false,
          'NO_EXACT_BINDING', 'UNKNOWN', 'UNKNOWN', 'UNKNOWN',
          %(source)s, %(source_digest)s
        )
        """,
        binding_rows,
    )
    after_group("document_bindings")

    return KnowledgeEntityCounts(
        subjects=len(subject_rows),
        contracts=len(contract_rows),
        coverages=len(coverage_rows),
        terms_assignments=len(assignment_rows),
        terms_assignment_sources=len(assignment_source_rows),
        terms_sections=len(section_rows),
        source_clauses=len(clause_rows),
        semantic_reviews=len(review_rows),
        facts=len(fact_rows),
        fact_citations=len(citation_rows),
        coverage_terms_mappings=len(mapping_rows),
        document_bindings=len(binding_rows),
    )
