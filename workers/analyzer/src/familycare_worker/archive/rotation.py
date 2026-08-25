"""Metadata-only managed archive key rotation."""

from __future__ import annotations

from dataclasses import replace

from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap, aes_key_wrap

from .crypto import ARCHIVE_SCHEME, ArchiveIntegrityError, ArchiveMetadata
from .keys import MasterKey


def rewrap_metadata(
    metadata: ArchiveMetadata,
    *,
    old_key: MasterKey,
    new_key: MasterKey,
) -> ArchiveMetadata:
    """Rewrap only the data key; ciphertext, nonce, tag, and AAD stay unchanged."""

    try:
        if metadata.scheme != ARCHIVE_SCHEME or metadata.key_version != old_key.key_version:
            raise ArchiveIntegrityError
        data_key = aes_key_unwrap(old_key.material, metadata.wrapped_data_key)
        return replace(
            metadata,
            key_version=new_key.key_version,
            wrapped_data_key=aes_key_wrap(new_key.material, data_key),
        )
    except ArchiveIntegrityError:
        raise
    except InvalidUnwrap, ValueError, TypeError:
        raise ArchiveIntegrityError from None


__all__ = ["rewrap_metadata"]
