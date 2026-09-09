"""Verified, persisted review roots reach the same engine without publishing global knowledge."""

from copy import deepcopy
from dataclasses import replace
from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.repository import RiderClauseLinkRepository
from familycare_api.clauses.source_repository import ClauseSourceProjector
from familycare_api.clauses.terms_change_repository import TermsChangeProjector
from familycare_api.common.scope import HouseholdScope
from familycare_api.guidance.engine import LocalGuidanceEngine
from familycare_api.guidance.repository import read_operational_guidance
from familycare_api.guidance_review.reassessment import (
    ReviewReassessmentInvalid,
    prepare_verified_review,
    read_review_overlay,
)
from familycare_api.guidance_review.repository import GuidanceReviewRepository
from familycare_api.guidance_review.sources import read_review_sources
from familycare_api.terms_knowledge.local_candidates import propose_local_candidates
from familycare_api.terms_knowledge.repository import _plan
from familycare_worker.guidance_review_jobs import GuidanceReviewQueue
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from apps.api.tests.test_clause_change_integration import _native_link
from apps.api.tests.test_clause_source_publication import _clause
from apps.api.tests.test_decision_integration import _service
from apps.api.tests.test_terms_change_integration import (
    _psycopg_url,
    _sources,
    enrollment_database,  # noqa: F401
    ranges_database,  # noqa: F401
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_terms_change_integration import (
    changes_database as changes_database,
)
from apps.api.tests.test_terms_source_verification import DAILY, FOOTNOTE, LIMIT

pytestmark = pytest.mark.integration


@pytest.fixture()
def unreviewed_original(changes_database, request):
    url, job = changes_database
    body = "\n".join(
        (
            DAILY,
            LIMIT,
            "Eligible admission days range from 1 to 365 inclusive.",
            "Apply Footnote 1.",
            "Footnote 1",
            FOOTNOTE,
        )
    )
    terms = f"Article 1\n{body}\nArticle 2\nSynthetic unrelated context."
    inventory = _sources(url, job, terms_body=terms, new_terms_body=terms, sample_amount=100)
    clause = _clause(url, job, inventory["EDITION-A"], body=body, label="Article 1")
    assert ClauseSourceProjector(url).refresh_pending() == 1
    assert TermsChangeProjector(url).refresh_pending() == 1
    scope = HouseholdScope(job.household_space_id)
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        link = _native_link(
            connection,
            scope.household_space_id,
            inventory["Sample Rider"],
            clause,
            inventory["EDITION-A"],
        )
    RiderClauseLinkRepository(url).confirm(scope, link, expected_version=1)
    selected_item = None
    if getattr(request, "param", False):
        from familycare_api.clauses.terms_applicability_repository import (
            TermsApplicabilityProjector,
        )
        from familycare_api.insurance_documents.repository import InsuranceDocumentRepository

        with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
            actor = connection.execute(
                "INSERT INTO app_users(household_space_id,username,display_name,password_hash) "
                "VALUES(%s,'synthetic-review-admin','Admin A','$argon2id$synthetic') RETURNING id",
                (scope.household_space_id,),
            ).fetchone()["id"]
            component = connection.execute(
                "SELECT source_component_id FROM terms_editions WHERE id=%s",
                (inventory["EDITION-A"],),
            ).fetchone()["source_component_id"]
        repository = InsuranceDocumentRepository(url)
        document_set = repository.create_document_set(
            scope,
            actor_id=actor,
            member_id=job.family_member_id,
            policy_contract_id=inventory["policy_id"],
            insurer_display=None,
            product_display=None,
            display_label="Sample Manual Terms Selection",
        )
        selected_item = repository.attach_set_item(
            scope,
            actor_id=actor,
            document_set_id=document_set.id,
            insurance_document_component_id=component,
            match_state="USER_CONFIRMED",
            evidence_id=None,
            expected_set_version=document_set.version,
        )
        assert TermsApplicabilityProjector(url).refresh_pending() == 1
        with psycopg.connect(_psycopg_url(url)) as connection:
            assert connection.execute(
                "SELECT status,selection_state FROM current_policy_terms_applicability "
                "WHERE policy_contract_id=%s AND terms_edition_id=%s",
                (inventory["policy_id"], inventory["EDITION-A"]),
            ).fetchone() == ("MATCH", "USER_SELECTED")
    service = _service(url, scope)
    event = service.create_medical_event(
        family_member_id=job.family_member_id,
        mode="post_treatment",
        situation="5일 입원했습니다.",
        event_date=date(2025, 6, 15),
        facts={"MedicalEvent.admission_days": 5},
        confirmation={"MedicalEvent.admission_days": "user"},
    )
    original = service.analyze_medical_event(event.id)
    assert original.local_guidance is not None and not original.local_guidance.candidates
    review = GuidanceReviewRepository(url).enqueue(
        scope, event.id, run_id=original.run_id, expected_event_version=event.version
    )
    lease = GuidanceReviewQueue(url).claim()
    assert lease is not None and lease.id == review.id
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        sources = read_review_sources(connection, scope, event, service.repository)
        packet = sources.packets[0]
        plan = _plan(connection, scope, inventory["EDITION-A"])
    graph = next(
        graph
        for graph in propose_local_candidates(plan.snapshot).graphs
        if any(node["payload"].get("mode") == "daily" for node in graph["nodes"])
    )
    supplied = {r["region_id"] for r in packet.to_payload()["envelope"]["regions"]}
    graph["processing"]["consumed_region_ids"] = [
        r for r in plan.snapshot.layout.expected_region_ids if r in supplied
    ]
    graph["processing"]["unresolved_region_ids"] = [
        r for r in plan.snapshot.layout.expected_region_ids if r not in supplied
    ]
    return SimpleNamespace(
        url=url,
        scope=scope,
        service=service,
        event=event,
        original=original,
        job=review,
        sources=sources,
        packet=packet,
        graph=graph,
        inventory=inventory,
        selected_item=selected_item,
    )


def _prepare(connection, sample, graph=None, packet=None):
    return prepare_verified_review(
        connection,
        sample.scope,
        sample.event,
        packet or sample.packet,
        graph or sample.graph,
        expected_source_digest=sample.sources.digest_sha256,
        decisions=sample.service.repository,
    )


def _persist(connection, sample, prepared):
    values = prepared.persistence_values()
    return connection.execute(
        "INSERT INTO guidance_review_publications(review_job_id,packet_id,source_digest,"
        "graph_json,proof_sha256,compiled_json,verifier_revision,compiler_revision) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (
            sample.job.id,
            values["packet_id"],
            values["source_digest"],
            Jsonb(values["graph_json"]),
            values["proof_sha256"],
            Jsonb(values["compiled_json"]),
            values["verifier_revision"],
            values["compiler_revision"],
        ),
    ).fetchone()["id"]


