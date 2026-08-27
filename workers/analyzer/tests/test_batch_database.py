from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from familycare_api.documents.batch_repository import (
    BatchRepository as ApiBatchRepository,
)
from familycare_api.documents.batch_repository import BatchSourceSelection
from familycare_api.documents.import_sources import ImportSourceCatalog
from familycare_worker.ai.evidence_loader import PolicyEvidenceLoader
from familycare_worker.ai.provider import ProviderResponse
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore
from familycare_worker.imports.batch import BatchRunner
from familycare_worker.imports.password_scope import PasswordScope
from familycare_worker.ocr.processor import SelectiveOcrProcessor
from familycare_worker.policy_candidates import PolicyCandidatePublisher
from familycare_worker.policy_jobs import PolicyStructuringJobQueue
from familycare_worker.repository import BatchRepository
from familycare_worker.runner import PolicyStructuringJobRunner
from psycopg.rows import dict_row

from workers.analyzer.tests.synthetic_pdf_factory import make_low_quality_pdf
from workers.analyzer.tests.test_batch_runner import BatchOcrEngine, BatchOcrRenderer

pytestmark = pytest.mark.integration


class _SyntheticPolicyProvider:
    def complete(
        self,
        *,
        schema_name: str,
        input_payload: Mapping[str, object],
        **_: object,
    ) -> ProviderResponse:
        evidence = input_payload["evidence"]
        assert isinstance(evidence, list) and len(evidence) == 1
        evidence_item = evidence[0]
        assert isinstance(evidence_item, Mapping)
        evidence_id = evidence_item["evidence_id"]
        if "batch_structurer" in schema_name:
            payload: Mapping[str, object] = {
                "schema_version": "2",
                "policy": {
                    "schema_version": "1",
                    "candidate_id": "00000000-0000-4000-8000-000000000801",
                    "candidate_kind": "policy_contract",
                    "fields": [
                        {
                            "field_id": "insurer",
                            "value": "Sample Insurer",
                            "evidence_ids": [evidence_id],
                        },
                        {
                            "field_id": "product_name",
                            "value": "Sample Plan",
                            "evidence_ids": [evidence_id],
                        },
                    ],
                },
                "riders": [],
            }
        else:
            candidate = input_payload["candidate"]
            assert isinstance(candidate, Mapping)
            payload = {
                "schema_version": "1",
                "candidate_id": candidate["candidate_id"],
                "decision": "approved",
                "evidence_ids": [evidence_id],
                "issue_codes": [],
            }
        return ProviderResponse(payload=payload, request_id="synthetic-policy-request")


def _url() -> str:
    value = os.getenv("FAMILYCARE_DATABASE_URL")
    if not value:
        pytest.skip("FAMILYCARE_DATABASE_URL is required")
    return value


def _psycopg_url(value: str) -> str:
    return value.replace("postgresql+psycopg://", "postgresql://", 1)


