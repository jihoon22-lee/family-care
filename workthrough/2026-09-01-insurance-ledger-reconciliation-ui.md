# Insurance Ledger Reconciliation UI

## Overview

The coverage ledger previously presented the complete private insurance catalog, the operational
document inventory summary, and the Evidence-linked policy list as competing contract views. This
change makes the member reconciliation projection the single contract/readiness summary, preserves
the document inventory as a focused editor, and gives stale unreadable work an explicit append-only
resolution path.

## Context

- A private knowledge contract and an operational `PolicyContract` have separate authority and may
  legitimately have different counts until a user confirms their exact identity.
- Historical password/OCR/processing failures must remain auditable after a later success or an
  explicit dismissal; they must not remain indefinitely in the current work queue.
- A stale browser must not overwrite a newer operational link or document resolution.
- No filename, source path, document body, policy number, private alias, or actual identifier may be
  added to the public API, fixtures, logs, or repository.

The API regression test first failed because the reconciliation source did not expose its current
resolution version. The Web regressions then fixed the expected single-summary structure and exact-ID
mutation boundary before the UI implementation was completed.

## Changes Made

### 1. Version the unresolved document projection

- `apps/api/src/familycare_api/insurance_reconciliation/domain.py` adds a reconciliation-specific
  unresolved source with a nullable current resolution ID.
- `apps/api/src/familycare_api/insurance_reconciliation/repository.py` reads the current
  `REOPENED` resolution in the same repeatable-read projection and excludes current `REPLACED` or
  `DISMISSED` rows without deleting history.
- `apps/api/src/familycare_api/insurance_reconciliation/schemas.py` publishes the bounded optimistic
  concurrency field.
- `apps/api/tests/test_insurance_reconciliation_api.py`,
  `apps/api/tests/test_insurance_reconciliation_domain.py`, and
  `apps/api/tests/test_insurance_reconciliation_repository_integration.py` cover the initial null
  version and the exact reopened version.
- `packages/contracts/schemas/insurance-reconciliation.v1.schema.json`,
  `packages/contracts/examples/insurance-reconciliation.v1.example.json`,
  `packages/contracts/openapi/familycare.v1.json`, and `apps/web/src/api/generated.ts` were updated
  from the canonical API and synthetic contract inputs.

### 2. Use one Web reconciliation source

- `apps/web/src/api/insurance-reconciliation.ts` and its test provide no-store GET, exact operational
  link POST, and exact document resolution POST clients with runtime response validation.
- `apps/web/src/features/ledger/useInsuranceReconciliation.ts` owns the member-scoped in-memory query
  and focus revalidation.
- `apps/web/src/features/ledger/PrivateInsuranceCatalog.tsx` now renders the closed four-state summary,
  per-contract readiness, explicit orphan-policy selection, an operational-only review group, and the
  unresolved document queue. It still loads clause-grounded contract details on demand.
- `apps/web/src/features/ledger/usePrivateInsuranceCatalog.ts` was removed, and
  `apps/web/src/api/private-insurance-catalog.ts` now retains only the detail request.
- `apps/web/src/features/ledger/private-insurance-catalog.test.tsx` covers the single summary, detailed
  analysis, exact-ID link and dismissal mutations, both-cache invalidation, focus, and manual refresh.

### 3. Keep document and Evidence views subordinate

- `apps/web/src/features/ledger/InsuranceDocumentInventory.tsx` is now labeled `문서 근거 정리`, removes
  its competing summary and unreadable queue, and keeps linked document-set editing collapsed by
  default. Unregistered sets and unpaired components remain editable.
- `apps/web/src/features/ledger/useInsuranceDocumentInventory.ts` invalidates both inventory and
  reconciliation queries after a document mutation.
- `apps/web/src/features/ledger/LedgerPage.tsx` labels the existing operational Evidence view
  `청구 근거 세부 원장` so it is not mistaken for the complete catalog.
- `apps/web/src/features/ledger/insurance-document-inventory.test.tsx`,
  `apps/web/src/features/ledger/candidate-review.test.tsx`, `apps/web/src/test/mockApi.ts`, and
  `apps/web/src/styles.css` cover the revised semantics, independent partial failures, native keyboard
  controls, and narrow responsive layouts.

### 4. Align documentation and review aids

- `docs/design/insurance-ledger-reconciliation.md` records the implemented UI boundary and optimistic
  resolution field.
