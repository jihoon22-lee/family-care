"""Retained synthetic amendments change event terms without rewriting enrollment."""

import os
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.terms_applicability_repository import TermsApplicabilityProjector
from familycare_api.clauses.terms_change_repository import TermsChangeProjector, read_event_terms
from familycare_api.clauses.terms_change_selection import TermsSelectionScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_api.policies.range_enrollment import RangeEnrollmentProjector
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from apps.api.tests.terms_change_seed import retain_terms_change_policy
from apps.api.tests.test_range_enrollment_integration import (
    _psycopg_url,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def changes_database(request: pytest.FixtureRequest) -> Any:
    # Root integration hooks validate this dedicated URL before fixture setup.
    url = os.environ["FAMILYCARE_TEST_DATABASE_URL"]
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("TRUNCATE household_spaces,documents CASCADE")
    url, job = request.getfixturevalue("enrollment_database")
    try:
        yield url, job
    finally:
        with psycopg.connect(_psycopg_url(url)) as connection:
            connection.execute("TRUNCATE household_spaces,documents CASCADE")


def _add_document(url: str, job: Any, text: str, *, kind: str, digest: str) -> None:
    document, version, extraction, item = (uuid4() for _ in range(4))
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        batch = connection.execute(
            "SELECT batch_id FROM document_batch_items WHERE id=%s", (job.batch_item_id,)
        ).fetchone()["batch_id"]
        connection.execute(
            "INSERT INTO documents(id,source_key,document_kind,status,page_count) "
            "VALUES(%s,%s,%s,'ready',1)",
            (document, f"synthetic/{document}.pdf", kind),
        )
        connection.execute(
            "INSERT INTO document_versions(id,document_id,version_number,content_sha256,"
            "byte_size,page_count) "
            "VALUES(%s,%s,1,%s,128,1)",
            (version, document, digest),
        )
        connection.execute(
            "INSERT INTO extractions(id,document_version_id,extractor_name,extractor_version,"
            "extractor_config_hash,quality_rule_version,status,succeeded_at) "
            "VALUES(%s,%s,'synthetic','v1',%s,'quality-v1','succeeded',clock_timestamp())",
            (extraction, version, "f" * 64),
        )
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "VALUES(%s,1,612,792,200,0.8,0,1,'TEXT_SUFFICIENT')",
            (extraction,),
        )
        connection.execute(
            "INSERT INTO document_batch_items(id,batch_id,document_id,source_id,source_key,"
            "display_label,document_kind,state,processed_document_version_id,completed_at) "
            "VALUES(%s,%s,%s,%s,%s,'Sample Source',%s,'succeeded',%s,clock_timestamp())",
            (
                item,
                batch,
                document,
                digest,
                f"synthetic/{document}.pdf",
                "supporting" if kind == "amendment" else kind,
                version,
            ),
        )
    structure = build_document_structure(
        {
            "document_version_id": str(version),
            "content_sha256": digest,
            "pages": [
                {
                    "page_number": 1,
                    "quality": {"classification": "TEXT_SUFFICIENT"},
                    "blocks": [{"text": text, "reading_order": 0, "bbox": [10, 10, 500, 700]}],
                    "tables": [],
                }
            ],
        },
        extraction_id=extraction,
        extraction_revision="synthetic-terms-change-v1",
    )
    DocumentStructureRepository(url).prepare(
        household_space_id=job.household_space_id,
        family_member_id=job.family_member_id,
        batch_item_id=item,
        structure=structure,
        plan=plan_structure_chunks(
            structure, max_content_chars=4096, max_context_chars=4096, max_chunks=10
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1


def _sources(
    url: str,
    job: Any,
    *,
    missing_date: bool = False,
    terms_body: str = "제1조 목적",
    new_terms_body: str | None = None,
    change_scope: str = "특약",
    change_fields: tuple[str, ...] = (),
    sample_amount: int = 317,
) -> dict[str, Any]:
    retain_terms_change_policy(url, job, sample_amount=sample_amount)
    assert RangeEnrollmentProjector(url).project_pending() == 3
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    for code, digest in (("A", "b" * 64), ("B", "c" * 64)):
        body = new_terms_body if code == "B" and new_terms_body is not None else terms_body
        _add_document(
            url,
            job,
            (
                "보험약관\n보험사: Sample Insurer\n상품명: Sample Plan\n상품코드: SAMPLE-P\n"
                f"약관코드: TERMS-{code}\n판본코드: EDITION-{code}\n{body}"
            ),
            kind="terms",
            digest=digest,
        )
    assert ComponentTermsProjector(url).project_pending() == 2
    assert TermsApplicabilityProjector(url).refresh_pending() == 1
    _add_document(
        url,
        job,
        "\n".join(
            (
                "계약변경서",
                "계약번호: synthetic-policy-001",
                "피보험자: Family Member A",
                "보험사: Sample Insurer",
                "변경구분: 조건변경",
                "변경방식: 교체",
                f"적용범위: {change_scope}",
                "대상특약명: Sample Rider",
                "변경전약관코드: TERMS-A",
                "변경전판본코드: EDITION-A",
                "변경후약관코드: TERMS-B",
                "변경후판본코드: EDITION-B",
                "변경적용일:" if missing_date else "변경적용일: 2025-07-01",
                *change_fields,
            )
        ),
        kind="amendment",
        digest="d" * 64,
    )
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        editions = connection.execute(
            "SELECT e.id,c.proof_json FROM terms_editions e JOIN "
            "terms_applicability_component_sources c ON c.id=e.source_component_id"
        ).fetchall()
        riders = connection.execute(
            "SELECT id,policy_contract_id,display_name FROM riders"
        ).fetchall()
        return {
            **{
                next(
                    f["value"] for f in e["proof_json"]["facts"] if f["field"] == "edition_code"
                ): e["id"]
                for e in editions
            },
            **{r["display_name"]: r["id"] for r in riders},
            "policy_id": riders[0]["policy_contract_id"],
        }


def test_change_is_append_only_scoped_and_selected_by_event_date(changes_database: Any) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    projector = TermsChangeProjector(url)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        component = connection.execute(
            "SELECT id FROM insurance_document_components WHERE role='amendment'"
        ).fetchone()["id"]
        assert projector._refresh(connection, component, job.household_space_id)
    assert projector.refresh_pending() == 0
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute("SELECT * FROM policy_terms_changes").fetchone()
        assert row["status"] == "MATCH" and row["rider_id"] == sources["Sample Rider"]
        scope = TermsSelectionScope(
            job.household_space_id,
            sources["policy_id"],
            job.family_member_id,
            sources["Sample Rider"],
        )
        june = read_event_terms(connection, scope, date(2025, 6, 30))
        july = read_event_terms(connection, scope, date(2025, 7, 1))
        assert {e.edition_id for e in june.editions if e.status == "MATCH"} == {
            sources["EDITION-A"]
        }
        assert {e.edition_id for e in july.editions if e.status == "MATCH"} == {
            sources["EDITION-B"]
        }
        other = read_event_terms(
            connection,
            TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Another Rider"],
            ),
            date(2025, 7, 1),
        )
        assert {e.edition_id for e in other.editions if e.status == "MATCH"} == {
            sources["EDITION-A"]
        }
        assert july.applied_relation_ids == (row["id"],)
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("UPDATE policy_terms_changes SET effective_from='2025-01-01'")
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute("DELETE FROM policy_terms_changes")
        assert connection.execute("SELECT count(*) AS n FROM policy_contracts").fetchone()["n"] == 1
        assert connection.execute("SELECT count(*) AS n FROM riders").fetchone()["n"] == 2


def test_missing_change_date_withholds_only_the_identified_rider(changes_database: Any) -> None:
    url, job = changes_database
    sources = _sources(url, job, missing_date=True)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        scope = TermsSelectionScope(
            job.household_space_id,
            sources["policy_id"],
            job.family_member_id,
            sources["Sample Rider"],
        )
        selection = read_event_terms(connection, scope, date(2025, 7, 1))
        assert selection.uncertain_relation_ids
        assert all(e.status == "UNKNOWN" for e in selection.editions)
        other = read_event_terms(
            connection,
            TermsSelectionScope(
                job.household_space_id,
                sources["policy_id"],
                job.family_member_id,
                sources["Another Rider"],
            ),
            date(2025, 7, 1),
        )
        assert {e.edition_id for e in other.editions if e.status == "MATCH"} == {
            sources["EDITION-A"]
        }
