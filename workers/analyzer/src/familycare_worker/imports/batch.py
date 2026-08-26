"""Per-file lifecycle for password-protected private document batches."""

from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import UUID, uuid4

from pypdf import PdfReader, PdfWriter

from familycare_worker.archive.crypto import ArchiveMetadata
from familycare_worker.archive.keys import MasterKey
from familycare_worker.archive.store import ArchiveStore, ArchiveStoreError
from familycare_worker.imports.cleanup import cleanup_workspace
from familycare_worker.imports.password_scope import PasswordScope
from familycare_worker.imports.secret_channel import BatchPasswordRegistry
from familycare_worker.ocr.models import (
    OcrCancelled,
    OcrConfigurationError,
    OcrExecutionError,
    OcrRenderError,
    OcrTempCleanupError,
    SelectiveOcrResult,
)
from familycare_worker.ocr.processor import SelectiveOcrProcessor
from familycare_worker.pdf.errors import DocumentTooLarge, PageLimitExceeded, PdfIntakeError
from familycare_worker.pdf.intake import (
    OpenedSource,
    ValidatedPdf,
    open_source,
    stream_sha256,
    validate_pdf,
)
from familycare_worker.pdf.isolation import ParseOutcome, run_isolated_parser
from familycare_worker.pdf.limits import MAX_INPUT_BYTES, MAX_PDF_PAGES
from familycare_worker.pdf.workspace import Workspace, create_workspace

LOGGER = logging.getLogger("familycare.worker")
_EXTRACTOR_CONFIG_HASH = hashlib.sha256(
    b'{"profile":"quality-v1","quality_rule_version":"quality-v1","table_strategy":"auto"}'
).hexdigest()


class BatchItem(Protocol):
    @property
    def id(self) -> UUID: ...

    @property
    def source_id(self) -> str: ...

    @property
    def source_key(self) -> str: ...


class BatchRepositoryLike(Protocol):
    def claim_next_item(self, worker_id: str, *, lease_seconds: int = 180) -> BatchItem | None: ...

    def heartbeat(self, item_id: UUID, worker_id: str) -> bool: ...

    def mark_password_required(self, item_id: UUID, worker_id: str, **kwargs: object) -> None: ...

    def mark_failed(
        self,
        item_id: UUID,
        worker_id: str,
        error_code: str,
        **kwargs: object,
    ) -> None: ...

    def mark_succeeded(
        self,
        item_id: UUID,
        worker_id: str,
        *args: object,
        archive: ArchiveMetadata | None = None,
        extraction: object = None,
        ocr: SelectiveOcrResult | None = None,
        validated: ValidatedPdf | None = None,
        **kwargs: object,
    ) -> None: ...

    def mark_ocr_progress(
        self,
        item_id: UUID,
        worker_id: str,
        *,
        state: str,
        pages_processed: int,
        warning_codes: tuple[str, ...] = (),
    ) -> bool: ...


class ParserRunner(Protocol):
    def __call__(
        self,
        source_fd: int,
        settings_json: str,
        *,
        on_progress: Callable[[], bool] | None = None,
        progress_interval_seconds: float = 30.0,
    ) -> ParseOutcome: ...


def _source_id(source_key: str, content_sha256: str) -> str:
    digest = hashlib.sha256()
    digest.update(source_key.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content_sha256.encode("ascii"))
    return digest.hexdigest()


def _reader(source_fd: int) -> tuple[PdfReader, BinaryIO]:
    handle = os.fdopen(os.dup(source_fd), "rb", closefd=True)
    try:
        return PdfReader(handle, strict=True), handle
    except Exception:
        handle.close()
        raise


class _BoundedPlaintextWriter:
    """Expose a seekable PDF target while rejecting oversized output extents."""

    def __init__(self, target: BinaryIO, *, limit: int) -> None:
        self._target = target
        self._limit = limit

    def write(self, data: bytes) -> int:
        if self._target.tell() + len(data) > self._limit:
            raise DocumentTooLarge
        return self._target.write(data)

    def seek(self, offset: int, whence: int = os.SEEK_SET) -> int:
        return self._target.seek(offset, whence)

    def tell(self) -> int:
        return self._target.tell()

    def flush(self) -> None:
        self._target.flush()


def _copy_or_decrypt(source_fd: int, target: BinaryIO, password: str | None) -> bool:
    """Write plaintext PDF bytes and return whether a password was required."""

    reader, reader_handle = _reader(source_fd)
    bounded_target = _BoundedPlaintextWriter(target, limit=MAX_INPUT_BYTES)
    try:
        if reader.is_encrypted:
            if password is None:
                raise PermissionError
            try:
                accepted = reader.decrypt(password)
            except Exception:
                raise PermissionError from None
            if accepted == 0:
                raise PermissionError
            if len(reader.pages) > MAX_PDF_PAGES:
                raise PageLimitExceeded
            writer = PdfWriter()
            writer.clone_document_from_reader(reader)
            writer.write(bounded_target)  # type: ignore[arg-type]
            return True
        offset = 0
        while chunk := os.pread(source_fd, 1024 * 1024, offset):
            bounded_target.write(chunk)
            offset += len(chunk)
        return False
    finally:
        reader_handle.close()