- `docs/design/insurance-document-inventory.md` records the editor-only inventory behavior.
- `docs/plan/020-insurance-ledger-reconciliation.md` records completed implementation and local
  verification tasks while leaving PR merge and protected-runtime acceptance open.
- `README.md` describes the integrated coverage-ledger behavior.
- `packages/contracts/README.md` updates the generated OpenAPI component-schema review count.

No dependency or configuration change was required.

## Key Implementation

The current resolution ID travels with each unresolved source so the dismissal request can reject a
stale browser version:

```python
# apps/api/src/familycare_api/insurance_reconciliation/domain.py
@dataclass(frozen=True)
class UnresolvedDocumentSource:
    document_batch_item_id: UUID
    source_kind: DocumentRole
    display_label: str
    processing_state: UnreadableProcessingState
    current_resolution_id: UUID | None
```

Either editor invalidates both member projections after a confirmed mutation:

```typescript
// apps/web/src/features/ledger/useInsuranceReconciliation.ts
cache.invalidate(`insurance-reconciliation:${memberId}`);
cache.invalidate(`insurance-document-inventory:${memberId}`);
```

Identity confirmation remains explicit and exact. The client sends the selected operational policy ID
and the expected current link ID; insurer and product display strings never create a match.

## Verification Results

- `python3 scripts/check_documentation.py` — passed for 50 documentation files.
- `python3 scripts/check_repository_safety.py` — passed for 690 repository paths.
- `corepack pnpm@11.22.0 web:check` — Prettier, ESLint, TypeScript, 152 tests in 23 files, and the
  production PWA build passed.
- `TMPDIR=/tmp uv run ruff format --check .` — 500 files already formatted.
- `TMPDIR=/tmp uv run ruff check .` — passed.
- `TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts` — no issues in 214 source files.
- `TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q` — 1,623 passed,
  186 integration tests deselected, and 3 subtests passed.
- The focused reconciliation migration/repository suite against a disposable synthetic PostgreSQL
  database — 4 passed.
- Contract, container-definition, and workflow-policy checks — passed.
- `git diff --check` — passed.

## PR and protected count-only acceptance

- PR #56 passed Repository safety, Web, Python, PostgreSQL integration, and all three container jobs,
  then merged as `bbe61d4b220f8753c2e6c6081b902d5ff93c90cb`. Its local and remote branch were
  deleted, stale remote refs were pruned, and only the root `main` worktree remained.
- The protected database had the previously recorded backup and restored-database rehearsal evidence.
  A second write was not required for this acceptance. The reconciliation was executed in one
  `REPEATABLE READ READ ONLY` transaction with a five-second statement limit and emitted counts only.
- The current snapshot contained 52 analyzed contracts. With zero exact operational identity links,
  all 52 were link-review items. The operational ledger contained 19 policies, all 19 were orphans
  relative to that snapshot, and there were zero conflicts or duplicate snapshot links.
- Of the 19 operational policies, 2 had confirmed terms Evidence and 17 were certificate-only. These
  readiness counts remain subordinate until an exact contract identity link is confirmed.
- There were 47 current failed-source rows: 5 password-required rows, 19 OCR-failed rows, and 42
  permanently-failed rows; the processing dimensions overlap. Thirty-seven exact sources had a later
  successful item of the same member and document role but a different opaque source ID. They are
  manual changed-source review candidates, not automatic replacements. Ten had no such later success.
- The protected runtime remained on migration `0023_advisory_disposition`; the new history tables,
  application migration, image rebuild, and service recreation were not applied by this count-only
  acceptance.

The closed contract partition (`52 = 0 + 0 + 52 + 0`), operational completeness partition
(`19 = 2 + 17`), and failure-source partition (`47 = 37 + 10`) were internally consistent. No actual
label, identifier, path, document content, database URL, credential, digest, or row-level result was
recorded.

## Remaining Boundaries

- Runtime migration/redeployment and the 37 explicit changed-source resolutions require a separately
  approved operational pass; this count-only acceptance did not mutate or auto-link anything.
- Windows browser, physical mobile PWA, actual insurance documents, OCR variants, tags, releases, and
  deployment were not exercised.
- All committed examples and tests are synthetic from inception. No protected document content,
  private identifier, source path, credential, or database value was copied into this record.
