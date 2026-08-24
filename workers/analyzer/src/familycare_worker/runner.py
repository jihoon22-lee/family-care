"""Single-process AnalysisJob runner for synthetic descriptor-only ingestion."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import psycopg

from familycare_worker.jobs import (
    AnalysisJobRecord,
    JobNotFound,
    JobQueue,
    JobStateConflict,
)
from familycare_worker.pdf.errors import (
    IntakeErrorCode,
    PdfIntakeError,
)
from familycare_worker.pdf.intake import open_source, validate_pdf
from familycare_worker.pdf.isolation import ParseOutcome, run_isolated_parser
from familycare_worker.pdf.workspace import Workspace, create_workspace
from familycare_worker.repository import (
    DocumentStateConflict,
    ExtractionRepository,
    InvalidExtractionResult,
)

LOGGER = logging.getLogger("familycare.worker")


class WorkspaceLike(Protocol):
    def close_and_cleanup(self, *, raise_on_failure: bool = True) -> bool: ...


class ParserRunner(Protocol):
    def __call__(
        self,
        source_fd: int,
        settings_json: str,
        *,
        on_progress: Callable[[], bool] | None = None,
        progress_interval_seconds: float = 30.0,
    ) -> ParseOutcome: ...


WorkspaceFactory = Callable[[Path], WorkspaceLike]


def _default_workspace_factory(root: Path) -> Workspace:
    return create_workspace(root)


class AnalysisJobRunner:
    """Claim and process at most one job at a time."""

    def __init__(
        self,
        queue: JobQueue,
        repository: ExtractionRepository,
        *,
        document_root: Path,
        work_root: Path,
        lease_seconds: int = 180,
        heartbeat_interval_seconds: float = 30.0,
        parser_runner: ParserRunner = run_isolated_parser,
        workspace_factory: WorkspaceFactory = _default_workspace_factory,
        logger: logging.Logger = LOGGER,
        stop_requested: Callable[[], bool] = lambda: False,
    ) -> None:
        document_root = Path(document_root)
        work_root = Path(work_root)
        if not document_root.is_absolute() or not document_root.is_dir():
            raise ValueError("document root must be an absolute directory")
        if not work_root.is_absolute() or not work_root.is_dir():
            raise ValueError("work root must be an absolute directory")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ValueError("invalid lease duration")
        if not 0 < heartbeat_interval_seconds < lease_seconds:
            raise ValueError("invalid heartbeat interval")
        self.queue = queue
        self.repository = repository
        self.document_root = document_root
        self.work_root = work_root
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.parser_runner = parser_runner
        self.workspace_factory = workspace_factory
        self.logger = logger
        self.stop_requested = stop_requested

    def run_once(self, worker_id: str) -> bool:
        """Process one due job and return whether a claim was obtained."""

        job = self.queue.claim_next_job(worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        try:
            self._run_claimed_job(job, worker_id)
        except JobNotFound, JobStateConflict:
            return True
        except psycopg.Error:
            self._safe_fail(job, worker_id, IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED)
        except Exception:
            self._safe_fail(job, worker_id, IntakeErrorCode.PDF_CORRUPT)
        return True

    def _run_claimed_job(self, job: AnalysisJobRecord, worker_id: str) -> None:
        try:
            with open_source(self.document_root, job.source_key) as source:
                validated = validate_pdf(source)
                document_version_id = self.repository.prepare_document_version(
                    job,
                    worker_id,
                    validated,
                )
                existing = self.repository.find_succeeded_extraction(
                    document_version_id,
                    job.extractor_config_hash,
                )
                if existing is not None:
                    self.repository.complete_with_existing(job, worker_id, existing)
                    return

                workspace = self._create_workspace(job, worker_id)
                if workspace is None:
                    return
                try:
                    settings_json = self._child_settings_json(
                        job,
                        document_version_id=document_version_id,
                        content_sha256=validated.content_sha256,
                    )
                    outcome = self.parser_runner(
                        source.fd,
                        settings_json,
                        on_progress=lambda: (
                            not self.stop_requested() and self.queue.heartbeat(job.id, worker_id)
                        ),
                        progress_interval_seconds=self.heartbeat_interval_seconds,
                    )
                except Exception:
                    outcome = ParseOutcome(
                        success=False,
                        error_code=IntakeErrorCode.PDF_CORRUPT,
                        error_message="parser failed",
                    )

                if not self._cleanup_workspace(workspace, job, worker_id):
                    return
                if outcome.metadata.get("cancelled") is True:
                    return
                if not outcome.success:
                    self._safe_fail(
                        job,
                        worker_id,
                        outcome.error_code or IntakeErrorCode.PDF_CORRUPT,
                    )
                    return
                if not self.queue.heartbeat(job.id, worker_id):
                    return
                try:
                    self.repository.persist_success(
                        job,
                        worker_id,
                        document_version_id,
                        outcome.result,
                    )
                except InvalidExtractionResult:
                    self._safe_fail(job, worker_id, IntakeErrorCode.PDF_CORRUPT)
        except PdfIntakeError as error:
            self._safe_fail(job, worker_id, error.code)
        except DocumentStateConflict:
            self._safe_fail(job, worker_id, IntakeErrorCode.INVALID_REQUEST)

    def _create_workspace(
        self,
        job: AnalysisJobRecord,
        worker_id: str,
    ) -> WorkspaceLike | None:
        try:
            return self.workspace_factory(self.work_root)
        except Exception:
            self._safe_fail(job, worker_id, IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED)
            return None

    def _cleanup_workspace(
        self,
        workspace: WorkspaceLike,
        job: AnalysisJobRecord,
        worker_id: str,
    ) -> bool:
        try:
            cleaned = workspace.close_and_cleanup(raise_on_failure=False)
        except Exception:
            cleaned = False
        if cleaned:
            return True
        self.logger.error("workspace_cleanup_failed job_id=%s", job.id)
        self._safe_fail(job, worker_id, IntakeErrorCode.TEMP_CLEANUP_FAILED)
        return False

    def _safe_fail(
        self,
        job: AnalysisJobRecord,
        worker_id: str,
        code: IntakeErrorCode,
    ) -> None:
        try:
            self.queue.fail_job(job.id, worker_id, code)
        except JobNotFound, JobStateConflict, psycopg.Error:
            return

    @staticmethod
    def _child_settings_json(
        job: AnalysisJobRecord,
        *,
        document_version_id: object,
        content_sha256: str,
    ) -> str:
        extractor_config = job.settings["extractor_config"]
        return json.dumps(
            {
                "content_sha256": content_sha256,
                "document_version_id": str(document_version_id),
                "extractor_config_hash": job.extractor_config_hash,
                "quality_rule_version": extractor_config["quality_rule_version"],
                "table_strategy": extractor_config["table_strategy"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )


__all__ = ["AnalysisJobRunner", "ParserRunner", "WorkspaceFactory", "WorkspaceLike"]
