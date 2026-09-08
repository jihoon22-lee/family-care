"""Source-scoped semantic work plans are durable and never call a provider."""

import psycopg
import pytest
from familycare_api.terms_knowledge.work_repository import TermsSemanticWorkRepository
from psycopg.rows import dict_row

from apps.api.tests.test_terms_knowledge_repository import (
    seeded_policy_database,  # noqa: F401
    semantic_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from workers.analyzer.tests.test_document_structure_repository import _psycopg_url

pytestmark = pytest.mark.integration


@pytest.fixture()
def semantic_context(request):
    return request.getfixturevalue("semantic_database")


def test_explicit_work_envelope_has_whole_required_regions_and_is_idempotent(semantic_context):
    url, scope, edition = semantic_context
    repository = TermsSemanticWorkRepository(url)
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
    job = repository.enqueue(scope, edition, (primary,))
    assert repository.enqueue(scope, edition, (primary,)).job_id == job.job_id
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        row = connection.execute(
            "SELECT * FROM terms_semantic_jobs WHERE id=%s", (job.job_id,)
        ).fetchone()
    assert row["state"] == "queued"
    assert row["attempts"] == 0
    assert row["envelope_json"]["primary_region_ids"] == [primary]
    assert {r["label"] for r in row["envelope_json"]["regions"]} == {
        "Article 1",
        "Appendix 1",
        "Footnote 1",
    }
    assert row["privacy_digest"]
    assert row["input_digest"] == plan.input_digest


def test_foreign_household_or_unknown_primary_cannot_enqueue(semantic_context):
    from uuid import UUID

    from familycare_api.clauses.errors import TermsEditionNotFound
    from familycare_api.common.scope import HouseholdScope
    from familycare_api.terms_knowledge.work_repository import SemanticWorkUnsupported

    url, scope, edition = semantic_context
    repository = TermsSemanticWorkRepository(url)
    with pytest.raises(TermsEditionNotFound):
        repository.enqueue(HouseholdScope(UUID(int=99, version=4)), edition, ("missing",))
    with pytest.raises(SemanticWorkUnsupported):
        repository.enqueue(scope, edition, ("missing",))
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone()[0] == 0


def test_automatic_work_accounts_for_unsupported_context_without_enqueuing_known_rules(
    semantic_context,
):
    from familycare_api.terms_knowledge.projector import TermsSemanticProjector

    url, _, _ = semantic_context
    repository = TermsSemanticWorkRepository(url)
    assert repository.prepare_pending() == 0
    assert TermsSemanticProjector(url).project_pending() == 1
    assert repository.prepare_pending() == 1
    assert repository.prepare_pending() == 0
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone()[0] == 0
        assert (
            connection.execute("SELECT outcome FROM terms_semantic_work_attempts").fetchone()[0]
            == "UNSUPPORTED"
        )


def test_privacy_revision_creates_new_request_without_rewriting_original(semantic_context):
    from familycare_api.terms_knowledge.repository import TermsSemanticRepository

    url, scope, edition = semantic_context
    repository = TermsSemanticWorkRepository(url)
    plan = TermsSemanticRepository(url).source_plan(scope, edition)
    primary = next(r.region_id for r in plan.snapshot.layout.regions if r.label == "Article 1")
    first = repository.enqueue(scope, edition, (primary,))
    with psycopg.connect(_psycopg_url(url)) as connection:
        connection.execute(
            "UPDATE family_members SET version=version+1 WHERE household_space_id=%s",
            (scope.household_space_id,),
        )
    second = repository.enqueue(scope, edition, (primary,))
    assert second.job_id != first.job_id
    with psycopg.connect(_psycopg_url(url)) as connection:
        assert connection.execute("SELECT count(*) FROM terms_semantic_jobs").fetchone()[0] == 2
