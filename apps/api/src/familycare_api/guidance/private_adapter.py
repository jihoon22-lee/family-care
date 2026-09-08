"""Retain private publication authority and actual IDs in the common guidance input."""

from dataclasses import fields
from uuid import UUID

from familycare_api.common.coverage_identity import CanonicalCoverageRef
from familycare_api.decisions.knowledge_domain import KnowledgeCitation, KnowledgeDecisionContext
from familycare_api.guidance.domain import (
    GuidanceCalculationInput,
    GuidanceCitation,
    GuidanceContext,
    GuidanceCoverageInput,
    GuidanceRuleInput,
)
from familycare_api.guidance.models import (
    GuidanceContractAmount,
    GuidanceEvidence,
    GuidancePrivateCertificate,
    GuidanceSourceReference,
    GuidanceVersions,
)
from familycare_api.guidance.trace_projection import decimal_text


def _citations(
    values: tuple[KnowledgeCitation, ...], publication: UUID
) -> tuple[GuidanceCitation, ...]:
    return tuple(
        GuidanceCitation(
            item.citation_key,
            GuidanceEvidence(
                kind="TERMS_SECTION",
                evidence_id=item.terms_section_id,
                page_start=item.page_start,
                page_end=item.page_end,
                publication_id=publication,
                source_sha256=item.source_text_sha256,
            ),
            item.lineage_valid,
        )
        for item in values
    )


def adapt_private_guidance(context: KnowledgeDecisionContext) -> GuidanceContext:
    coverages = []
    for coverage in context.coverages:
        common = {
            item.name: getattr(coverage, item.name)
            for item in fields(GuidanceCoverageInput)
            if item.name
            not in {
                "ref",
                "rules",
                "calculation",
                "knowledge_incomplete",
                "contract_amount",
                "cases",
            }
        }
        rules = tuple(
            GuidanceRuleInput(
                publication_id=rule.publication_id,
                rule_key=rule.rule_key,
                rule_kind=rule.rule_kind,
                required=rule.required,
                result_reason_code=rule.result_reason_code,
                rule_document=rule.rule_document,
                citations=_citations(rule.citations, rule.publication_id),
                source_kind="PRIVATE_RULE_PUBLICATION",
            )
            for rule in coverage.rules
        )
        publication = coverage.calculation
        calculation = (
            None
            if publication is None
            else GuidanceCalculationInput(
                publication_id=publication.publication_id,
                calculation_key=publication.calculation_key,
                calculation_kind=publication.calculation_kind,
                result_reason_code=publication.result_reason_code,
                calculation_document=publication.calculation_document,
                citations=_citations(publication.citations, publication.publication_id),
                source_kind="PRIVATE_RULE_PUBLICATION",
            )
        )
        coverages.append(
            GuidanceCoverageInput(
                **common,
                ref=CanonicalCoverageRef(
                    kind="PRIVATE_KNOWLEDGE_COVERAGE",
                    contract_id=coverage.knowledge_contract_id,
                    coverage_id=coverage.knowledge_coverage_id,
                ),
                rules=rules,
                calculation=calculation,
                contract_amount=GuidanceContractAmount(
                    amount=decimal_text(coverage.insured_amount),
                    currency=coverage.currency,
                    amount_authority=(
                        "DOCUMENT_REVIEWED"
                        if coverage.certificate_amount_decision == "MATCH"
                        and coverage.certificate_amount_evidence_state == "DIRECT"
                        and coverage.certificate_evidence
                        else "UNCONFIRMED"
                    ),
                    # Currency is a separately reviewed catalog field; an unresolved
                    # certificate amount must not erase a known currency.
                    currency_authority="DOCUMENT_REVIEWED" if coverage.currency else "UNCONFIRMED",
                    source_refs=(
                        GuidanceSourceReference(
                            source_kind="PRIVATE_COVERAGE",
                            source_id=str(coverage.knowledge_coverage_id),
                            version=str(context.knowledge_import_run_id),
                            digest_sha256=context.status_projection_digest_sha256,
                        ),
                        GuidanceSourceReference(
                            source_kind="PRIVATE_IMPORT",
                            source_id=str(context.knowledge_import_run_id),
                        ),
                    ),
                    evidence=tuple(
                        GuidancePrivateCertificate(
                            catalog_import_run_id=context.knowledge_import_run_id,
                            coverage_id=coverage.knowledge_coverage_id,
                            document_alias=item.document_alias,
                            page_start=page,
                            page_end=page,
                        )
                        for item in coverage.certificate_evidence
                        for page in item.evidence_pages
                    ),
                ),
            )
        )
    return GuidanceContext(
        household_space_id=context.household_space_id,
        family_member_id=context.family_member_id,
        coverages=tuple(coverages),
        normalizers=context.normalizers,
        supporting_facts=context.supporting_facts,
        receipt_currency=context.receipt_currency,
        versions=GuidanceVersions(
            catalog_import_run_id=context.knowledge_import_run_id,
            rule_import_run_id=context.rule_import_run_id,
            status_digest=context.status_projection_digest_sha256,
        ),
    )
