"""Provider-bound policy Evidence minimization with synthetic identifiers."""

from __future__ import annotations

from uuid import UUID

import pytest
from familycare_worker.ai.minimizer import EvidenceMinimizationError, minimize_evidence
from familycare_worker.ai.provider import EvidenceSlice

EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000201")
DOCUMENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000202")


def _evidence(text: str) -> EvidenceSlice:
    return EvidenceSlice(
        evidence_id=EVIDENCE_ID,
        document_version_id=DOCUMENT_VERSION_ID,
        page=1,
        text=text,
        bbox=None,
    )


def test_minimizer_removes_runtime_member_and_format_identifiers_but_keeps_terms() -> None:
    source = _evidence(
        "Family Member A 이메일 sample@example.invalid 전화 010-0000-0000 "
        "증권번호: synthetic-policy-001 계약일 2026-01-01 가입금액 1000000 KRW"
    )

    minimized = minimize_evidence((source,), sensitive_terms=("Family Member A",))

    assert len(minimized) == 1
    text = minimized[0].text
    assert "Family Member A" not in text
    assert "sample@example.invalid" not in text
    assert "010-0000-0000" not in text
    assert "synthetic-policy-001" not in text
    assert "2026-01-01" in text
    assert "1000000 KRW" in text
    assert minimized[0].evidence_id == EVIDENCE_ID
    assert minimized[0].document_version_id == DOCUMENT_VERSION_ID
    assert "가입금액" not in repr(minimized[0])


def test_minimizer_removes_labelled_party_and_address_without_runtime_terms() -> None:
    source = _evidence(
        "계약자: External Party A 주소: Synthetic City Sample Road 101 "
        "계약일 2026-01-01 가입금액 1000000 KRW"
    )

    minimized = minimize_evidence((source,), sensitive_terms=("Family Member A",))

    text = minimized[0].text
    assert "External Party A" not in text
    assert "Synthetic City Sample Road 101" not in text
    assert text.count("[REDACTED]") == 2
    assert "2026-01-01" in text
    assert "1000000 KRW" in text


@pytest.mark.parametrize(
    ("evidence_count", "sensitive_terms"),
    [
        (65, ("Family Member A",)),
        (1, tuple(f"Synthetic Member {index}" for index in range(17))),
        (1, ("x",)),
    ],
)
def test_minimizer_rejects_unbounded_or_overbroad_configuration(
    evidence_count: int,
    sensitive_terms: tuple[str, ...],
) -> None:
    evidence = tuple(
        EvidenceSlice(
            evidence_id=UUID(int=index + 1),
            document_version_id=DOCUMENT_VERSION_ID,
            page=index + 1,
            text="Sample Policy Evidence",
            bbox=None,
        )
        for index in range(evidence_count)
    )

    with pytest.raises(EvidenceMinimizationError):
        minimize_evidence(evidence, sensitive_terms=sensitive_terms)
