"""Exercise the coordinator against wholly synthetic retained PostgreSQL sources."""

import psycopg
import pytest
from familycare_worker.policy_jobs import PolicyStructuringJobQueue

from scripts.restructure_existing_documents import (
    PostgresReconstructionAdapter,
    capture_plan,
    reconstruct,
)
from workers.analyzer.tests.test_document_preparation import _seed_page
from workers.analyzer.tests.test_document_structure_repository import (
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)

pytestmark = pytest.mark.integration


def test_reconstruction_sql_resume_and_source_change(request):
    url, _ = request.getfixturevalue("structure_database")
    _, _, job_ids, _ = request.getfixturevalue("seeded_policy_database")
    queue = PolicyStructuringJobQueue(url)
    jobs = [queue.get_job(job_id) for job_id in job_ids]
    assert all(job is not None for job in jobs)
    for job in jobs:
        _seed_page(url, job)

    plan = capture_plan(url, [job.batch_item_id for job in jobs])
    adapter = PostgresReconstructionAdapter(url)
    first = reconstruct(plan, adapter=adapter, deadline_seconds=120)
    assert first.status == "PARTIAL"
    assert first.prepared == 3
    assert first.unresolved_pages == 3
    before = [adapter.observe(source) for source in plan.sources]
    assert all(p.metadata == "PREPARED" for p in before)

    second = reconstruct(plan, adapter=adapter, deadline_seconds=120)
    assert second.status == "PARTIAL"
    assert [adapter.observe(source) for source in plan.sources] == before

    with psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://")) as conn:
        conn.execute(
            "UPDATE extraction_blocks b SET text=b.text || ' synthetic edit' "
            "FROM extraction_pages p WHERE p.id=b.page_id AND p.extraction_id=%s",
            (jobs[0].extraction_id,),
        )
    refused = reconstruct(plan, adapter=adapter)
    assert refused.status == "SOURCE_CHANGED"
    assert refused.steps == 0
