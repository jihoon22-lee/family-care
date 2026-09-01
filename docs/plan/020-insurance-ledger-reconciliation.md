# Insurance Ledger Reconciliation Implementation Plan

**Status:** In progress.

**Goal:** Reconcile the complete private insurance catalog with the operational policy/document
subset without overwriting either authority, and remove resolved historical failures from the current
work queue while retaining their audit history.

**Architecture:** Add append-only operational-link and unreadable-resolution histories. Build one
member-scoped, repeatable-read reconciliation projection that partitions every current knowledge
contract into evidence-ready, document-pending, link-review, or conflict state and separately exposes
orphan operational policies and unresolved sources.

**Spec:** `docs/design/insurance-ledger-reconciliation.md`,
`docs/design/private-knowledge-catalog.md`, and `docs/design/insurance-document-inventory.md`.

## Fixed boundaries

- The current private snapshot remains the complete analysis authority and is never rewritten.
- Existing PolicyContract, Rider, Evidence, component, set, and batch rows remain operational history.
- `NO_MATCH` requires decisive operational identity mismatch and never means the user is uninsured.
- User-confirmed identity links do not confirm terms edition, coverage eligibility, or benefit amount.
- Tests and examples are synthetic from inception; no actual labels, paths, values, document text, or IDs
  enter Git, logs, fixtures, or CI.
- Runtime inspection is count-only until backup and explicit apply gates are satisfied.

## Task 1: Freeze design and contracts

1. [x] Record the authority split, append-only histories, state derivation, API boundary, privacy rules,
   cache invalidation, and operational acceptance gate.
2. [ ] Add the versioned transport-neutral reconciliation schema and synthetic example.
3. [ ] Extend documentation and contract drift checks for the new public boundary.

## Task 2: Add scoped append-only histories

1. [ ] Write RED structural migration tests for `0024_insurance_reconciliation` after
   `0023_advisory_disposition`.
2. [ ] Require `private_knowledge_operational_links` with current/superseded state, tri-state decision,
   conflict, optional policy link, actor/time, authority, reason, digest, household/run/contract FKs,
   one current link per knowledge contract, and one current `MATCH` per operational policy.
3. [ ] Require `document_batch_item_resolutions` with current/superseded state, resolution kind,
   optional successful replacement, actor/time, authority, reason, digest, and one current resolution
   per failed item.
4. [ ] Prove cross-household/member references, invalid state combinations, duplicate current links,
   invalid replacements, and destructive history changes are rejected or fail closed.
5. [ ] Verify clean upgrade/downgrade against a disposable PostgreSQL database.

## Task 3: Implement reconciliation and mutation use cases

1. [ ] Write RED domain tests for the closed four-state contract partition and independent unreadable
   source count.
2. [ ] Write RED PostgreSQL tests for exact snapshot binding, current history precedence, orphan policy,
   document readiness, conflict, cross-scope rejection, stale expected IDs, idempotent confirmation,
   supersede history, changed-source replacement, dismissal, and reopen.
3. [ ] Implement one repeatable-read read-only member projection with bounded results and sanitized
   repository errors.
4. [ ] Implement transactional link and resolution confirmation with row locks, exact current snapshot,
   active member policy checks, successful replacement checks, canonical digests, and optimistic
   concurrency.

## Task 4: Publish the API and generated contracts

1. [ ] Write RED API tests for the member reconciliation GET, operational-link POST, and unreadable
   resolution POST, including no-store, auth, validation, bounded output, and private-field absence.
2. [ ] Register the router and stable error codes without weakening the existing authentication/CSRF
   boundary.
3. [ ] Regenerate OpenAPI and TypeScript consumers from FastAPI; do not hand-edit generated files.
4. [ ] Run focused migration, domain, repository, API, contract, privacy, and PostgreSQL tests.

## Task 5: Consolidate the Web ledger

1. [ ] Consume the reconciliation projection as the single contract/readiness summary.
2. [ ] Add per-contract readiness badges, an orphan operational-policy review group, and a document work
   queue without duplicating a second authoritative contract list.
3. [ ] Invalidate reconciliation and inventory caches after link/resolution/document mutations and
   revalidate on focus and explicit refresh.
4. [ ] Verify keyboard, narrow viewport, partial failure, login expiry, and no persistent API caching.

## Task 6: Verification and protected acceptance

1. [ ] Run the complete required repository verification serially.
2. [ ] Merge each reviewed PR only after every required GitHub Action passes, then delete its local and
   remote branch and any completed worktree.
3. [ ] Run a count-only protected-runtime reconciliation after backup and restored-database rehearsal;
   stop on any conflict, unknown target, baseline drift, or count mismatch.
4. [ ] Record only aggregate counts, stable reason codes, CI/merge references, and unverified visual/OCR
   boundaries. Do not record actual private values.

