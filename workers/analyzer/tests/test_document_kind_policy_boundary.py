"""Synthetic tests for the non-policy policy-structuring boundary."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from familycare_worker.jobs import _validate_settings
from familycare_worker.policy_candidates import _locked_job_matches
from familycare_worker.policy_jobs import PolicyStructuringJobRecord
from familycare_worker.repository import _should_enqueue_policy_structuring_job

_KINDS = ("application", "product_explanation")
_HOUSEHOLD_ID = UUID("00000000-0000-4000-8000-000000000801")
_BATCH_ITEM_ID = UUID("00000000-0000-4000-8000-000000000802")
_MEMBER_ID = UUID("00000000-0000-4000-8000-000000000803")
_VERSION_ID = UUID("00000000-0000-4000-8000-000000000804")
_EXTRACTION_ID = UUID("00000000-0000-4000-8000-000000000805")
_JOB_ID = UUID("00000000-0000-4000-8000-000000000806")
_AGGREGATE_ID = UUID("00000000-0000-4000-8000-000000000807")
_ROOT = Path(__file__).resolve().parents[3]


def _contract(path: str) -> dict[str, Any]:
    value = json.loads((_ROOT / path).read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _settings(document_kind: str) -> dict[str, Any]:
    return {
        "document_kind": document_kind,
        "extractor_config": {
            "profile": "quality-v1",
            "quality_rule_version": "quality-v1",
            "table_strategy": "auto",
        },
    }


@pytest.mark.parametrize("document_kind", _KINDS)
def test_worker_analysis_settings_accept_non_policy_document_kinds(document_kind: str) -> None:
    settings = _validate_settings(_settings(document_kind))

    assert settings["document_kind"] == document_kind


@pytest.mark.parametrize(
    "schema_path",
    (
        "packages/contracts/schemas/document-ingestion.v1.schema.json",
        "packages/contracts/schemas/analysis-job.v1.schema.json",
    ),
)
def test_common_document_contracts_list_product_explanation(schema_path: str) -> None:
    schema = _contract(schema_path)
    if schema_path.endswith("document-ingestion.v1.schema.json"):
        kind_schema = schema["properties"]["document_kind"]
    else:
        kind_schema = schema["$defs"]["AnalysisSettings"]["properties"]["document_kind"]

    assert "product_explanation" in kind_schema["enum"]


def test_generated_worker_document_contract_lists_product_explanation() -> None:
    generated = (_ROOT / "workers/analyzer/src/familycare_worker/generated_contracts.py").read_text(
        encoding="utf-8"
    )

    assert '"product_explanation"' in generated


@pytest.mark.parametrize("document_kind", _KINDS)
def test_non_policy_document_kinds_never_enqueue_policy_structuring_jobs(
    document_kind: str,
) -> None:
    assert _should_enqueue_policy_structuring_job(document_kind) is False


def _job() -> PolicyStructuringJobRecord:
    now = datetime.now(UTC)
    return PolicyStructuringJobRecord(
        id=_JOB_ID,
        household_space_id=_HOUSEHOLD_ID,
        batch_item_id=_BATCH_ITEM_ID,
        family_member_id=_MEMBER_ID,
        document_version_id=_VERSION_ID,
        extraction_id=_EXTRACTION_ID,
        policy_aggregate_id=_AGGREGATE_ID,
        state="running",
        pipeline_version="synthetic-policy-v1",
        available_at=now,
        lease_owner="worker-a",
        lease_expires_at=now + timedelta(minutes=3),
        heartbeat_at=now,
        attempts=1,
        max_attempts=5,
        error_code=None,
    )


def _locked_row(*, item_document_kind: str, document_kind: str) -> dict[str, object]:
    job = _job()
    return {
        "id": job.id,
        "household_space_id": job.household_space_id,
        "batch_item_id": job.batch_item_id,
        "family_member_id": job.family_member_id,
        "document_version_id": job.document_version_id,
        "extraction_id": job.extraction_id,
        "policy_aggregate_id": job.policy_aggregate_id,
        "pipeline_version": job.pipeline_version,
        "state": "running",
        "lease_owner": "worker-a",
        "lease_valid": True,
        "batch_household_space_id": _HOUSEHOLD_ID,
        "batch_family_member_id": _MEMBER_ID,
        "member_household_space_id": _HOUSEHOLD_ID,
        "member_deleted_at": None,
        "item_state": "succeeded",
        "item_document_kind": item_document_kind,
        "item_document_id": _VERSION_ID,
        "version_document_id": _VERSION_ID,
        "document_kind": document_kind,
        "document_deleted_at": None,
        "extraction_status": "succeeded",
    }


@pytest.mark.parametrize("document_kind", _KINDS)
def test_non_policy_document_kinds_cannot_publish_policy_candidates(
    document_kind: str,
) -> None:
    for item_kind, stored_kind in (
        (document_kind, "policy"),
        ("policy", document_kind),
        (document_kind, document_kind),
    ):
        row = _locked_row(
            item_document_kind=item_kind,
            document_kind=stored_kind,
        )

        assert _locked_job_matches(row, _job(), "worker-a") is False
