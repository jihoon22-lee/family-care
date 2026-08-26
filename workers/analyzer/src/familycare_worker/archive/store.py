"""Opaque atomic ciphertext storage for the managed archive."""

from __future__ import annotations

import io
import os
import re
import stat
from contextlib import suppress
from pathlib import Path
from threading import Lock
from typing import BinaryIO
from uuid import UUID

from .crypto import MAX_ARCHIVE_BYTES, ArchiveMetadata, decrypt_document, encrypt_document
from .keys import MasterKey
from .rotation import rewrap_metadata

_OBJECT_KEY_PATTERN = re.compile(r"^[a-f0-9]{32}$")
_READ_CHUNK_BYTES = 1024 * 1024


class ArchiveStoreError(RuntimeError):
    """Sanitized archive filesystem or size failure."""

    def __init__(self, code: str = "ARCHIVE_WRITE_FAILED") -> None:
        self.code = code
        super().__init__(code)


def _read_bounded(source: BinaryIO) -> bytes:
    plaintext = bytearray()
    while True:
        chunk = source.read(_READ_CHUNK_BYTES)
        if not chunk:
            break
        plaintext.extend(chunk)
        if len(plaintext) > MAX_ARCHIVE_BYTES:
            plaintext.clear()
            raise ArchiveStoreError("ARCHIVE_INPUT_TOO_LARGE")
    result = bytes(plaintext)
    plaintext[:] = b"\x00" * len(plaintext)
    plaintext.clear()
    return result


class ArchiveStore:
    """Write ciphertext durably before exposing metadata to a repository."""

    def __init__(self, root: Path) -> None:
        archive_root = Path(root)
        if not archive_root.is_absolute() or not archive_root.is_dir():
            raise ValueError("invalid archive root")
        self._root = archive_root
        self._metadata: dict[UUID, ArchiveMetadata] = {}
        self._lock = Lock()

    def _object_path(self, object_key: str) -> Path:
        if _OBJECT_KEY_PATTERN.fullmatch(object_key) is None:
            raise ArchiveStoreError("ARCHIVE_INTEGRITY_ERROR")
        return self._root / object_key

    def put(
        self,
        document_version_id: UUID,
        source: BinaryIO,
        *,
        master_key: MasterKey,
    ) -> ArchiveMetadata:
        plaintext = _read_bounded(source)
        try:
            metadata, ciphertext = encrypt_document(
                plaintext,
                document_version_id=document_version_id,
                master_key=master_key,
            )
        finally:
            plaintext = b""
        object_path = self._object_path(metadata.object_key)
        temporary_path = self._root / f".tmp-{metadata.archive_id.hex}"
        descriptor = -1
        object_replaced = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(temporary_path, flags, 0o600)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=True) as handle:
                descriptor = -1
                handle.write(ciphertext)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, object_path)
            object_replaced = True
            directory_descriptor = os.open(self._root, os.O_RDONLY | os.O_CLOEXEC)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            if descriptor >= 0:
                os.close(descriptor)
            with suppress(FileNotFoundError):
                temporary_path.unlink()
            if object_replaced:
                with suppress(FileNotFoundError):
                    object_path.unlink()
            raise ArchiveStoreError from None
        finally:
            ciphertext = b""
        with self._lock:
            self._metadata[metadata.archive_id] = metadata
        return metadata

    def open(self, metadata: ArchiveMetadata, *, master_key: MasterKey) -> BinaryIO:
        object_path = self._object_path(metadata.object_key)
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(object_path, flags)
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_size != metadata.ciphertext_size:
                raise ArchiveStoreError("ARCHIVE_INTEGRITY_ERROR")
            with os.fdopen(descriptor, "rb", closefd=True) as handle:
                descriptor = -1
                ciphertext = handle.read(metadata.ciphertext_size + 1)
            if len(ciphertext) != metadata.ciphertext_size:
                raise ArchiveStoreError("ARCHIVE_INTEGRITY_ERROR")
            return io.BytesIO(decrypt_document(metadata, ciphertext, master_key=master_key))
        except ArchiveStoreError:
            raise
        except OSError:
            raise ArchiveStoreError("ARCHIVE_READ_FAILED") from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def delete(self, metadata: ArchiveMetadata) -> None:
        """Remove one exact orphan ciphertext after a metadata transaction failure."""

        object_path = self._object_path(metadata.object_key)
        try:
            object_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            raise ArchiveStoreError("ARCHIVE_ORPHAN_CLEANUP_FAILED") from None
        with self._lock:
            self._metadata.pop(metadata.archive_id, None)

    def rewrap_all(self, old_key: MasterKey, new_key: MasterKey) -> int:
        """Rewrap in-process metadata; DB-backed rotation is wired by the repository layer."""

        with self._lock:
            candidates = [
                metadata
                for metadata in self._metadata.values()
                if metadata.key_version == old_key.key_version
            ]
            rotated = [
                rewrap_metadata(metadata, old_key=old_key, new_key=new_key)
                for metadata in candidates
            ]
            for metadata in rotated:
                self._metadata[metadata.archive_id] = metadata
        return len(rotated)


__all__ = ["ArchiveStore", "ArchiveStoreError"]
