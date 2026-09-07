"""Program refinements retain old sources and never undo a user's decision."""

from typing import Any

import psycopg
import pytest
from familycare_api.clauses.component_editions import ComponentTermsProjector
from familycare_api.clauses.repository import TermsEditionRepository
from familycare_api.common.scope import HouseholdScope
from familycare_api.insurance_documents.metadata_publication import DocumentMetadataProjector
from familycare_worker.document_metadata import metadata_proposal
from familycare_worker.document_metadata_repository import DocumentMetadataRunner
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_document_metadata_publication import (
    publication_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_document_metadata_validation import _legacy_identity
from workers.analyzer.tests.test_document_metadata_repository import _seed, _source
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


def _pending_refinement(database: Any) -> tuple[str, Any, dict[str, Any]]:
    text = "보험약관\n보험사: Sample Assurance\n상품코드: SAMPLE-A\n상품명 Sample Policy"
    url, job, generation = _seed(database, text=text)
    source = _source(job, text=text)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        identity = connection.execute(
            "SELECT identity_sha256 FROM document_structure_generations WHERE id=%s", (generation,)
        ).fetchone()["identity_sha256"]
        payload = metadata_proposal(source, generation, identity)
        payload["revision"] = "document-metadata-v1"
        component = payload["components"][0]
        component["facts"] = [
            fact for fact in component["facts"] if fact["field"] != "product_name"
        ]
        _legacy_identity(component, source.to_dict())
        connection.execute(
            "INSERT INTO document_metadata_proposals("
            "generation_id,revision,state,attempts,proposal_json) "
            "VALUES(%s,'document-metadata-v1','PREPARED',1,%s)",
            (generation, Jsonb(payload)),
        )
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert ComponentTermsProjector(url).project_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        previous = connection.execute(
            "SELECT c.id AS component_id,c.metadata_publication_id,e.id AS edition_id,"
            "e.source_metadata_json FROM insurance_document_components c JOIN terms_editions e "
            "ON e.source_component_id=c.id WHERE c.document_batch_item_id=%s",
            (job.batch_item_id,),
        ).fetchone()
        assert previous is not None
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    return url, job, previous


def test_stronger_metadata_atomically_replaces_only_the_current_projection(
    request: pytest.FixtureRequest,
) -> None:
    url, job, previous = _pending_refinement(request.getfixturevalue("publication_database"))
    assert DocumentMetadataProjector(url).project_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    editions = TermsEditionRepository(url).list(scope)
    assert len(editions) == 1 and editions[0].product_display == "Sample Policy"
    assert editions[0].id != previous["edition_id"]
    assert TermsEditionRepository(url).get(scope, previous["edition_id"]) is None
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        retained = connection.execute(
            "SELECT source_metadata_json,deleted_at,version FROM terms_editions WHERE id=%s",
            (previous["edition_id"],),
        ).fetchone()
        assert retained == {
            "source_metadata_json": previous["source_metadata_json"],
            "deleted_at": None,
            "version": 1,
        }
        pointer = connection.execute(
            "SELECT superseded_by_component_id FROM insurance_document_components WHERE id=%s",
            (previous["component_id"],),
        ).fetchone()["superseded_by_component_id"]
        assert pointer == editions[0].source_component_id
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM document_component_supersessions"
            ).fetchone()["n"]
            == 1
        )
    assert DocumentMetadataProjector(url).project_pending() == 0
    assert ComponentTermsProjector(url).project_pending() == 0


