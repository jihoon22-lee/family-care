"""Race-resistant local PDF source opening and structural validation."""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from pypdf import PdfReader

from familycare_worker.pdf.errors import (
    DocumentNotFound,
    DocumentPathEscape,
    DocumentTooLarge,
    PageLimitExceeded,
    PasswordRequired,
    PdfCorrupt,
    PdfIntakeError,
    UnsupportedFileType,
)
from familycare_worker.pdf.limits import MAX_INPUT_BYTES, MAX_PDF_PAGES

SHA256_CHUNK_SIZE = 1_048_576
PDF_MAGIC = b"%PDF-"
_WINDOWS_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")


@dataclass
class OpenedSource:
    """An opened source identity; it deliberately exposes no filesystem path."""

    fd: int
    source_key: str
    byte_size: int

    def close(self) -> None:
        """Close the owned descriptor exactly once."""

        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> OpenedSource:
        return self

    def __exit__(self, _exc_type: object, _exc_value: object, _traceback: object) -> None:
        self.close()


@dataclass(frozen=True)
class ValidatedPdf:
    """Safe metadata produced from one opened source identity."""

    media_type: str
    byte_size: int
    page_count: int
    encrypted: bool
    content_sha256: str


def _validate_source_key(source_key: str) -> list[str]:
    if not isinstance(source_key, str) or not source_key or len(source_key) > 512:
        raise DocumentPathEscape
    if "\x00" in source_key or "\\" in source_key or any(c in source_key for c in "\r\n"):
        raise DocumentPathEscape
    if source_key.startswith("/") or _WINDOWS_DRIVE_PREFIX.match(source_key):
        raise DocumentPathEscape
    components = source_key.split("/")
    if any(component == ".." for component in components):
        raise DocumentPathEscape
    if any(component == "" for component in components):
        raise DocumentPathEscape
    return components


def _close_fds(descriptors: list[int]) -> None:
    for descriptor in reversed(descriptors):
        with suppress(OSError):
            os.close(descriptor)


def _required_open_flags(*, directory: bool = False) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise DocumentPathEscape
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _map_component_error(error: OSError) -> PdfIntakeError:
    if error.errno == errno.ENOENT:
        return DocumentNotFound()
    if error.errno in {errno.ENOTDIR, errno.ELOOP}:
        return DocumentPathEscape()
    return DocumentPathEscape()


def open_source(root: Path, source_key: str) -> OpenedSource:
    """Open one regular PDF source beneath an absolute configured root.

    Every component is opened relative to the descriptor obtained immediately
    before it. No validated path is reopened, and no absolute path is retained
    in the returned object or errors.
    """

    components = _validate_source_key(source_key)
    if not isinstance(root, Path) or not root.is_absolute():
        raise DocumentPathEscape

    directory_fds: list[int] = []
    try:
        try:
            root_fd = os.open(str(root), _required_open_flags(directory=True))
        except OSError:
            raise DocumentPathEscape from None
        directory_fds.append(root_fd)
        current_fd = root_fd

        for component in components[:-1]:
            try:
                current_fd = os.open(
                    component,
                    _required_open_flags(directory=True),
                    dir_fd=current_fd,
                )
            except OSError as error:
                raise _map_component_error(error) from None
            directory_fds.append(current_fd)

        try:
            final_fd = os.open(
                components[-1],
                _required_open_flags() | getattr(os, "O_NONBLOCK", 0),
                dir_fd=current_fd,
            )
        except OSError as error:
            raise _map_component_error(error) from None

        try:
            metadata = os.fstat(final_fd)
            if not stat.S_ISREG(metadata.st_mode):
                raise UnsupportedFileType
            if metadata.st_size > MAX_INPUT_BYTES:
                raise DocumentTooLarge
            if os.pread(final_fd, len(PDF_MAGIC), 0) != PDF_MAGIC:
                raise UnsupportedFileType
            byte_size = metadata.st_size
        except UnsupportedFileType, DocumentTooLarge:
            os.close(final_fd)
            raise
        except OSError:
            os.close(final_fd)
            raise PdfCorrupt from None

        _close_fds(directory_fds)
        directory_fds.clear()
        return OpenedSource(final_fd, source_key, byte_size)
    finally:
        _close_fds(directory_fds)


def stream_sha256(
    handle: BinaryIO,
    chunk_size: int = SHA256_CHUNK_SIZE,
    on_read: Callable[[int], None] | None = None,
) -> str:
    """Hash a seekable binary stream in bounded chunks and reset its offset."""

    if chunk_size <= 0:
        raise ValueError("chunk size must be positive")
    digest = hashlib.sha256()
    handle.seek(0)
    try:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
            if on_read is not None:
                on_read(len(chunk))
    finally:
        handle.seek(0)
    return digest.hexdigest()


def _duplicate_handle(fd: int) -> BinaryIO:
    return os.fdopen(os.dup(fd), "rb", closefd=True)


def validate_pdf(source: OpenedSource) -> ValidatedPdf:
    """Validate magic, structure, encryption, pages, and hash through duplicates."""

    try:
        original_offset = os.lseek(source.fd, 0, os.SEEK_CUR)
    except OSError:
        raise PdfCorrupt from None
    try:
        metadata = os.fstat(source.fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise UnsupportedFileType
        if metadata.st_size > MAX_INPUT_BYTES:
            raise DocumentTooLarge
        if os.pread(source.fd, len(PDF_MAGIC), 0) != PDF_MAGIC:
            raise UnsupportedFileType

        try:
            with _duplicate_handle(source.fd) as handle:
                reader = PdfReader(handle, strict=False)
                if reader.is_encrypted:
                    raise PasswordRequired
                page_count = len(reader.pages)
        except PasswordRequired, PageLimitExceeded, DocumentTooLarge, UnsupportedFileType:
            raise
        except Exception:
            raise PdfCorrupt from None

        if page_count > MAX_PDF_PAGES:
            raise PageLimitExceeded

        with _duplicate_handle(source.fd) as handle:
            content_sha256 = stream_sha256(handle)
    except DocumentTooLarge, PageLimitExceeded, PasswordRequired, PdfCorrupt, UnsupportedFileType:
        raise
    except OSError, ValueError:
        raise PdfCorrupt from None
    finally:
        with suppress(OSError):
            os.lseek(source.fd, original_offset, os.SEEK_SET)

    return ValidatedPdf(
        media_type="application/pdf",
        byte_size=metadata.st_size,
        page_count=page_count,
        encrypted=False,
        content_sha256=content_sha256,
    )
