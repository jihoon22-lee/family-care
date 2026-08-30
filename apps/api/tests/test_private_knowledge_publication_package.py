"""Strict package tests for reviewed private-knowledge rules."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from familycare_api.private_knowledge import publication_package as package_module
from familycare_api.private_knowledge.errors import (
    PublicationErrorCode,
    PublicationPackageError,
)
from familycare_api.private_knowledge.publication_package import (
    canonical_rule_publication_digest,
    load_rule_publication_package,
    validate_loaded_rule_publication_package,
)

from apps.api.tests.private_knowledge_publication_fixtures import (
    append_publication_jsonl,
    mutate_publication_jsonl,
    refresh_publication_manifest,
    write_synthetic_rule_publication_package,
)


def _load(root: Path, repository_root: Path):
    return load_rule_publication_package(root, repository_root=repository_root)


def test_loads_referentially_closed_reviewed_publication_package(tmp_path: Path) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    package = _load(root, tmp_path / "repository")

    assert package.schema_version == "private-knowledge-rule-publication.sol-v1"
    assert package.manifest.review_state == "USER_CONFIRMED"
    assert package.subject_aliases == ("Family Member A",)
    assert package.coverage_dispositions[0].value.canonical_policy_id == ("synthetic-policy-001")
    assert package.coverage_dispositions[0].value.canonical_coverage_id == (
        "synthetic-coverage-001"
    )
    assert package.rule_publications[0].value.rule_key == "synthetic-rule-001"
    assert package.rule_citations[0].value.source_section_key == "synthetic-section-001"
    assert package.fact_normalizers[0].value.normalized_value == "sample_category"
    assert package.reconciliation.rule_publication_count == 1
    assert package.package_digest_sha256 == canonical_rule_publication_digest(package)

    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files"] = list(reversed(manifest["files"]))
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    manifest_path.chmod(0o600)

    reordered = _load(root, tmp_path / "repository")
    assert reordered.package_digest_sha256 == package.package_digest_sha256


@pytest.mark.parametrize(
    ("role", "mutation", "expected_code"),
    [
        (
            "fact-normalizers.jsonl",
            lambda row: row.__setitem__("unexpected_field", "synthetic"),
            PublicationErrorCode.INVALID_RECORD,
        ),
        (
            "rule-citations.jsonl",
            lambda row: row.__setitem__("rule_key", "missing-rule"),
            PublicationErrorCode.BROKEN_REFERENCE,
        ),
        (
            "rule-publications.jsonl",
            lambda row: row["rule_document"]["expression"].__setitem__("op", "unsupported"),
            PublicationErrorCode.UNSUPPORTED_DSL,
        ),
        (
            "rule-publications.jsonl",
            lambda row: row["rule_document"]["expression"].__setitem__(
                "value", "eval('synthetic')"
            ),
            PublicationErrorCode.EXECUTABLE_INPUT,
        ),
    ],
)
def test_shape_references_and_dsl_fail_closed(
    tmp_path: Path,
    role: str,
    mutation,
    expected_code: PublicationErrorCode,
) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    mutate_publication_jsonl(root, role, mutation)

    with pytest.raises(PublicationPackageError) as caught:
        _load(root, tmp_path / "repository")

    assert caught.value.code is expected_code
    assert caught.value.file_role == role


def test_duplicate_missing_citation_and_incomplete_disposition_are_rejected(
    tmp_path: Path,
) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "duplicate-package")
    duplicate = json.loads((root / "coverage-dispositions.jsonl").read_text(encoding="utf-8"))
    append_publication_jsonl(root, "coverage-dispositions.jsonl", duplicate)
    with pytest.raises(PublicationPackageError) as duplicate_error:
        _load(root, tmp_path / "repository")
    assert duplicate_error.value.code is PublicationErrorCode.DUPLICATE_CANONICAL_KEY

    root = write_synthetic_rule_publication_package(tmp_path / "citation-package")
    (root / "rule-citations.jsonl").write_text("", encoding="utf-8")
    (root / "rule-citations.jsonl").chmod(0o600)
    refresh_publication_manifest(root)
    with pytest.raises(PublicationPackageError) as citation_error:
        _load(root, tmp_path / "repository")
    assert citation_error.value.code is PublicationErrorCode.MISSING_CITATION

    root = write_synthetic_rule_publication_package(tmp_path / "closure-package")
    second_rule = json.loads((root / "rule-publications.jsonl").read_text(encoding="utf-8"))
    second_rule["rule_key"] = "synthetic-rule-002"
    second_rule["canonical_coverage_id"] = "synthetic-coverage-002"
    second_rule["rule_document"]["evidence_ids"] = ["synthetic-rule-citation-002"]
    append_publication_jsonl(root, "rule-publications.jsonl", second_rule)
    second_citation = json.loads((root / "rule-citations.jsonl").read_text(encoding="utf-8"))
    second_citation["citation_key"] = "synthetic-rule-citation-002"
    second_citation["rule_key"] = "synthetic-rule-002"
    second_citation["canonical_coverage_id"] = "synthetic-coverage-002"
    append_publication_jsonl(root, "rule-citations.jsonl", second_citation)
    with pytest.raises(PublicationPackageError) as closure_error:
        _load(root, tmp_path / "repository")
    assert closure_error.value.code is (PublicationErrorCode.INCOMPLETE_DISPOSITION_CLOSURE)


def test_manifest_hash_reconciliation_and_filesystem_boundaries(tmp_path: Path) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "hash-package")
    target = root / "fact-normalizers.jsonl"
    target.write_bytes(target.read_bytes() + b" ")
    target.chmod(0o600)
    with pytest.raises(PublicationPackageError) as hash_error:
        _load(root, tmp_path / "repository")
    assert hash_error.value.code is PublicationErrorCode.FILE_SIZE_MISMATCH

    root = write_synthetic_rule_publication_package(tmp_path / "digest-package")
    target = root / "fact-normalizers.jsonl"
    changed = bytearray(target.read_bytes())
    changed[1] = ord("Z")
    target.write_bytes(changed)
    target.chmod(0o600)
    with pytest.raises(PublicationPackageError) as digest_error:
        _load(root, tmp_path / "repository")
    assert digest_error.value.code is PublicationErrorCode.FILE_DIGEST_MISMATCH

    root = write_synthetic_rule_publication_package(tmp_path / "counts-package")
    reconciliation_path = root / "reconciliation.json"
    reconciliation = json.loads(reconciliation_path.read_text(encoding="utf-8"))
    reconciliation["coverage_count"] = 2
    reconciliation_path.write_text(json.dumps(reconciliation), encoding="utf-8")
    reconciliation_path.chmod(0o600)
    refresh_publication_manifest(root)
    with pytest.raises(PublicationPackageError) as count_error:
        _load(root, tmp_path / "repository")
    assert count_error.value.code is PublicationErrorCode.RECONCILIATION_MISMATCH

    root = write_synthetic_rule_publication_package(tmp_path / "boundary-package")
    with pytest.raises(PublicationPackageError) as relative_error:
        load_rule_publication_package(Path("relative"), repository_root=tmp_path)
    assert relative_error.value.code is PublicationErrorCode.ROOT_NOT_ABSOLUTE

    with pytest.raises(PublicationPackageError) as inside_error:
        _load(root, tmp_path)
    assert inside_error.value.code is PublicationErrorCode.ROOT_INSIDE_REPOSITORY

    root.chmod(0o750)
    with pytest.raises(PublicationPackageError) as mode_error:
        _load(root, tmp_path / "repository")
    assert mode_error.value.code is PublicationErrorCode.ROOT_MODE_INVALID

    root = write_synthetic_rule_publication_package(tmp_path / "file-mode-package")
    (root / "rule-publications.jsonl").chmod(0o640)
    with pytest.raises(PublicationPackageError) as file_mode_error:
        _load(root, tmp_path / "repository")
    assert file_mode_error.value.code is PublicationErrorCode.FILE_MODE_INVALID

    root = write_synthetic_rule_publication_package(tmp_path / "symlink-package")
    original = root / "normalizers-original.jsonl"
    (root / "fact-normalizers.jsonl").rename(original)
    os.symlink(original.name, root / "fact-normalizers.jsonl")
    with pytest.raises(PublicationPackageError) as symlink_error:
        _load(root, tmp_path / "repository")
    assert symlink_error.value.code is PublicationErrorCode.FILE_NOT_REGULAR


def test_loaded_package_detects_nested_projection_mutation(tmp_path: Path) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    package = _load(root, tmp_path / "repository")

    package.rule_publications[0].value.rule_document["required"] = False

    with pytest.raises(PublicationPackageError) as changed:
        validate_loaded_rule_publication_package(package)

    assert changed.value.code is PublicationErrorCode.FILE_CHANGED
    assert changed.value.file_role == "rule-publications.jsonl"
    assert changed.value.row_number == 1


def test_path_replacement_during_validation_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "publication-package")
    original_read = package_module._read_file
    replaced = False

    def replacing_read(root_fd: int, name: str, **kwargs) -> bytes:
        nonlocal replaced
        payload = original_read(root_fd, name, **kwargs)
        if name == "fact-normalizers.jsonl" and not replaced:
            replacement = tmp_path / "replacement.jsonl"
            replacement.write_bytes(payload)
            replacement.chmod(0o600)
            os.replace(replacement, root / name)
            replaced = True
        return payload

    monkeypatch.setattr(package_module, "_read_file", replacing_read)

    with pytest.raises(PublicationPackageError) as changed:
        _load(root, tmp_path / "repository")

    assert changed.value.code is PublicationErrorCode.FILE_CHANGED


def test_missing_unexpected_invalid_utf8_and_invalid_json_are_rejected(tmp_path: Path) -> None:
    root = write_synthetic_rule_publication_package(tmp_path / "missing-package")
    (root / "rule-publications.jsonl").unlink()
    with pytest.raises(PublicationPackageError) as missing_error:
        _load(root, tmp_path / "repository")
    assert missing_error.value.code is PublicationErrorCode.MISSING_REQUIRED_FILE

    root = write_synthetic_rule_publication_package(tmp_path / "unexpected-package")
    unexpected = root / "unexpected.json"
    unexpected.write_text("{}", encoding="utf-8")
    unexpected.chmod(0o600)
    with pytest.raises(PublicationPackageError) as unexpected_error:
        _load(root, tmp_path / "repository")
    assert unexpected_error.value.code is PublicationErrorCode.UNEXPECTED_FILE

    root = write_synthetic_rule_publication_package(tmp_path / "utf8-package")
    (root / "rule-publications.jsonl").write_bytes(b"\xff\n")
    (root / "rule-publications.jsonl").chmod(0o600)
    refresh_publication_manifest(root)
    with pytest.raises(PublicationPackageError) as utf8_error:
        _load(root, tmp_path / "repository")
    assert utf8_error.value.code is PublicationErrorCode.INVALID_JSON

    root = write_synthetic_rule_publication_package(tmp_path / "json-package")
    (root / "rule-publications.jsonl").write_text("{\n", encoding="utf-8")
    (root / "rule-publications.jsonl").chmod(0o600)
    refresh_publication_manifest(root)
    with pytest.raises(PublicationPackageError) as json_error:
        _load(root, tmp_path / "repository")
    assert json_error.value.code is PublicationErrorCode.INVALID_JSON