@pytest.mark.parametrize("failure", ["deferred", "storage_error"])
def test_failed_successor_edition_keeps_previous_usable_source(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    url, job, previous = _pending_refinement(request.getfixturevalue("publication_database"))

    def fail(connection: Any, source: Any) -> None:
        if failure == "storage_error":
            connection.execute("SELECT 1/0")

    monkeypatch.setattr(ComponentTermsProjector, "_publish", staticmethod(fail))
    if failure == "storage_error":
        with pytest.raises(psycopg.DataError):
            DocumentMetadataProjector(url).project_pending()
    else:
        assert DocumentMetadataProjector(url).project_pending() == 1
    editions = TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))
    assert len(editions) == 1 and editions[0].id == previous["edition_id"]
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute(
                "SELECT superseded_by_component_id FROM insurance_document_components WHERE id=%s",
                (previous["component_id"],),
            ).fetchone()["superseded_by_component_id"]
            is None
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM insurance_document_components"
            ).fetchone()["n"]
            == 1
        )
        assert (
            connection.execute(
                "SELECT count(*) AS n FROM document_component_supersessions"
            ).fetchone()["n"]
            == 0
        )
    if failure == "storage_error":
        monkeypatch.undo()
        assert DocumentMetadataProjector(url).project_pending() == 1
        assert (
            TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))[0].id
            != previous["edition_id"]
        )


@pytest.mark.parametrize("deleted", [False, True])
def test_any_clause_history_preserves_original_edition(
    request: pytest.FixtureRequest, deleted: bool
) -> None:
    from familycare_api.clauses.repository import ClauseRepository

    url, job, previous = _pending_refinement(request.getfixturevalue("publication_database"))
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE extractions SET status='succeeded',succeeded_at=clock_timestamp() WHERE id=%s",
            (job.extraction_id,),
        )
        connection.execute(
            "INSERT INTO extraction_pages(extraction_id,page_number,width_points,height_points,"
            "non_whitespace_chars,alphanumeric_ratio,replacement_character_ratio,"
            "maximum_repeated_character_run,classification) "
            "VALUES(%s,1,612,792,80,0.8,0,1,'TEXT_SUFFICIENT') ON CONFLICT DO NOTHING",
            (job.extraction_id,),
        )
        evidence = connection.execute(
            "INSERT INTO evidence(household_space_id,document_version_id,extraction_id,"
            "content_sha256,physical_page,review_state) "
            "VALUES(%s,%s,%s,%s,1,'USER_CONFIRMED') RETURNING id",
            (job.household_space_id, job.document_version_id, job.extraction_id, "a" * 64),
        ).fetchone()[0]
    scope = HouseholdScope(job.household_space_id)
    clauses = ClauseRepository(url)
    clause = clauses.create(
        scope,
        terms_edition_id=previous["edition_id"],
        parent_clause_id=None,
        clause_type="article",
        label="Article A",
        normalized_title="synthetic eligibility",
        normalized_text="synthetic eligibility",
        physical_page_start=1,
        physical_page_end=1,
        evidence_ids=(evidence,),
    )
    if deleted:
        clauses.soft_delete(scope, clause.id, expected_version=clause.version)
    assert DocumentMetadataProjector(url).project_pending() == 1
    assert TermsEditionRepository(url).list(scope)[0].id == previous["edition_id"]
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM document_component_supersessions").fetchone()[
                0
            ]
            == 0
        )


def test_database_rejects_missing_receipt_and_mutation_of_retired_history(
    request: pytest.FixtureRequest,
) -> None:
    from uuid import uuid4

    url, _, previous = _pending_refinement(request.getfixturevalue("publication_database"))
    with pytest.raises(psycopg.IntegrityError), psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE insurance_document_components SET superseded_by_component_id=%s WHERE id=%s",
            (uuid4(), previous["component_id"]),
        )
    assert DocumentMetadataProjector(url).project_pending() == 1
    for command in (
        "UPDATE insurance_document_components SET superseded_by_component_id=NULL WHERE id=%s",
        "UPDATE insurance_document_components SET version=version+1 WHERE id=%s",
        "DELETE FROM insurance_document_components WHERE id=%s",
        "DELETE FROM document_component_supersessions WHERE predecessor_component_id=%s",
        "UPDATE document_component_supersessions SET created_at=clock_timestamp() "
        "WHERE predecessor_component_id=%s",
    ):
        with (
            pytest.raises(psycopg.IntegrityError),
            psycopg.connect(_psycopg_url(url)) as connection,
        ):
            connection.execute(command, (previous["component_id"],))


