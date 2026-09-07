"""Synthetic multi-edition registration and page-scoped Clause storage."""

from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import date
from threading import Barrier
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.repository import ClauseRepository, TermsEditionRepository
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from workers.analyzer.tests.test_document_structure_repository import (
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def component_database(request: pytest.FixtureRequest) -> Iterator[Any]:
    database = request.getfixturevalue("structure_database")
    with psycopg.connect(_psycopg_url(database[0])) as connection:
        connection.execute("TRUNCATE document_structure_generations CASCADE")
    try:
        yield database
    finally:
        with psycopg.connect(_psycopg_url(database[0])) as connection:
            connection.execute("TRUNCATE evidence CASCADE")


def _projector(url: str) -> Any:
    from familycare_api.clauses.component_editions import ComponentTermsProjector

    return ComponentTermsProjector(url)


def _seed(
    database: Any,
    *,
    metadata_suffix: str = "",
    product_name: bool = True,
    insurer: str = "Sample Assurance",
) -> tuple[str, Any]:
    url, job = database
    pages = []
    for number, year in ((1, 2024), (2, 2025)):
        pages.append(
            {
                "page_number": number,
                "quality": {"classification": "TEXT_SUFFICIENT"},
                "blocks": [
                    {
                        "text": f"보험약관\n보험사: {insurer}\n"
                        + ("상품명: Sample Product\n" if product_name else "")
                        + f"상품코드: SAMPLE-001\n판본코드: SAMPLE-{year}\n판본일: {year}-01-01"
                        + metadata_suffix,
                        "reading_order": 0,
                        "bbox": [10, 10, 400, 180],
                    }
                ],
                "tables": [],
            }
        )
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE extractions SET status='succeeded',succeeded_at=clock_timestamp() WHERE id=%s",
            (job.extraction_id,),
        )
        connection.execute(
            "UPDATE document_versions SET page_count=2 WHERE id=%s", (job.document_version_id,)
        )
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "SELECT %s,number,612,792,80,0.8,0,1,'TEXT_SUFFICIENT' "
            "FROM generate_series(1,2) number "
            "ON CONFLICT DO NOTHING",
            (job.extraction_id,),
        )
    source = build_document_structure(
        {
            "document_version_id": str(job.document_version_id),
            "content_sha256": "a" * 64,
            "pages": pages,
        },
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-editions-v1",
    )
    _prepare(
        DocumentStructureRepository(url),
        job,
        source,
        plan_structure_chunks(
            source, max_content_chars=4096, max_context_chars=4096, max_chunks=100
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending(limit=1) == 1
    return url, job


def test_two_component_editions_in_one_wrongly_classified_file(component_database: Any) -> None:
    url, job = _seed(component_database)
    projector = _projector(url)
    assert projector.project_pending() == 2
    assert projector.project_pending() == 0
    editions = TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))
    assert len(editions) == 2
    assert {e.insurer_key for e in editions} == {"sample assurance"}
    assert {e.product_key for e in editions} == {"sample product"}
    assert {e.edition_date for e in editions} == {date(2024, 1, 1), date(2025, 1, 1)}
    assert {(e.source_page_start, e.source_page_end) for e in editions} == {(1, 1), (2, 2)}
    assert all(e.applicability_start is None and e.applicability_end is None for e in editions)
    assert not TermsEditionRepository(url).list(HouseholdScope(uuid4()))
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        rows = connection.execute("SELECT source_metadata_json FROM terms_editions").fetchall()
        assert all(row["source_metadata_json"]["facts"] for row in rows)


def test_component_edition_publication_is_concurrent_and_preserves_deletion(
    component_database: Any,
) -> None:
    url, job = _seed(component_database)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: _projector(url).project_pending(), range(2)))
    assert sum(results) == 2
    repository = TermsEditionRepository(url)
    scope = HouseholdScope(job.household_space_id)
    edition = repository.list(scope)[0]
    repository.soft_delete(scope, edition.id, expected_version=edition.version)
    assert _projector(url).project_pending() == 0
    assert len(repository.list(scope)) == 1


def test_explicit_product_code_preserves_an_edition_without_guessing_its_name(
    component_database: Any,
) -> None:
    url, job = _seed(component_database, product_name=False)
    assert _projector(url).project_pending() == 2
    editions = TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))
    assert len(editions) == 2
    assert {edition.product_display for edition in editions} == {"SAMPLE-001"}


def test_normalization_expansion_does_not_stall_other_editions(component_database: Any) -> None:
    url, job = _seed(component_database, insurer="㍿" * 50)
    assert _projector(url).project_pending() == 2
    editions = TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))
    assert len(editions) == 2
    assert all(len(edition.insurer_key) == 160 for edition in editions)


