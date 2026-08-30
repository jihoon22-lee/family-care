"""End-to-end operator CLI proof against disposable PostgreSQL."""

from __future__ import annotations

from pathlib import Path

import pytest
from familycare_api.private_knowledge import cli
from familycare_api.private_knowledge.reconciliation import load_dry_run_report

from apps.api.tests.test_private_knowledge_apply_integration import (
    ACTOR_ID,
    HOUSEHOLD_ID,
    _database_url,
    _package,
    _seed,
)

pytestmark = pytest.mark.integration


def test_cli_validate_dry_run_apply_verify_and_idempotent_apply(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _seed()
    package_root, _ = _package(tmp_path)
    repository_root = tmp_path / "repository"
    report_root = tmp_path / "reports"
    report_root.mkdir(mode=0o700)
    report_path = report_root / "dry-run.json"
    environment = {
        "FAMILYCARE_DATABASE_URL": _database_url(),
        "FAMILYCARE_PRIVATE_KNOWLEDGE_PACKAGE_ROOT": str(package_root),
        "FAMILYCARE_PRIVATE_KNOWLEDGE_REPORT_PATH": str(report_path),
        "FAMILYCARE_PRIVATE_KNOWLEDGE_REPOSITORY_ROOT": str(repository_root),
        "FAMILYCARE_PRIVATE_KNOWLEDGE_HOUSEHOLD_ID": str(HOUSEHOLD_ID),
        "FAMILYCARE_PRIVATE_KNOWLEDGE_ACTOR_ID": str(ACTOR_ID),
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert cli.main(["validate"]) == 0
    assert cli.main(["dry-run"]) == 0
    first_report = load_dry_run_report(report_path)
    assert first_report.operation == "CREATE"
    monkeypatch.setenv(
        "FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST",
        first_report.report_digest_sha256,
    )
    assert cli.main(["apply"]) == 0
    first_apply = capsys.readouterr()
    assert first_apply.err == ""
    assert "status=APPLIED" in first_apply.out
    assert str(package_root) not in first_apply.out

    assert cli.main(["verify"]) == 0
    assert cli.main(["dry-run"]) == 0
    second_report = load_dry_run_report(report_path)
    assert second_report.operation == "NO_OP"
    monkeypatch.setenv(
        "FAMILYCARE_PRIVATE_KNOWLEDGE_APPROVAL_DIGEST",
        second_report.report_digest_sha256,
    )
    assert cli.main(["apply"]) == 0
    assert cli.main(["verify"]) == 0
    repeated = capsys.readouterr()
    assert repeated.err == ""
    assert "status=VERIFIED" in repeated.out
    assert "status=DRY_RUN_NO_OP" in repeated.out
    assert "status=APPLIED" in repeated.out
    assert repeated.out.count("status=VERIFIED") == 2
    assert str(report_path) not in repeated.out
