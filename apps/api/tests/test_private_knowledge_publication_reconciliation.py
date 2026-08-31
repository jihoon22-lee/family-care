"""Count-only reconciliation for reviewed rule publications."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.private_knowledge.publication_models import (
    PublicationCounts,
    PublicationCountsV2,
)
from familycare_api.private_knowledge.publication_package import (
    load_rule_publication_package,
)
from familycare_api.private_knowledge.publication_reconciliation import (
    DispositionCounts,
    DispositionCountsV2,
    PublicationCoverageBaseline,
    PublicationDatabaseBaseline,
    PublicationEvidenceBaseline,
    build_rule_publication_dry_run,
    canonical_rule_publication_report_digest,
)

from apps.api.tests.private_knowledge_publication_fixtures import (
    SYNTHETIC_SOURCE_SUBJECT_KEY,
    convert_to_v2_advisory_publication_package,
    set_v2_coverage_disposition,
    write_synthetic_rule_publication_package,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000004001")
KNOWLEDGE_RUN_ID = UUID("00000000-0000-4000-8000-000000004002")
CONTRACT_ID = UUID("00000000-0000-4000-8000-000000004003")
COVERAGE_ID = UUID("00000000-0000-4000-8000-000000004004")
SECTION_ID = UUID("00000000-0000-4000-8000-000000004005")
CLAUSE_ID = UUID("00000000-0000-4000-8000-000000004006")
FACT_ID = UUID("00000000-0000-4000-8000-000000004007")
PUBLICATION_RUN_ID = UUID("00000000-0000-4000-8000-000000004008")


def _package(tmp_path: Path):
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    return load_rule_publication_package(
        root,
        repository_root=tmp_path / "repository",
    )


def _baseline(package, **updates):
    coverage = PublicationCoverageBaseline(
        knowledge_contract_id=CONTRACT_ID,
        knowledge_coverage_id=COVERAGE_ID,
        source_subject_key=SYNTHETIC_SOURCE_SUBJECT_KEY,
        family_alias="Family Member A",
        canonical_policy_id="synthetic-policy-001",
        canonical_coverage_id="synthetic-coverage-001",
        subject_binding_decision="MATCH",
        enrollment_decision="MATCH",
        component_classification="BENEFIT_COVERAGE",
        benefit_type="FIXED",
        mapping_applicability="APPLICABLE",
        mapping_enrollment_decision="MATCH",
        document_identity_decision="MATCH",
        edition_applicability_decision="MATCH",
        section_mapping_decision="MATCH",
        overall_mapping_decision="MATCH",
        current_confirmation_decision="MATCH",
        current_confirmed_status="active",
    )
    evidence = PublicationEvidenceBaseline(
        terms_section_id=SECTION_ID,
        source_clause_id=CLAUSE_ID,
        fact_id=FACT_ID,
        canonical_policy_id="synthetic-policy-001",
        terms_source_alias="synthetic-terms-source",
        source_section_key="synthetic-section-001",
        source_clause_index=1,
        source_fact_key="synthetic-fact-001",
        page_start=2,
        page_end=2,
        source_text_sha256="a" * 64,
    )
    values = {
        "household_space_id": HOUSEHOLD_ID,
        "baseline_digest_sha256": "3" * 64,
        "knowledge_import_run_id": KNOWLEDGE_RUN_ID,
        "knowledge_package_digest_sha256": package.manifest.source_knowledge_package_digest_sha256,
        "knowledge_projection_digest_sha256": (
            package.manifest.source_knowledge_projection_digest_sha256
        ),
        "known_publication_digests": (),
        "current_publication_run_id": None,
        "current_publication_package_digest_sha256": None,
        "current_publication_counts": PublicationCounts.zero(),
        "current_disposition_counts": DispositionCounts(
            published=0,
            blocked=0,
            not_applicable=0,
        ),
        "coverage_authorities": (coverage,),
        "evidence": (evidence,),
        "actor_identity_digest_sha256": "4" * 64,
    }
    values.update(updates)
    return PublicationDatabaseBaseline(**values)


def test_first_apply_is_count_only_create_with_deterministic_digest(tmp_path: Path) -> None:
    package = _package(tmp_path)
    report = build_rule_publication_dry_run(package, _baseline(package))

    assert report.operation == "CREATE"
    assert report.apply_block_count == 0
    assert report.input_counts == package.reconciliation
    assert report.expected_insert_counts == package.reconciliation
    assert report.expected_current_counts == package.reconciliation
    assert report.dispositions == DispositionCounts(
        published=1,
        blocked=0,
        not_applicable=0,
    )
    assert report.report_digest_sha256 == canonical_rule_publication_report_digest(report)
    serialized = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    assert "Family Member A" not in serialized
    assert "synthetic-policy-001" not in serialized
    assert str(HOUSEHOLD_ID) not in serialized


def test_v2_advisory_is_counted_without_becoming_executable(tmp_path: Path) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    convert_to_v2_advisory_publication_package(root)
    package = load_rule_publication_package(root, repository_root=tmp_path / "repository")
    baseline = _baseline(
        package,
        current_publication_counts=PublicationCountsV2(
            **PublicationCounts.zero().model_dump(),
            advisory_disposition_count=0,
            user_confirmed_enrollment_count=0,
        ),
        current_disposition_counts=DispositionCountsV2(
            published=0, advisory=0, blocked=0, not_applicable=0
        ),
    )

    report = build_rule_publication_dry_run(package, baseline)

    assert report.package_schema_version == "private-knowledge-rule-publication.sol-v2"
    assert report.input_counts.advisory_disposition_count == 1
    assert report.input_counts.user_confirmed_enrollment_count == 0
    assert report.dispositions == DispositionCountsV2(
        published=0, advisory=1, blocked=0, not_applicable=0
    )


def test_v2_advisory_allows_unresolved_mapping_and_current_status(tmp_path: Path) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    convert_to_v2_advisory_publication_package(root)
    package = load_rule_publication_package(root, repository_root=tmp_path / "repository")
    baseline = _baseline(package)
    coverage = baseline.coverage_authorities[0].model_copy(
        update={
            "mapping_applicability": "UNKNOWN",
            "mapping_enrollment_decision": "UNKNOWN",
            "document_identity_decision": "UNKNOWN",
            "edition_applicability_decision": "UNKNOWN",
            "section_mapping_decision": "UNKNOWN",
            "overall_mapping_decision": "UNKNOWN",
            "current_confirmation_decision": None,
            "current_confirmed_status": None,
        }
    )

    report = build_rule_publication_dry_run(
        package,
        baseline.model_copy(update={"coverage_authorities": (coverage,)}),
    )

    assert report.operation == "CREATE"
    assert report.block_counts.missing_current_confirmation == 0
    assert report.block_counts.coverage_authority_mismatch == 0


@pytest.mark.parametrize(
    "authority_update",
    [
        {"enrollment_decision": "NO_MATCH"},
        {"enrollment_decision": "UNKNOWN"},
        {"component_classification": "NON_BENEFIT_CONTRACT_COMPONENT"},
        {"benefit_type": "INDEMNITY"},
    ],
)
def test_v2_advisory_requires_enrolled_matching_benefit_authority(
    tmp_path: Path,
    authority_update: dict[str, str],
) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    convert_to_v2_advisory_publication_package(root)
    package = load_rule_publication_package(root, repository_root=tmp_path / "repository")
    baseline = _baseline(package)
    coverage = baseline.coverage_authorities[0].model_copy(update=authority_update)

    report = build_rule_publication_dry_run(
        package,
        baseline.model_copy(update={"coverage_authorities": (coverage,)}),
    )

    assert report.operation == "BLOCKED"
    assert report.block_counts.coverage_authority_mismatch == 1


def test_v2_advisory_user_authority_only_overrides_unknown_enrollment(
    tmp_path: Path,
) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    convert_to_v2_advisory_publication_package(root)
    set_v2_coverage_disposition(
        root,
        disposition="ADVISORY",
        enrollment_authority="USER_CONFIRMED_COVERAGE_ENROLLMENT",
        reason_codes=["USER_CONFIRMED_COVERAGE_ENROLLMENT"],
    )
    package = load_rule_publication_package(root, repository_root=tmp_path / "repository")
    baseline = _baseline(package)
    certificate_match_cannot_use_user_authority = build_rule_publication_dry_run(
        package,
        baseline,
    )
    coverage = baseline.coverage_authorities[0].model_copy(
        update={"enrollment_decision": "UNKNOWN"}
    )

    allowed = build_rule_publication_dry_run(
        package,
        baseline.model_copy(update={"coverage_authorities": (coverage,)}),
    )
    rejected = build_rule_publication_dry_run(
        package,
        baseline.model_copy(
            update={
                "coverage_authorities": (
                    coverage.model_copy(update={"enrollment_decision": "NO_MATCH"}),
                )
            }
        ),
    )

    assert certificate_match_cannot_use_user_authority.operation == "BLOCKED"
    assert certificate_match_cannot_use_user_authority.block_counts.coverage_authority_mismatch == 1
    assert allowed.operation == "CREATE"
    assert allowed.input_counts.user_confirmed_enrollment_count == 1
    assert allowed.block_counts.coverage_authority_mismatch == 0
    assert allowed.report_digest_sha256 == canonical_rule_publication_report_digest(allowed)
    assert rejected.operation == "BLOCKED"
    assert rejected.block_counts.coverage_authority_mismatch == 1


def test_current_same_is_no_op_and_new_package_supersedes(tmp_path: Path) -> None:
    package = _package(tmp_path)
    same = _baseline(
        package,
        known_publication_digests=(package.package_digest_sha256,),
        current_publication_run_id=PUBLICATION_RUN_ID,
        current_publication_package_digest_sha256=package.package_digest_sha256,
        current_publication_counts=package.reconciliation,
        current_disposition_counts=DispositionCounts(
            published=1,
            blocked=0,
            not_applicable=0,
        ),
    )

    no_op = build_rule_publication_dry_run(package, same)

    assert no_op.operation == "NO_OP"
    assert all(value == 0 for value in no_op.expected_insert_counts.model_dump().values())
    assert no_op.expected_current_counts == package.reconciliation

    supersede = same.model_copy(
        update={
            "known_publication_digests": (),
            "current_publication_package_digest_sha256": "5" * 64,
        }
    )
    replaced = build_rule_publication_dry_run(package, supersede)
    assert replaced.operation == "SUPERSEDE"
    assert replaced.apply_block_count == 0


def test_historical_digest_and_stale_knowledge_snapshot_block(tmp_path: Path) -> None:
    package = _package(tmp_path)
    historical = _baseline(
        package,
        known_publication_digests=(package.package_digest_sha256,),
        current_publication_run_id=PUBLICATION_RUN_ID,
        current_publication_package_digest_sha256="5" * 64,
        current_publication_counts=package.reconciliation,
    )

    historical_report = build_rule_publication_dry_run(package, historical)
    assert historical_report.operation == "BLOCKED"
    assert historical_report.block_counts.historical_digest_conflict == 1

    stale = _baseline(package, knowledge_projection_digest_sha256="6" * 64)
    stale_report = build_rule_publication_dry_run(package, stale)
    assert stale_report.operation == "BLOCKED"
    assert stale_report.block_counts.snapshot_mismatch == 1


def test_closure_confirmation_authority_and_citation_mismatch_block(tmp_path: Path) -> None:
    package = _package(tmp_path)
    baseline = _baseline(package)

    missing_coverage = baseline.model_copy(update={"coverage_authorities": ()})
    closure = build_rule_publication_dry_run(package, missing_coverage)
    assert closure.block_counts.disposition_closure_mismatch == 1

    coverage = baseline.coverage_authorities[0]
    missing_confirmation = baseline.model_copy(
        update={
            "coverage_authorities": (
                coverage.model_copy(
                    update={
                        "current_confirmation_decision": None,
                        "current_confirmed_status": None,
                    }
                ),
            )
        }
    )
    confirmation = build_rule_publication_dry_run(package, missing_confirmation)
    assert confirmation.block_counts.missing_current_confirmation == 1

    invalid_mapping = baseline.model_copy(
        update={
            "coverage_authorities": (
                coverage.model_copy(update={"edition_applicability_decision": "UNKNOWN"}),
            )
        }
    )
    mapping = build_rule_publication_dry_run(package, invalid_mapping)
    assert mapping.block_counts.coverage_authority_mismatch == 1

    changed_evidence = baseline.evidence[0].model_copy(update={"source_text_sha256": "7" * 64})
    citation = build_rule_publication_dry_run(
        package,
        baseline.model_copy(update={"evidence": (changed_evidence,)}),
    )
    assert citation.block_counts.citation_mismatch == 2


def test_baseline_digest_change_changes_approval_report(tmp_path: Path) -> None:
    package = _package(tmp_path)
    initial = build_rule_publication_dry_run(package, _baseline(package))
    changed = build_rule_publication_dry_run(
        package,
        _baseline(package, baseline_digest_sha256="8" * 64),
    )

    assert initial.baseline_digest_sha256 != changed.baseline_digest_sha256
    assert initial.report_digest_sha256 != changed.report_digest_sha256