def test_component_edition_origin_and_publication_are_immutable(component_database: Any) -> None:
    url, _ = _seed(component_database)
    _projector(url).project_pending()
    with psycopg.connect(_psycopg_url(url)) as connection:
        for command in (
            "UPDATE terms_editions SET source_component_id=NULL",
            "UPDATE terms_editions SET source_page_end=source_page_end+1",
            "UPDATE terms_editions SET content_sha256=repeat('f',64)",
            "UPDATE terms_editions SET source_metadata_json='{}'::jsonb",
            "UPDATE terms_editions SET edition_date='2020-01-01'",
            "UPDATE component_terms_publications SET reason_code='LEGACY_EDITION_EXISTS'",
            "DELETE FROM component_terms_publications",
        ):
            with pytest.raises(psycopg.IntegrityError), connection.transaction():
                connection.execute(command)


def test_clause_storage_rejects_other_component_pages(component_database: Any) -> None:
    url, job = _seed(component_database)
    _projector(url).project_pending()
    scope = HouseholdScope(job.household_space_id)
    edition = next(e for e in TermsEditionRepository(url).list(scope) if e.source_page_start == 1)
    with psycopg.connect(_psycopg_url(url)) as connection:
        ids = []
        for number in (1, 2):
            ids.append(
                connection.execute(
                    "INSERT INTO evidence(household_space_id,document_version_id,"
                    "extraction_id,content_sha256,physical_page,review_state) "
                    "VALUES(%s,%s,%s,%s,%s,'USER_CONFIRMED') RETURNING id",
                    (
                        job.household_space_id,
                        job.document_version_id,
                        job.extraction_id,
                        "a" * 64,
                        number,
                    ),
                ).fetchone()[0]
            )
    repository = ClauseRepository(url)
    params = dict(
        terms_edition_id=edition.id,
        parent_clause_id=None,
        clause_type="article",
        label="Article A",
        normalized_title="synthetic eligibility",
        normalized_text="synthetic eligibility",
    )
    clause = repository.create(
        scope, **params, physical_page_start=1, physical_page_end=1, evidence_ids=(ids[0],)
    )
    assert clause.terms_edition_id == edition.id
    from familycare_api.clauses.errors import ClauseEvidenceInvalid

    with pytest.raises(ClauseEvidenceInvalid):
        repository.create(
            scope, **params, physical_page_start=2, physical_page_end=2, evidence_ids=(ids[1],)
        )
    with (
        psycopg.connect(_psycopg_url(url)) as connection,
        pytest.raises(psycopg.IntegrityError),
        connection.transaction(),
    ):
        connection.execute("UPDATE clauses SET physical_page_end=2 WHERE id=%s", (clause.id,))


@pytest.mark.parametrize(
    "change", ["deleted", "rejected", "corrected_range", "member_deleted", "document_deleted"]
)
def test_component_change_removes_edition_from_current_catalog(
    component_database: Any, change: str
) -> None:
    url, job = _seed(component_database)
    _projector(url).project_pending()
    scope = HouseholdScope(job.household_space_id)
    repository = TermsEditionRepository(url)
    edition = repository.list(scope)[0]
    with psycopg.connect(_psycopg_url(url)) as connection:
        if change == "member_deleted":
            connection.execute(
                "UPDATE family_members SET deleted_at=clock_timestamp() WHERE id=%s",
                (job.family_member_id,),
            )
        elif change == "document_deleted":
            connection.execute(
                "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=("
                "SELECT document_id FROM document_versions WHERE id=%s)",
                (job.document_version_id,),
            )
        else:
            changes = {
                "deleted": "deleted_at=clock_timestamp()",
                "rejected": "review_state='REJECTED'",
                "corrected_range": "review_state='USER_CONFIRMED',page_start=1,page_end=2",
            }
            connection.execute(
                f"UPDATE insurance_document_components SET {changes[change]} WHERE id=%s",
                (edition.source_component_id,),
            )
    assert repository.get(scope, edition.id) is None
    assert edition.id not in {e.id for e in repository.list(scope)}
    assert _projector(url).project_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            "SELECT id FROM terms_editions WHERE id=%s", (edition.id,)
        ).fetchone()


