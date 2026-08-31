"""Protected exact-binding and current-enrollment confirmation workflow."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import UUID

import pytest
from familycare_api.private_knowledge.confirmations import (
    AppliedConfirmationSet,
    ConfirmationDryRunReport,
    ConfirmationError,
    ConfirmationErrorCode,
    apply_confirmation_manifest,
    canonical_confirmation_report_digest,
    load_confirmation_manifest,
    prepare_confirmation_dry_run,
)

HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000002001")
ACTOR_ID = UUID("00000000-0000-4000-8000-000000002002")
MEMBER_ID = UUID("00000000-0000-4000-8000-000000002003")
RUN_ID = UUID("00000000-0000-4000-8000-000000002004")
PACKAGE_DIGEST = "a" * 64


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": "private-knowledge-confirmation.sol-v1",
        "package_digest_sha256": PACKAGE_DIGEST,
        "household_space_id": str(HOUSEHOLD_ID),
        "confirmed_by": str(ACTOR_ID),
        "status_as_of": "2026-08-30",
        "authority": "USER_CONFIRMED_CURRENT_ENROLLMENT",
        "subjects": [
            {
                "source_subject_key": "synthetic-subject-001",
                "family_member_id": str(MEMBER_ID),
            }
        ],
        "contracts": [
            {
                "canonical_policy_id": "synthetic-policy-001",
                "decision": "MATCH",
                "confirmed_status": "active",
                "reason_code": "USER_ATTESTED_CURRENT",
            }
        ],
    }


def _write_manifest(
    root: Path,
    payload: dict[str, object] | None = None,
) -> Path:
    root.mkdir(mode=0o700)
    path = root / "confirmation.json"
    path.write_text(
        json.dumps(payload or _manifest_payload(), ensure_ascii=False),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _report(manifest_digest: str) -> ConfirmationDryRunReport:
    provisional = ConfirmationDryRunReport(
        schema_version="private-knowledge-confirmation-dry-run.v1",
        manifest_digest_sha256=manifest_digest,
        package_digest_sha256=PACKAGE_DIGEST,
        household_space_id=HOUSEHOLD_ID,
        current_run_id=RUN_ID,
        baseline_digest_sha256="b" * 64,
        operation="APPLY",
        subject_count=1,
        contract_count=1,
        binding_change_count=1,
        confirmation_insert_count=1,
        confirmation_supersede_count=0,
        report_digest_sha256="0" * 64,
    )
    return provisional.model_copy(
        update={"report_digest_sha256": canonical_confirmation_report_digest(provisional)}
    )


class _Repository:
    def __init__(self) -> None:
        self.prepared = []
        self.applied = []

    def prepare_confirmation_dry_run(self, manifest):
        self.prepared.append(manifest)
        return _report(manifest.manifest_digest_sha256)

    def apply_confirmations(self, manifest, *, approved_report):
        self.applied.append((manifest, approved_report))
        return AppliedConfirmationSet(
            run_id=RUN_ID,
            package_digest_sha256=PACKAGE_DIGEST,
            subject_count=1,
            contract_count=1,
            current_confirmation_count=1,
        )


def test_manifest_loader_accepts_only_protected_external_exact_keys(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    path = _write_manifest(tmp_path / "private")

    manifest = load_confirmation_manifest(path, repository_root=repository_root)

    assert manifest.package_digest_sha256 == PACKAGE_DIGEST
    assert manifest.household_space_id == HOUSEHOLD_ID
    assert manifest.confirmed_by == ACTOR_ID
    assert manifest.subjects[0].source_subject_key == "synthetic-subject-001"
    assert manifest.contracts[0].canonical_policy_id == "synthetic-policy-001"
    assert len(manifest.manifest_digest_sha256) == 64


@pytest.mark.parametrize("duplicate_role", ["subjects", "contracts"])
def test_manifest_loader_rejects_duplicate_exact_keys(
    tmp_path: Path,
    duplicate_role: str,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    payload = _manifest_payload()
    rows = payload[duplicate_role]
    assert isinstance(rows, list)
    rows.append(dict(rows[0]))
    path = _write_manifest(tmp_path / "private", payload)

    with pytest.raises(ConfirmationError) as caught:
        load_confirmation_manifest(path, repository_root=repository_root)

    expected = (
        ConfirmationErrorCode.DUPLICATE_SUBJECT_KEY
        if duplicate_role == "subjects"
        else ConfirmationErrorCode.DUPLICATE_CONTRACT_KEY
    )
    assert caught.value.code is expected


def test_manifest_loader_rejects_repository_paths_modes_and_symlinks(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    repository_private = _write_manifest(repository_root / "private")
    with pytest.raises(ConfirmationError) as inside:
        load_confirmation_manifest(repository_private, repository_root=repository_root)
    assert inside.value.code is ConfirmationErrorCode.MANIFEST_PATH_INVALID

    external = _write_manifest(tmp_path / "external")
    external.chmod(0o644)
    with pytest.raises(ConfirmationError) as mode:
        load_confirmation_manifest(external, repository_root=repository_root)
    assert mode.value.code is ConfirmationErrorCode.MANIFEST_FILE_MODE_INVALID

    external.chmod(0o600)
    symlink = tmp_path / "external" / "linked.json"
    symlink.symlink_to(external)
    with pytest.raises(ConfirmationError) as linked:
        load_confirmation_manifest(symlink, repository_root=repository_root)
    assert linked.value.code is ConfirmationErrorCode.MANIFEST_FILE_NOT_REGULAR


def test_manifest_errors_never_echo_private_values(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    private_value = "private-person-value-must-not-escape"
    payload = _manifest_payload()
    payload["authority"] = private_value
    path = _write_manifest(tmp_path / "private", payload)

    with pytest.raises(ConfirmationError) as caught:
        load_confirmation_manifest(path, repository_root=repository_root)

    assert private_value not in str(caught.value)
    assert caught.value.code is ConfirmationErrorCode.MANIFEST_INVALID


def test_confirmation_service_persists_authenticated_dry_run_and_applies(
    tmp_path: Path,
) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    manifest_path = _write_manifest(tmp_path / "private")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)
    report_path = report_root / "confirmation-dry-run.json"
    repository = _Repository()

    report = prepare_confirmation_dry_run(
        manifest_path=manifest_path,
        report_path=report_path,
        repository_root=repository_root,
        expected_household_space_id=HOUSEHOLD_ID,
        repository=repository,
    )
    assert report.operation == "APPLY"
    assert report_path.stat().st_mode & 0o777 == 0o600
    assert len(repository.prepared) == 1

    applied = apply_confirmation_manifest(
        manifest_path=manifest_path,
        report_path=report_path,
        repository_root=repository_root,
        expected_household_space_id=HOUSEHOLD_ID,
        approved_report_digest_sha256=report.report_digest_sha256,
        repository=repository,
    )
    assert applied.current_confirmation_count == 1
    assert len(repository.applied) == 1

    with pytest.raises(ConfirmationError) as unapproved:
        apply_confirmation_manifest(
            manifest_path=manifest_path,
            report_path=report_path,
            repository_root=repository_root,
            expected_household_space_id=HOUSEHOLD_ID,
            approved_report_digest_sha256="f" * 64,
            repository=repository,
        )
    assert unapproved.value.code is ConfirmationErrorCode.APPROVAL_INVALID
    assert len(repository.applied) == 1


def test_confirmation_service_rejects_manifest_household_mismatch(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    manifest_path = _write_manifest(tmp_path / "private")
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)

    with pytest.raises(ConfirmationError) as caught:
        prepare_confirmation_dry_run(
            manifest_path=manifest_path,
            report_path=report_root / "report.json",
            repository_root=repository_root,
            expected_household_space_id=UUID("00000000-0000-4000-8000-000000002099"),
            repository=_Repository(),
        )

    assert caught.value.code is ConfirmationErrorCode.MANIFEST_SCOPE_MISMATCH
