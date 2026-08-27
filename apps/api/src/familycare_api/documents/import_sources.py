"""Bounded, path-free catalog for the configured private PDF inbox."""

from __future__ import annotations

import errno
import hashlib
import os
import stat
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_SOURCE_COUNT = 256
_PDF_MAGIC = b"%PDF-"
_HASH_CHUNK_BYTES = 1024 * 1024
_FALLBACK_DISPLAY_LABEL = "PDF document"


class ImportSourceError(RuntimeError):
    """Fixed-message catalog error that never contains a filesystem path."""


class ImportSourceNotFound(ImportSourceError):
    def __init__(self) -> None:
        super().__init__("IMPORT_SOURCE_NOT_FOUND")


@dataclass(frozen=True)
class ImportSource:
    source_id: str
    display_label: str
    size_bytes: int
    encrypted: bool


@dataclass(frozen=True)
class ResolvedImportSource:
    source_id: str
    source_key: str
    display_label: str
    size_bytes: int
    encrypted: bool


def _open_flags(*, directory: bool = False) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if nofollow == 0:
        raise ImportSourceNotFound
    flags = os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _source_components(source_key: str) -> list[str]:
    components = source_key.split("/")
    if (
        not source_key
        or len(source_key) > 512
        or source_key.startswith("/")
        or "\\" in source_key
        or any(component in {"", ".", ".."} for component in components)
    ):
        raise ImportSourceNotFound
    return components


def _open_beneath(root: Path, source_key: str) -> int:
    components = _source_components(source_key)
    directories: list[int] = []
    try:
        try:
            current = os.open(root, _open_flags(directory=True))
        except OSError:
            raise ImportSourceNotFound from None
        directories.append(current)
        for component in components[:-1]:
            try:
                current = os.open(component, _open_flags(directory=True), dir_fd=current)
            except OSError as error:
                if error.errno in {errno.ENOENT, errno.ENOTDIR, errno.ELOOP}:
                    raise ImportSourceNotFound from None
                raise ImportSourceNotFound from None
            directories.append(current)
        try:
            descriptor = os.open(
                components[-1],
                _open_flags() | getattr(os, "O_NONBLOCK", 0),
                dir_fd=current,
            )
        except OSError:
            raise ImportSourceNotFound from None
        try:
            metadata = os.fstat(descriptor)
            if (
                not stat.S_ISREG(metadata.st_mode)
                or metadata.st_size > MAX_SOURCE_BYTES
                or os.pread(descriptor, len(_PDF_MAGIC), 0) != _PDF_MAGIC
            ):
                raise ImportSourceNotFound
        except Exception:
            os.close(descriptor)
            raise
        return descriptor
    finally:
        for directory in reversed(directories):
            with suppress(OSError):
                os.close(directory)


def _hash_descriptor(descriptor: int) -> str:
    digest = hashlib.sha256()
    offset = 0
    while chunk := os.pread(descriptor, _HASH_CHUNK_BYTES, offset):
        digest.update(chunk)
        offset += len(chunk)
    return digest.hexdigest()


def _is_encrypted(descriptor: int) -> bool:
    marker = b"/Encrypt"
    overlap = b""
    offset = 0
    while chunk := os.pread(descriptor, _HASH_CHUNK_BYTES, offset):
        candidate = overlap + chunk
        if marker in candidate:
            return True
        overlap = candidate[-(len(marker) - 1) :]
        offset += len(chunk)
    return False


def _opaque_id(source_key: str, content_sha256: str) -> str:
    digest = hashlib.sha256()
    digest.update(source_key.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(content_sha256.encode("ascii"))
    return digest.hexdigest()


def normalize_display_label(value: object) -> str:
    """Return a printable, path-free label suitable for API and DB projection."""

    if not isinstance(value, str):
        return _FALLBACK_DISPLAY_LABEL
    leaf = value.replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in leaf if character.isprintable())
    return cleaned.strip()[:160] or _FALLBACK_DISPLAY_LABEL


class ImportSourceCatalog:
    """Rescan one exact root and expose stable IDs instead of source paths."""

    def __init__(self, root: Path) -> None:
        candidate = Path(root)
        try:
            details = candidate.lstat()
        except OSError:
            raise ValueError("import root must be an absolute directory") from None
        if not candidate.is_absolute() or not stat.S_ISDIR(details.st_mode):
            raise ValueError("import root must be an absolute directory")
        self._root = candidate

    def _keys(self) -> tuple[str, ...]:
        keys: list[str] = []

        def visit(directory: Path, prefix: tuple[str, ...]) -> None:
            if len(keys) >= MAX_SOURCE_COUNT:
                return
            try:
                entries = sorted(os.scandir(directory), key=lambda item: item.name)
            except OSError:
                return
            for entry in entries:
                if len(keys) >= MAX_SOURCE_COUNT:
                    return
                if entry.name in {".", ".."} or "/" in entry.name or "\\" in entry.name:
                    continue
                try:
                    if entry.is_symlink():
                        continue
                    if entry.is_dir(follow_symlinks=False):
                        visit(Path(entry.path), (*prefix, entry.name))
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(
                        ".pdf"
                    ):
                        keys.append("/".join((*prefix, entry.name)))
                except OSError:
                    continue

        visit(self._root, ())
        return tuple(keys)

    def _inspect(self, source_key: str) -> ResolvedImportSource:
        descriptor = _open_beneath(self._root, source_key)
        try:
            details = os.fstat(descriptor)
            content_sha256 = _hash_descriptor(descriptor)
            return ResolvedImportSource(
                source_id=_opaque_id(source_key, content_sha256),
                source_key=source_key,
                display_label=normalize_display_label(source_key),
                size_bytes=details.st_size,
                encrypted=_is_encrypted(descriptor),
            )
        except OSError:
            raise ImportSourceNotFound from None
        finally:
            os.close(descriptor)

    def list(self, _context: object = None) -> tuple[ImportSource, ...]:
        sources: list[ImportSource] = []
        for key in self._keys():
            try:
                resolved = self._inspect(key)
            except ImportSourceNotFound:
                continue
            sources.append(
                ImportSource(
                    source_id=resolved.source_id,
                    display_label=resolved.display_label,
                    size_bytes=resolved.size_bytes,
                    encrypted=resolved.encrypted,
                )
            )
        return tuple(sorted(sources, key=lambda item: (item.display_label, item.source_id)))

    def resolve(self, source_id: str) -> ResolvedImportSource:
        if (
            not isinstance(source_id, str)
            or len(source_id) != 64
            or any(character not in "0123456789abcdef" for character in source_id)
        ):
            raise ImportSourceNotFound
        for key in self._keys():
            try:
                candidate = self._inspect(key)
            except ImportSourceNotFound:
                continue
            if candidate.source_id == source_id:
                return candidate
        raise ImportSourceNotFound


__all__ = [
    "ImportSource",
    "ImportSourceCatalog",
    "ImportSourceError",
    "ImportSourceNotFound",
    "ResolvedImportSource",
    "normalize_display_label",
]
