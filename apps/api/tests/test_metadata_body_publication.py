"""V3 publication retains its own validator and the original source scope."""

import hashlib
import json
from typing import Any

import psycopg
import pytest
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_document_metadata_publication import (
    publication_database as publication_database,
)
from apps.api.tests.test_document_metadata_publication import (
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_document_metadata_repository import _seed, _source
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


def test_v3_publication_uses_its_own_validator_revision(publication_database: Any) -> None:
    url, job, generation = _seed(publication_database)
    source = _source(job)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
        payload = metadata_proposal(source, generation, identity)
        payload["revision"] = "document-metadata-v3"
        component = payload["components"][0]
        lineage = source.to_dict()["lineage"]
        component["identity"] = hashlib.sha256(
            json.dumps(
                [
                    payload["revision"],
                    lineage["document_version_id"],
                    lineage["extraction_id"],
                    lineage["source_payload_sha256"],
                    lineage["ocr_revision"],
                    component["role"],
                    1,
                    1,
                ],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        component["range_evidence"] = [
            {
                "page_number": 1,
                "basis": "FORMAL_METADATA",
                "previous_page": None,
                "article_numbers": [],
                "article_sequence_verified": False,
                "role_span_indices": list(range(len(component["role_spans"]))),
            }
        ]
        connection.execute(
            "INSERT INTO document_metadata_proposals("
            "generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v3','PREPARED',1,%s)",
            (generation, Jsonb(payload)),
        )
    assert DocumentMetadataProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        publication = connection.execute("SELECT * FROM document_metadata_publications").fetchone()
        assert publication["outcome"] == "APPLIED"
        assert publication["validator_revision"] == "document-metadata-api-v3"
    assert DocumentMetadataProjector(url).project_pending() == 0


def test_body_range_refines_a_v2_cover_and_preserves_the_original_edition(
    publication_database: Any,
) -> None:
    from familycare_api.clauses.component_editions import ComponentTermsProjector

    from apps.api.tests.test_document_metadata_validation import _legacy_identity

    url, job, generation = _seed(publication_database)
    source = _source(job)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
        payload = metadata_proposal(source, generation, identity)
        payload["revision"] = "document-metadata-v2"
        _legacy_identity(
            payload["components"][0], source.to_dict(), revision="document-metadata-v2"
        )
        connection.execute(
            "INSERT INTO document_metadata_proposals("
            "generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v2','PREPARED',1,%s)",
            (generation, Jsonb(payload)),
        )
    projector = DocumentMetadataProjector(url)
    assert projector.project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        old_publication = connection.execute(
            "SELECT * FROM document_metadata_publications"
        ).fetchone()
        old_edition = connection.execute("SELECT * FROM terms_editions").fetchone()
        connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s", (job.document_version_id,)
        )
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "VALUES(%s,2,600,800,200,1,0,1,'TEXT_SUFFICIENT')",
            (job.extraction_id,),
        )
    extended = build_document_structure(
        {
            "document_version_id": str(job.document_version_id),
            "content_sha256": "a" * 64,
            "pages": [
                {
                    "page_number": number,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [{"text": text, "reading_order": 0, "bbox": [10, 10, 400, 180]}],
                    "tables": [],
                }
                for number, text in enumerate(
                    (
                        "보험약관\n보험사: Sample Assurance\n상품코드: 001-SAMPLE",
                        "제1조 (계약 당사자)\n회사는 계약자와 이 보험계약을 체결합니다.",
                    ),
                    1,
                )
            ],
        },
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-body-v3",
    )
    DocumentStructureRepository(url).prepare(
        household_space_id=job.household_space_id,
        family_member_id=job.family_member_id,
        batch_item_id=job.batch_item_id,
        structure=extended,
        plan=plan_structure_chunks(
            extended, max_content_chars=4096, max_context_chars=4096, max_chunks=10
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-body-worker")
    assert projector.project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT * FROM document_metadata_publications WHERE id=%s", (old_publication["id"],)
            ).fetchone()
            == old_publication
        )
        assert (
            connection.execute(
                "SELECT * FROM terms_editions WHERE id=%s", (old_edition["id"],)
            ).fetchone()
            == old_edition
        )
        current = connection.execute(
            "SELECT c.page_start,c.page_end,p.validator_revision "
            "FROM insurance_document_components c "
            "JOIN document_metadata_publications p ON p.id=c.metadata_publication_id "
            "WHERE c.superseded_by_component_id IS NULL"
        ).fetchall()
        assert current == [
            {"page_start": 1, "page_end": 2, "validator_revision": "document-metadata-api-v3"}
        ]
        assert connection.execute("SELECT count(*) AS n FROM terms_editions").fetchone()["n"] == 2
    assert projector.project_pending() == 0
