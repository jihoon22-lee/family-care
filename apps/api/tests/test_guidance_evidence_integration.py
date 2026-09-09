"""Saved-reference membership precedes original/summary disclosure in PostgreSQL."""

from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from familycare_api.decisions.errors import EvidenceNotFound, MedicalEventNotFound
from familycare_api.guidance.models import GuidanceSemanticEvidence, LocalGuidanceResponse
from familycare_api.guidance_evidence.models import GuidanceEvidenceRequest
from familycare_api.guidance_evidence.repository import GuidanceEvidenceRepository

from apps.api.tests.test_guidance_claim_concurrency_integration import (
    _psycopg_url,
)
from apps.api.tests.test_guidance_claim_concurrency_integration import (
    saved_guidance_source as saved_guidance_source,
)
from apps.api.tests.test_guidance_review_projection_integration import _project, _read, _stage
from apps.api.tests.test_guidance_review_reassessment_integration import (
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_guidance_review_reassessment_integration import (
    unreviewed_original as unreviewed_original,
)

pytestmark = pytest.mark.integration


def _evidence(candidate):
    pending = [candidate.model_dump(mode="json")]
    found = []
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if value.get("kind") in {"OPERATIONAL_EVIDENCE", "TERMS_SECTION", "SEMANTIC_CITATION"}:
                found.append(value)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return found


def _saved_request(source):
    guidance = LocalGuidanceResponse.model_validate(source.snapshot)
    candidate = next(c for c in guidance.candidates if c.ref == source.ref)
    kind = (
        "TERMS_SECTION"
        if source.ref.kind == "PRIVATE_KNOWLEDGE_COVERAGE"
        else "OPERATIONAL_EVIDENCE"
    )
    reference = next(ref for ref in _evidence(candidate) if ref["kind"] == kind)
    return GuidanceEvidenceRequest(
        decision_run_id=source.run_id,
        expected_event_version=1,
        coverage=source.ref,
        evidence=reference,
    )


def test_saved_operational_original_and_private_summary_are_distinct(saved_guidance_source):
    source = saved_guidance_source
    request = _saved_request(source)
    if source.ref.kind == "OPERATIONAL_RIDER":
        # The legacy ledger seed supplies Evidence addresses but no extracted body.
        # Retain a wholly synthetic original at that exact extraction/page.
        with psycopg.connect(_psycopg_url(source.url)) as connection:
            connection.execute(
                "INSERT INTO extraction_blocks(page_id,text,bbox,reading_order) "
                "SELECT p.id,%s,'[10,10,100,30]',0 "
                "FROM evidence e JOIN extraction_pages p ON p.extraction_id=e.extraction_id "
                "AND p.page_number=e.physical_page WHERE e.id=%s",
                (
                    "Synthetic retained insurance evidence. " + "x" * 2100,
                    request.evidence.evidence_id,
                ),
            )
    value = GuidanceEvidenceRepository(source.url).get_detail(
        source.scope, source.event_id, request
    )
    assert value.evidence == request.evidence
    assert value.page_start == request.evidence.page_start
    if source.ref.kind == "PRIVATE_KNOWLEDGE_COVERAGE":
        assert value.content_kind == "SUMMARY"
        assert "EVIDENCE_PRIVATE_SUMMARY" in value.reason_codes
        assert value.source_document_ref is not None
    else:
        assert value.content_kind == "ORIGINAL"
        assert value.document_version_id is not None
        assert value.text.startswith("Synthetic retained insurance evidence.")
        assert value.truncated and len(value.text) == 2048
    assert value.text and len(value.text) <= 2048
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        assert connection.execute("SELECT count(*) FROM guidance_review_jobs").fetchone() == (0,)
        assert connection.execute("SELECT count(*) FROM claim_cases").fetchone() == (0,)


@pytest.mark.parametrize("fault", ["reference", "coverage", "run", "household"])
def test_nonmember_reference_cannot_disclose_saved_source(saved_guidance_source, fault):
    source = saved_guidance_source
    request = _saved_request(source)
    values = request.model_dump(mode="json")
    scope = source.scope
    if fault == "reference":
        values["evidence"]["page_start"] += 1
        values["evidence"]["page_end"] += 1
    elif fault == "coverage":
        values["coverage"]["coverage_id"] = str(uuid4())
    elif fault == "run":
        values["decision_run_id"] = str(uuid4())
    else:
        scope = HouseholdScope(uuid4())
    with pytest.raises((EvidenceNotFound, MedicalEventNotFound)):
        GuidanceEvidenceRepository(source.url).get_detail(
            scope, source.event_id, GuidanceEvidenceRequest.model_validate(values)
        )


def test_review_citation_discloses_exact_retained_original_and_deleted_source_is_unavailable(
    unreviewed_original,
):
    sample = unreviewed_original
    _stage(sample)
    assert _project(sample) == 1
    review = _read(sample)
    candidate = next(
        c for c in review.result.guidance.candidates if c.ref == sample.packet.coverage_ref
    )
    reference = next(ref for ref in _evidence(candidate) if ref["kind"] == "SEMANTIC_CITATION")
    request = GuidanceEvidenceRequest(
        decision_run_id=sample.original.run_id,
        expected_event_version=sample.event.version,
        coverage=candidate.ref,
        evidence=reference,
        review_job_id=review.id,
    )
    repository = GuidanceEvidenceRepository(sample.url)
    detail = repository.get_detail(sample.scope, sample.event.id, request)
    assert detail.content_kind == "ORIGINAL"
    assert detail.text in {c["text"] for c in sample.graph["citations"]}
    assert detail.document_version_id == UUID(reference["document_version_id"])
    assert detail.bbox == tuple(reference["bbox"])
    assert isinstance(detail.evidence, GuidanceSemanticEvidence)
    with pytest.raises(EvidenceNotFound):
        repository.get_detail(
            sample.scope, sample.event.id, request.model_copy(update={"review_job_id": None})
        )
    with psycopg.connect(_psycopg_url(sample.url)) as connection:
        connection.execute(
            "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=(SELECT document_id "
            "FROM document_versions WHERE id=%s)",
            (detail.document_version_id,),
        )
    missing = repository.get_detail(sample.scope, sample.event.id, request)
    assert missing.content_kind == "UNAVAILABLE" and missing.text is None


@pytest.mark.parametrize("saved_guidance_source", ["operational"], indirect=True)
def test_table_cell_original_and_missing_original_do_not_fall_back_to_editable_clause(
    saved_guidance_source,
):
    source = saved_guidance_source
    request = _saved_request(source)
    repository = GuidanceEvidenceRepository(source.url)
    before = repository.get_detail(source.scope, source.event_id, request)
    assert before.content_kind == "UNAVAILABLE" and before.text is None
    with psycopg.connect(_psycopg_url(source.url)) as connection:
        table = connection.execute(
            "INSERT INTO extraction_tables(page_id,bbox,metadata_json) "
            "SELECT p.id,'[10,10,100,50]','{}' FROM evidence e JOIN extraction_pages p "
            "ON p.extraction_id=e.extraction_id AND p.page_number=e.physical_page "
            "WHERE e.id=%s RETURNING id",
            (request.evidence.evidence_id,),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO extraction_cells(table_id,row_index,column_index,text,bbox) "
            "VALUES(%s,0,0,'Synthetic table benefit','[10,10,100,50]')",
            (table,),
        )
    after = repository.get_detail(source.scope, source.event_id, request)
    assert after.content_kind == "ORIGINAL" and after.text == "Synthetic table benefit"


def test_global_semantic_citation_uses_its_exact_publication_and_original(unreviewed_original):
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    sample = unreviewed_original
    envelope = sample.packet.to_payload()["envelope"]
    TermsSemanticRepository(sample.url).publish_candidate(
        sample.scope,
        sample.inventory["EDITION-A"],
        sample.graph,
        expected_input_digest=envelope["input_digest"],
    )
    run = sample.service.analyze_medical_event(sample.event.id)
    candidate = next(
        c for c in run.local_guidance.candidates if c.ref == sample.packet.coverage_ref
    )
    reference = next(ref for ref in _evidence(candidate) if ref["kind"] == "SEMANTIC_CITATION")
    assert reference["review_job_id"] is None
    request = GuidanceEvidenceRequest(
        decision_run_id=run.run_id,
        expected_event_version=sample.event.version,
        coverage=candidate.ref,
        evidence=reference,
    )
    detail = GuidanceEvidenceRepository(sample.url).get_detail(
        sample.scope, sample.event.id, request
    )
    assert detail.content_kind == "ORIGINAL"
    assert detail.text in {c["text"] for c in sample.graph["citations"]}
