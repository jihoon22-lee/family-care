"""Clause source reads carry the retained page manifest in both storage layouts."""

from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.clauses.source_projection import ClauseSourceProjectionReader
from familycare_worker import document_structure_repository as storage
from familycare_worker.document_structure import build_document_structure, plan_structure_chunks
from familycare_worker.document_structure_repository import DocumentStructureRepository
from psycopg.rows import dict_row

from apps.api.tests.test_structure_page_projection_integration import (
    _inputs,
    _prepare,
    _psycopg_url,
    seeded_policy_database,  # noqa: F401
    structure_database,  # noqa: F401
)
from apps.api.tests.test_structure_page_projection_integration import (
    stored_structure as stored_structure,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("paged", [False, True])
@pytest.mark.parametrize("unavailable", [False, True])
def test_clause_projection_retains_page_nodes_and_unresolved_without_scope_fallback(
    stored_structure: Any,
    monkeypatch: pytest.MonkeyPatch,
    paged: bool,
    unavailable: bool,
) -> None:
    url, job = stored_structure
    structure, plan = _inputs(job)
    source = structure.to_dict()["source_extraction"]
    if unavailable:
        source["pages"][0]["quality"]["classification"] = "OCR_REQUIRED"
    structure = build_document_structure(
        source,
        extraction_id=job.extraction_id,
        extraction_revision="synthetic-clause-projection",
    )
    plan = plan_structure_chunks(
        structure, max_content_chars=240, max_context_chars=240, max_chunks=200
    )
    monkeypatch.setattr(storage, "_INLINE_STRUCTURE_BYTES", 0 if paged else 64 * 1024 * 1024)
    generation = _prepare(DocumentStructureRepository(url), job, structure, plan)
    expected = structure.to_dict()
    with psycopg.connect(_psycopg_url(url), row_factory=dict_row) as connection:
        reader = ClauseSourceProjectionReader(connection, job.household_space_id)
        actual = reader.read(generation, (1,))
        assert actual is not None
        assert actual["lineage"] == expected["lineage"]
        assert actual["nodes"] == expected["nodes"]
        assert actual["pages"] == expected["pages"]
        assert actual["unresolved"] == expected["unresolved"]
        assert reader.read(generation, (2,)) is None
        assert reader.read(uuid4(), (1,)) is None
        assert ClauseSourceProjectionReader(connection, uuid4()).read(generation, (1,)) is None


@pytest.mark.parametrize("pages", [(), (0,), (-1,), tuple(range(1, 27))])
def test_clause_projection_rejects_invalid_or_unbounded_page_requests(
    pages: tuple[int, ...],
) -> None:
    with pytest.raises(ValueError, match="CLAUSE_SOURCE_PAGE_REQUEST_INVALID"):
        ClauseSourceProjectionReader(None, uuid4()).read(uuid4(), pages)
