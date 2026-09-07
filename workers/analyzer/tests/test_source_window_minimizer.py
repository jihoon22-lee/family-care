"""Redaction is resolved before provider ranges split identifiers."""

import pytest
from familycare_worker.ai.minimizer import (
    EvidenceMinimizationError,
    SourceWindowMinimizer,
    minimize_source_window,
)


def test_known_identity_split_across_windows_is_redacted_in_both() -> None:
    source = "Synthetic prefix " * 15 + "Family Member A" + " Sum assured 317"
    boundary = source.index("Member") + 3
    first = minimize_source_window(
        source, start=0, end=boundary, sensitive_terms=("Family Member A",)
    )
    second = minimize_source_window(
        source, start=boundary, end=len(source), sensitive_terms=("Family Member A",)
    )
    assert first.endswith("[REDACTED]")
    assert second.startswith("[REDACTED]")
    assert "Family" not in first and "ber A" not in second
    assert "317" in second


@pytest.mark.parametrize(
    "value",
    [
        "Policy Number: SYNTHETIC-12345678",
        "Contact: sample@example.invalid",
        "주소: Synthetic Lane 317",
    ],
)
def test_labelled_identifier_is_detected_outside_requested_window(value: str) -> None:
    source = value + "\n가입금액: 317"
    # Only the value's middle would be sent. Its label must still govern redaction.
    start, end = source.index(":") + 4, source.index("\n") - 1
    assert minimize_source_window(source, start=start, end=end, sensitive_terms=()) == "[REDACTED]"


def test_unbounded_windows_are_rejected_without_source_echo() -> None:
    with pytest.raises(EvidenceMinimizationError) as error:
        minimize_source_window("Synthetic " * 2000, start=0, end=16000, sensitive_terms=())
    assert str(error.value) == "EVIDENCE_MINIMIZATION_ERROR"


def test_reusable_source_minimizer_keeps_raw_offsets_for_multiple_windows() -> None:
    source = "Synthetic preface Family Member A\n가입금액: 317"
    redactor = SourceWindowMinimizer(source, sensitive_terms=("Family Member A",))
    split = source.index("Member") + 2
    assert redactor.window(0, split).endswith("[REDACTED]")
    assert redactor.window(split, len(source)).startswith("[REDACTED]")
    assert redactor.window(split, len(source)).endswith("317")
    assert "Family Member A" not in repr(redactor)
