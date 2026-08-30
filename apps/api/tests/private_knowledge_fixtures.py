"""Wholly synthetic private-knowledge package fixtures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

DATA_FILES = (
    "contracts.jsonl",
    "coverage-components.jsonl",
    "policy-terms-pairings.jsonl",
    "coverage-terms-mappings.jsonl",
    "terms-sections.jsonl",
    "clause-evidence-index.jsonl",
    "terms-semantic-review.jsonl",
    "reconciliation.json",
)


def synthetic_reconciliation() -> dict[str, int]:
    return {
        "policy_count": 1,
        "coverage_component_count": 1,
        "benefit_coverage_count": 1,
        "non_benefit_contract_component_count": 0,
        "certificate_enrollment_match_count": 1,
        "certificate_enrollment_unknown_count": 0,
        "policy_terms_identity_match_count": 1,
        "policy_terms_edition_match_count": 1,
        "coverage_section_match_count": 1,
        "coverage_section_unknown_count": 0,
        "coverage_section_not_applicable_count": 0,
        "terms_section_review_count": 1,
        "source_clause_review_count": 1,
        "semantic_fact_count": 1,
        "previous_fact_recheck_count": 0,
        "previous_fact_needs_review_count": 0,
        "restored_distinct_object_row_count": 0,
        "true_duplicate_row_count": 0,
        "current_status_unknown_count": 1,
        "database_write_count": 0,
        "executable_rule_count": 0,
    }


def synthetic_records() -> dict[str, list[dict[str, Any]]]:
    citation = {
        "clause_index": 1,
        "physical_page_end": 2,
        "physical_page_start": 2,
        "source_text_sha256": "a" * 64,
    }
    evidence_location = {
        "document_alias": "synthetic-certificate-source",
        "line": 1,
        "physical_page": 1,
    }
    terms_candidate = {
        "candidate_score": 1.0,
        "clause_count": 1,
        "clause_hashes": ["a" * 64],
        "clause_pages": [2],
        "direct_body_name_hit": True,
        "physical_page": 2,
        "physical_page_lineage": "synthetic-physical-page",
        "section_id": "synthetic-section-001",
        "terms_alias": "synthetic-terms-source",
        "title_similarity": 1.0,
    }
    terms_pairing = {
        "canonical_policy_id": "synthetic-policy-001",
        "document_identity_decision": "MATCH",
        "edition_applicability_decision": "MATCH",
        "executable_rule": False,
        "previous_decision": "linked",
        "reason_codes": ["SYNTHETIC_EXACT_MATCH"],
        "review_decision": "MATCH",
        "selected_terms_aliases": ["synthetic-terms-source"],
    }
    terms_mapping = {
        "canonical_policy_id": "synthetic-policy-001",
        "canonical_rider_id": "synthetic-coverage-001",
        "clause_count": 1,
        "component_class": "BENEFIT_COVERAGE",
        "current_coverage_applicability_decision": "UNKNOWN",
        "enrollment_decision": "MATCH",
        "executable_rule": False,
        "mapping_decision": "MATCH",
        "mapping_inherited_from_rider_id": None,
        "pairing_aliases": ["synthetic-terms-source"],
        "pairing_document_identity_decision": "MATCH",
        "pairing_edition_applicability_decision": "MATCH",
        "pairing_review_decision": "MATCH",
        "physical_page": 2,
        "previous_mapping_decision": "needs_review",
        "previous_section_id": None,
        "previous_terms_alias": None,
        "reason_codes": ["SYNTHETIC_SECTION_MATCH"],
        "selected_section_id": "synthetic-section-001",
        "selected_terms_alias": "synthetic-terms-source",
        "top_candidates": [terms_candidate],
    }
    return {
        "contracts.jsonl": [
            {
                "canonical_policy_id": "synthetic-policy-001",
                "family_alias": "Family Member A",
                "insurer": "Sample Insurer",
                "product_name": "Sample Policy",
                "contract_start": "2024-01-01",
                "contract_end": None,
                "current_status": "UNKNOWN",
                "monthly_premium_krw": 1000,
                "source_members": [
                    {
                        "decision": "MATCH",
                        "document_alias": "synthetic-certificate-source",
                        "evidence_pages": [1],
                        "local_policy_id": "synthetic-local-policy-001",
                    }
                ],
                "group_review": {
                    "confidence": "high",
                    "merge_decision": "keep_separate",
                    "reason_codes": ["SYNTHETIC_SINGLE_SOURCE"],
                },
                "duplicate_rows_removed": 0,
                "field_conflicts": [],
                "review_state": "NEEDS_REVIEW",
                "previous_candidate_review_state": "AI_VERIFIED",
                "direct_review_state": "SOL_DIRECT_GROUNDED",
                "candidate_current_status": "unknown",
                "field_reviews": [
                    {
                        "candidate_value": "2024-01-01",
                        "decision": "MATCH",
                        "evidence_locations": [evidence_location],
                        "field": "contract_start",
                    }
                ],
                "row_reconciliation": {
                    "balanced": True,
                    "benefit_coverages": 1,
                    "canonical_components": 1,
                    "certificate_rows_detected": 1,
                    "duplicate_rows_removed": 0,
                    "non_benefit_contract_components": 0,
                    "unresolved_enrollment_rows": 0,
                },
                "terms_pairing": terms_pairing,
            }
        ],
        "coverage-components.jsonl": [
            {
                "canonical_policy_id": "synthetic-policy-001",
                "family_alias": "Family Member A",
                "name": "Sample Hospital Benefit",
                "coverage_role": "rider",
                "benefit_type": "fixed",
                "sum_assured_krw": 10000,
                "currency": "KRW",
                "coverage_start": "2024-01-01",
                "coverage_end": None,
                "renewable": False,
                "current_status": "UNKNOWN",
                "warnings": [],
                "source_refs": [
                    {
                        "document_alias": "synthetic-certificate-source",
                        "evidence_pages": [1],
                        "local_policy_id": "synthetic-local-policy-001",
                        "local_rider_id": "synthetic-local-rider-001",
                    }
                ],
                "canonical_rider_id": "synthetic-coverage-001",
                "candidate_current_status": "unknown",
                "certificate_review": {
                    "amount_decision": "MATCH",
                    "amount_evidence_locations": [evidence_location],
                    "amount_support": "synthetic-supported",
                    "candidate_benefit_type": "fixed",
                    "candidate_current_status": "unknown",
                    "candidate_sum_assured_krw": 10000,
                    "canonical_policy_id": "synthetic-policy-001",
                    "canonical_rider_id": "synthetic-coverage-001",
                    "classification_findings": [],
                    "component_class": "BENEFIT_COVERAGE",
                    "enrollment_decision": "MATCH",
                    "evidence_inherited_from_rider_id": None,
                    "evidence_locations": [evidence_location],
                    "executable_rule": False,
                    "insured_object_ref": "synthetic-insured-object-001",
                    "manual_override_reason": None,
                    "name": "Sample Hospital Benefit",
                    "name_support": "synthetic-supported",
                    "object_identity_review_state": "UNKNOWN",
                    "reviewed_benefit_type": "fixed",
                    "reviewed_current_status": "UNKNOWN",
                },
                "review_state": "NEEDS_REVIEW",
                "direct_review_state": "SOL_DIRECT_GROUNDED",
                "terms_mapping": terms_mapping,
                "current_coverage_applicability_decision": "UNKNOWN",
                "executable_rule": False,
                "insured_object_ref": "synthetic-insured-object-001",
                "object_identity_review_state": "UNKNOWN",
            }
        ],
        "policy-terms-pairings.jsonl": [
            {
                "canonical_policy_id": "synthetic-policy-001",
                "previous_decision": "linked",
                "selected_terms_aliases": ["synthetic-terms-source"],
                "document_identity_decision": "MATCH",
                "edition_applicability_decision": "MATCH",
                "review_decision": "MATCH",
                "reason_codes": ["SYNTHETIC_EXACT_MATCH"],
                "selected_evidence": [
                    {
                        "candidate_score": 1.0,
                        "clause_count": 1,
                        "direct_insurer_identity": True,
                        "direct_product_identity": True,
                        "edition_decision": "MATCH",
                        "edition_reason_code": "SYNTHETIC_DATE_MATCH",
                        "physical_page_lineage": "synthetic-physical-page",
                        "profile_insurer_grounded": True,
                        "profile_product_grounded": True,
                        "rider_count": 1,
                        "rider_exact_count": 1,
                        "rider_overlap_count": 1,
                        "rider_overlap_ratio": 1.0,
                        "section_count": 1,
                        "terms_alias": "synthetic-terms-source",
                    }
                ],
                "top_alternative_evidence": [],
                "executable_rule": False,
            }
        ],
        "coverage-terms-mappings.jsonl": [terms_mapping],
        "terms-sections.jsonl": [
            {
                "terms_alias": "synthetic-terms-source",
                "position": 1,
                "title": "Sample Benefit Section",
                "section_id": "synthetic-section-001",
                "physical_page": 2,
                "page_mode": "physical",
                "source_clause_count": 1,
                "semantic_fact_count": 1,
                "section_review_state": "SOL_DIRECT_GROUNDED",
                "legacy_review_only": False,
                "executable_rule": False,
            }
        ],
        "clause-evidence-index.jsonl": [
            {
                "terms_alias": "synthetic-terms-source",
                "section_id": "synthetic-section-001",
                "clause_index": 1,
                "label": "Synthetic clause 1",
                "title": "Sample payment condition",
                "physical_page_start": 2,
                "physical_page_end": 2,
                "source_text_sha256": "a" * 64,
                "semantic_facets": ["payment_reason"],
            }
        ],
        "terms-semantic-review.jsonl": [
            {
                "terms_alias": "synthetic-terms-source",
                "section_id": "synthetic-section-001",
                "section_physical_page": 2,
                "analysis_status": "complete",
                "confidence": "high",
                "section_review_state": "SOL_DIRECT_GROUNDED",
                "section_summary_ko": "Synthetic section summary.",
                "summary_citations": [citation],
                "facts": [
                    {
                        "category": "payment_reason",
                        "citations": [citation],
                        "condition_details_ko": ["Synthetic condition."],
                        "confidence": "high",
                        "decision_impact": "establishes_payment_trigger",
                        "executable_rule": False,
                        "fact_id": "synthetic-fact-001",
                        "numeric_terms_ko": [],
                        "review_state": "SOL_DIRECT_GROUNDED",
                        "statement_ko": "Synthetic payment condition.",
                        "unresolved_reference": False,
                    }
                ],
                "found_categories": ["payment_reason"],
                "missing_categories": [],
                "warnings": [],
                "source_clause_count": 1,
                "classified_clause_count": 1,
                "unclassified_clause_count": 0,
                "previous_result_status": "available",
                "previous_fact_audit": [],
                "executable_rule": False,
                "coverage_references": [
                    {
                        "canonical_policy_id": "synthetic-policy-001",
                        "canonical_rider_id": "synthetic-coverage-001",
                        "current_coverage_applicability_decision": "UNKNOWN",
                        "enrollment_decision": "MATCH",
                        "pairing_document_identity_decision": "MATCH",
                        "pairing_edition_applicability_decision": "MATCH",
                        "section_mapping_decision": "MATCH",
                    }
                ],
                "legacy_review_only": False,
            }
        ],
    }


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _write_private_file(path: Path, content: bytes) -> None:
    path.write_bytes(content)
    path.chmod(0o600)


def refresh_manifest(root: Path) -> None:
    files = []
    for name in DATA_FILES:
        payload = (root / name).read_bytes()
        files.append(
            {
                "name": name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    manifest = {
        "schema_version": "private-analysis-package.sol-v2",
        "review_authority": "synthetic-direct-local-review",
        "authority_boundaries": {
            "enrollment": "certificate_only",
            "terms_presence_never_establishes_enrollment": True,
            "current_status": "latest_contract_state_required",
            "edition_applicability": "contract_date_and_exact_edition_required",
            "individual_claim_decision": "not_performed",
            "executable_rules": False,
        },
        "counts": synthetic_reconciliation(),
        "files": files,
    }
    _write_private_file(root / "manifest.json", _canonical_json(manifest) + b"\n")


def write_synthetic_private_knowledge_package(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=False)
    root.chmod(0o700)
    records = synthetic_records()
    for name, rows in records.items():
        content = b"".join(_canonical_json(row) + b"\n" for row in rows)
        _write_private_file(root / name, content)
    _write_private_file(
        root / "reconciliation.json",
        _canonical_json(synthetic_reconciliation()) + b"\n",
    )
    refresh_manifest(root)
    return root


def mutate_jsonl(
    root: Path,
    name: str,
    mutate: Callable[[dict[str, Any]], None],
    *,
    refresh: bool = True,
) -> None:
    rows = [json.loads(line) for line in (root / name).read_text(encoding="utf-8").splitlines()]
    mutate(rows[0])
    _write_private_file(
        root / name,
        b"".join(_canonical_json(row) + b"\n" for row in rows),
    )
    if refresh:
        refresh_manifest(root)


def append_jsonl(root: Path, name: str, row: dict[str, Any]) -> None:
    with (root / name).open("ab") as stream:
        stream.write(_canonical_json(row) + b"\n")
    (root / name).chmod(0o600)
    refresh_manifest(root)
