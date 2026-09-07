"""Exact manifests bind old knowledge sources without rewriting their snapshot."""

import hashlib
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from familycare_api.common.scope import HouseholdScope
from psycopg.rows import dict_row

from apps.api.tests.test_insurance_document_inventory_integration import (
    EVIDENCE_ID,
    HOUSEHOLD_ID,
    POLICY_VERSION_ID,
)
from apps.api.tests.test_insurance_reconciliation_repository_integration import (
    RUN_ID,
    _database_url,
    _prepare,
)

pytestmark = pytest.mark.integration


def _source() -> tuple[str, dict[str, Any]]:
    url = _database_url().replace("postgresql+psycopg://", "postgresql://", 1)
    _prepare(url)
    binding = uuid4()
    alias_digest = hashlib.sha256(b"synthetic-certificate-source").hexdigest()
    with psycopg.connect(url, row_factory=dict_row) as connection:
        connection.execute(
            "INSERT INTO private_knowledge_document_bindings(id,import_run_id,household_space_id,"
            "source_alias,source_alias_digest_sha256,binding_decision,binding_reason_code,"
            "content_digest_decision,page_count_decision,document_kind_decision,source_record_json,"
            "source_record_digest_sha256) VALUES (%s,%s,%s,'synthetic-certificate-source',%s,"
            "'UNKNOWN','NO_EXACT_BINDING','UNKNOWN','UNKNOWN','UNKNOWN','{}'::jsonb,%s)",
            (binding, RUN_ID, HOUSEHOLD_ID, alias_digest, "9" * 64),
        )
        version = connection.execute(
            "SELECT content_sha256,page_count FROM document_versions WHERE id=%s",
            (POLICY_VERSION_ID,),
        ).fetchone()
    assert version is not None
    return url, {
        "schema_version": "knowledge-source-bindings-v1",
        "import_run_id": str(RUN_ID),
        "package_digest_sha256": "1" * 64,
        "entries": [
            {
                "document_binding_id": str(binding),
                "source_alias_digest_sha256": alias_digest,
                "document_version_id": str(POLICY_VERSION_ID),
                "evidence_id": str(EVIDENCE_ID),
                "content_sha256": version["content_sha256"],
                "page_count": version["page_count"],
                "document_kind": "policy",
                "expected_current_binding_id": None,
            }
        ],
    }


def test_source_manifest_is_exact_idempotent_and_preserves_snapshot() -> None:
    from familycare_api.insurance_reconciliation.source_bindings import (
        KnowledgeSourceBindingRepository,
        KnowledgeSourceManifest,
    )

    url, payload = _source()
    repository = KnowledgeSourceBindingRepository(url)
    manifest = KnowledgeSourceManifest.model_validate(payload)
    scope = HouseholdScope(HOUSEHOLD_ID)
    with psycopg.connect(url) as connection:
        original = connection.execute(
            "SELECT to_jsonb(b) FROM private_knowledge_document_bindings b WHERE id=%s",
            (manifest.entries[0].document_binding_id,),
        ).fetchone()
    ids = repository.apply_manifest(scope, manifest)
    assert len(ids) == 1
    assert repository.apply_manifest(scope, manifest) == ids
    with psycopg.connect(url) as connection:
        assert (
            connection.execute(
                "SELECT to_jsonb(b) FROM private_knowledge_document_bindings b WHERE id=%s",
                (manifest.entries[0].document_binding_id,),
            ).fetchone()
            == original
        )
        assert connection.execute(
            "SELECT count(*) FROM private_knowledge_source_bindings WHERE import_run_id=%s",
            (RUN_ID,),
        ).fetchone() == (1,)
        with pytest.raises(psycopg.Error), connection.transaction():
            connection.execute(
                "DELETE FROM private_knowledge_source_bindings WHERE id=%s", (ids[0],)
            )


@pytest.mark.parametrize(
    "change", ["content", "pages", "kind", "alias", "package", "scope", "stale"]
)
def test_source_manifest_rejects_mismatched_or_stale_identity(change: str) -> None:
    from familycare_api.insurance_reconciliation.source_bindings import (
        KnowledgeSourceBindingError,
        KnowledgeSourceBindingRepository,
        KnowledgeSourceManifest,
    )

    url, payload = _source()
    entry = payload["entries"][0]
    scope = HouseholdScope(HOUSEHOLD_ID)
    if change == "content":
        entry["content_sha256"] = "f" * 64
    elif change == "pages":
        entry["page_count"] += 1
    elif change == "kind":
        entry["document_kind"] = "terms"
    elif change == "alias":
        entry["source_alias_digest_sha256"] = "f" * 64
    elif change == "package":
        payload["package_digest_sha256"] = "f" * 64
    elif change == "scope":
        scope = HouseholdScope(uuid4())
    else:
        entry["expected_current_binding_id"] = str(uuid4())
    with pytest.raises(KnowledgeSourceBindingError, match="KNOWLEDGE_SOURCE_BINDING_INVALID"):
        KnowledgeSourceBindingRepository(url).apply_manifest(
            scope, KnowledgeSourceManifest.model_validate(payload)
        )