def test_persisted_review_finds_missing_candidate_and_calculates_300_with_same_engine(
    unreviewed_original,
):
    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        prepared = _prepare(connection, sample)
        publication = _persist(connection, sample, prepared)
        overlay = read_review_overlay(
            connection,
            sample.scope,
            sample.event,
            publication,
            review_job_id=sample.job.id,
            decisions=sample.service.repository,
        )
        context = read_operational_guidance(
            connection,
            sample.scope,
            sample.event,
            sample.service.repository,
            semantic_overlay=overlay,
        )
        reviewed = LocalGuidanceEngine().evaluate(sample.scope, sample.event, context)
        candidate = next(c for c in reviewed.candidates if c.ref == sample.packet.coverage_ref)
        assert candidate.estimate.amount == "300"
        assert all(
            e.review_job_id == sample.job.id and e.publication_id == publication
            for e in candidate.estimate.evidence
        )
        assert (
            connection.execute(
                "SELECT count(*) AS count FROM terms_semantic_publications"
            ).fetchone()["count"]
            == 0
        )
        assert connection.execute(
            "SELECT local_guidance_json FROM decision_runs WHERE id=%s", (sample.original.run_id,)
        ).fetchone()["local_guidance_json"] == sample.original.local_guidance.model_dump(
            mode="json"
        )
    assert sample.event.facts == sample.service.get_medical_event(sample.event.id).facts


@pytest.mark.parametrize("fault", ["citation", "region", "source", "invented_fact", "packet"])
def test_unprovided_or_invented_meaning_never_becomes_review_authority(unreviewed_original, fault):
    sample = unreviewed_original
    graph = deepcopy(sample.graph)
    packet = sample.packet.to_payload()
    if fault == "citation":
        graph["citations"][0]["text"] = "Synthetic fabricated contrary quotation."
    elif fault == "region":
        graph["nodes"][0]["region_ids"] = ["synthetic-unprovided-region"]
    elif fault == "source":
        graph["sources"][0]["document_version_id"] = str(uuid4())
    elif fault == "invented_fact":
        node = next(n for n in graph["nodes"] if n["payload"].get("kind") == "footnote")
        node["payload"]["days"] = 0
    else:
        packet["coverage_ref"]["coverage_id"] = str(uuid4())
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        with pytest.raises(ReviewReassessmentInvalid):
            _prepare(connection, sample, graph, packet)
        assert (
            connection.execute(
                "SELECT count(*) AS count FROM guidance_review_publications"
            ).fetchone()["count"]
            == 0
        )


def test_only_real_matching_publication_rows_can_materialize_review_overlay(unreviewed_original):
    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        with pytest.raises(ReviewReassessmentInvalid):
            read_review_overlay(
                connection,
                sample.scope,
                sample.event,
                uuid4(),
                review_job_id=sample.job.id,
                decisions=sample.service.repository,
            )
        prepared = _prepare(connection, sample)
        publication = _persist(connection, sample, prepared)
        for scope, event, job_id in (
            (HouseholdScope(uuid4()), sample.event, sample.job.id),
            (sample.scope, replace(sample.event, version=2), sample.job.id),
            (sample.scope, sample.event, uuid4()),
        ):
            with pytest.raises(ReviewReassessmentInvalid):
                read_review_overlay(
                    connection,
                    scope,
                    event,
                    publication,
                    review_job_id=job_id,
                    decisions=sample.service.repository,
                )


@pytest.mark.parametrize("fault", ["proof", "compiled", "revision"])
def test_persisted_row_is_replayed_instead_of_trusting_its_claimed_proof(
    unreviewed_original, fault
):
    sample = unreviewed_original
    with psycopg.connect(_psycopg_url(sample.url), row_factory=dict_row) as connection:
        prepared = _prepare(connection, sample)
        changed = {
            "proof": {"proof_sha256": "0" * 64},
            "compiled": {"compiled_json": "{}"},
            "revision": {"verifier_revision": "synthetic-unverified-v1"},
        }[fault]
        publication = _persist(connection, sample, replace(prepared, **changed))
        with pytest.raises(ReviewReassessmentInvalid):
            read_review_overlay(
                connection,
                sample.scope,
                sample.event,
                publication,
                review_job_id=sample.job.id,
                decisions=sample.service.repository,
            )
