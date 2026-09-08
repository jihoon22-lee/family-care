"""Publish the actual amount provenance without changing source authority."""

from familycare_api.guidance.amount_source import OperationalAmountSource
from familycare_api.guidance.models import (
    GuidanceContractAmount,
    GuidanceEvidence,
    GuidanceSourceReference,
)
from familycare_api.guidance.trace_projection import decimal_text


def operational_contract_amount(source: OperationalAmountSource) -> GuidanceContractAmount:
    evidence = {
        item.evidence_id: item for item in (*source.amount_evidence, *source.currency_evidence)
    }
    publication = source.publication_ids[0] if source.publication_ids else None
    return GuidanceContractAmount(
        amount=decimal_text(source.amount),
        currency=source.currency,
        amount_authority=source.amount_authority or "UNCONFIRMED",
        currency_authority=source.currency_authority or "UNCONFIRMED",
        source_refs=(
            GuidanceSourceReference(
                source_kind="OPERATIONAL_RIDER",
                source_id=str(source.rider_id),
                version=source.ledger_version,
                digest_sha256=source.digest_sha256,
            ),
            *(
                GuidanceSourceReference(source_kind="ENROLLMENT_PUBLICATION", source_id=str(key))
                for key in source.publication_ids
            ),
            *(
                GuidanceSourceReference(
                    source_kind="OPERATIONAL_EVIDENCE",
                    source_id=str(item.evidence_id),
                    digest_sha256=item.content_sha256,
                )
                for item in evidence.values()
            ),
        ),
        evidence=tuple(
            GuidanceEvidence(
                kind="OPERATIONAL_EVIDENCE",
                evidence_id=item.evidence_id,
                page_start=item.physical_page,
                page_end=item.physical_page,
                publication_id=publication,
                source_sha256=item.content_sha256,
            )
            for item in evidence.values()
        ),
    )
