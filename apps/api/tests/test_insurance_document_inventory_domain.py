"""Pure insurance-document inventory semantics with synthetic metadata only."""

from __future__ import annotations

from uuid import UUID

import pytest
from familycare_api.insurance_documents.domain import (
    InventoryComponent,
    InventoryPolicy,
    InventorySet,
    InventorySetItem,
    UnreadableSource,
    build_member_inventory,
)

MEMBER_ID = UUID("00000000-0000-4000-8000-000000000101")
POLICY_ID = UUID("00000000-0000-4000-8000-000000000201")
POLICY_VERSION_ID = UUID("00000000-0000-4000-8000-000000000301")


def _component(
    suffix: int,
    role: str,
    *,
    content: str | None = None,
    document_version_id: UUID | None = None,
    page_start: int = 1,
    page_end: int = 2,
) -> InventoryComponent:
    return InventoryComponent(
        id=UUID(f"00000000-0000-4000-8000-{suffix:012d}"),
        document_batch_item_id=UUID(f"00000000-0000-4000-8001-{suffix:012d}"),
        document_version_id=document_version_id or UUID(f"00000000-0000-4000-8002-{suffix:012d}"),
        content_sha256=content or f"{suffix:064x}",
        role=role,  # type: ignore[arg-type]
        page_start=page_start,
        page_end=page_end,
        review_state="USER_CONFIRMED",
        processing_state="READY",
        duplicate_state="UNIQUE",
    )


def _policy() -> InventoryPolicy:
    return InventoryPolicy(
        id=POLICY_ID,
        source_document_version_id=POLICY_VERSION_ID,
        source_content_sha256="f" * 64,
        source_evidence_page=1,
        insurer_display="Sample Insurer",
        product_display="Sample Policy",
        status="unknown",
        rider_count=3,
    )


def test_registered_policy_without_reviewed_set_is_certificate_only() -> None:
    inventory = build_member_inventory(MEMBER_ID, policies=(_policy(),), document_sets=())

    assert inventory.summary.certificate_backed_policies == 1
    assert inventory.summary.certificate_only == 1
    assert inventory.summary.certificate_and_terms == 0
    registered = inventory.registered_policies[0]
    assert registered.completeness == "CERTIFICATE_ONLY"
    assert registered.missing_document_roles == ("terms",)
    assert registered.documents[0].role == "policy"
    assert registered.documents[0].source_count == 1


def test_only_confirmed_authoritative_policy_and_terms_complete_a_policy() -> None:
    policy_component = _component(
        1,
        "policy",
        content="f" * 64,
        document_version_id=POLICY_VERSION_ID,
        page_start=1,
        page_end=3,
    )
    terms = _component(
        2,
        "terms",
        content="f" * 64,
        document_version_id=POLICY_VERSION_ID,
        page_start=4,
        page_end=6,
    )
    explanation = _component(3, "product_explanation")
    application = _component(4, "application")
    document_set = InventorySet(
        id=UUID("00000000-0000-4000-8000-000000000401"),
        policy_contract_id=POLICY_ID,
        insurer_display="Sample Insurer",
        product_display="Sample Policy",
        display_label="Sample Policy",
        version=1,
        items=(
            InventorySetItem(policy_component, "USER_CONFIRMED"),
            InventorySetItem(terms, "USER_CONFIRMED"),
            InventorySetItem(explanation, "USER_CONFIRMED"),
            InventorySetItem(application, "USER_CONFIRMED"),
        ),
    )

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(_policy(),),
        document_sets=(document_set,),
    )

    registered = inventory.registered_policies[0]
    assert registered.completeness == "CERTIFICATE_AND_TERMS"
    assert registered.has_product_explanation is True
    assert registered.has_application is True
    assert registered.missing_document_roles == ()
    assert registered.documents[0].bundled_source is True
    assert registered.documents[1].bundled_source is True
    assert inventory.summary.certificate_and_terms == 1
    assert inventory.summary.product_explanation_documents == 1
    assert inventory.summary.application_documents == 1


def test_suggested_terms_and_unconfirmed_policy_component_do_not_complete_policy() -> None:
    document_set = InventorySet(
        id=UUID("00000000-0000-4000-8000-000000000402"),
        policy_contract_id=POLICY_ID,
        insurer_display="Sample Insurer",
        product_display="Sample Policy",
        display_label="Sample Policy",
        version=1,
        items=(
            InventorySetItem(
                _component(5, "policy", document_version_id=POLICY_VERSION_ID),
                "SUGGESTED",
            ),
            InventorySetItem(_component(6, "terms"), "SUGGESTED"),
        ),
    )

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(_policy(),),
        document_sets=(document_set,),
    )

    assert inventory.registered_policies[0].completeness == "CERTIFICATE_ONLY"
    assert inventory.summary.pairing_conflicts == 0


def test_unregistered_terms_and_explanation_remain_not_enrollment_authority() -> None:
    terms = _component(7, "terms", content="a" * 64)
    explanation = _component(8, "product_explanation", content="a" * 64)
    document_set = InventorySet(
        id=UUID("00000000-0000-4000-8000-000000000403"),
        policy_contract_id=None,
        insurer_display="Sample Insurer",
        product_display="Sample Material",
        display_label="Sample Material",
        version=2,
        items=(
            InventorySetItem(terms, "USER_CONFIRMED"),
            InventorySetItem(explanation, "USER_CONFIRMED"),
        ),
    )

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(),
        document_sets=(document_set,),
    )

    assert inventory.summary.certificate_backed_policies == 0
    assert inventory.summary.terms_only_documents == 1
    unregistered = inventory.unregistered_document_sets[0]
    assert unregistered.primary_classification == "TERMS_ONLY"
    assert unregistered.enrollment_confirmed is False
    assert unregistered.has_product_explanation is True
    assert unregistered.source_count == 1
    assert unregistered.component_count == 2


