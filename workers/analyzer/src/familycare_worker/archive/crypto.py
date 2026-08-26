"""AES-GCM document encryption with AES-KW envelope keys."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from uuid import UUID, uuid4

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap, aes_key_wrap

from .keys import MasterKey

ARCHIVE_SCHEME = "aes-256-gcm+aes-kw-v1"
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_GCM_NONCE_BYTES = 12
_GCM_TAG_BYTES = 16
_DATA_KEY_BYTES = 32


class ArchiveIntegrityError(RuntimeError):
    """Sanitized archive authentication or key-unwrapping failure."""

    def __init__(self) -> None:
        super().__init__("ARCHIVE_INTEGRITY_ERROR")


@dataclass(frozen=True)
class ArchiveMetadata:
    """Non-plaintext metadata persisted beside one ciphertext object."""

    archive_id: UUID
    document_version_id: UUID
    object_key: str
    scheme: str
    key_version: str
    nonce: bytes = field(repr=False)
    wrapped_data_key: bytes = field(repr=False)
    ciphertext_size: int
    auth_tag: bytes = field(repr=False)


def _aad(document_version_id: UUID) -> bytes:
    return b"familycare-archive-v1\x00" + document_version_id.bytes


def encrypt_document(
    plaintext: bytes,
    *,
    document_version_id: UUID,
    master_key: MasterKey,
) -> tuple[ArchiveMetadata, bytes]:
    """Encrypt one bounded plaintext with a fresh per-document data key."""

    if not isinstance(plaintext, bytes) or len(plaintext) > MAX_ARCHIVE_BYTES:
        raise ValueError("ARCHIVE_INPUT_TOO_LARGE")
    if not isinstance(document_version_id, UUID) or document_version_id.int == 0:
        raise ValueError("invalid archive document")
    data_key = os.urandom(_DATA_KEY_BYTES)
    nonce = os.urandom(_GCM_NONCE_BYTES)
    encrypted = AESGCM(data_key).encrypt(nonce, plaintext, _aad(document_version_id))
    ciphertext, auth_tag = encrypted[:-_GCM_TAG_BYTES], encrypted[-_GCM_TAG_BYTES:]
    metadata = ArchiveMetadata(
        archive_id=uuid4(),
        document_version_id=document_version_id,
        object_key=uuid4().hex,
        scheme=ARCHIVE_SCHEME,
        key_version=master_key.key_version,
        nonce=nonce,
        wrapped_data_key=aes_key_wrap(master_key.material, data_key),
        ciphertext_size=len(ciphertext),
        auth_tag=auth_tag,
    )
    return metadata, ciphertext


def decrypt_document(
    metadata: ArchiveMetadata,
    ciphertext: bytes,
    *,
    master_key: MasterKey,
) -> bytes:
    """Authenticate archive metadata and return plaintext only in memory."""

    try:
        if (
            metadata.scheme != ARCHIVE_SCHEME
            or metadata.key_version != master_key.key_version
            or len(metadata.nonce) != _GCM_NONCE_BYTES
            or len(metadata.auth_tag) != _GCM_TAG_BYTES
            or len(metadata.wrapped_data_key) != _DATA_KEY_BYTES + 8
            or metadata.ciphertext_size != len(ciphertext)
            or len(ciphertext) > MAX_ARCHIVE_BYTES
        ):
            raise ArchiveIntegrityError
        data_key = aes_key_unwrap(master_key.material, metadata.wrapped_data_key)
        return AESGCM(data_key).decrypt(
            metadata.nonce,
            ciphertext + metadata.auth_tag,
            _aad(metadata.document_version_id),
        )
    except ArchiveIntegrityError:
        raise
    except InvalidTag, InvalidUnwrap, ValueError, TypeError:
        raise ArchiveIntegrityError from None


__all__ = [
    "ARCHIVE_SCHEME",
    "MAX_ARCHIVE_BYTES",
    "ArchiveIntegrityError",
    "ArchiveMetadata",
    "decrypt_document",
    "encrypt_document",
]
