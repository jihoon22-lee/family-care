"""Sanitized immutable ClaimCase snapshot helpers."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest
from familycare_api.claims.snapshot import (
    SnapshotPrivacyError,
    build_claim_snapshot,
    canonical_json,
    snapshot_sha256,
)

RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
EVENT_ID = UUID("22222222-2222-4222-8222-222222222222")
RIDER_ID = UUID("33333333-3333-4333-8333-333333333333")
RULE_ID = UUID("44444444-4444-4444-8444-444444444444")
EVIDENCE_ID = UUID("55555555-5555-4555-8555-555555555555")
DOCUMENT_VERSION_ID = UUID("66666666-6666-4666-8666-666666666666")
EXTRACTION_ID = UUID("77777777-7777-4777-8777-777777777777")


def _evidence() -> SimpleNamespace:
    return SimpleNamespace(
        evidence_id=EVIDENCE_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        extraction_id=EXTRACTION_ID,
        content_sha256="a" * 64,
        physical_page=3,
        bbox=(Decimal("1.0"), Decimal("2.0"), Decimal("20.0"), Decimal("30.0")),
        review_state="USER_CONFIRMED",
    )


def _result() -> tuple[SimpleNamespace, SimpleNamespace, SimpleNamespace, SimpleNamespace]:
    evidence = _evidence()
    evaluation = SimpleNamespace(
        id=UUID("88888888-8888-4888-8888-888888888888"),
        rider_id=RIDER_ID,
        rule_version_id=RULE_ID,
        result="MATCH",
        required=True,
        reason_code="DIAGNOSIS_MATCHED",
        fact_paths=("MedicalEvent.event_date",),
        missing_fields=(),
        conflicting_fields=(),
        evidence_ids=(EVIDENCE_ID,),
        evidence=(evidence,),
        evaluator_version="decision-engine-v1",
        # This must never cross the claim snapshot boundary.
        facts={"MedicalEvent.situation": "synthetic clinical detail"},
    )
    candidate = SimpleNamespace(
        id=UUID("99999999-9999-4999-8999-999999999999"),
        rider_id=RIDER_ID,
        aggregate_result="MATCH",
        rider_type="fixed",
        rider_label="Sample Rider",
        evaluations=(evaluation,),
        questions=(
            SimpleNamespace(field_path="MedicalEvent.event_date", reason_code="DATE_REQUIRED"),
        ),
        hold_reason_codes=(),
        required_match_count=1,
        required_unknown_count=0,
        required_no_match_count=0,
        decision_run_id=RUN_ID,
        version=2,
    )
    result = SimpleNamespace(
        run_id=RUN_ID,
        medical_event_id=EVENT_ID,
        event_version=4,
        engine_version="decision-engine-v1",
        rule_set_version="ruleset-v1",
        policy_snapshot_at=datetime(2026, 8, 26, 1, 2, 3, tzinfo=UTC),
        candidates=(candidate,),
        evaluations=(evaluation,),
        stale=False,
        # The natural situation is intentionally present on the source object.
        situation="synthetic clinical detail",
    )
    policy = SimpleNamespace(
        policy_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        rider_id=RIDER_ID,
        effective_status="active",
        evidence_ids=(EVIDENCE_ID,),
        rider_type="fixed",
        rider_label="Sample Rider",
        contract_start=date(2020, 1, 1),
        contract_end=None,
        rider_coverage_start=date(2020, 1, 1),
        rider_coverage_end=None,
        rider_status="active",
        insured_amount=Decimal("1000000"),
        currency="KRW",
        renewable=True,
        status_checked_at=datetime(2026, 8, 25, tzinfo=UTC),
    )
    rule = SimpleNamespace(
        id=RULE_ID,
        coverage_rule_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        candidate_version_id=UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
        version_number=3,
        schema_version="coverage-rule-v1",
        rule_kind="fixed_amount",
        required=True,
        input_field_paths=("MedicalEvent.event_date",),
        rule_document={"schema_version": "coverage-rule-v1", "operator": "equals"},
        result_reason_code="DIAGNOSIS_MATCHED",
        review_state="USER_CONFIRMED",
        executable=True,
        generator_version="generator-v1",
        verifier_version="verifier-v1",
        published_at=datetime(2026, 8, 25, tzinfo=UTC),
        evidence=(evidence,),
    )
    return result, policy, rule, evidence


def _calculation() -> SimpleNamespace:
    return SimpleNamespace(
        calculation_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        claim_candidate_id=UUID("99999999-9999-4999-8999-999999999999"),
        rule_version_id=RULE_ID,
        kind="fixed",
        status="computed",
        confirmed=SimpleNamespace(amount=Decimal("120000"), currency="KRW"),
        additional=None,
        excluded=None,
        deductible=None,
        applied_rate=None,
        applied_limit=None,
        steps=(),
        hold_reason_codes=(),
        excluded_reason_codes=(),
        evidence_ids=(EVIDENCE_ID,),
        engine_version="benefit-calculation-v1",
        version=2,
        # Receipt lines are deliberately not part of the source calculation.
    )


def test_build_claim_snapshot_keeps_versions_and_excludes_raw_medical_fields() -> None:
    result, policy, rule, evidence = _result()

    snapshot = build_claim_snapshot(
        result,
        _calculation(),
        policy_snapshot=policy,
        rule_versions=(rule,),
        evidence=(evidence,),
        snapshot_version=1,
    )

    assert snapshot.snapshot_version == 1
    assert snapshot.snapshot_sha256 == snapshot_sha256(snapshot.payload())
    assert snapshot.candidate_snapshot["run_id"] == str(RUN_ID)
    assert snapshot.candidate_snapshot["candidates"][0]["version"] == 2
    assert snapshot.rule_snapshot["versions"][0]["version_number"] == 3
    assert snapshot.policy_snapshot["snapshots"][0]["policy_id"] == str(policy.policy_id)
    assert snapshot.evidence_snapshot["evidence"][0]["physical_page"] == 3
    assert snapshot.calculation_snapshot["calculations"][0]["confirmed"]["amount"] == "120000"
    assert snapshot.calculation_snapshot["calculations"][0]["version"] == 2
    assert snapshot.calculation_snapshot["calculations"][0]["evidence_ids"] == (
        str(EVIDENCE_ID),
    )
    serialized = canonical_json(snapshot.payload())
    assert "synthetic clinical detail" not in serialized
    assert "situation" not in serialized
    assert "facts" not in serialized
    assert "receipt" not in serialized
    assert "source_path" not in serialized


def test_snapshot_hash_is_deterministic_for_mapping_order_and_decimal_values() -> None:
    first = {"z": Decimal("1.00"), "a": {"y": 2, "x": True}}
    second = {"a": {"x": True, "y": 2}, "z": Decimal("1.00")}

    assert canonical_json(first) == canonical_json(second)
    assert snapshot_sha256(first) == snapshot_sha256(second)
    assert len(snapshot_sha256(first)) == 64


@pytest.mark.parametrize(
    "forbidden_key",
    [
        "medical_text",
        "source_path",
        "file_name",
        "ocr_text",
        "receipt_number",
        "receipt_note",
        "diagnosis",
    ],
)
def test_canonicalization_rejects_forbidden_snapshot_keys(forbidden_key: str) -> None:
    with pytest.raises(SnapshotPrivacyError) as error:
        canonical_json({"safe": {forbidden_key: "synthetic"}})

    assert error.value.code == "FORBIDDEN_SNAPSHOT_FIELD"
    assert forbidden_key not in str(error.value)


def test_snapshot_payload_is_deeply_immutable_and_detached_from_sources() -> None:
    result, policy, rule, evidence = _result()
    snapshot = build_claim_snapshot(
        result,
        _calculation(),
        policy_snapshot=policy,
        rule_versions=(rule,),
        evidence=(evidence,),
    )

    with pytest.raises(TypeError):
        snapshot.candidate_snapshot["run_id"] = "changed"  # type: ignore[index]
    with pytest.raises(TypeError):
        snapshot.candidate_snapshot["candidates"][0]["version"] = 99  # type: ignore[index]

    # The dataclass is frozen and nested sources are copied before freezing.
    with pytest.raises((AttributeError, TypeError)):
        snapshot.snapshot_version = 2  # type: ignore[misc]
    assert asdict(snapshot)["snapshot_version"] == 1


def test_forbidden_fields_in_mapping_inputs_are_rejected_before_snapshot_build() -> None:
    result, policy, rule, evidence = _result()
    unsafe_rule = {"id": str(rule.id), "rule_document": {"source_path": "/outside"}}

    with pytest.raises(SnapshotPrivacyError) as error:
        build_claim_snapshot(
            result,
            _calculation(),
            policy_snapshot=policy,
            rule_versions=(unsafe_rule,),
            evidence=(evidence,),
        )

    assert error.value.code == "FORBIDDEN_SNAPSHOT_FIELD"
