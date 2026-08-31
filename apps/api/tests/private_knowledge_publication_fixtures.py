"""Wholly synthetic fixtures for reviewed rule-publication packages."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

PUBLICATION_DATA_FILES = (
    "coverage-dispositions.jsonl",
    "contract-status-intervals.jsonl",
    "fact-normalizers.jsonl",
    "rule-publications.jsonl",
    "rule-citations.jsonl",
    "calculation-publications.jsonl",
    "calculation-citations.jsonl",
    "reconciliation.json",
)

SYNTHETIC_SOURCE_SUBJECT_KEY = "subject:" + hashlib.sha256(b'["Family Member A"]').hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def _write_private_file(path: Path, payload: bytes) -> None:
    path.write_bytes(payload)
    path.chmod(0o600)


def refresh_publication_manifest(root: Path) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = []
    for name in PUBLICATION_DATA_FILES:
        payload = (root / name).read_bytes()
        manifest["files"].append(
            {
                "name": name,
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    _write_private_file(
        manifest_path,
        (_canonical_json(manifest) + "\n").encode("utf-8"),
    )


def bind_publication_package_to_knowledge(
    root: Path,
    *,
    package_digest_sha256: str,
    projection_digest_sha256: str,
) -> None:
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_knowledge_package_digest_sha256"] = package_digest_sha256
    manifest["source_knowledge_projection_digest_sha256"] = projection_digest_sha256
    _write_private_file(
        manifest_path,
        (_canonical_json(manifest) + "\n").encode("utf-8"),
    )


def mutate_publication_jsonl(
    root: Path,
    role: str,
    mutation: Callable[[dict[str, Any]], None],
    *,
    row_index: int = 0,
    refresh: bool = True,
) -> None:
    path = root / role
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    mutation(rows[row_index])
    _write_private_file(
        path,
        ("\n".join(_canonical_json(row) for row in rows) + "\n").encode("utf-8"),
    )
    if refresh:
        refresh_publication_manifest(root)


def append_publication_jsonl(root: Path, role: str, row: dict[str, Any]) -> None:
    path = root / role
    payload = path.read_text(encoding="utf-8") + _canonical_json(row) + "\n"
    _write_private_file(path, payload.encode("utf-8"))
    refresh_publication_manifest(root)


def write_synthetic_rule_publication_package(root: Path) -> Path:
    root.mkdir(mode=0o700)
    root.chmod(0o700)

    coverage_disposition = {
        "source_subject_key": SYNTHETIC_SOURCE_SUBJECT_KEY,
        "family_alias": "Family Member A",
        "canonical_policy_id": "synthetic-policy-001",
        "canonical_coverage_id": "synthetic-coverage-001",
        "benefit_type": "FIXED",
        "disposition": "PUBLISHED",
        "reason_codes": ["SYNTHETIC_REVIEW_COMPLETE"],
        "review_state": "USER_CONFIRMED",
    }
    status_interval = {
        "canonical_policy_id": "synthetic-policy-001",
        "effective_from": "2026-01-01",
        "effective_through": "2026-12-31",
        "decision": "MATCH",
        "confirmed_status": "active",
        "authority": "USER_CONFIRMED_EVENT_DATE",
        "reason_code": "SYNTHETIC_STATUS_CONFIRMED",
        "review_state": "USER_CONFIRMED",
    }
    normalizer = {
        "normalizer_key": "synthetic-normalizer-001",
        "field_path": "MedicalEvent.classification",
        "match_kind": "EXACT_TOKEN_SEQUENCE",
        "phrase": "sample category phrase",
        "normalized_value": "sample_category",
        "priority": 100,
        "review_state": "USER_CONFIRMED",
    }
    rule_publication = {
        "rule_key": "synthetic-rule-001",
        "canonical_policy_id": "synthetic-policy-001",
        "canonical_coverage_id": "synthetic-coverage-001",
        "rule_kind": "eligibility",
        "schema_version": "coverage-rule-v1",
        "required": True,
        "rule_document": {
            "schema_version": "coverage-rule-v1",
            "rule_kind": "eligibility",
            "required": True,
            "input_field_paths": ["MedicalEvent.classification"],
            "expression": {
                "op": "equals",
                "field": "MedicalEvent.classification",
                "value": "sample_category",
            },
            "result_reason_code": "SYNTHETIC_CLASSIFICATION_MATCH",
            "evidence_ids": ["synthetic-rule-citation-001"],
        },
        "result_reason_code": "SYNTHETIC_CLASSIFICATION_MATCH",
        "review_state": "USER_CONFIRMED",
    }
    rule_citation = {
        "citation_key": "synthetic-rule-citation-001",
        "rule_key": "synthetic-rule-001",
        "canonical_policy_id": "synthetic-policy-001",
        "canonical_coverage_id": "synthetic-coverage-001",
        "terms_source_alias": "synthetic-terms-source",
        "source_section_key": "synthetic-section-001",
        "source_clause_index": 1,
        "source_fact_key": "synthetic-fact-001",
        "evidence_purpose": "ELIGIBILITY",
        "page_start": 2,
        "page_end": 2,
        "source_text_sha256": "a" * 64,
    }
    calculation_publication = {
        "calculation_key": "synthetic-calculation-001",
        "canonical_policy_id": "synthetic-policy-001",
        "canonical_coverage_id": "synthetic-coverage-001",
        "calculation_kind": "FIXED",
        "schema_version": "coverage-rule-v1",
        "calculation_document": {
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
            "result_reason_code": "SYNTHETIC_FIXED_AMOUNT",
            "evidence_ids": ["synthetic-calculation-citation-001"],
        },
        "result_reason_code": "SYNTHETIC_FIXED_AMOUNT",
        "review_state": "USER_CONFIRMED",
    }
    calculation_citation = {
        "citation_key": "synthetic-calculation-citation-001",
        "calculation_key": "synthetic-calculation-001",
        "canonical_policy_id": "synthetic-policy-001",
        "canonical_coverage_id": "synthetic-coverage-001",
        "terms_source_alias": "synthetic-terms-source",
        "source_section_key": "synthetic-section-001",
        "source_clause_index": 1,
        "source_fact_key": "synthetic-fact-001",
        "evidence_purpose": "AMOUNT",
        "page_start": 2,
        "page_end": 2,
        "source_text_sha256": "a" * 64,
    }
    counts = {
        "subject_count": 1,
        "contract_count": 1,
        "coverage_count": 1,
        "disposition_count": 1,
        "published_disposition_count": 1,
        "blocked_disposition_count": 0,
        "not_applicable_disposition_count": 0,
        "status_interval_count": 1,
        "fact_normalizer_count": 1,
        "rule_publication_count": 1,
        "rule_citation_count": 1,
        "calculation_publication_count": 1,
        "calculation_citation_count": 1,
    }
    rows_by_role: dict[str, object] = {
        "coverage-dispositions.jsonl": coverage_disposition,
        "contract-status-intervals.jsonl": status_interval,
        "fact-normalizers.jsonl": normalizer,
        "rule-publications.jsonl": rule_publication,
        "rule-citations.jsonl": rule_citation,
        "calculation-publications.jsonl": calculation_publication,
        "calculation-citations.jsonl": calculation_citation,
        "reconciliation.json": counts,
    }
    for role, value in rows_by_role.items():
        payload = _canonical_json(value) + "\n"
        _write_private_file(root / role, payload.encode("utf-8"))

    manifest = {
        "schema_version": "private-knowledge-rule-publication.sol-v1",
        "source_knowledge_package_digest_sha256": "1" * 64,
        "source_knowledge_projection_digest_sha256": "2" * 64,
        "publisher_version": "synthetic-publisher-v1",
        "review_state": "USER_CONFIRMED",
        "counts": counts,
        "files": [],
    }
    _write_private_file(
        root / "manifest.json",
        (_canonical_json(manifest) + "\n").encode("utf-8"),
    )
    refresh_publication_manifest(root)
    return root


def convert_to_v2_advisory_publication_package(
    root: Path, *, include_reviewed_artifacts: bool = False
) -> Path:
    """Convert the wholly synthetic v1 fixture into an advisory-only v2 package."""

    disposition_path = root / "coverage-dispositions.jsonl"
    disposition = json.loads(disposition_path.read_text(encoding="utf-8"))
    disposition["disposition"] = "ADVISORY"
    disposition["enrollment_authority"] = "CERTIFICATE_SNAPSHOT"
    disposition["reason_codes"] = ["SYNTHETIC_ADVISORY_ONLY"]
    _write_private_file(
        disposition_path,
        (_canonical_json(disposition) + "\n").encode("utf-8"),
    )
    if not include_reviewed_artifacts:
        for role in (
            "rule-publications.jsonl",
            "rule-citations.jsonl",
            "calculation-publications.jsonl",
            "calculation-citations.jsonl",
        ):
            _write_private_file(root / role, b"")

    counts_path = root / "reconciliation.json"
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    counts.update(
        {
            "published_disposition_count": 0,
            "advisory_disposition_count": 1,
            "user_confirmed_enrollment_count": 0,
            "rule_publication_count": int(include_reviewed_artifacts),
            "rule_citation_count": int(include_reviewed_artifacts),
            "calculation_publication_count": int(include_reviewed_artifacts),
            "calculation_citation_count": int(include_reviewed_artifacts),
        }
    )
    _write_private_file(counts_path, (_canonical_json(counts) + "\n").encode("utf-8"))

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "private-knowledge-rule-publication.sol-v2"
    manifest["publisher_version"] = "synthetic-publisher-v2"
    manifest["counts"] = counts
    _write_private_file(
        manifest_path,
        (_canonical_json(manifest) + "\n").encode("utf-8"),
    )
    refresh_publication_manifest(root)
    return root


def set_v2_coverage_disposition(
    root: Path,
    *,
    disposition: str,
    enrollment_authority: str | None,
    reason_codes: list[str],
) -> None:
    """Rewrite the one-row synthetic v2 disposition and its aggregate counts."""

    disposition_path = root / "coverage-dispositions.jsonl"
    row = json.loads(disposition_path.read_text(encoding="utf-8"))
    row["disposition"] = disposition
    row["enrollment_authority"] = enrollment_authority
    row["reason_codes"] = reason_codes
    _write_private_file(disposition_path, (_canonical_json(row) + "\n").encode("utf-8"))

    counts_path = root / "reconciliation.json"
    counts = json.loads(counts_path.read_text(encoding="utf-8"))
    counts.update(
        {
            "published_disposition_count": int(disposition == "PUBLISHED"),
            "advisory_disposition_count": int(disposition == "ADVISORY"),
            "blocked_disposition_count": int(disposition == "BLOCKED"),
            "not_applicable_disposition_count": int(disposition == "NOT_APPLICABLE"),
            "user_confirmed_enrollment_count": int(
                enrollment_authority == "USER_CONFIRMED_COVERAGE_ENROLLMENT"
            ),
        }
    )
    _write_private_file(counts_path, (_canonical_json(counts) + "\n").encode("utf-8"))

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["counts"] = counts
    _write_private_file(
        manifest_path,
        (_canonical_json(manifest) + "\n").encode("utf-8"),
    )
    refresh_publication_manifest(root)
