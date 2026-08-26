from __future__ import annotations

from scripts.check_documentation import REQUIRED_HEADINGS

V0_1_REQUIRED_DOCUMENTS = {
    "docs/design/v0.1-product.md",
    "docs/design/ai-document-analysis.md",
    "docs/design/authentication.md",
    "docs/design/claim-workflow.md",
    "docs/design/clause-linking-search.md",
    "docs/design/event-result-pwa.md",
    "docs/design/policy-ledger.md",
    "docs/design/private-data-runtime.md",
    "docs/plan/003-v0.1-implementation-index.md",
    "docs/plan/004-policy-ledger.md",
    "docs/plan/005-policy-candidate-review.md",
    "docs/plan/006-clause-search.md",
    "docs/plan/007-rider-clause-rules.md",
    "docs/plan/008-coverage-decision-engine.md",
    "docs/plan/009-benefit-calculations.md",
    "docs/plan/010-event-result-pwa.md",
    "docs/plan/011-claim-workflow.md",
    "docs/plan/012-local-authentication.md",
    "docs/plan/013-encrypted-document-import.md",
    "docs/plan/014-selective-ocr.md",
    "docs/plan/014a-private-import-reliability.md",
    "docs/plan/015-private-local-runtime.md",
    "docs/plan/016-v0.1-release.md",
    "docs/release/v0.1.0-verification.md",
}


def test_v0_1_design_and_plan_documents_are_required() -> None:
    assert REQUIRED_HEADINGS.keys() >= V0_1_REQUIRED_DOCUMENTS
