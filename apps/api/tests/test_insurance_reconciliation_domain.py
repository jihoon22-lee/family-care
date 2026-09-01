"""Closed-state rules for the member insurance reconciliation projection."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from familycare_api.insurance_documents.domain import UnreadableSource
from familycare_api.insurance_reconciliation.domain import (
    KnowledgeContractSource,
    OperationalLinkHistory,
    OperationalPolicySource,
    build_member_reconciliation,
)

MEMBER_ID = UUID("00000000-0000-4000-8000-000000002501")
RUN_ID = UUID("00000000-0000-4000-8000-000000002502")
CONTRACT_A = UUID("00000000-0000-4000-8000-000000002503")
CONTRACT_B = UUID("00000000-0000-4000-8000-000000002504")
CONTRACT_C = UUID("00000000-0000-4000-8000-000000002505")
CONTRACT_D = UUID("00000000-0000-4000-8000-000000002506")
POLICY_A = UUID("00000000-0000-4000-8000-000000002507")
POLICY_B = UUID("00000000-0000-4000-8000-000000002508")
POLICY_D = UUID("00000000-0000-4000-8000-000000002509")
POLICY_ORPHAN = UUID("00000000-0000-4000-8000-000000002510")


def _contract(
    contract_id: UUID,
    *,
    snapshot_policy_id: UUID | None = None,
    snapshot_decision: str = "UNKNOWN",
) -> KnowledgeContractSource:
    return KnowledgeContractSource(
        id=contract_id,
        insurer_display="Sample Insurer",
        product_display=f"Sample Policy {str(contract_id)[-1]}",
        certificate_decision="MATCH",
        current_status="unknown",
        snapshot_policy_contract_id=snapshot_policy_id,
        snapshot_operational_decision=snapshot_decision,
        snapshot_operational_reason_code="SYNTHETIC_SNAPSHOT_DECISION",
    )


def _policy(
    policy_id: UUID,
    *,
    completeness: str,
) -> OperationalPolicySource:
    return OperationalPolicySource(
        id=policy_id,
        insurer_display="Sample Insurer",
        product_display=f"Operational Policy {str(policy_id)[-1]}",
        status="unknown",
        completeness=completeness,
        has_product_explanation=False,
        has_application=False,
    )


def _link(
    contract_id: UUID,
    *,
    decision: str,
    policy_id: UUID | None,
    conflict: bool = False,
) -> OperationalLinkHistory:
    return OperationalLinkHistory(
        id=UUID(f"00000000-0000-4000-8000-{str(contract_id.int)[-12:].zfill(12)}"),
        knowledge_contract_id=contract_id,
        policy_contract_id=policy_id,
        decision=decision,
        conflict=conflict,
        authority="USER_CONFIRMED_OPERATIONAL_IDENTITY",
        reason_code="SYNTHETIC_USER_DECISION",
        confirmed_at=datetime(2026, 9, 1, tzinfo=UTC),
    )


def test_projection_partitions_every_contract_and_keeps_document_work_independent() -> None:
    unresolved = (
        UnreadableSource(
            document_batch_item_id=UUID("00000000-0000-4000-8000-000000002511"),
            source_kind="policy",
            display_label="보험증권 문서",
            processing_state="PASSWORD_REQUIRED",
        ),
        UnreadableSource(
            document_batch_item_id=UUID("00000000-0000-4000-8000-000000002512"),
            source_kind="terms",
            display_label="보험약관 문서",
            processing_state="OCR_REQUIRED",
        ),
    )

    result = build_member_reconciliation(
        member_id=MEMBER_ID,
        knowledge_run_id=RUN_ID,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        contracts=(
            _contract(CONTRACT_A),
            _contract(
                CONTRACT_B,
                snapshot_policy_id=POLICY_B,
                snapshot_decision="MATCH",
            ),
            _contract(CONTRACT_C),
            _contract(CONTRACT_D),
        ),
        current_links=(
            _link(CONTRACT_A, decision="MATCH", policy_id=POLICY_A),
            _link(CONTRACT_D, decision="UNKNOWN", policy_id=None, conflict=True),
        ),
        operational_policies=(
            _policy(POLICY_A, completeness="CERTIFICATE_AND_TERMS"),
            _policy(POLICY_B, completeness="CERTIFICATE_ONLY"),
            _policy(POLICY_D, completeness="CERTIFICATE_ONLY"),
            _policy(POLICY_ORPHAN, completeness="CERTIFICATE_AND_TERMS"),
        ),
        unresolved_sources=unresolved,
    )

    assert [item.reconciliation_state for item in result.contracts] == [
        "EVIDENCE_READY",
        "DOCUMENTS_PENDING",
        "LINK_REVIEW_REQUIRED",
        "CONFLICT",
    ]
    assert result.contracts[0].operational_link.authority == ("USER_CONFIRMED_OPERATIONAL_IDENTITY")
    assert result.contracts[1].operational_link.authority == "SNAPSHOT_EXACT_EVIDENCE"
    assert result.contracts[1].document_readiness.completeness == "CERTIFICATE_ONLY"
    assert result.contracts[2].document_readiness is None
    assert result.summary.total_contracts == 4
    assert result.summary.evidence_ready_contracts == 1
    assert result.summary.documents_pending_contracts == 1
    assert result.summary.link_review_required_contracts == 1
    assert result.summary.conflict_contracts == 1
    assert (
        result.summary.evidence_ready_contracts
        + result.summary.documents_pending_contracts
        + result.summary.link_review_required_contracts
        + result.summary.conflict_contracts
        == result.summary.total_contracts
    )
    assert result.summary.orphan_operational_contracts == 2
    assert {item.policy_contract_id for item in result.orphan_operational_contracts} == {
        POLICY_D,
        POLICY_ORPHAN,
    }
    assert result.summary.unresolved_unreadable_sources == 2
    assert result.unresolved_sources == unresolved


def test_current_history_supersedes_snapshot_binding_without_mutating_snapshot() -> None:
    result = build_member_reconciliation(
        member_id=MEMBER_ID,
        knowledge_run_id=RUN_ID,
        generated_at=datetime(2026, 9, 1, tzinfo=UTC),
        contracts=(
            _contract(
                CONTRACT_A,
                snapshot_policy_id=POLICY_A,
                snapshot_decision="MATCH",
            ),
        ),
        current_links=(_link(CONTRACT_A, decision="UNKNOWN", policy_id=None),),
        operational_policies=(_policy(POLICY_A, completeness="CERTIFICATE_AND_TERMS"),),
        unresolved_sources=(),
    )

    assert result.contracts[0].reconciliation_state == "LINK_REVIEW_REQUIRED"
    assert result.contracts[0].operational_link.decision == "UNKNOWN"
    assert result.contracts[0].operational_link.authority == ("USER_CONFIRMED_OPERATIONAL_IDENTITY")
    assert result.orphan_operational_contracts[0].policy_contract_id == POLICY_A


def test_projection_rejects_duplicate_current_links_and_duplicate_sources() -> None:
    contract = _contract(CONTRACT_A)
    link = _link(CONTRACT_A, decision="MATCH", policy_id=POLICY_A)
    policy = _policy(POLICY_A, completeness="CERTIFICATE_ONLY")

    with pytest.raises(ValueError, match="duplicate current operational link"):
        build_member_reconciliation(
            member_id=MEMBER_ID,
            knowledge_run_id=RUN_ID,
            generated_at=datetime(2026, 9, 1, tzinfo=UTC),
            contracts=(contract,),
            current_links=(link, link),
            operational_policies=(policy,),
            unresolved_sources=(),
        )

    with pytest.raises(ValueError, match="duplicate operational policy"):
        build_member_reconciliation(
            member_id=MEMBER_ID,
            knowledge_run_id=RUN_ID,
            generated_at=datetime(2026, 9, 1, tzinfo=UTC),
            contracts=(contract,),
            current_links=(link,),
            operational_policies=(policy, policy),
            unresolved_sources=(),
        )
