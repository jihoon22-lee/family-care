"""Application-encrypted managed document archive."""

from .crypto import (
    ARCHIVE_SCHEME,
    ArchiveIntegrityError,
    ArchiveMetadata,
    decrypt_document,
    encrypt_document,
)
from .keys import MasterKey, MasterKeyError
from .store import ArchiveStore, ArchiveStoreError

__all__ = [
    "ARCHIVE_SCHEME",
    "ArchiveIntegrityError",
    "ArchiveMetadata",
    "ArchiveStore",
    "ArchiveStoreError",
    "MasterKey",
    "MasterKeyError",
    "decrypt_document",
    "encrypt_document",
]
