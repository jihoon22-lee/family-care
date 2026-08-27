"""Repository projections for synthetic inventory components."""

from __future__ import annotations

from uuid import UUID

from familycare_api.insurance_documents.domain import InventoryComponent
from familycare_api.insurance_documents.repository import _synthetic_component

ITEM_ID = UUID("00000000-0000-4000-8000-000000000951")
VERSION_ID = UUID("00000000-0000-4000-8000-000000000952")


def test_synthetic_component_is_full_document_suggested_and_duplicate_aware() -> None:
    component = _synthetic_component(
        {
            "document_batch_item_id": ITEM_ID,
            "document_version_id": VERSION_ID,
            "content_sha256": "a" * 64,
            "document_kind": "terms",
            "page_count": 7,
            "same_member_source_count": 2,
            "has_cross_member_copy": True,
        }
    )

    assert component == InventoryComponent(
        id=None,
        document_batch_item_id=ITEM_ID,
        document_version_id=VERSION_ID,
        content_sha256="a" * 64,
        role="terms",
        page_start=1,
        page_end=7,
        review_state="SUGGESTED",
        processing_state="READY",
        duplicate_state="CROSS_MEMBER_COPY_POSSIBLE",
    )