def test_conflict_and_duplicate_dimensions_do_not_replace_document_role() -> None:
    conflicted = _component(9, "application")
    conflicted = InventoryComponent(
        **{
            **conflicted.__dict__,
            "duplicate_state": "SAME_MEMBER_DUPLICATE",
        }
    )
    document_set = InventorySet(
        id=UUID("00000000-0000-4000-8000-000000000404"),
        policy_contract_id=None,
        insurer_display=None,
        product_display=None,
        display_label="Synthetic application",
        version=1,
        items=(InventorySetItem(conflicted, "CONFLICT"),),
    )

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(),
        document_sets=(document_set,),
        unreadable_sources=(
            UnreadableSource(
                document_batch_item_id=UUID("00000000-0000-4000-8000-000000000901"),
                source_kind="policy",
                display_label="보험증권 문서",
                processing_state="PASSWORD_REQUIRED",
            ),
            UnreadableSource(
                document_batch_item_id=UUID("00000000-0000-4000-8000-000000000902"),
                source_kind="terms",
                display_label="보험약관 문서",
                processing_state="OCR_REQUIRED",
            ),
        ),
    )

    assert inventory.summary.application_documents == 1
    assert inventory.summary.pairing_conflicts == 1
    assert inventory.summary.unreadable_documents == 2
    assert inventory.unregistered_document_sets[0].primary_classification == "APPLICATION_ONLY"
    assert inventory.unregistered_document_sets[0].items[0].component.role == "application"
    assert inventory.unreadable_sources[0].source_kind == "policy"
    assert inventory.unreadable_sources[0].processing_state == "PASSWORD_REQUIRED"


def test_unpaired_duplicate_terms_count_once_by_component_identity() -> None:
    first = _component(10, "terms", content="c" * 64, page_start=1, page_end=3)
    copy = _component(11, "terms", content="c" * 64, page_start=1, page_end=3)

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(),
        document_sets=(),
        unpaired_components=(first, copy),
    )

    assert inventory.summary.terms_only_documents == 1


def test_two_policy_components_in_one_source_are_kept_separate_and_bundled() -> None:
    policy_a = _policy()
    policy_b = InventoryPolicy(
        id=UUID("00000000-0000-4000-8000-000000000202"),
        source_document_version_id=POLICY_VERSION_ID,
        source_content_sha256="f" * 64,
        source_evidence_page=5,
        insurer_display="Sample Insurer",
        product_display="Sample Policy B",
        status="unknown",
        rider_count=1,
    )
    component_a = _component(
        12,
        "policy",
        content="f" * 64,
        document_version_id=POLICY_VERSION_ID,
        page_start=1,
        page_end=2,
    )
    component_b = _component(
        13,
        "policy",
        content="f" * 64,
        document_version_id=POLICY_VERSION_ID,
        page_start=5,
        page_end=6,
    )
    sets = (
        InventorySet(
            id=UUID("00000000-0000-4000-8000-000000000405"),
            policy_contract_id=policy_a.id,
            insurer_display="Sample Insurer",
            product_display="Sample Policy",
            display_label="Sample Policy",
            version=2,
            items=(InventorySetItem(component_a, "USER_CONFIRMED"),),
        ),
        InventorySet(
            id=UUID("00000000-0000-4000-8000-000000000406"),
            policy_contract_id=policy_b.id,
            insurer_display="Sample Insurer",
            product_display="Sample Policy B",
            display_label="Sample Policy B",
            version=2,
            items=(InventorySetItem(component_b, "USER_CONFIRMED"),),
        ),
    )

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(policy_a, policy_b),
        document_sets=sets,
    )

    assert inventory.summary.certificate_backed_policies == 2
    assert [item.policy.id for item in inventory.registered_policies] == [
        policy_a.id,
        policy_b.id,
    ]
    assert all(item.documents[0].bundled_source for item in inventory.registered_policies)


@pytest.mark.parametrize(
    ("role", "expected"),
    (
        ("product_explanation", "PRODUCT_EXPLANATION_ONLY"),
        ("application", "APPLICATION_ONLY"),
        ("policy", "POLICY_UNREVIEWED"),
        ("supporting", "SUPPORTING_ONLY"),
    ),
)
def test_unregistered_roles_never_become_enrollment_authority(
    role: str,
    expected: str,
) -> None:
    component = _component(20, role)
    document_set = InventorySet(
        id=UUID("00000000-0000-4000-8000-000000000407"),
        policy_contract_id=None,
        insurer_display=None,
        product_display=None,
        display_label="Sample unregistered material",
        version=1,
        items=(InventorySetItem(component, "USER_CONFIRMED"),),
    )

    inventory = build_member_inventory(
        MEMBER_ID,
        policies=(),
        document_sets=(document_set,),
    )

    assert inventory.summary.certificate_backed_policies == 0
    assert inventory.unregistered_document_sets[0].primary_classification == expected
    assert inventory.unregistered_document_sets[0].enrollment_confirmed is False
