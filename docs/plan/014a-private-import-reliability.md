# Private Import Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Every behavior change follows RED, GREEN, focused verification, and a reviewable Conventional Commit.

**Goal:** Close the bounded-decryption, cancellation/lease secret lifecycle, archive commit ambiguity, and display-label contract gaps found in the concentrated OCR pre-PR review before any private Compose or actual-document acceptance begins.

**Architecture:** Keep the existing API-owned authenticated batch boundary, Worker-owned password registry, descriptor-only parser/OCR path, encrypted archive, and PostgreSQL transaction model. Add an output-counting plaintext writer and pre-clone page bound; require an owned heartbeat immediately before and after archive creation; dispose and deactivate runtime passwords whenever ownership is lost or processing is cancelled; and preserve durable ciphertext when DB commit outcome is uncertain. Normalize labels once at the source catalog and enforce the same API/OpenAPI/JSON Schema contract.

**Scope:** This is an independent corrective PR between selective OCR and private local runtime. The approved v0.1 runtime is one household with one shared import root. Multi-household source-root partitioning, archive orphan reconciliation UI, Cloud Run, Google Drive API, Tailscale mutation, and actual private documents remain out of scope.

**Primary files:**

- `workers/analyzer/src/familycare_worker/imports/batch.py`
- `workers/analyzer/src/familycare_worker/repository.py`
- `workers/analyzer/src/familycare_worker/imports/secret_channel.py`
- `workers/analyzer/src/familycare_worker/__main__.py`
- `apps/api/src/familycare_api/documents/import_sources.py`
- `apps/api/src/familycare_api/documents/batch_router.py`
- `packages/contracts/schemas/document-batch-status.v1.schema.json`
- generated OpenAPI/Python/TypeScript contracts and focused synthetic tests

## Invariants

- The encrypted input and decrypted output are each bounded to the existing 25 MiB policy, and page count is checked before `PdfWriter.clone_document_from_reader()` writes plaintext.
- Decrypted bytes and PDF passwords never enter logs, DB rows, API responses, fixtures, command output, or Git.
- A Worker may create an archive only while it still owns a live item lease. It checks ownership again immediately after the durable archive write and before any DB success transaction.
- Cancellation, stop request, lease loss, and uncertain DB completion dispose the batch registry entry and deactivate its secret-server batch identity.
- Before DB persistence begins, a lost lease makes the new archive a definite orphan and it is deleted. After DB persistence begins, an exception is commit-ambiguous and the ciphertext is retained to avoid a committed `managed_archives` row pointing to a missing object.
- Commit-ambiguous retention is logged only as a stable category; no path, object key, password, document label, or content is logged.
- Import labels contain 1–160 printable characters and never NUL, CR, LF, control characters, or a path prefix. The catalog, Pydantic/OpenAPI model, and JSON Schema agree.
- PR/CI uses only generated synthetic PDFs, fake repositories, and task-owned PostgreSQL.

## Task 1: Bound encrypted-PDF plaintext creation

- [ ] Add focused RED tests in `workers/analyzer/tests/test_batch_runner.py` for an encrypted reader whose page count exceeds 500 and a writer that attempts to exceed 25 MiB. Assert parser/archive/persistence are not called, the item receives `PAGE_LIMIT_EXCEEDED` or `DOCUMENT_TOO_LARGE`, and the mode-0700 workspace is removed.
- [ ] Implement a small seek-aware bounded binary writer around the pre-created mode-0600 plaintext handle. Reject a write whose resulting maximum extent exceeds `MAX_INPUT_BYTES`; do not buffer the complete output in Python.
- [ ] After password acceptance and before cloning, evaluate `len(reader.pages)` and reject more than `MAX_PDF_PAGES`. Keep wrong/missing passwords mapped to `PASSWORD_REQUIRED`.
- [ ] Run focused unit tests, Ruff, and mypy for the batch module; commit as `fix(import): bound decrypted pdf output`.