def test_source_binding_replacement_is_atomic_and_preserves_old_proof() -> None:
    from familycare_api.insurance_reconciliation.source_bindings import (
        KnowledgeSourceBindingError,
        KnowledgeSourceBindingRepository,
        KnowledgeSourceManifest,
    )

    url, payload = _source()
    scope = HouseholdScope(HOUSEHOLD_ID)
    repository = KnowledgeSourceBindingRepository(url)
    first = repository.apply_manifest(scope, KnowledgeSourceManifest.model_validate(payload))[0]
    with psycopg.connect(url) as connection:
        evidence = connection.execute(
            "INSERT INTO evidence(household_space_id,document_version_id,extraction_id,"
            "content_sha256,physical_page,review_state) SELECT "
            "household_space_id,document_version_id,"
            "extraction_id,content_sha256,2,review_state FROM evidence WHERE id=%s RETURNING id",
            (EVIDENCE_ID,),
        ).fetchone()[0]
    payload["entries"][0]["evidence_id"] = str(evidence)
    payload["entries"][0]["expected_current_binding_id"] = str(first)
    payload["entries"].append(
        dict(
            payload["entries"][0],
            document_binding_id=str(uuid4()),
            source_alias_digest_sha256="f" * 64,
            expected_current_binding_id=None,
        )
    )
    with pytest.raises(KnowledgeSourceBindingError):
        repository.apply_manifest(scope, KnowledgeSourceManifest.model_validate(payload))
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT id,is_current FROM private_knowledge_source_bindings"
        ).fetchall() == [(first, True)]
    payload["entries"].pop()
    second = repository.apply_manifest(scope, KnowledgeSourceManifest.model_validate(payload))[0]
    assert second != first
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT is_current FROM private_knowledge_source_bindings WHERE id=%s", (first,)
        ).fetchone() == (False,)
        assert connection.execute(
            "SELECT count(*) FROM private_knowledge_source_bindings"
        ).fetchone() == (2,)


def test_concurrent_source_manifest_retries_publish_once() -> None:
    from familycare_api.insurance_reconciliation.source_bindings import (
        KnowledgeSourceBindingRepository,
        KnowledgeSourceManifest,
    )

    url, payload = _source()
    repository = KnowledgeSourceBindingRepository(url)
    manifest = KnowledgeSourceManifest.model_validate(payload)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(
            executor.map(
                lambda _: repository.apply_manifest(HouseholdScope(HOUSEHOLD_ID), manifest),
                range(2),
            )
        )
    assert len(results[0]) == 1 and results[0] == results[1]


@pytest.mark.parametrize("change", ["content", "deleted", "alias", "old_run"])
def test_idempotent_binding_rechecks_current_source_identity(change: str) -> None:
    from familycare_api.insurance_reconciliation.source_bindings import (
        KnowledgeSourceBindingError,
        KnowledgeSourceBindingRepository,
        KnowledgeSourceManifest,
    )

    url, payload = _source()
    repository = KnowledgeSourceBindingRepository(url)
    manifest = KnowledgeSourceManifest.model_validate(payload)
    scope = HouseholdScope(HOUSEHOLD_ID)
    repository.apply_manifest(scope, manifest)
    with psycopg.connect(url) as connection:
        if change == "content":
            connection.execute(
                "UPDATE document_versions SET content_sha256=%s WHERE id=%s",
                ("f" * 64, POLICY_VERSION_ID),
            )
        elif change == "deleted":
            connection.execute(
                "UPDATE documents SET deleted_at=clock_timestamp() WHERE id=(SELECT document_id "
                "FROM document_versions WHERE id=%s)",
                (POLICY_VERSION_ID,),
            )
        elif change == "alias":
            connection.execute(
                "UPDATE private_knowledge_document_bindings SET source_alias_digest_sha256=%s "
                "WHERE id=%s",
                ("f" * 64, manifest.entries[0].document_binding_id),
            )
        else:
            connection.execute(
                "UPDATE private_knowledge_import_runs SET "
                "is_current=false,superseded_at=clock_timestamp() WHERE id=%s",
                (RUN_ID,),
            )
    with pytest.raises(KnowledgeSourceBindingError):
        repository.apply_manifest(scope, manifest)


def test_source_binding_history_blocks_destructive_downgrade() -> None:
    from alembic import command
    from alembic.config import Config
    from familycare_api.insurance_reconciliation.source_bindings import (
        KnowledgeSourceBindingRepository,
        KnowledgeSourceManifest,
    )
    from sqlalchemy.exc import DBAPIError

    url, payload = _source()
    KnowledgeSourceBindingRepository(url).apply_manifest(
        HouseholdScope(HOUSEHOLD_ID), KnowledgeSourceManifest.model_validate(payload)
    )
    with pytest.raises(DBAPIError, match="knowledge source binding history must be retained"):
        command.downgrade(Config("apps/api/alembic.ini"), "0032_unclassified_riders")
    with psycopg.connect(url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM private_knowledge_source_bindings"
        ).fetchone() == (1,)