class BatchRunner:
    """Claim one batch item, archive before DB success, and always clean plaintext."""

    def __init__(
        self,
        *,
        repository: BatchRepositoryLike,
        document_root: Path,
        work_root: Path,
        archive_store: ArchiveStore,
        master_key: MasterKey,
        password_scope: PasswordScope | BatchPasswordRegistry | None,
        parser_runner: ParserRunner = run_isolated_parser,
        ocr_processor: SelectiveOcrProcessor | None = None,
        lease_seconds: int = 180,
        heartbeat_interval_seconds: float = 30.0,
        workspace_factory: Callable[[Path], Workspace] = create_workspace,
        logger: logging.Logger = LOGGER,
        stop_requested: Callable[[], bool] = lambda: False,
        on_password_required: Callable[[UUID], None] = lambda _batch_id: None,
        on_password_discarded: Callable[[UUID], None] = lambda _batch_id: None,
    ) -> None:
        document_root = Path(document_root)
        work_root = Path(work_root)
        if not document_root.is_absolute() or not document_root.is_dir():
            raise ValueError("document root must be an absolute directory")
        if not work_root.is_absolute() or not work_root.is_dir():
            raise ValueError("work root must be an absolute directory")
        if lease_seconds <= 0 or not 0 < heartbeat_interval_seconds < lease_seconds:
            raise ValueError("invalid batch lease")
        self.repository = repository
        self.document_root = document_root
        self.work_root = work_root
        self.archive_store = archive_store
        self.master_key = master_key
        self.password_scope = password_scope
        self.parser_runner = parser_runner
        self.ocr_processor = ocr_processor
        self.lease_seconds = lease_seconds
        self.heartbeat_interval_seconds = heartbeat_interval_seconds
        self.workspace_factory = workspace_factory
        self.logger = logger
        self.stop_requested = stop_requested
        self.on_password_required = on_password_required
        self.on_password_discarded = on_password_discarded

    def _batch_id(self, item: BatchItem) -> UUID:
        value = getattr(item, "batch_id", None)
        if isinstance(value, UUID) and value.int != 0:
            return value
        if isinstance(self.password_scope, PasswordScope):
            return self.password_scope.batch_id
        raise ValueError("batch identity unavailable")

    def _password_for(self, item: BatchItem) -> str | None:
        if isinstance(self.password_scope, PasswordScope):
            return self.password_scope.password_for(item.id)
        if isinstance(self.password_scope, BatchPasswordRegistry):
            return self.password_scope.password_for(self._batch_id(item), item.id)
        return None

    def _discard_password_for(self, item: BatchItem) -> None:
        batch_id = self._batch_id(item)
        if isinstance(self.password_scope, BatchPasswordRegistry):
            self.password_scope.discard(batch_id)
        elif isinstance(self.password_scope, PasswordScope):
            self.password_scope.dispose()
        try:
            self.on_password_discarded(batch_id)
        except Exception:
            self.logger.error("batch_password_deactivation_failed item_id=%s", item.id)

    def run_once(self, worker_id: str) -> bool:
        if isinstance(self.password_scope, BatchPasswordRegistry):
            self.password_scope.purge_expired()
        item = self.repository.claim_next_item(worker_id, lease_seconds=self.lease_seconds)
        if item is None:
            return False
        self._run_claimed(item, worker_id)
        return True

    def _run_claimed(self, item: BatchItem, worker_id: str) -> None:
        workspace: Workspace | None = None
        archive = None
        ocr_result: SelectiveOcrResult | None = None
        try:
            with open_source(self.document_root, item.source_key) as source:
                with os.fdopen(os.dup(source.fd), "rb", closefd=True) as handle:
                    content_sha256 = stream_sha256(handle)
                if _source_id(item.source_key, content_sha256) != item.source_id:
                    self.repository.mark_failed(item.id, worker_id, "SOURCE_CHANGED")
                    return
                workspace = self.workspace_factory(self.work_root)
                with workspace.create_file("decrypted.pdf") as plaintext:
                    try:
                        _copy_or_decrypt(
                            source.fd,
                            plaintext,
                            self._password_for(item),
                        )
                    except PermissionError:
                        self.repository.mark_password_required(item.id, worker_id)
                        self.on_password_required(self._batch_id(item))
                        return
                    plaintext.flush()
                    os.fsync(plaintext.fileno())
                read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
                if hasattr(os, "O_NOFOLLOW"):
                    read_flags |= os.O_NOFOLLOW
                read_descriptor = os.open(workspace.path / "decrypted.pdf", read_flags)
                with os.fdopen(read_descriptor, "rb", closefd=True) as plaintext:
                    with OpenedSource(
                        os.dup(plaintext.fileno()),
                        "decrypted.pdf",
                        os.fstat(plaintext.fileno()).st_size,
                    ) as decrypted:
                        validated = validate_pdf(decrypted)
                    version_id = getattr(item, "document_version_id", None)
                    if not isinstance(version_id, UUID) or version_id.int == 0:
                        version_id = uuid4()
                    settings = json.dumps(
                        {
                            "content_sha256": validated.content_sha256,
                            "document_version_id": str(version_id),
                            "extractor_config_hash": _EXTRACTOR_CONFIG_HASH,
                            "quality_rule_version": "quality-v1",
                            "table_strategy": "auto",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    outcome = self.parser_runner(
                        plaintext.fileno(),
                        settings,
                        on_progress=lambda: (
                            not self.stop_requested()
                            and self.repository.heartbeat(item.id, worker_id)
                        ),
                        progress_interval_seconds=self.heartbeat_interval_seconds,
                    )
                    if outcome.metadata.get("cancelled") is True:
                        self._discard_password_for(item)
                        return
                    if not outcome.success:
                        self.repository.mark_failed(
                            item.id,
                            worker_id,
                            str(outcome.error_code or "PDF_CORRUPT"),
                        )
                        return
                    if self.stop_requested() or not self.repository.heartbeat(
                        item.id,
                        worker_id,
                    ):
                        self._discard_password_for(item)
                        return
                    if self.ocr_processor is not None:
                        try:
                            ocr_result = self.ocr_processor.process(
                                outcome.result,
                                plaintext.fileno(),
                                workspace,
                                document_version_id=version_id,
                                content_sha256=validated.content_sha256,
                                on_progress=lambda processed: (
                                    not self.stop_requested()
                                    and self.repository.mark_ocr_progress(
                                        item.id,
                                        worker_id,
                                        state="running",
                                        pages_processed=processed,
                                    )
                                ),
                            )
                            if ocr_result is None and not self.repository.mark_ocr_progress(
                                item.id,
                                worker_id,
                                state="native_only",
                                pages_processed=0,
                            ):
                                self._discard_password_for(item)
                                return
                        except OcrCancelled:
                            self._discard_password_for(item)
                            return
                        except OcrTempCleanupError:
                            self.repository.mark_failed(
                                item.id,
                                worker_id,
                                "TEMP_CLEANUP_FAILED",
                            )
                            return
                        except OcrExecutionError as error:
                            self.repository.mark_failed(item.id, worker_id, error.code)
                            return
                        except OcrConfigurationError, OcrRenderError:
                            self.repository.mark_failed(item.id, worker_id, "OCR_FAILED")
                            return
                        except Exception:
                            self.repository.mark_failed(item.id, worker_id, "OCR_FAILED")
                            return
                    if self.stop_requested() or not self.repository.heartbeat(
                        item.id,
                        worker_id,
                    ):
                        self._discard_password_for(item)
                        return
                    plaintext.seek(0)
                    archive = self.archive_store.put(
                        version_id,
                        plaintext,
                        master_key=self.master_key,
                    )
                    if self.stop_requested() or not self.repository.heartbeat(
                        item.id,
                        worker_id,
                    ):
                        self.archive_store.delete(archive)
                        archive = None
                        self._discard_password_for(item)
                        return
            if workspace is None or not cleanup_workspace(workspace):
                if archive is not None:
                    self.archive_store.delete(archive)
                self.repository.mark_failed(item.id, worker_id, "TEMP_CLEANUP_FAILED")
                return
            workspace = None
            try:
                self.repository.mark_succeeded(
                    item.id,
                    worker_id,
                    archive=archive,
                    extraction=outcome.result,
                    ocr=ocr_result,
                    validated=validated,
                )
            except Exception:
                self._discard_password_for(item)
                self.logger.error("batch_archive_commit_uncertain item_id=%s", item.id)
                raise
        except ArchiveStoreError as error:
            self._safe_fail(item, worker_id, error.code)
        except PdfIntakeError as error:
            self._safe_fail(item, worker_id, error.code.value)
        except Exception:
            self._safe_fail(item, worker_id, "RESOURCE_LIMIT_EXCEEDED")
        finally:
            if workspace is not None and not cleanup_workspace(workspace):
                self.logger.error("batch_workspace_cleanup_failed item_id=%s", item.id)

    def _safe_fail(self, item: BatchItem, worker_id: str, code: str) -> None:
        try:
            self.repository.mark_failed(item.id, worker_id, code)
        except Exception:
            return

    def shutdown(self) -> None:
        if self.password_scope is not None:
            self.password_scope.dispose()


__all__ = ["BatchItem", "BatchRepositoryLike", "BatchRunner"]