def test_cyclic_receipts_are_rejected_without_unbounded_ancestry(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    url, job, previous = _pending_refinement(request.getfixturevalue("publication_database"))
    publish = ComponentTermsProjector._publish

    def cyclic(connection: Any, source: Any) -> None:
        publish(connection, source)
        connection.execute("SET LOCAL statement_timeout='1s'")
        connection.execute(
            "UPDATE insurance_document_components SET superseded_by_component_id=%s WHERE id=%s",
            (previous["component_id"], source["id"]),
        )
        connection.execute(
            "INSERT INTO document_component_supersessions(predecessor_component_id,"
            "successor_component_id,predecessor_publication_id,successor_publication_id,"
            "successor_terms_edition_id,revision) SELECT id,%s,metadata_publication_id,%s,%s,"
            "'metadata-refinement-v1' FROM insurance_document_components WHERE id=%s",
            (
                previous["component_id"],
                previous["metadata_publication_id"],
                previous["edition_id"],
                source["id"],
            ),
        )

    monkeypatch.setattr(ComponentTermsProjector, "_publish", staticmethod(cyclic))
    with pytest.raises(psycopg.errors.CheckViolation):
        DocumentMetadataProjector(url).project_pending()
    assert (
        TermsEditionRepository(url).list(HouseholdScope(job.household_space_id))[0].id
        == previous["edition_id"]
    )


def test_component_cannot_be_inserted_as_retired_without_receipt(
    request: pytest.FixtureRequest,
) -> None:
    url, _, previous = _pending_refinement(request.getfixturevalue("publication_database"))
    with (
        pytest.raises(psycopg.errors.CheckViolation),
        psycopg.connect(_psycopg_url(url)) as connection,
    ):
        connection.execute(
            "INSERT INTO insurance_document_components(household_space_id,family_member_id,"
            "document_batch_item_id,document_version_id,role,page_start,page_end,review_state,"
            "created_by,superseded_by_component_id) "
            "SELECT c.household_space_id,c.family_member_id,c.document_batch_item_id,"
            "c.document_version_id,c.role,c.page_start,c.page_end,"
            "'USER_CONFIRMED',b.created_by,c.id "
            "FROM insurance_document_components c JOIN document_batch_items i "
            "ON i.id=c.document_batch_item_id JOIN document_batches b ON b.id=i.batch_id "
            "WHERE c.id=%s",
            (previous["component_id"],),
        )


def test_reextraction_refines_a_chain_and_identical_replay_stops(
    request: pytest.FixtureRequest,
) -> None:
    database = request.getfixturevalue("publication_database")
    url, job, previous = _pending_refinement(database)
    assert DocumentMetadataProjector(url).project_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    middle = TermsEditionRepository(url).list(scope)[0]
    # A later synthetic extraction recovers another explicit source field.
    _seed(
        database,
        text=(
            "보험약관\n보험사: Sample Assurance\n상품코드: SAMPLE-A\n"
            "상품명 Sample Policy\n판본코드 SAMPLE-2025"
        ),
    )
    assert DocumentMetadataRunner(url).run_once("synthetic-worker")
    assert DocumentMetadataProjector(url).project_pending() == 1
    current = TermsEditionRepository(url).list(scope)
    assert len(current) == 1 and current[0].id not in {previous["edition_id"], middle.id}
    assert TermsEditionRepository(url).get(scope, middle.id) is None
    assert DocumentMetadataProjector(url).project_pending() == 0
    assert ComponentTermsProjector(url).project_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert (
            connection.execute("SELECT count(*) FROM document_component_supersessions").fetchone()[
                0
            ]
            == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM terms_editions WHERE deleted_at IS NULL"
            ).fetchone()[0]
            == 3
        )
