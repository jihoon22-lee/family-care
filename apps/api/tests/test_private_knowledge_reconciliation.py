"""Count-only dry-run reconciliation for private knowledge snapshots."""

from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from familycare_api.private_knowledge import reconciliation as reconciliation_module
from familycare_api.private_knowledge.package import load_private_knowledge_package
from familycare_api.private_knowledge.reconciliation import (
    BaselineCounts,
    KnowledgeDatabaseBaseline,
    KnowledgeEntityCounts,
    LabelKeyCount,
    PrivateKnowledgeReconciliationError,
    ReconciliationErrorCode,
    build_dry_run_report,
    canonical_report_digest,
    load_dry_run_report,
    operational_label_key,
    write_dry_run_report,
)

from apps.api.tests.private_knowledge_fixtures import (
    write_synthetic_private_knowledge_package,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000001901")


def _package(tmp_path: Path):
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    return load_private_knowledge_package(root, repository_root=tmp_path / "repository")


def _baseline(
    *,
    package_digest: str | None = None,
    current_package_digest: str | None = None,
    baseline_digest: str = "b" * 64,
    current_counts: KnowledgeEntityCounts | None = None,
    include_label_candidates: bool = False,
) -> KnowledgeDatabaseBaseline:
    policy_keys: tuple[LabelKeyCount, ...] = ()
    coverage_keys: tuple[LabelKeyCount, ...] = ()
    if include_label_candidates:
        policy_keys = (
            LabelKeyCount(
                key=operational_label_key("Sample Insurer", "Sample Policy"),
                count=1,
            ),
        )
        coverage_keys = (
            LabelKeyCount(
                key=operational_label_key(
                    "Sample Insurer",
                    "Sample Policy",
                    "Sample Hospital Benefit",
                ),
                count=1,
            ),
        )
    return KnowledgeDatabaseBaseline(
        household_space_id=HOUSEHOLD_ID,
        baseline_digest_sha256=baseline_digest,
        current_run_id=None,
        current_package_digest_sha256=current_package_digest,
        known_package_digests=(() if package_digest is None else (package_digest,)),
        counts=BaselineCounts(
            family_members=1,
            policy_contracts=1,
            riders=1,
            document_versions=1,
            evidence=1,
            import_runs=0 if package_digest is None else 1,
            current_import_runs=0 if current_package_digest is None else 1,
        ),
        current_snapshot_counts=current_counts or KnowledgeEntityCounts.zero(),
        policy_label_key_counts=policy_keys,
        coverage_label_key_counts=coverage_keys,
    )


def test_first_import_report_is_count_only_and_keeps_authorities_independent(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    report = build_dry_run_report(
        package,
        _baseline(include_label_candidates=True),
    )

    assert report.operation == "CREATE"
    assert report.target_already_current is False
    assert report.input_counts == KnowledgeEntityCounts(
        subjects=1,
        contracts=1,
        coverages=1,
        terms_assignments=1,
        terms_assignment_sources=1,
        terms_sections=1,
        source_clauses=1,
        semantic_reviews=1,
        facts=1,
        fact_citations=1,
        coverage_terms_mappings=1,
        document_bindings=2,
    )
    assert report.expected_insert_counts == report.input_counts
    assert report.expected_current_counts == report.input_counts
    assert report.enrollment_decisions.model_dump() == {
        "match": 1,
        "no_match": 0,
        "unknown": 0,
    }
    assert report.benefit_types.model_dump() == {
        "fixed": 1,
        "indemnity": 0,
        "unknown": 0,
        "not_applicable": 0,
    }
    assert report.mapping_applicability.model_dump() == {
        "applicable": 1,
        "not_applicable": 0,
        "unknown": 0,
    }
    assert report.mapping_source_decisions.model_dump() == {
        "match": 1,
        "no_match": 0,
        "unknown": 0,
        "not_applicable": 0,
    }
    assert report.operational_reconciliation.policy_label_review_candidates == 1
    assert report.operational_reconciliation.coverage_label_review_candidates == 1
    assert report.operational_reconciliation.policy_exact_bindings == 0
    assert report.operational_reconciliation.coverage_exact_bindings == 0
    assert report.operational_reconciliation.document_exact_bindings == 0
    assert report.operational_reconciliation.operational_publish_blocked_coverages == 1
    assert report.snapshot_conflict_count == 0
    assert report.apply_block_count == 0
    assert report.report_digest_sha256 == canonical_report_digest(report)

    serialized = json.dumps(report.model_dump(mode="json"), sort_keys=True)
    for forbidden in (
        "Family Member A",
        "Sample Insurer",
        "Sample Policy",
        "Sample Hospital Benefit",
        "synthetic-certificate-source",
        "synthetic-terms-source",
        str(HOUSEHOLD_ID),
        "/tmp/",
    ):
        assert forbidden not in serialized


def test_same_digest_is_no_op_and_new_digest_supersedes(tmp_path: Path) -> None:
    package = _package(tmp_path)
    input_counts = build_dry_run_report(package, _baseline()).input_counts
    no_op = build_dry_run_report(
        package,
        _baseline(
            package_digest=package.package_digest_sha256,
            current_package_digest=package.package_digest_sha256,
            current_counts=input_counts,
        ),
    )
    assert no_op.operation == "NO_OP"
    assert no_op.target_already_current is True
    assert no_op.expected_insert_counts == KnowledgeEntityCounts.zero()
    assert no_op.expected_current_counts == input_counts

    supersede = build_dry_run_report(
        package,
        _baseline(
            package_digest="d" * 64,
            current_package_digest="d" * 64,
            current_counts=KnowledgeEntityCounts(
                subjects=1,
                contracts=1,
                coverages=2,
                terms_assignments=1,
                terms_assignment_sources=0,
                terms_sections=0,
                source_clauses=0,
                semantic_reviews=0,
                facts=0,
                fact_citations=0,
                coverage_terms_mappings=2,
                document_bindings=0,
            ),
        ),
    )
    assert supersede.operation == "SUPERSEDE"
    assert supersede.target_already_current is False
    assert supersede.expected_insert_counts == supersede.input_counts
    assert supersede.expected_current_counts == supersede.input_counts


def test_historical_non_current_digest_is_blocked_instead_of_false_no_op(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    current_counts = KnowledgeEntityCounts(
        subjects=1,
        contracts=1,
        coverages=2,
        terms_assignments=1,
        terms_assignment_sources=0,
        terms_sections=0,
        source_clauses=0,
        semantic_reviews=0,
        facts=0,
        fact_citations=0,
        coverage_terms_mappings=2,
        document_bindings=0,
    )

    report = build_dry_run_report(
        package,
        _baseline(
            package_digest=package.package_digest_sha256,
            current_package_digest="d" * 64,
            current_counts=current_counts,
        ),
    )

    assert report.operation == "BLOCKED"
    assert report.target_already_current is False
    assert report.expected_insert_counts == KnowledgeEntityCounts.zero()
    assert report.expected_current_counts == current_counts
    assert report.apply_block_count == 1


def test_benefit_type_counts_join_mappings_by_coverage_id_not_file_order(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    second_coverage = replace(
        package.coverages[0],
        value=package.coverages[0].value.model_copy(
            update={
                "canonical_rider_id": "synthetic-rider-002",
                "benefit_type": "indemnity",
            }
        ),
    )
    non_benefit_mapping = replace(
        package.mappings[0],
        value=package.mappings[0].value.model_copy(
            update={
                "canonical_rider_id": "synthetic-rider-002",
                "component_class": "NON_BENEFIT_CONTRACT_COMPONENT",
                "mapping_decision": "NOT_APPLICABLE",
            }
        ),
    )
    shuffled = replace(
        package,
        coverages=(*package.coverages, second_coverage),
        mappings=(non_benefit_mapping, *package.mappings),
    )

    report = build_dry_run_report(shuffled, _baseline())

    assert report.benefit_types.model_dump() == {
        "fixed": 1,
        "indemnity": 0,
        "unknown": 0,
        "not_applicable": 1,
    }


def test_mapping_source_decision_and_applicability_axes_remain_independent(
    tmp_path: Path,
) -> None:
    package = _package(tmp_path)
    unknown_coverage = replace(
        package.coverages[0],
        value=package.coverages[0].value.model_copy(update={"benefit_type": "unknown"}),
    )
    unknown_mapping = replace(
        package.mappings[0],
        value=package.mappings[0].value.model_copy(
            update={
                "component_class": "UNKNOWN",
                "mapping_decision": "NO_MATCH",
            }
        ),
    )

    report = build_dry_run_report(
        replace(package, coverages=(unknown_coverage,), mappings=(unknown_mapping,)),
        _baseline(),
    )

    assert report.mapping_source_decisions.model_dump() == {
        "match": 0,
        "no_match": 1,
        "unknown": 0,
        "not_applicable": 0,
    }
    assert report.mapping_applicability.model_dump() == {
        "applicable": 0,
        "not_applicable": 0,
        "unknown": 1,
    }
    assert report.benefit_types.model_dump() == {
        "fixed": 0,
        "indemnity": 0,
        "unknown": 1,
        "not_applicable": 0,
    }


def test_report_digest_changes_with_baseline_and_detects_tampering(tmp_path: Path) -> None:
    package = _package(tmp_path)
    first = build_dry_run_report(package, _baseline(baseline_digest="1" * 64))
    second = build_dry_run_report(package, _baseline(baseline_digest="2" * 64))
    assert first.report_digest_sha256 != second.report_digest_sha256

    report_root = tmp_path / "reports"
    report_root.mkdir()
    report_root.chmod(0o700)
    report_path = report_root / "dry-run.json"
    write_dry_run_report(first, report_path, repository_root=tmp_path / "repository")
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert load_dry_run_report(report_path, repository_root=tmp_path / "repository") == first

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    payload["apply_block_count"] = 1
    report_path.write_text(json.dumps(payload), encoding="utf-8")
    report_path.chmod(0o600)
    with pytest.raises(PrivateKnowledgeReconciliationError) as tampered:
        load_dry_run_report(report_path, repository_root=tmp_path / "repository")
    assert tampered.value.code is ReconciliationErrorCode.REPORT_DIGEST_MISMATCH


def test_report_path_must_be_absolute_external_private_storage(tmp_path: Path) -> None:
    package = _package(tmp_path)
    report = build_dry_run_report(package, _baseline())

    with pytest.raises(PrivateKnowledgeReconciliationError) as relative:
        write_dry_run_report(
            report,
            Path("dry-run.json"),
            repository_root=tmp_path / "repository",
        )
    assert relative.value.code is ReconciliationErrorCode.REPORT_PATH_INVALID

    repository = tmp_path / "repository"
    repository.mkdir()
    repository.chmod(0o700)
    with pytest.raises(PrivateKnowledgeReconciliationError) as inside:
        write_dry_run_report(
            report,
            repository / "dry-run.json",
            repository_root=repository,
        )
    assert inside.value.code is ReconciliationErrorCode.REPORT_PATH_INVALID

    report_root = tmp_path / "wide-reports"
    report_root.mkdir()
    report_root.chmod(0o750)
    with pytest.raises(PrivateKnowledgeReconciliationError) as mode:
        write_dry_run_report(
            report,
            report_root / "dry-run.json",
            repository_root=repository,
        )
    assert mode.value.code is ReconciliationErrorCode.REPORT_PARENT_MODE_INVALID


def test_report_load_rechecks_external_private_descriptor_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package(tmp_path)
    report = build_dry_run_report(package, _baseline())
    repository = tmp_path / "repository"
    repository.mkdir()
    repository.chmod(0o700)
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)
    report_path = report_root / "dry-run.json"
    write_dry_run_report(report, report_path, repository_root=repository)

    with pytest.raises(PrivateKnowledgeReconciliationError) as inside:
        load_dry_run_report(report_path, repository_root=tmp_path)
    assert inside.value.code is ReconciliationErrorCode.REPORT_PATH_INVALID

    real_fstat = reconciliation_module.os.fstat

    def changed_file_fstat(fd: int):
        observed = real_fstat(fd)
        if not stat.S_ISREG(observed.st_mode):
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino + 1,
            st_mode=observed.st_mode,
            st_size=observed.st_size,
            st_mtime_ns=observed.st_mtime_ns,
        )

    monkeypatch.setattr(reconciliation_module.os, "fstat", changed_file_fstat)
    with pytest.raises(PrivateKnowledgeReconciliationError) as replaced:
        load_dry_run_report(report_path, repository_root=repository)
    assert replaced.value.code is ReconciliationErrorCode.REPORT_INVALID


def test_report_write_rechecks_private_parent_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = _package(tmp_path)
    report = build_dry_run_report(package, _baseline())
    repository = tmp_path / "repository"
    repository.mkdir()
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)
    real_fstat = reconciliation_module.os.fstat

    def changed_directory_fstat(fd: int):
        observed = real_fstat(fd)
        if not stat.S_ISDIR(observed.st_mode):
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino + 1,
            st_mode=observed.st_mode,
            st_ctime_ns=observed.st_ctime_ns,
            st_mtime_ns=observed.st_mtime_ns,
        )

    monkeypatch.setattr(reconciliation_module.os, "fstat", changed_directory_fstat)
    with pytest.raises(PrivateKnowledgeReconciliationError) as replaced:
        write_dry_run_report(
            report,
            report_root / "dry-run.json",
            repository_root=repository,
        )
    assert replaced.value.code is ReconciliationErrorCode.REPORT_PATH_INVALID
