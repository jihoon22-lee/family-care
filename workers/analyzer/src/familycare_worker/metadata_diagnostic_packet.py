"""Minimal development diagnostic payloads; never classification or publication authority."""

import re
from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from familycare_worker.ai.minimizer import EvidenceMinimizationError, SourceWindowMinimizer

_MAX_SOURCE_CHARACTERS = 65536
_MAX_CONTEXT_CHARACTERS = 1200
_REDACTED = "[REDACTED]"
_LOCATION_START = r"(?:[a-z][a-z0-9+.-]{1,31}://|www\.|[a-z]:[\\/]|[/\\]{1,2}|~[\\/])"
_QUOTED_LOCATION = re.compile(r"(?P<quote>[\"'])" + _LOCATION_START + r"[^\r\n]*?(?P=quote)", re.I)
_URL = re.compile(r"(?:[a-z][a-z0-9+.-]{1,31}://|www\.)[^\s<>\"']+", re.I)
# Without explicit quotes the end of a path containing spaces is uncertain.
# Exclude the remainder of that line rather than retaining a private suffix.
_ABSOLUTE_PATH = re.compile(r"(?:[a-z]:[\\/]|\\\\|//|~[\\/]|(?<!\d)/(?!/))[^\r\n]*", re.I)
_NUMBER = re.compile(r"\d+(?:[.,:/-]\d+)*")


class MetadataDiagnosticError(ValueError):
    def __init__(self) -> None:
        super().__init__("METADATA_DIAGNOSTIC_INVALID")


class MetadataBoundaryDiagnostic(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    reference_kind: Literal[
        "NAVIGATION", "LOCAL_EXAMPLE", "DOCUMENT_REFERENCE", "MIXED_SOURCE", "UNCLEAR"
    ]
    body_kind: Literal["TERMS_PROVISION", "EXAMPLE_PROVISION", "UNCLEAR"]
    parser_issue: Literal[
        "OVERBROAD_REFERENCE_CONTEXT",
        "MISSING_DOCUMENT_BOUNDARY",
        "UNRESOLVED_EXTRACTION_LAYOUT",
        "INSUFFICIENT_CONTEXT",
        "OTHER",
    ]
    needs_more_context: bool


def _context(text: str, sensitive_terms: Sequence[str]) -> str:
    minimizer = SourceWindowMinimizer(text, sensitive_terms=sensitive_terms)
    # Resolve sensitive spans against the entire source before any clipping.
    # Small windows also bound expansion when a short name repeats many times.
    minimized = "".join(
        minimizer.window(start, min(start + _MAX_CONTEXT_CHARACTERS, len(text)))
        for start in range(0, len(text), _MAX_CONTEXT_CHARACTERS)
    )
    minimized = _QUOTED_LOCATION.sub(_REDACTED, minimized)
    minimized = _URL.sub(_REDACTED, minimized)
    minimized = _ABSOLUTE_PATH.sub(_REDACTED, minimized)
    minimized = _NUMBER.sub("[NUMBER]", minimized)
    return minimized[:_MAX_CONTEXT_CHARACTERS]


def build_diagnostic_packet(
    reference_text: str, body_text: str, *, sensitive_terms: Sequence[str]
) -> dict[str, str]:
    """Return only bounded, minimized context text for one development diagnostic."""
    try:
        if (
            any(
                not isinstance(text, str) or not text.strip() or len(text) > _MAX_SOURCE_CHARACTERS
                for text in (reference_text, body_text)
            )
            or not isinstance(sensitive_terms, Sequence)
            or isinstance(sensitive_terms, str | bytes)
        ):
            raise MetadataDiagnosticError
        terms = tuple(sensitive_terms)
        return {
            "reference_context": _context(reference_text, terms),
            "operative_context": _context(body_text, terms),
        }
    except EvidenceMinimizationError, TypeError, ValueError, AttributeError, OverflowError:
        raise MetadataDiagnosticError from None
