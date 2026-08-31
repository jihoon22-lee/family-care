"""Single-process AnalysisJob runner for synthetic descriptor-only ingestion."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Protocol
from uuid import UUID

import psycopg

from familycare_worker.ai.event_structurer import EventStructuringRequest, structure_event
from familycare_worker.ai.evidence_loader import (
    EvidenceLoadError,
    EvidenceRepositoryUnavailable,
    PolicyEvidenceLoader,
)
from familycare_worker.ai.minimizer import EvidenceMinimizationError, minimize_evidence
from familycare_worker.ai.policy_pipeline import run_policy_batch_pipeline, run_policy_pipeline
from familycare_worker.ai.provider import (
    DEFAULT_STRUCTURER_MODEL,
    DEFAULT_VERIFIER_MODEL,
    AiProvider,
    EvidenceSlice,
    ProviderConfigurationError,
    ProviderRateLimitError,
    ProviderResponse,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from familycare_worker.ai.recommender import (
    DEFAULT_ASSISTANCE_MODEL,
    RecommendationCandidate,
    RecommendationFact,
    RecommendationRequest,
    RecommendationValidationError,
    recommend_clauses,
)
from familycare_worker.ai.schemas import CandidatePipelineResult
from familycare_worker.event_jobs import (
    EventStructuringQueue,
    StructuringErrorCode,
    StructuringJobNotFound,
    StructuringJobStateConflict,
    map_structuring_error,
)
from familycare_worker.jobs import (
    AnalysisJobRecord,
    JobNotFound,
    JobQueue,
    JobStateConflict,
)
from familycare_worker.ocr.models import (
    OcrCancelled,
    OcrConfigurationError,
    OcrExecutionError,
    OcrRenderError,
    OcrTempCleanupError,
    SelectiveOcrResult,
)
from familycare_worker.ocr.processor import SelectiveOcrProcessor
from familycare_worker.pdf.errors import (
    IntakeErrorCode,
    PdfIntakeError,
)
from familycare_worker.pdf.intake import open_source, validate_pdf
from familycare_worker.pdf.isolation import ParseOutcome, run_isolated_parser
from familycare_worker.pdf.workspace import Workspace, create_workspace
from familycare_worker.policy_candidates import (
    InvalidPolicyCandidateBatch,
    PolicyCandidateJobConflict,
    PolicyCandidateRepositoryUnavailable,
)
from familycare_worker.policy_jobs import (
    PolicyStructuringErrorCode,
    PolicyStructuringJobNotFound,
    PolicyStructuringJobRecord,
    PolicyStructuringJobStateConflict,
    PolicyStructuringNoEvidenceError,
    PolicyStructuringQueue,
    PolicyStructuringQueueUnavailable,
    map_policy_structuring_error,
)
from familycare_worker.recommendation_jobs import (
    InvalidRecommendationWork,
    RecommendationJobRecord,
    RecommendationQueue,
    RecommendationQueueUnavailable,
    RecommendationWorkItem,
)
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


class CandidateBatchPublisher(Protocol):
    """Persist one sanitized candidate batch and its bounded Evidence slices."""

    def publish(
        self,
        result: CandidatePipelineResult,
        evidence: tuple[EvidenceSlice, ...],
    ) -> None: ...


class PolicyCandidatePipelineRunner:
    """Run candidate analysis and publish only a sanitized candidate result."""

    def __init__(
        self,
        *,
        provider: AiProvider,
        publisher: CandidateBatchPublisher,
        structurer_model: str = DEFAULT_STRUCTURER_MODEL,
        verifier_model: str = DEFAULT_VERIFIER_MODEL,
    ) -> None:
        if not structurer_model or not verifier_model:
            raise ValueError("candidate model configuration is required")
        self.provider = provider
        self.publisher = publisher
        self.structurer_model = structurer_model
        self.verifier_model = verifier_model

    def run(self, *, evidence: Sequence[EvidenceSlice]) -> CandidatePipelineResult:
        bounded_evidence = tuple(evidence)
        result = run_policy_pipeline(
            evidence=bounded_evidence,
            provider=self.provider,
            structurer_model=self.structurer_model,
            verifier_model=self.verifier_model,
        )
        if result.candidates:
            self.publisher.publish(result, bounded_evidence)
        return result


class PolicyEvidenceLoaderLike(Protocol):
    def load(
        self,
        *,
        household_space_id: UUID,
        document_version_id: UUID,
        extraction_id: UUID,
    ) -> tuple[EvidenceSlice, ...]: ...

    def load_member_terms(
        self,
        *,
        household_space_id: UUID,
        family_member_id: UUID,
    ) -> tuple[str, ...]: ...


class PolicyJobCandidatePublisher(Protocol):
    def publish(
        self,
        *,
        job: PolicyStructuringJobRecord,
        worker_id: str,
        result: CandidatePipelineResult,
        evidence: Sequence[EvidenceSlice],
    ) -> tuple[UUID, ...]: ...


_POLICY_RESULT_ERRORS: dict[str, PolicyStructuringErrorCode] = {
    "CONFIGURATION_ERROR": "POLICY_STRUCTURING_AUTHENTICATION_FAILED",
    "RETRYABLE_PROVIDER_ERROR": "POLICY_STRUCTURING_PROVIDER_TIMEOUT",
    "VALIDATION_ERROR": "POLICY_STRUCTURING_INVALID_RESPONSE",
    "SUCCESS": "POLICY_STRUCTURING_INVALID_RESPONSE",
    "NEEDS_REVIEW": "POLICY_STRUCTURING_INVALID_RESPONSE",
}


class _PolicyStructuringLeaseLost(RuntimeError):
    """Internal control flow that carries no job or provider data."""


class _LeasedPolicyProvider:
    """Refresh the database lease immediately before every provider call."""

    def __init__(self, provider: AiProvider, heartbeat: Callable[[], bool]) -> None:
        self.provider = provider
        self.heartbeat = heartbeat

    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse:
        if not self.heartbeat():
            raise _PolicyStructuringLeaseLost
        return self.provider.complete(
            model=model,
            schema_name=schema_name,
            system_instruction=system_instruction,
            input_payload=input_payload,
        )


class PolicyStructuringJobRunner:
    """Run one private policy job without coupling provider success to import success."""

    def __init__(
        self,
        *,
        queue: PolicyStructuringQueue,
        evidence_loader: PolicyEvidenceLoaderLike,
        provider: AiProvider,
        publisher: PolicyJobCandidatePublisher,
        structurer_model: str = DEFAULT_STRUCTURER_MODEL,
        verifier_model: str = DEFAULT_VERIFIER_MODEL,
        lease_seconds: int = 180,
    ) -> None:
        if (
            not isinstance(structurer_model, str)
            or not 1 <= len(structurer_model) <= 128
            or not isinstance(verifier_model, str)
            or not 1 <= len(verifier_model) <= 128
            or isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or not 1 <= lease_seconds <= 3_600
        ):
            raise ValueError("invalid policy structuring runner configuration")
        self.queue = queue
        self.evidence_loader = evidence_loader
        self.provider = provider
        self.publisher = publisher
        self.structurer_model = structurer_model
        self.verifier_model = verifier_model
        self.lease_seconds = lease_seconds

    def run_once(self, worker_id: str) -> bool:
        """Process one due policy job and preserve commit ambiguity for lease recovery."""

        job = self.queue.claim_next_job(worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        try:
            evidence = self.evidence_loader.load(
                household_space_id=job.household_space_id,
                document_version_id=job.document_version_id,
                extraction_id=job.extraction_id,
            )
            if not evidence:
                raise PolicyStructuringNoEvidenceError
            member_terms = self.evidence_loader.load_member_terms(
                household_space_id=job.household_space_id,
                family_member_id=job.family_member_id,
            )
            minimized = minimize_evidence(evidence, sensitive_terms=member_terms)
            leased_provider = _LeasedPolicyProvider(
                self.provider,
                lambda: self.queue.heartbeat(
                    job.id,
                    worker_id,
                    lease_seconds=self.lease_seconds,
                ),
            )
            result = run_policy_batch_pipeline(
                evidence=minimized,
                provider=leased_provider,
                structurer_model=self.structurer_model,
                verifier_model=self.verifier_model,
            )
            if not result.candidates:
                self._safe_fail(
                    job.id,
                    worker_id,
                    _POLICY_RESULT_ERRORS[result.classification],
                )
                return True
            if not self.queue.heartbeat(
                job.id,
                worker_id,
                lease_seconds=self.lease_seconds,
            ):
                return True
            self.publisher.publish(
                job=job,
                worker_id=worker_id,
                result=result,
                evidence=minimized,
            )
        except EvidenceRepositoryUnavailable, PolicyCandidateRepositoryUnavailable:
            return True
        except _PolicyStructuringLeaseLost:
            return True
        except PolicyStructuringJobNotFound, PolicyStructuringJobStateConflict:
            return True
        except PolicyStructuringQueueUnavailable, PolicyCandidateJobConflict:
            return True
        except InvalidPolicyCandidateBatch, EvidenceLoadError, EvidenceMinimizationError:
            self._safe_fail(
                job.id,
                worker_id,
                "POLICY_STRUCTURING_INVALID_RESPONSE",
            )
        except PolicyStructuringNoEvidenceError:
            self._safe_fail(
                job.id,
                worker_id,
                "POLICY_STRUCTURING_NO_EVIDENCE",
            )
        except Exception as error:
            self._safe_fail(
                job.id,
                worker_id,
                map_policy_structuring_error(error),
            )
        return True

    def _safe_fail(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: PolicyStructuringErrorCode,
    ) -> None:
        try:
            self.queue.fail_job(job_id, worker_id, error_code)
        except (
            PolicyStructuringJobNotFound,
            PolicyStructuringJobStateConflict,
            PolicyStructuringQueueUnavailable,
        ):
            return


class EventStructuringJobRunner:
    """Claim one event job and persist only the validated structuring result."""

    def __init__(
        self,
        *,
        queue: EventStructuringQueue,
        provider: AiProvider,
        structurer_model: str = DEFAULT_STRUCTURER_MODEL,
        lease_seconds: int = 180,
    ) -> None:
        if not isinstance(structurer_model, str) or not 1 <= len(structurer_model) <= 128:
            raise ValueError("invalid event structurer model")
        if (
            isinstance(lease_seconds, bool)
            or not isinstance(lease_seconds, int)
            or lease_seconds <= 0
        ):
            raise ValueError("invalid lease duration")
        self.queue = queue
        self.provider = provider
        self.structurer_model = structurer_model
        self.lease_seconds = lease_seconds

    def run_once(self, worker_id: str) -> bool:
        """Process one due event job and return whether a claim was obtained."""

        job = self.queue.claim_next_job(worker_id, lease_seconds=self.lease_seconds)
        if job is None:
            return False
        try:
            if self.queue.cancel_if_event_version_changed(job, worker_id):
                return True
            result = structure_event(
                request=EventStructuringRequest(
                    situation=job.situation,
                    mode=job.mode,
                    event_date=job.event_date,
                    visit_date=job.visit_date,
                    normalization_hints=job.normalization_hints,
                ),
                provider=self.provider,
                model=self.structurer_model,
            )
            if not self.queue.heartbeat(job.id, worker_id):
                return True
            self.queue.complete_job(job, worker_id, result)
        except StructuringJobNotFound, StructuringJobStateConflict:
            return True
        except psycopg.Error:
            return True
        except Exception as error:
            self._safe_fail(job.id, worker_id, map_structuring_error(error))
        return True

    def _safe_fail(
        self,
        job_id: UUID,
        worker_id: str,
        error_code: StructuringErrorCode,
    ) -> None:
        try:
            self.queue.fail_job(job_id, worker_id, error_code)
        except StructuringJobNotFound, StructuringJobStateConflict, psycopg.Error:
            return


class RecommendationJobRunner:
    """Refine one local recommendation job with at most one provider call."""

    def __init__(
        self,
        *,
        queue: RecommendationQueue,
        provider: AiProvider,
        model: str = DEFAULT_ASSISTANCE_MODEL,
        provider_label: str = "openai",
        config_version: str = "event-clause-recommendations.v1",
    ) -> None:
        if not all(
            isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
            for value, maximum in (
                (model, 120),
                (provider_label, 64),
                (config_version, 64),
            )
        ):
            raise ValueError("invalid recommendation runner configuration")
        self.queue = queue
        self.provider = provider
        self.model = model
        self.provider_label = provider_label
        self.config_version = config_version

    def run_once(self, worker_id: str) -> bool:
        """Claim once, never retry a provider call, and retain local search on failure."""

        try:
            job = self.queue.claim_next_job(worker_id)
        except RecommendationQueueUnavailable:
            return False
        if job is None:
            return False
        work: RecommendationWorkItem | None = None
        try:
            work = self.queue.load_work(job)
            if not work.targets:
                self._safe_fallback(job, work, "NO_PENDING_TARGETS")
                return True
            request = _recommendation_request(work)
        except InvalidRecommendationWork, ValueError, RecommendationQueueUnavailable:
            self._safe_fallback(job, work, "LOCAL_CANDIDATES_INVALID")
            return True

        try:
            sensitive_terms = _recommendation_member_terms(self.queue, job)
            result = recommend_clauses(
                request=request,
                provider=self.provider,
                model=self.model,
                sensitive_terms=sensitive_terms,
            )
        except EvidenceLoadError, EvidenceMinimizationError, ValueError:
            self._safe_fallback(job, work, "PROVIDER_INPUT_REJECTED")
            return True
        except ProviderConfigurationError:
            self._safe_fallback(job, work, "PROVIDER_NOT_CONFIGURED")
            return True
        except ProviderTimeoutError:
            self._safe_fallback(job, work, "PROVIDER_TIMEOUT")
            return True
        except ProviderRateLimitError:
            self._safe_fallback(job, work, "PROVIDER_RATE_LIMIT")
            return True
        except ProviderUnavailableError:
            self._safe_fallback(job, work, "PROVIDER_UNAVAILABLE")
            return True
        except ProviderValidationError, RecommendationValidationError:
            self._safe_fallback(job, work, "PROVIDER_INVALID_RESPONSE")
            return True

        try:
            self.queue.complete_with_llm(
                job,
                work,
                result,
                provider_label=self.provider_label,
                model_label=self.model,
                config_version=self.config_version,
            )
        except InvalidRecommendationWork, RecommendationQueueUnavailable:
            return True
        return True

    def _safe_fallback(
        self,
        job: RecommendationJobRecord,
        work: RecommendationWorkItem | None,
        outcome_code: str,
    ) -> None:
        try:
            self.queue.complete_with_fallback(job, work, outcome_code)
        except InvalidRecommendationWork, RecommendationQueueUnavailable:
            return


def _recommendation_request(work: RecommendationWorkItem) -> RecommendationRequest:
    target = work.targets[0]
    return RecommendationRequest(
        situation=work.situation[:800],
        facts=tuple(
            RecommendationFact(
                field_id=field_id,
                value=value,
                confirmation=confirmation,
            )
            for field_id, value, confirmation in work.facts
        ),
        candidates=tuple(
            RecommendationCandidate(
                token=f"candidate-{index:02d}",
                contract_label=item.contract_label,
                coverage_label=item.coverage_label,
                clause_label=item.clause_label,
                excerpt=item.excerpt,
                page_start=item.page_start,
                page_end=item.page_end,
                citation_kind=item.citation_kind,
            )
            for index, item in enumerate(target.recommendations, start=1)
        ),
    )


def _recommendation_member_terms(
    queue: RecommendationQueue,
    job: RecommendationJobRecord,
) -> tuple[str, ...]:
    queue_loader = getattr(queue, "load_member_terms", None)
    if callable(queue_loader):
        terms = queue_loader(job)
        if not isinstance(terms, tuple):
            raise EvidenceLoadError
        return terms

    database_url = getattr(queue, "database_url", None)
    if not isinstance(database_url, str) or not database_url:
        raise EvidenceLoadError
    try:
        with psycopg.connect(database_url) as connection:
            row = connection.execute(
                """
                SELECT family_member_id
                FROM medical_events
                WHERE id = %s AND household_space_id = %s
                  AND deleted_at IS NULL
                """,
                (job.medical_event_id, job.household_space_id),
            ).fetchone()
    except psycopg.Error:
        raise EvidenceRepositoryUnavailable from None
    if row is None or not isinstance(row[0], UUID):
        raise EvidenceLoadError
    return PolicyEvidenceLoader(database_url).load_member_terms(
        household_space_id=job.household_space_id,
        family_member_id=row[0],
    )


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
        ocr_processor: SelectiveOcrProcessor | None = None,
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
        self.ocr_processor = ocr_processor
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
                    require_ocr=self.ocr_processor is not None,
                )
                if existing is not None:
                    self.repository.complete_with_existing(job, worker_id, existing)
                    return

                workspace = self._create_workspace(job, worker_id)
                if workspace is None:
                    return
                ocr_result: SelectiveOcrResult | None = None
                ocr_error: IntakeErrorCode | None = None
                cancelled = False
                lease_lost = False
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
                if outcome.metadata.get("cancelled") is True:
                    cancelled = True
                elif outcome.success and not self.queue.heartbeat(job.id, worker_id):
                    lease_lost = True
                elif outcome.success and self.ocr_processor is not None:
                    if not isinstance(workspace, Workspace):
                        ocr_error = IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED
                    else:
                        try:
                            ocr_result = self.ocr_processor.process(
                                outcome.result,
                                source.fd,
                                workspace,
                                document_version_id=document_version_id,
                                content_sha256=validated.content_sha256,
                                on_progress=lambda _processed: (
                                    not self.stop_requested()
                                    and self.queue.heartbeat(job.id, worker_id)
                                ),
                            )
                        except OcrCancelled:
                            cancelled = True
                        except OcrTempCleanupError:
                            ocr_error = IntakeErrorCode.TEMP_CLEANUP_FAILED
                        except OcrExecutionError as error:
                            ocr_error = (
                                IntakeErrorCode.EXTRACTION_TIMEOUT
                                if error.code == "OCR_TIMEOUT"
                                else IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED
                            )
                        except OcrConfigurationError, OcrRenderError:
                            ocr_error = IntakeErrorCode.PDF_CORRUPT
                        except psycopg.Error:
                            ocr_error = IntakeErrorCode.RESOURCE_LIMIT_EXCEEDED
                        except Exception:
                            ocr_error = IntakeErrorCode.PDF_CORRUPT

                if not self._cleanup_workspace(workspace, job, worker_id):
                    return
                if cancelled or lease_lost:
                    return
                if not outcome.success:
                    self._safe_fail(
                        job,
                        worker_id,
                        outcome.error_code or IntakeErrorCode.PDF_CORRUPT,
                    )
                    return
                if ocr_error is not None:
                    self._safe_fail(job, worker_id, ocr_error)
                    return
                if not self.queue.heartbeat(job.id, worker_id):
                    return
                try:
                    self.repository.persist_success(
                        job,
                        worker_id,
                        document_version_id,
                        outcome.result,
                        ocr=ocr_result,
                        ocr_attempted=self.ocr_processor is not None,
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


__all__ = [
    "AnalysisJobRunner",
    "CandidateBatchPublisher",
    "EventStructuringJobRunner",
    "ParserRunner",
    "PolicyCandidatePipelineRunner",
    "WorkspaceFactory",
    "WorkspaceLike",
]
