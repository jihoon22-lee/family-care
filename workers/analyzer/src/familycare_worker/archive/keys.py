"""Strict external master-key loading for the managed archive."""

from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

MASTER_KEY_BYTES = 32
_KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class MasterKeyError(RuntimeError):
    """Sanitized master-key configuration failure."""

    def __init__(self) -> None:
        super().__init__("ARCHIVE_KEY_UNAVAILABLE")


def _validate_version(value: str) -> str:
    if _KEY_VERSION_PATTERN.fullmatch(value) is None:
        raise ValueError("invalid archive key version")
    return value


@dataclass(frozen=True)
class MasterKey:
    """One exact 256-bit archive wrapping key with a non-secret version ID."""

    _material: bytes = field(repr=False)
    key_version: str

    def __post_init__(self) -> None:
        if len(self._material) != MASTER_KEY_BYTES:
            raise ValueError("invalid archive key")
        _validate_version(self.key_version)

    @property
    def material(self) -> bytes:
        """Return key bytes only to archive cryptography code."""

        return self._material

    @classmethod
    def synthetic(cls, material: bytes, *, key_version: str) -> MasterKey:
        """Construct a test-only key without weakening the file loader."""

        return cls(bytes(material), _validate_version(key_version))

    @classmethod
    def from_file(cls, path: Path, *, key_version: str | None = None) -> MasterKey:
        """Read one absolute, no-follow, regular mode-0600 32-byte key file."""

        candidate = Path(path)
        if not candidate.is_absolute():
            raise MasterKeyError from None
        flags = os.O_RDONLY | os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = -1
        try:
            descriptor = os.open(candidate, flags)
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or stat.S_IMODE(details.st_mode) != 0o600:
                raise MasterKeyError
            material = os.read(descriptor, MASTER_KEY_BYTES + 1)
            if len(material) != MASTER_KEY_BYTES or os.read(descriptor, 1):
                raise MasterKeyError
        except MasterKeyError:
            raise
        except OSError:
            raise MasterKeyError from None
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        version = key_version or f"sha256-{hashlib.sha256(material).hexdigest()[:16]}"
        try:
            return cls(material, _validate_version(version))
        except ValueError:
            raise MasterKeyError from None


__all__ = ["MASTER_KEY_BYTES", "MasterKey", "MasterKeyError"]