## Task 2: Dispose passwords on cancellation and ownership loss

- [ ] Add RED tests for parser heartbeat loss, stop request, OCR cancellation, pre-archive ownership loss, and post-archive ownership loss. Use `BatchPasswordRegistry`, assert `password_for()` returns `None`, and assert a deactivation callback receives exactly the batch UUID.
- [ ] Add a sanitized `on_password_discarded` callback to `BatchRunner`. Make `_discard_password_for()` idempotently dispose the registry/scope and invoke the callback without exposing values.
- [ ] Check `stop_requested()` and repository heartbeat immediately before archive creation. Check heartbeat again after `ArchiveStore.put()`; when it fails, delete the definite orphan before returning.
- [ ] Wire production `on_password_discarded` to `BatchSecretSocketServer.deactivate`. Preserve sibling-password reuse during normal successful processing; do not discard merely because one sibling succeeded.
- [ ] Run batch runner, secret-channel, and Worker lifecycle tests; commit as `fix(import): discard secrets after ownership loss`.

## Task 3: Preserve ciphertext across ambiguous DB commit

- [ ] Add a RED test whose repository mutates the item to succeeded and then raises a synthetic connection error from `mark_succeeded()`. Assert the archive object remains, no second success/failure mutation corrupts the state, the password is discarded, and the log contains only `batch_archive_commit_uncertain`.
- [ ] Add a second test for an exception before DB persistence begins (workspace cleanup or post-archive heartbeat loss) and assert that definite orphan cleanup still removes the object.
- [ ] Remove the unconditional archive delete from the `mark_succeeded()` exception path. Emit the stable uncertain-commit event, discard runtime password state, and let the existing owner-safe failure transition no-op when the database already committed.
- [ ] Document that retained encrypted orphans require a future repository/archive reconciler and are intentionally safer than deleting possibly referenced ciphertext.
- [ ] Run focused unit and PostgreSQL integration tests; commit as `fix(import): preserve ambiguous archive commits`.

## Task 4: Align the display-label boundary

- [ ] Add RED catalog/API/contract tests using from-scratch synthetic filenames containing CR, LF, tab, and control bytes where the filesystem permits them. Assert returned/persisted labels are printable leaf labels, never contain a path prefix, and fall back to `PDF document` when empty after normalization.
- [ ] Normalize the leaf label in `ImportSourceCatalog._inspect()` before it reaches the repository. Add the same no-NUL/CR/LF pattern to both import-source and batch-item Pydantic fields.
- [ ] Regenerate canonical OpenAPI and generated Python/TypeScript contracts. Keep the canonical JSON Schema pattern and verify checker parity.
- [ ] Run focused API, contract, Web generated-type, repository-safety, and `git diff --check` checks; commit as `fix(import): normalize source display labels`.

## Task 5: Documentation, whole-PR review, and merge

- [ ] Update `CHANGELOG.md`, `docs/design/pdf-ingestion.md`, `docs/design/private-data-runtime.md`, and this plan with implemented behavior and honest unverified boundaries.
- [ ] Run the complete serial Root PR gate from `docs/plan/003-v0.1-implementation-index.md`, followed by task-owned PostgreSQL integration tests. Build no image unless runtime code or container definitions require it.
- [ ] Root reviews the complete `origin/main...HEAD` diff once, tracing password disposal, archive deletion decisions, DB state transitions, error/log fields, generated contracts, and actual failure-path tests.
- [ ] Push `fix/private-import-reliability`, open one PR, wait for all required checks, merge with a merge commit, verify main, and remove the branch/worktree.

## Completion boundary

Completion means synthetic unit/integration evidence proves all four corrective boundaries and PR/main CI pass. It does not mean private Compose, Tailscale, provider, Windows/mobile, actual PDF, or v0.1.0 release acceptance is complete; those remain owned by plans 015 and 016.
