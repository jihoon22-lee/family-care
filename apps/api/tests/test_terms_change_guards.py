"""Synthetic retained change identities remain scoped after edits and restoration."""

from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    changes_database,  # noqa: F401
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("target", ["policy", "rider"])
def test_user_source_replacement_is_not_recertified_from_old_publication(
    changes_database: Any,  # noqa: F811
    target: str,
) -> None:
    url, job = changes_database
    sources = _sources(url, job)
    projector = TermsChangeProjector(url)
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        evidence = uuid4()
        source = connection.execute(
            "SELECT e.document_version_id,v.content_sha256,x.id AS extraction_id "
            "FROM terms_editions e JOIN document_versions v ON v.id=e.document_version_id "
            "JOIN extractions x ON x.document_version_id=v.id WHERE e.id=%s",
            (sources["EDITION-B"],),
        ).fetchone()
        connection.execute(
            "INSERT INTO evidence(id,household_space_id,document_version_id,extraction_id,"
            "content_sha256,physical_page,review_state) VALUES(%s,%s,%s,%s,%s,1,'USER_CONFIRMED')",
            (
                evidence,
                job.household_space_id,
                source["document_version_id"],
                source["extraction_id"],
                source["content_sha256"],
            ),
        )
        if target == "policy":
            connection.execute(
                "UPDATE policy_contracts SET source_document_version_id=%s,source_evidence_id=%s,"
                "version=version+1 WHERE id=%s",
                (source["document_version_id"], evidence, sources["policy_id"]),
            )
        else:
            connection.execute(
                "UPDATE riders SET source_evidence_id=%s,version=version+1 WHERE id=%s",
                (evidence, sources["Sample Rider"]),
            )
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT status FROM current_policy_terms_changes").fetchone()[
                "status"
            ]
            == "UNKNOWN"
        )


def test_source_document_kind_change_invalidates_current_assessment(changes_database: Any) -> None:  # noqa: F811
    url, job = changes_database
    _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        connection.execute(
            "UPDATE documents SET document_kind='supporting' "
            "WHERE id=(SELECT document_id FROM document_versions WHERE id=%s)",
            (job.document_version_id,),
        )
        assert (
            connection.execute("SELECT count(*) AS n FROM current_policy_terms_changes").fetchone()[
                "n"
            ]
            == 0
        )


def test_restoring_same_source_reuses_original_immutable_assessment(changes_database: Any) -> None:  # noqa: F811
    url, job = changes_database
    sources = _sources(url, job)
    projector = TermsChangeProjector(url)
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute("SELECT id FROM current_policy_terms_changes").fetchone()[
            "id"
        ]
        document = connection.execute(
            "SELECT v.document_id FROM terms_editions e JOIN document_versions v "
            "ON v.id=e.document_version_id WHERE e.id=%s",
            (sources["EDITION-B"],),
        ).fetchone()["document_id"]
        connection.execute(
            "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=%s", (document,)
        )
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute("UPDATE documents SET deleted_at=NULL WHERE id=%s", (document,))
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        restored = connection.execute(
            "SELECT id,status FROM current_policy_terms_changes"
        ).fetchone()
        assert restored is not None and restored["status"] == "MATCH" and restored["id"] == original


