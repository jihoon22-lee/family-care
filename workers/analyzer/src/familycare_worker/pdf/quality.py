"""Deterministic quality-v1 metrics for extracted page text."""

from __future__ import annotations

from typing import Literal

from familycare_worker.generated_contracts import PageQuality

__all__ = ["classify_page_quality"]

QualityClassification = Literal["OCR_REQUIRED", "TEXT_SUFFICIENT"]


def max_repeated_character_run(text: str) -> int:
    """Return the longest run of one repeated character in ``text``."""

    maximum = 0
    current = 0
    previous: str | None = None
    for character in text:
        current = current + 1 if character == previous else 1
        maximum = max(maximum, current)
        previous = character
    return maximum


def classify_page_quality(text: str, rule_version: Literal["quality-v1"]) -> PageQuality:
    """Classify one page using the versioned quality-v1 thresholds."""

    if rule_version != "quality-v1":
        raise ValueError("unsupported quality rule version")

    non_whitespace = sum(not character.isspace() for character in text)
    alphanumeric = sum(character.isalnum() for character in text)
    alphanumeric_ratio = alphanumeric / max(non_whitespace, 1)
    replacement_ratio = text.count("\ufffd") / max(len(text), 1)
    maximum_run = max_repeated_character_run(text)
    classification: QualityClassification = (
        "OCR_REQUIRED"
        if non_whitespace < 20
        or alphanumeric_ratio < 0.25
        or replacement_ratio > 0.05
        or maximum_run > 20
        else "TEXT_SUFFICIENT"
    )

    return PageQuality(
        rule_version=rule_version,
        classification=classification,
        non_whitespace_chars=non_whitespace,
        alphanumeric_ratio=alphanumeric_ratio,
        replacement_character_ratio=replacement_ratio,
        maximum_repeated_character_run=maximum_run,
    )
