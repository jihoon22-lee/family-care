# Family insurance-document inventory design

## Overview

This workthrough records the design and implementation plan for a FamilyMember-scoped insurance-document inventory. The feature will show certificate-backed registered policies separately from terms-only, product-explanation-only, unreadable, conflicting, supporting, and duplicate material so missing documents can be supplemented without overstating enrollment.

## Context

- The existing ledger intentionally publishes only `PolicyContract` and Rider records supported by policy Evidence.
- The private batch currently distinguishes `policy`, `terms`, and `supporting`, but does not give product explanations an explicit role.
- The current persistence model does not represent reviewed page-range components inside mixed PDFs or a member-scoped document set that can exist before a certificate-backed policy is confirmed.
- Actual family source review remains root-owned and outside Git. This change contains only public design, synthetic implementation guidance, and aggregate workflow boundaries.

## Changes Made

### 1. Defined the product and domain boundary

File: `docs/design/insurance-document-inventory.md`

- Split the UI into certificate-backed registered policies and unpaired or incomplete material.
- Limited registered-policy completeness to `CERTIFICATE_AND_TERMS` and `CERTIFICATE_ONLY`.
- Kept product-explanation presence independent so it never substitutes for terms or policy Evidence.
- Defined terms-only, product-explanation-only, unpublished-policy, unreadable, conflict, supporting-only, and duplicate states.
- Kept document role, processing, pairing, and duplicate state as independent dimensions so unreadable or conflict warnings do not hide the original document role.
- Added reviewed page-range components and member-scoped insurance document sets, then required active `USER_CONFIRMED` set items for completeness.
- Kept physical source counts separate from role component counts so a bundled PDF or duplicate does not inflate missing-document totals.
- Added explicit application handling while preserving that neither an application nor a product explanation is enrollment authority.

### 2. Defined the follow-up implementation plan

File: `docs/plan/018-insurance-document-inventory.md`

- Sequenced product-explanation/application classification, mixed-source components, insurance document sets, the member inventory projection, the ledger UI, and grouped verification.
- Required RED tests before each behavior change and a full serial repository gate after the assembled work unit.
- Kept actual-data acceptance after the remaining root-owned family review.
- Prohibited credential, session, port, and key changes during this follow-up.

### 3. Integrated the design with existing plans

Files:

- `docs/design/data-model.md`
- `docs/design/policy-ledger.md`
- `docs/design/v0.1-product.md`
- `docs/plan/000-project-roadmap.md`
- `docs/plan/003-v0.1-implementation-index.md`
- `docs/plan/017-private-policy-structuring.md`

The updates assign one owner to the inventory semantics, place the feature after actual family review and before release, and preserve the policy-ledger rule that non-policy documents cannot establish enrollment.

### 4. Extended the documentation contract

Files:

- `scripts/check_documentation.py`
- `scripts/tests/test_documentation.py`

The documentation test was first changed to require the new design and plan and failed because the checker did not yet include them. The checker now requires the new documents and the active private-policy-structuring plan.

## Key decisions

1. `PolicyContract` remains the sole registered-insurance aggregate.
2. `product_explanation` and `application` become explicit source/component roles and have no enrollment authority.
3. A physical source can contain several insurance products or roles; final classification uses reviewed 1-based page-range components rather than filename or source kind alone.
4. Product-explanation and application presence are independent flags/counts, not completeness categories.
5. Only active user-confirmed terms set items make a policy certificate-and-terms complete.
6. Content-hash and component duplicates are counted once but never moved between family members automatically.
7. Missing or unreadable material remains visible and does not produce a coverage decision.

## Verification Results

The documentation requirement test was observed failing for the expected missing-checker reason before the checker update. After the design edits, the following focused checks passed:

```text
TMPDIR=/tmp uv run pytest scripts/tests/test_documentation.py -q
python3 scripts/check_documentation.py
python3 scripts/check_repository_safety.py
git diff --check
```

The application, Python domain, contract, container, and workflow suites were not required for this design-only unit. They remain mandatory in the implementation plan.

## Privacy and authority boundary

- No actual document, extracted text, OCR output, screenshot, identifier, name, amount, source path, archive key, password, token, or provider payload was added.
- The feature design returns only bounded inventory metadata through a no-store API.
- Root retains responsibility for actual document pairing and runtime acceptance.
- Terms-only and product-explanation-only material remains explicitly outside PolicyContract and Rider publication.

## Next Steps

- Complete remaining password/font/raw-visual boundaries and current/renewal verification without inferring missing facts.
- Implement the inventory plan after that review, using synthetic tests and grouped verification.
- Compare the final runtime counts and pairings against the root-owned external review before release.