@pytest.mark.parametrize(
    "malformation", ["empty", "missing_offsets", "wrong_date", "wrong_operation"]
)
def test_missing_source_fields_cannot_be_inserted_as_a_match(
    changes_database: Any,  # noqa: F811
    malformation: str,
) -> None:
    url, job = changes_database
    _sources(url, job)
    assert TermsChangeProjector(url).refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute("SELECT * FROM current_policy_terms_changes").fetchone()
        row["id"] = uuid4()
        if malformation == "empty":
            row["source_fields"] = []
        elif malformation == "missing_offsets":
            span = row["source_fields"][0]["spans"][0]
            row["source_fields"] = [
                {
                    "name": "effective_from",
                    "spans": [
                        {
                            "node_id": span["node_id"],
                            "page_number": span["page_number"],
                        }
                    ],
                }
            ]
        elif malformation == "wrong_date":
            row["effective_from"] = "2025-01-01"
        else:
            row["operation"] = "ADD"
            row["previous_edition_id"] = None
        connection.execute("UPDATE policy_contracts SET version=version+1")
        context = connection.execute(
            "SELECT terms_change_input_context(%s,%s) AS context",
            (row["source_component_id"], job.household_space_id),
        ).fetchone()["context"]
        with pytest.raises(psycopg.IntegrityError), connection.transaction():
            connection.execute(
                "INSERT INTO policy_terms_changes SELECT (jsonb_populate_record("
                "NULL::policy_terms_changes, "
                "%s::jsonb || jsonb_build_object('input_context',%s::jsonb,'input_digest',"
                "encode(sha256(convert_to((%s::jsonb)::text,'UTF8')),'hex')))).*",
                (
                    Jsonb(
                        {
                            key: str(value)
                            if isinstance(value, UUID) or hasattr(value, "isoformat")
                            else value
                            for key, value in row.items()
                        }
                    ),
                    Jsonb(context),
                    Jsonb(context),
                ),
            )


def test_display_name_correction_preserves_original_change_target(changes_database: Any) -> None:  # noqa: F811
    url, job = changes_database
    sources = _sources(url, job)
    projector = TermsChangeProjector(url)
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        original = connection.execute("SELECT id FROM current_policy_terms_changes").fetchone()[
            "id"
        ]
        connection.execute(
            "UPDATE riders SET display_name='Sample Corrected Label',version=version+1 WHERE id=%s",
            (sources["Sample Rider"],),
        )
        current = connection.execute(
            "SELECT id,status FROM current_policy_terms_changes"
        ).fetchone()
        assert current is not None and current["id"] == original and current["status"] == "MATCH"
    assert projector.refresh_pending() == 0


def test_same_bytes_reimport_can_use_new_verified_rider_source(changes_database: Any) -> None:  # noqa: F811
    from familycare_api.policies.range_enrollment import RangeEnrollmentProjector

    from apps.api.tests.terms_change_seed import retain_terms_change_policy
    from apps.api.tests.test_native_range_enrollment_integration import _reextract

    url, job = changes_database
    sources = _sources(url, job)
    projector = TermsChangeProjector(url)
    assert projector.refresh_pending() == 1
    newer = _reextract(url, job, reimport=True)
    retain_terms_change_policy(url, newer)
    assert RangeEnrollmentProjector(url).project_pending() == 3
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        source = connection.execute(
            "SELECT ce.evidence_id FROM range_enrollment_publications p "
            "JOIN analysis_candidate_evidence ce ON ce.candidate_version_id=p.candidate_version_id "
            "AND ce.field_id='rider_name' JOIN evidence e ON e.id=ce.evidence_id "
            "WHERE p.rider_id=%s AND e.document_version_id=%s",
            (sources["Sample Rider"], newer.document_version_id),
        ).fetchone()["evidence_id"]
        connection.execute(
            "UPDATE riders SET source_evidence_id=%s,version=version+1 WHERE id=%s",
            (source, sources["Sample Rider"]),
        )
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT status FROM current_policy_terms_changes").fetchone()[
                "status"
            ]
            == "MATCH"
        )


def test_product_correction_is_preserved_when_change_is_reassessed(changes_database: Any) -> None:  # noqa: F811
    url, job = changes_database
    _sources(url, job)
    projector = TermsChangeProjector(url)
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE policy_contracts SET product_display='Sample Different Product',"
            "version=version+1"
        )
    assert projector.refresh_pending() == 1
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        assert (
            connection.execute("SELECT status FROM current_policy_terms_changes").fetchone()[
                "status"
            ]
            == "UNKNOWN"
        )