def test_batch_runner_persists_extraction_and_archive_atomically(tmp_path: Path) -> None:
    database_url = _url()
    household_id = uuid4()
    user_id = uuid4()
    member_id = uuid4()
    suffix = household_id.hex[:12]
    import_root = tmp_path / "import"
    work_root = tmp_path / "work"
    archive_root = tmp_path / "archive"
    import_root.mkdir()
    work_root.mkdir()
    archive_root.mkdir()
    make_low_quality_pdf(import_root / "sample-policy.pdf")
    catalog = ImportSourceCatalog(import_root)
    source = catalog.resolve(catalog.list()[0].source_id)

    with psycopg.connect(_psycopg_url(database_url)) as connection:
        connection.execute(
            """
            INSERT INTO household_spaces (id, space_key, display_name)
            VALUES (%s, %s, 'Synthetic Worker Household')
            """,
            (household_id, f"synthetic-worker-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO app_users (
                id, household_space_id, username, display_name, password_hash
            )
            VALUES (%s, %s, %s, 'Synthetic Worker Admin', '$argon2id$synthetic-hash')
            """,
            (user_id, household_id, f"synthetic-worker-{suffix}"),
        )
        connection.execute(
            """
            INSERT INTO family_members (id, household_space_id, display_name, internal_alias)
            VALUES (%s, %s, 'Family Member A', %s)
            """,
            (member_id, household_id, f"synthetic-worker-member-{suffix}"),
        )

    try:
        created = ApiBatchRepository(database_url).create(
            household_space_id=household_id,
            created_by=user_id,
            family_member_id=member_id,
            sources=(BatchSourceSelection(source=source, document_kind="policy"),),
        )
        assert created is not None
        runner = BatchRunner(
            repository=BatchRepository(database_url),
            document_root=import_root,
            work_root=work_root,
            archive_store=ArchiveStore(archive_root),
            master_key=MasterKey.synthetic(b"s" * 32, key_version="synthetic-v1"),
            password_scope=PasswordScope(
                batch_id=created.batch_id,
                password="synthetic-unused-password",
                expires_at=datetime.now(UTC) + timedelta(minutes=5),
            ),
            ocr_processor=SelectiveOcrProcessor(BatchOcrRenderer(), BatchOcrEngine),
        )

        assert runner.run_once("synthetic-worker") is True

        status = ApiBatchRepository(database_url).get(
            household_space_id=household_id,
            batch_id=created.batch_id,
        )
        assert status is not None
        assert status.state == "succeeded", status
        assert status.items[0].state == "succeeded"
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            persisted = connection.execute(
                """
                SELECT document.status, document.document_kind, count(archive.id) AS archives,
                       count(DISTINCT extraction.id) AS extractions,
                       count(DISTINCT ocr_layer.id) AS ocr_layers,
                       count(DISTINCT ocr_page.id) AS ocr_pages,
                       count(DISTINCT ocr_block.id) AS ocr_blocks,
                       item.processed_document_version_id = version.id
                         AS processed_version_matches,
                       item.ocr_state, item.ocr_pages_processed,
                       item.ocr_warning_codes
                FROM document_batch_items AS item
                JOIN documents AS document ON document.id = item.document_id
                JOIN document_versions AS version ON version.document_id = document.id
                JOIN managed_archives AS archive ON archive.document_version_id = version.id
                JOIN extractions AS extraction ON extraction.document_version_id = version.id
                JOIN ocr_layers AS ocr_layer ON ocr_layer.extraction_id = extraction.id
                JOIN ocr_pages AS ocr_page ON ocr_page.ocr_layer_id = ocr_layer.id
                JOIN ocr_blocks AS ocr_block ON ocr_block.ocr_page_id = ocr_page.id
                WHERE item.batch_id = %s
                GROUP BY document.status, document.document_kind,
                         item.processed_document_version_id, version.id,
                         item.ocr_state, item.ocr_pages_processed,
                         item.ocr_warning_codes
                """,
                (created.batch_id,),
            ).fetchone()
            evidence = connection.execute(
                """
                SELECT e.id, e.document_version_id, e.extraction_id,
                       e.household_space_id,
                       e.document_version_id = version.id AS version_matches,
                       e.extraction_id = extraction.id AS extraction_matches,
                       e.content_sha256 = version.content_sha256 AS hash_matches,
                       e.physical_page, e.x0, e.y0, e.x1, e.y1, e.review_state
                FROM document_batch_items AS item
                JOIN document_batches AS batch ON batch.id = item.batch_id
                JOIN documents AS document ON document.id = item.document_id
                JOIN document_versions AS version ON version.document_id = document.id
                JOIN extractions AS extraction ON extraction.document_version_id = version.id
                JOIN evidence AS e
                  ON e.document_version_id = version.id
                 AND e.extraction_id = extraction.id
                WHERE item.batch_id = %s
                ORDER BY e.physical_page
                """,
                (created.batch_id,),
            ).fetchall()
            structuring_jobs = connection.execute(
                """
                SELECT job.household_space_id, job.batch_item_id,
                       job.family_member_id, job.document_version_id,
                       job.extraction_id, job.policy_aggregate_id,
                       job.state, job.pipeline_version, job.attempts,
                       job.max_attempts, job.error_code
                FROM policy_structuring_jobs AS job
                JOIN document_batch_items AS item ON item.id = job.batch_item_id
                WHERE item.batch_id = %s
                """,
                (created.batch_id,),
            ).fetchall()
        assert persisted == (
            "ready",
            "policy",
            1,
            1,
            1,
            1,
            1,
            True,
            "completed",
            1,
            [],
        )
        assert len(evidence) == 1
        evidence_row = evidence[0]
        assert evidence_row[3:] == (
            household_id,
            True,
            True,
            True,
            1,
            None,
            None,
            None,
            None,
            "NEEDS_REVIEW",
        )
        slices = PolicyEvidenceLoader(database_url).load(
            household_space_id=household_id,
            document_version_id=evidence_row[1],
            extraction_id=evidence_row[2],
        )
        assert len(slices) == 1
        assert slices[0].evidence_id == evidence_row[0]
        assert slices[0].document_version_id == evidence_row[1]
        assert slices[0].page == 1
        assert slices[0].text == "Synthetic OCR Evidence"
        assert slices[0].bbox is None
        assert slices[0].document_kind == "policy"
        assert PolicyEvidenceLoader(database_url).load_member_terms(
            household_space_id=household_id,
            family_member_id=member_id,
        ) == ("Family Member A", f"synthetic-worker-member-{suffix}")
        assert len(structuring_jobs) == 1
        structuring_job = structuring_jobs[0]
        assert structuring_job[0] == household_id
        assert structuring_job[1].int != 0
        assert structuring_job[2:5] == (member_id, evidence_row[1], evidence_row[2])
        assert structuring_job[5].int != 0
        assert structuring_job[6:] == (
            "queued",
            "policy-candidate-batch-v2",
            0,
            5,
            None,
        )
        policy_runner = PolicyStructuringJobRunner(
            queue=PolicyStructuringJobQueue(database_url),
            evidence_loader=PolicyEvidenceLoader(database_url),
            provider=_SyntheticPolicyProvider(),
            publisher=PolicyCandidatePublisher(database_url),
            structurer_model="synthetic-structurer",
            verifier_model="synthetic-verifier",
        )
        assert policy_runner.run_once("synthetic-policy-worker") is True
        with psycopg.connect(_psycopg_url(database_url), row_factory=dict_row) as connection:
            structured = connection.execute(
                """
                SELECT job.state, candidate.aggregate_id, candidate.status,
                       candidate.structuring_job_id, candidate.source_candidate_id,
                       evidence.bounded_excerpt
                FROM policy_structuring_jobs AS job
                JOIN analysis_candidate_versions AS candidate
                  ON candidate.structuring_job_id = job.id
                JOIN analysis_candidate_evidence AS evidence
                  ON evidence.candidate_version_id = candidate.id
                WHERE job.batch_item_id = %s
                ORDER BY evidence.field_id
                """,
                (structuring_job[1],),
            ).fetchall()
            ledger_count = connection.execute(
                "SELECT count(*) FROM policy_contracts WHERE id = %s",
                (structuring_job[5],),
            ).fetchone()
        assert len(structured) == 2
        assert all(row["state"] == "succeeded" for row in structured)
        assert all(row["aggregate_id"] == structuring_job[5] for row in structured)
        assert all(row["status"] == "NEEDS_REVIEW" for row in structured)
        assert all(row["structuring_job_id"].int != 0 for row in structured)
        assert all(row["source_candidate_id"].int != 0 for row in structured)
        assert all(row["bounded_excerpt"] == "Synthetic OCR Evidence" for row in structured)
        assert ledger_count is not None and ledger_count["count"] == 0
        assert len(list(archive_root.iterdir())) == 1
        assert list(work_root.iterdir()) == []
    finally:
        with psycopg.connect(_psycopg_url(database_url)) as connection:
            connection.execute(
                "DELETE FROM analysis_candidate_versions WHERE structuring_job_id IN "
                "(SELECT id FROM policy_structuring_jobs WHERE batch_item_id IN "
                "(SELECT id FROM document_batch_items WHERE batch_id IN "
                "(SELECT id FROM document_batches WHERE household_space_id = %s)))",
                (household_id,),
            )
            connection.execute(
                "DELETE FROM policy_structuring_jobs WHERE batch_item_id IN "
                "(SELECT id FROM document_batch_items WHERE batch_id IN "
                "(SELECT id FROM document_batches WHERE household_space_id = %s))",
                (household_id,),
            )
            document_rows = connection.execute(
                "SELECT document_id FROM document_batch_items WHERE batch_id IN "
                "(SELECT id FROM document_batches WHERE household_space_id = %s)",
                (household_id,),
            ).fetchall()
            document_ids = [row[0] for row in document_rows if row[0] is not None]
            connection.execute(
                "DELETE FROM document_batches WHERE household_space_id = %s",
                (household_id,),
            )
            if document_ids:
                connection.execute(
                    "DELETE FROM evidence WHERE document_version_id IN "
                    "(SELECT id FROM document_versions WHERE document_id = ANY(%s))",
                    (document_ids,),
                )
                connection.execute(
                    "DELETE FROM managed_archives WHERE document_version_id IN "
                    "(SELECT id FROM document_versions WHERE document_id = ANY(%s))",
                    (document_ids,),
                )
                connection.execute("DELETE FROM documents WHERE id = ANY(%s)", (document_ids,))
            connection.execute("DELETE FROM app_users WHERE id = %s", (user_id,))
            connection.execute("DELETE FROM family_members WHERE id = %s", (member_id,))
            connection.execute("DELETE FROM household_spaces WHERE id = %s", (household_id,))
