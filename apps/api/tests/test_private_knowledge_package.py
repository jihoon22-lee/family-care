"""Validation contract for external private-knowledge packages."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from familycare_api.private_knowledge import package as package_module
from familycare_api.private_knowledge.errors import PackageErrorCode, PrivateKnowledgePackageError
from familycare_api.private_knowledge.package import (
    canonical_package_digest,
    contract_certificate_decision,
    load_private_knowledge_package,
)

from apps.api.tests.private_knowledge_fixtures import (
    append_jsonl,
    mutate_jsonl,
    refresh_manifest,
    write_synthetic_private_knowledge_package,
)


def _load(root: Path, repository_root: Path):
    return load_private_knowledge_package(root, repository_root=repository_root)


def test_valid_package_is_lossless_deterministic_and_reference_closed(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")

    package = _load(root, tmp_path / "repository")

    assert package.schema_version == "private-analysis-package.sol-v2"
    assert package.manifest.review_authority == ("gpt-5.6-sol_direct_local_review_no_model_api")
    assert len(package.contracts) == 1
    assert len(package.coverages) == 1
    assert len(package.pairings) == 1
    assert len(package.mappings) == 1
    assert len(package.sections) == 1
    assert len(package.clauses) == 1
    assert len(package.semantic_reviews) == 1
    assert package.fact_count == 1
    assert package.subject_aliases == ("Family Member A",)
    assert package.contracts[0].source_record["canonical_policy_id"] == "synthetic-policy-001"
    assert package.contracts[0].source_record_digest_sha256
    assert package.package_digest_sha256 == canonical_package_digest(package)
    assert package.reconciliation.policy_count == 1
    assert package.reconciliation.executable_rule_count == 0

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = list(reversed(manifest["files"]))
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    manifest_path.chmod(0o600)
    reordered = _load(root, tmp_path / "repository")
    assert reordered.package_digest_sha256 == package.package_digest_sha256


def test_certificate_document_match_is_independent_from_unresolved_coverage_rows(
    tmp_path: Path,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    package = _load(root, tmp_path / "repository")
    contract = package.contracts[0].value
    unresolved = contract.model_copy(
        update={
            "row_reconciliation": contract.row_reconciliation.model_copy(
                update={"unresolved_enrollment_rows": 1}
            )
        }
    )

    assert contract_certificate_decision(unresolved) == "MATCH"


def test_loaded_package_integrity_rejects_in_memory_projection_change(
    tmp_path: Path,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    package = _load(root, tmp_path / "repository")
    changed_contract = replace(
        package.contracts[0],
        value=package.contracts[0].value.model_copy(
            update={"product_name": "Changed Sample Policy"}
        ),
    )
    changed_package = replace(package, contracts=(changed_contract,))

    with pytest.raises(PrivateKnowledgePackageError) as changed:
        package_module.validate_loaded_private_knowledge_package(changed_package)

    assert changed.value.code is PackageErrorCode.FILE_CHANGED
    assert changed.value.file_role == "contracts.jsonl"
    assert changed.value.row_number == 1


@pytest.mark.parametrize(
    ("mutation", "expected_code", "expected_role"),
    [
        (
            lambda row: row.__setitem__("unexpected_private_field", "not-accepted"),
            PackageErrorCode.INVALID_RECORD,
            "contracts.jsonl",
        ),
        (
            lambda row: row.__setitem__("canonical_policy_id", "missing-policy"),
            PackageErrorCode.BROKEN_REFERENCE,
            "coverage-components.jsonl",
        ),
        (
            lambda row: row.__setitem__("executable_rule", True),
            PackageErrorCode.EXECUTABLE_INPUT,
            "coverage-components.jsonl",
        ),
    ],
)
def test_record_shape_reference_and_executable_fail_closed(
    tmp_path: Path,
    mutation,
    expected_code: PackageErrorCode,
    expected_role: str,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    target = (
        "contracts.jsonl" if expected_role == "contracts.jsonl" else "coverage-components.jsonl"
    )
    mutate_jsonl(root, target, mutation)

    with pytest.raises(PrivateKnowledgePackageError) as caught:
        _load(root, tmp_path / "repository")

    assert caught.value.code is expected_code
    assert caught.value.file_role == expected_role
    assert caught.value.row_number == 1


def test_manifest_hash_duplicate_entry_and_unexpected_file_are_rejected(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    contract_path = root / "contracts.jsonl"
    changed = bytearray(contract_path.read_bytes())
    changed[1] = ord("Z")
    contract_path.write_bytes(changed)
    contract_path.chmod(0o600)

    with pytest.raises(PrivateKnowledgePackageError) as hash_error:
        _load(root, tmp_path / "repository")
    assert hash_error.value.code is PackageErrorCode.FILE_DIGEST_MISMATCH

    root = write_synthetic_private_knowledge_package(tmp_path / "duplicate-package")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"].append(manifest["files"][0])
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    with pytest.raises(PrivateKnowledgePackageError) as duplicate_error:
        _load(root, tmp_path / "repository")
    assert duplicate_error.value.code is PackageErrorCode.DUPLICATE_MANIFEST_ENTRY

    root = write_synthetic_private_knowledge_package(tmp_path / "unexpected-package")
    unexpected = root / "unexpected.json"
    unexpected.write_text("{}", encoding="utf-8")
    unexpected.chmod(0o600)
    with pytest.raises(PrivateKnowledgePackageError) as unexpected_error:
        _load(root, tmp_path / "repository")
    assert unexpected_error.value.code is PackageErrorCode.UNEXPECTED_FILE


def test_root_and_file_security_boundaries_are_enforced(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")

    with pytest.raises(PrivateKnowledgePackageError) as relative_error:
        load_private_knowledge_package(Path("relative-package"), repository_root=tmp_path)
    assert relative_error.value.code is PackageErrorCode.ROOT_NOT_ABSOLUTE

    with pytest.raises(PrivateKnowledgePackageError) as inside_error:
        _load(root, tmp_path)
    assert inside_error.value.code is PackageErrorCode.ROOT_INSIDE_REPOSITORY

    root.chmod(0o750)
    with pytest.raises(PrivateKnowledgePackageError) as root_mode_error:
        _load(root, tmp_path / "repository")
    assert root_mode_error.value.code is PackageErrorCode.ROOT_MODE_INVALID
    root.chmod(0o700)

    (root / "contracts.jsonl").chmod(0o640)
    with pytest.raises(PrivateKnowledgePackageError) as file_mode_error:
        _load(root, tmp_path / "repository")
    assert file_mode_error.value.code is PackageErrorCode.FILE_MODE_INVALID

    root = write_synthetic_private_knowledge_package(tmp_path / "symlink-package")
    original = root / "contracts-original.jsonl"
    (root / "contracts.jsonl").rename(original)
    os.symlink(original.name, root / "contracts.jsonl")
    with pytest.raises(PrivateKnowledgePackageError) as symlink_error:
        _load(root, tmp_path / "repository")
    assert symlink_error.value.code is PackageErrorCode.FILE_NOT_REGULAR


def test_reconciliation_and_nested_bounds_are_verified(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    reconciliation_path = root / "reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    reconciliation["policy_count"] = 2
    reconciliation_path.write_text(json.dumps(reconciliation), encoding="utf-8")
    reconciliation_path.chmod(0o600)
    refresh_manifest(root)

    with pytest.raises(PrivateKnowledgePackageError) as count_error:
        _load(root, tmp_path / "repository")
    assert count_error.value.code is PackageErrorCode.RECONCILIATION_MISMATCH

    root = write_synthetic_private_knowledge_package(tmp_path / "bounded-package")
    mutate_jsonl(
        root,
        "coverage-components.jsonl",
        lambda row: row.__setitem__("warnings", ["synthetic-warning"] * 129),
    )
    with pytest.raises(PrivateKnowledgePackageError) as bounds_error:
        _load(root, tmp_path / "repository")
    assert bounds_error.value.code is PackageErrorCode.NESTED_VALUE_LIMIT


def test_errors_never_echo_private_values_paths_or_hashes(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    private_marker = "private-marker-that-must-not-be-echoed"
    mutate_jsonl(
        root,
        "contracts.jsonl",
        lambda row: row.__setitem__("current_status", private_marker),
    )

    with pytest.raises(PrivateKnowledgePackageError) as caught:
        _load(root, tmp_path / "repository")

    rendered = str(caught.value)
    assert caught.value.code is PackageErrorCode.INVALID_RECORD
    assert rendered == "INVALID_RECORD:contracts.jsonl:1"
    assert private_marker not in rendered
    assert str(root) not in rendered
    assert "sha256" not in rendered.lower()


def test_duplicate_identity_and_citation_lineage_fail_closed(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "duplicate-package")
    coverage = json.loads(
        (root / "coverage-components.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    append_jsonl(root, "coverage-components.jsonl", coverage)
    with pytest.raises(PrivateKnowledgePackageError) as duplicate_error:
        _load(root, tmp_path / "repository")
    assert duplicate_error.value.code is PackageErrorCode.DUPLICATE_CANONICAL_KEY
    assert duplicate_error.value.file_role == "coverage-components.jsonl"
    assert duplicate_error.value.row_number == 2

    root = write_synthetic_private_knowledge_package(tmp_path / "lineage-package")

    def break_citation(row):
        row["facts"][0]["citations"][0]["source_text_sha256"] = "b" * 64

    mutate_jsonl(root, "terms-semantic-review.jsonl", break_citation)
    with pytest.raises(PrivateKnowledgePackageError) as lineage_error:
        _load(root, tmp_path / "repository")
    assert lineage_error.value.code is PackageErrorCode.SOURCE_LINEAGE_MISMATCH
    assert lineage_error.value.file_role == "terms-semantic-review.jsonl"
    assert lineage_error.value.row_number == 1


def test_assignment_alias_can_exist_without_a_structured_section(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    mutate_jsonl(
        root,
        "contracts.jsonl",
        lambda row: row["terms_pairing"]["selected_terms_aliases"].append(
            "synthetic-unstructured-terms"
        ),
        refresh=False,
    )
    mutate_jsonl(
        root,
        "policy-terms-pairings.jsonl",
        lambda row: row["selected_terms_aliases"].append("synthetic-unstructured-terms"),
        refresh=False,
    )
    mutate_jsonl(
        root,
        "coverage-components.jsonl",
        lambda row: row["terms_mapping"]["pairing_aliases"].append("synthetic-unstructured-terms"),
        refresh=False,
    )
    mutate_jsonl(
        root,
        "coverage-terms-mappings.jsonl",
        lambda row: row["pairing_aliases"].append("synthetic-unstructured-terms"),
    )

    package = _load(root, tmp_path / "repository")

    assert package.pairings[0].value.selected_terms_aliases == [
        "synthetic-terms-source",
        "synthetic-unstructured-terms",
    ]


def test_schema_missing_file_row_limit_and_supplementary_file_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "future-package")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = "private-analysis-package.future"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    with pytest.raises(PrivateKnowledgePackageError) as schema_error:
        _load(root, tmp_path / "repository")
    assert schema_error.value.code is PackageErrorCode.UNSUPPORTED_SCHEMA

    root = write_synthetic_private_knowledge_package(tmp_path / "missing-package")
    (root / "contracts.jsonl").unlink()
    with pytest.raises(PrivateKnowledgePackageError) as missing_error:
        _load(root, tmp_path / "repository")
    assert missing_error.value.code is PackageErrorCode.MISSING_REQUIRED_FILE
    assert missing_error.value.file_role == "contracts.jsonl"

    root = write_synthetic_private_knowledge_package(tmp_path / "limited-package")
    monkeypatch.setitem(package_module.MAX_ROWS_BY_ROLE, "contracts.jsonl", 0)
    with pytest.raises(PrivateKnowledgePackageError) as row_error:
        _load(root, tmp_path / "repository")
    assert row_error.value.code is PackageErrorCode.ROW_LIMIT
    monkeypatch.setitem(package_module.MAX_ROWS_BY_ROLE, "contracts.jsonl", 10_000)

    root = write_synthetic_private_knowledge_package(tmp_path / "supplement-package")
    supplement = root / "ANALYSIS-REPORT.md"
    supplement_payload = b"# Synthetic analysis report\n"
    supplement.write_bytes(supplement_payload)
    supplement.chmod(0o600)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    import hashlib

    manifest["files"].append(
        {
            "name": supplement.name,
            "bytes": len(supplement_payload),
            "sha256": hashlib.sha256(supplement_payload).hexdigest(),
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)
    assert _load(root, tmp_path / "repository").schema_version == (
        "private-analysis-package.sol-v2"
    )


def test_post_open_file_identity_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    real_stat = package_module.os.stat
    target_calls = 0

    def changed_stat(path, *args, **kwargs):
        nonlocal target_calls
        observed = real_stat(path, *args, **kwargs)
        if path != "contracts.jsonl":
            return observed
        target_calls += 1
        if target_calls == 1:
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino,
            st_mode=observed.st_mode,
            st_mtime_ns=observed.st_mtime_ns + 1,
            st_size=observed.st_size,
        )

    monkeypatch.setattr(package_module.os, "stat", changed_stat)
    with pytest.raises(PrivateKnowledgePackageError) as changed_error:
        _load(root, tmp_path / "repository")
    assert changed_error.value.code is PackageErrorCode.FILE_CHANGED
    assert changed_error.value.file_role == "contracts.jsonl"


def test_opened_root_descriptor_must_match_the_preflight_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    real_fstat = package_module.os.fstat

    def changed_root_fstat(fd: int):
        observed = real_fstat(fd)
        if not package_module.stat.S_ISDIR(observed.st_mode):
            return observed
        return SimpleNamespace(
            st_dev=observed.st_dev,
            st_ino=observed.st_ino + 1,
            st_mode=observed.st_mode,
            st_ctime_ns=observed.st_ctime_ns,
            st_mtime_ns=observed.st_mtime_ns,
        )

    monkeypatch.setattr(package_module.os, "fstat", changed_root_fstat)

    with pytest.raises(PrivateKnowledgePackageError) as changed_error:
        _load(root, tmp_path / "repository")

    assert changed_error.value.code is PackageErrorCode.ROOT_NOT_DIRECTORY


@pytest.mark.parametrize(
    ("target", "mutation", "expected_role"),
    [
        (
            "contracts.jsonl",
            lambda row: row["terms_pairing"].__setitem__("review_decision", "UNKNOWN"),
            "contracts.jsonl",
        ),
        (
            "coverage-components.jsonl",
            lambda row: row["terms_mapping"].__setitem__("mapping_decision", "UNKNOWN"),
            "coverage-components.jsonl",
        ),
        (
            "coverage-components.jsonl",
            lambda row: row.__setitem__(
                "current_coverage_applicability_decision",
                "MATCH",
            ),
            "coverage-components.jsonl",
        ),
        (
            "coverage-terms-mappings.jsonl",
            lambda row: row.__setitem__(
                "pairing_document_identity_decision",
                "UNKNOWN",
            ),
            "coverage-components.jsonl",
        ),
        (
            "terms-semantic-review.jsonl",
            lambda row: row["coverage_references"][0].__setitem__(
                "enrollment_decision",
                "UNKNOWN",
            ),
            "terms-semantic-review.jsonl",
        ),
    ],
)
def test_duplicated_cross_file_authority_axes_must_agree(
    tmp_path: Path,
    target: str,
    mutation,
    expected_role: str,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    mutate_jsonl(root, target, mutation)

    with pytest.raises(PrivateKnowledgePackageError) as mismatch:
        _load(root, tmp_path / "repository")

    assert mismatch.value.code is PackageErrorCode.SOURCE_LINEAGE_MISMATCH
    assert mismatch.value.file_role == expected_role


def test_inherited_coverage_references_must_exist_in_the_same_contract(
    tmp_path: Path,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    missing = "synthetic-missing-coverage"
    mutate_jsonl(
        root,
        "coverage-components.jsonl",
        lambda row: (
            row["certificate_review"].__setitem__(
                "evidence_inherited_from_rider_id",
                missing,
            ),
            row["terms_mapping"].__setitem__("mapping_inherited_from_rider_id", missing),
        ),
        refresh=False,
    )
    mutate_jsonl(
        root,
        "coverage-terms-mappings.jsonl",
        lambda row: row.__setitem__("mapping_inherited_from_rider_id", missing),
    )

    with pytest.raises(PrivateKnowledgePackageError) as missing_parent:
        _load(root, tmp_path / "repository")

    assert missing_parent.value.code is PackageErrorCode.BROKEN_REFERENCE


def test_unknown_mapping_selected_section_is_still_reference_closed(
    tmp_path: Path,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")

    def unknown_missing_section(row):
        row.update(
            {
                "mapping_decision": "UNKNOWN",
                "selected_terms_alias": "synthetic-missing-terms",
                "selected_section_id": "synthetic-missing-section",
                "physical_page": 2,
                "clause_count": 1,
            }
        )

    mutate_jsonl(
        root,
        "coverage-components.jsonl",
        lambda row: unknown_missing_section(row["terms_mapping"]),
        refresh=False,
    )
    mutate_jsonl(root, "coverage-terms-mappings.jsonl", unknown_missing_section)

    with pytest.raises(PrivateKnowledgePackageError) as missing_section:
        _load(root, tmp_path / "repository")

    assert missing_section.value.code is PackageErrorCode.BROKEN_REFERENCE
    assert missing_section.value.file_role == "coverage-terms-mappings.jsonl"


def test_duplicate_assignment_alias_is_rejected_before_database_access(
    tmp_path: Path,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    duplicate_alias = "synthetic-terms-source"
    mutate_jsonl(
        root,
        "contracts.jsonl",
        lambda row: row["terms_pairing"]["selected_terms_aliases"].append(duplicate_alias),
        refresh=False,
    )
    mutate_jsonl(
        root,
        "policy-terms-pairings.jsonl",
        lambda row: row["selected_terms_aliases"].append(duplicate_alias),
    )

    with pytest.raises(PrivateKnowledgePackageError) as duplicate:
        _load(root, tmp_path / "repository")

    assert duplicate.value.code is PackageErrorCode.DUPLICATE_CANONICAL_KEY
    assert duplicate.value.file_role == "policy-terms-pairings.jsonl"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda row: row.__setitem__("product_name", "   "),
        lambda row: row.__setitem__("product_name", "Sample\x00Policy"),
        lambda row: row.__setitem__("monthly_premium_krw", 10**20),
        lambda row: row["group_review"].__setitem__("password", "synthetic-secret"),
        lambda row: row["field_conflicts"].append({"source_path": "/synthetic/private/source.pdf"}),
    ],
)
def test_database_incompatible_and_forbidden_nested_values_fail_validation(
    tmp_path: Path,
    mutation,
) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    mutate_jsonl(root, "contracts.jsonl", mutation)

    with pytest.raises(PrivateKnowledgePackageError) as invalid:
        _load(root, tmp_path / "repository")

    assert invalid.value.code is PackageErrorCode.INVALID_RECORD
    assert invalid.value.file_role == "contracts.jsonl"


def test_manifest_review_authority_is_not_caller_defined(tmp_path: Path) -> None:
    root = write_synthetic_private_knowledge_package(tmp_path / "private-package")
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["review_authority"] = "synthetic-untrusted-authority"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    with pytest.raises(PrivateKnowledgePackageError) as invalid:
        _load(root, tmp_path / "repository")

    assert invalid.value.code is PackageErrorCode.MANIFEST_INVALID
