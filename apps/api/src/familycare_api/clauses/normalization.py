"""Deterministic, versioned text normalization for searchable Clauses."""

from __future__ import annotations

import re
import unicodedata

NORMALIZATION_VERSION = "unicode-nfc-v1"
MAX_SEARCH_QUERY_LENGTH = 160
MAX_EXCERPT_LENGTH = 320


def _require_text(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field} must be text")
    return value


def _normalize(value: str, *, field: str) -> str:
    text = _require_text(value, field=field)
    text = unicodedata.normalize("NFC", text)
    text = "".join(
        " " if unicodedata.category(character).startswith("P") else character for character in text
    )
    return re.sub(r"\s+", " ", text).strip()


def normalize_clause_text(text: str) -> str:
    """Normalize Clause text without consulting external or private dictionaries."""

    return _normalize(text, field="clause text")


def normalize_search_query(query: str) -> str:
    """Normalize a search query; length and emptiness are service-level policy."""

    return _normalize(query, field="search query")


def bounded_excerpt(text: str, *, max_chars: int = MAX_EXCERPT_LENGTH) -> str:
    """Return a deterministic normalized prefix no longer than ``max_chars``."""

    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
        raise ValueError("excerpt bound must be a positive integer")
    return normalize_clause_text(text)[:max_chars]


__all__ = [
    "MAX_EXCERPT_LENGTH",
    "MAX_SEARCH_QUERY_LENGTH",
    "NORMALIZATION_VERSION",
    "bounded_excerpt",
    "normalize_clause_text",
    "normalize_search_query",
]
