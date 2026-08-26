from __future__ import annotations

from pathlib import Path

from scripts.check_documentation import parse_release_evidence

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_PATH = ROOT / "docs/release/v0.1.0-verification.md"


def test_v0_1_release_evidence_has_strict_pre_tag_shape() -> None:
    evidence = parse_release_evidence(EVIDENCE_PATH)

    assert evidence.image_components == ("web", "api", "worker")
    assert evidence.digest_format == "sha256:<64 lowercase hexadecimal characters>"
    assert evidence.pending_fields >= {
        "tag-workflow-run",
        "web-version-digest",
        "web-commit-digest",
        "api-version-digest",
        "api-commit-digest",
        "worker-version-digest",
        "worker-commit-digest",
    }
    assert evidence.statuses >= {"PASSED", "FAILED", "UNVERIFIED", "PENDING"}


def test_v0_1_release_evidence_keeps_scope_boundaries_explicit() -> None:
    evidence = parse_release_evidence(EVIDENCE_PATH)

    assert evidence.no_latest_tag
    assert evidence.no_cloud_run
    assert evidence.private_data_findings == ()