@pytest.mark.parametrize("aggregate", ["clause", "edition"])
def test_failed_restore_keeps_tombstone_and_version(
    component_database: Any, aggregate: str
) -> None:
    from familycare_api.clauses.errors import ClauseVersionConflict

    url, job = _seed(component_database)
    _projector(url).project_pending()
    scope = HouseholdScope(job.household_space_id)
    edition = TermsEditionRepository(url).list(scope)[0]
    identifier = edition.id
    repository: Any = TermsEditionRepository(url)
    table = "terms_editions"
    if aggregate == "clause":
        table = "clauses"
        repository = ClauseRepository(url)
        with psycopg.connect(_psycopg_url(url)) as connection:
            identifier = connection.execute(
                "INSERT INTO clauses(household_space_id,terms_edition_id,clause_type,label,"
                "normalized_title,normalized_text,physical_page_start,physical_page_end,"
                "normalization_version) VALUES(%s,%s,'article','Article A','synthetic',"
                "'synthetic',%s,%s,'unicode-nfc-v1') RETURNING id",
                (
                    job.household_space_id,
                    edition.id,
                    edition.source_page_start,
                    edition.source_page_end,
                ),
            ).fetchone()[0]
    repository.soft_delete(scope, identifier, expected_version=1)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE insurance_document_components SET review_state='REJECTED' WHERE id=%s",
            (edition.source_component_id,),
        )
    with pytest.raises(ClauseVersionConflict):
        repository.restore(scope, identifier, expected_version=2)
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute(
            f"SELECT deleted_at IS NOT NULL,version FROM {table} WHERE id=%s", (identifier,)
        ).fetchone() == (True, 2)


@pytest.mark.parametrize("deleted", [False, True])
def test_same_bytes_legacy_edition_survives_a_new_document_version(
    component_database: Any, deleted: bool
) -> None:
    url, job = _seed(component_database)
    with psycopg.connect(_psycopg_url(url)) as connection:
        version = connection.execute(
            "SELECT id FROM document_versions WHERE id<>%s AND content_sha256=%s LIMIT 1",
            (job.document_version_id, "a" * 64),
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO terms_editions(household_space_id,document_version_id,insurer_display,"
            "insurer_key,product_display,product_key,content_sha256,"
            "normalization_version,deleted_at) "
            "VALUES(%s,%s,'Sample Assurance','sample assurance','Sample Product','sample product',"
            "%s,'unicode-nfc-v1',CASE WHEN %s THEN clock_timestamp() ELSE NULL END)",
            (job.household_space_id, version, "a" * 64, deleted),
        )
    assert _projector(url).project_pending() == 2
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute(
                "SELECT count(*) FROM terms_editions WHERE source_component_id IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        assert connection.execute(
            "SELECT DISTINCT reason_code FROM component_terms_publications"
        ).fetchall() == [("LEGACY_EDITION_EXISTS",)]


@pytest.mark.parametrize(
    "period,verified",
    [
        ("\n적용시작일: 2025-01-01\n적용종료일: 2025-12-31", True),
        ("\n적용시작일: 2025-12-31\n적용종료일: 2025-01-01", False),
        ("\n적용시작일: 2025-02-31", False),
        ("", False),
    ],
)
def test_only_source_proven_periods_are_usable_for_date_validation(
    component_database: Any, period: str, verified: bool
) -> None:
    url, job = _seed(component_database, metadata_suffix=period)
    assert _projector(url).project_pending() == 2
    editions = TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))
    assert len(editions) == 2
    assert all(e.source_period_verified is verified for e in editions)
    if not verified:
        assert all(e.applicability_start is None for e in editions)


def test_manual_and_program_registration_cannot_install_competing_editions(
    component_database: Any,
) -> None:
    from familycare_api.clauses.errors import ClauseStateConflict

    url, job = _seed(component_database)
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE documents SET document_kind='terms' WHERE id=("
            "SELECT document_id FROM document_versions WHERE id=%s)",
            (job.document_version_id,),
        )
        evidence_id = connection.execute(
            "UPDATE evidence SET review_state='USER_CONFIRMED' WHERE document_version_id=%s "
            "RETURNING id",
            (job.document_version_id,),
        ).fetchone()[0]
    barrier = Barrier(2)

    def register(manual: bool) -> None:
        barrier.wait(timeout=5)
        if not manual:
            _projector(url).project_pending()
            return
        with suppress(ClauseStateConflict):
            TermsEditionRepository(url).create(
                HouseholdScope(job.household_space_id),
                source_evidence_id=evidence_id,
                document_version_id=job.document_version_id,
                insurer_display="Sample Assurance",
                insurer_key="sample assurance",
                product_display="Sample Product",
                product_key="sample product",
                applicability_start=None,
                applicability_end=None,
                content_sha256="a" * 64,
            )

    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(register, [False, True]))
    with psycopg.connect(_psycopg_url(url)) as connection:
        manual, program = connection.execute(
            "SELECT count(*) FILTER (WHERE source_component_id IS NULL),"
            "count(*) FILTER (WHERE source_component_id IS NOT NULL) FROM terms_editions "
            "WHERE household_space_id=%s",
            (job.household_space_id,),
        ).fetchone()
        assert (manual, program) in {(1, 0), (0, 2)}
