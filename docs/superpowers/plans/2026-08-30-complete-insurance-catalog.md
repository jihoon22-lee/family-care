# Complete Private Insurance Catalog Implementation Plan

> **For Codex:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the incomplete 18-contract Web projection with a household-scoped catalog that preserves all 52 externally reviewed certificate-and-terms pairs, binds all six private aliases to the intended FamilyMember rows, and records the user's current-enrollment confirmation separately from certificate and terms evidence.

**Architecture:** Keep the immutable private-knowledge snapshot as the document-analysis authority. Add an append-only confirmation layer for exact subject binding and current contract status, then expose a member-filtered catalog projection to the Web. The operational policy ledger and document inventory remain the claim-ready/upload-management subset and are relabeled so that they cannot be mistaken for the complete enrolled-insurance catalog.

**Tech Stack:** FastAPI, Pydantic, psycopg/PostgreSQL, Alembic, React/TypeScript, Vitest, Testing Library, JSON Schema/OpenAPI generation.

---

## Privacy and completion gates

- No actual insurance source, extracted text, identifiers, names, amounts, Drive IDs, confirmation manifest, backup, or package output enters Git.
- Private package and confirmation files remain in external mode-0700 directories with mode-0600 regular files.
- The reviewed external package must contain exactly six subjects and 52 certificate-and-terms contract pairs.
- A contract is enrolled from certificate evidence only when its certificate decision is MATCH.
- Current status is displayed as confirmed only from a separate append-only user confirmation with actor and status_as_of.
- Semantic facts are retained only when the cited clause directly supports the fact category; generic template facts are removed.
- Actual apply follows backup -> dry run -> restored-database apply/verify -> real apply -> authenticated API verification.

## Task 1: Freeze the corrected authority and UI design

**Files:**
- Modify: docs/design/private-knowledge-catalog.md
- Modify: docs/design/insurance-document-inventory.md
- Add: docs/superpowers/plans/2026-08-30-complete-insurance-catalog.md

- [x] Document the independent certificate enrollment, current-status confirmation, terms identity, edition applicability, section mapping, and semantic-fact axes.
- [x] Define the complete catalog as the enrolled-insurance source of truth and the operational ledger/inventory as a claim-ready and upload-management subset.
- [x] Define exact FamilyMember binding and current-status confirmation provenance without storing private values in documentation.
- [x] Run python3 scripts/check_documentation.py and git diff --check.

## Task 2: Add append-only subject and contract confirmation persistence

**Files:**
- Add: apps/api/migrations/versions/0019_private_confirmations.py
- Add: apps/api/tests/test_private_knowledge_confirmation_migration.py
- Add: apps/api/tests/test_private_knowledge_confirmation_migration_integration.py

- [x] Write RED tests requiring a private_knowledge_contract_confirmations table scoped by import run, household, contract, and confirming AppUser.
- [x] Require decision IN (MATCH, NO_MATCH, UNKNOWN), bounded status values, UTC confirmation time, explicit status_as_of, stable reason code, and nonempty authority.
- [x] Require append-only identity, same-household composite foreign keys, and one current confirmation per contract through a partial unique index.
- [x] Preserve existing subject binding columns; prove a confirmed binding requires a same-household FamilyMember and actor/timestamp pair.
- [x] Implement the additive migration and run unit plus disposable-PostgreSQL upgrade/downgrade/upgrade tests.

## Task 3: Add a protected confirmation manifest and atomic apply service

**Files:**
- Add: apps/api/src/familycare_api/private_knowledge/confirmations.py
- Modify: apps/api/src/familycare_api/private_knowledge/cli.py
- Modify: apps/api/src/familycare_api/private_knowledge/repository.py
- Add: apps/api/tests/test_private_knowledge_confirmations.py
- Add: apps/api/tests/test_private_knowledge_confirmation_integration.py
- Modify: apps/api/tests/test_private_knowledge_cli.py

- [x] Write RED package tests for an external private-knowledge-confirmation.sol-v1 manifest containing exact subject-key to FamilyMember-ID bindings and canonical-contract current-status confirmations.
- [x] Reject repository-contained paths, symlinks, wrong modes, duplicate keys, unknown snapshot digest, cross-household IDs, missing actors, and private-value error echo.
- [x] Write RED dry-run/apply tests that compare the current snapshot digest, all six bindings, all contract confirmations, and the database baseline before mutation.
- [x] Atomically bind subjects and append confirmations; never fuzzy-match names and never rewrite prior confirmation history.
- [x] Add sanitized CLI commands confirmation-dry-run, confirmation-apply, and confirmation-verify.

## Task 4: Expose a member-filtered complete catalog API

**Files:**
- Modify: apps/api/src/familycare_api/private_knowledge/schemas.py
- Modify: apps/api/src/familycare_api/private_knowledge/query.py
- Modify: apps/api/src/familycare_api/private_knowledge/query_repository.py
- Modify: apps/api/src/familycare_api/private_knowledge/router.py
- Modify: apps/api/tests/test_private_knowledge_api.py
- Modify: apps/api/tests/test_private_knowledge_query_integration.py
- Modify: packages/contracts/schemas/private-knowledge.v1.schema.json
- Modify: packages/contracts/examples/private-knowledge.v1.json
- Regenerate: packages/contracts/openapi/familycare.v1.json
- Regenerate: apps/api/src/familycare_api/contracts/generated_business.py
- Regenerate: apps/web/src/api/generated.ts

- [x] Write RED tests for family_member_id filtering and household isolation.
- [x] Add subject binding, certificate enrollment, confirmed current status, status authority, and status_as_of to bounded list/detail responses.
- [x] Return per-contract certificate/terms completeness and semantic review/fact counts without exposing source aliases or full documents.
- [x] Keep Cache-Control: no-store, cursor bounds, response-size bounds, and tri-state decisions.
- [x] Regenerate contracts and run API, contract, privacy, and integration tests.

## Task 5: Make the complete catalog the primary Ledger-page insurance view

**Files:**
- Add: apps/web/src/api/private-insurance-catalog.ts
- Add: apps/web/src/features/ledger/usePrivateInsuranceCatalog.ts
- Add: apps/web/src/features/ledger/PrivateInsuranceCatalog.tsx
- Add: apps/web/src/features/ledger/private-insurance-catalog.test.tsx
- Modify: apps/web/src/features/ledger/LedgerPage.tsx
- Modify: apps/web/src/features/ledger/InsuranceDocumentInventory.tsx
- Modify: apps/web/src/features/ledger/insurance-document-inventory.test.tsx
- Modify: apps/web/src/styles.css

- [x] Write RED component tests showing a selected member's complete contract count, every contract card, certificate-and-terms state, enrolled coverage count, current-status confirmation, and expandable clause-grounded analysis.
- [x] Implement the member-filtered catalog client and hook with abort, retry, invalid-response, and authentication handling.
- [x] Place “전체 가입 보험 분석” before the operational ledger and inventory.
- [x] Relabel the operational policy ledger as the claim-ready evidence subset.
- [x] Relabel unconnected document sets as app-linking work rather than asserting that the user is not enrolled.
- [x] Run focused Vitest/Testing Library tests and corepack pnpm@11.22.0 web:check.

## Task 6: Rebuild and audit the external 52-pair analysis package

**Files:** External private package only; no source or output is committed.

- [x] Securely extract all 52 certificates and 52 terms, using local decryption/OCR and no model API.
- [x] Reconcile six subjects and 52 exact certificate-and-terms pairs against the current Drive inventory.
- [x] Re-audit all existing contracts plus the missing contracts; rebuild certificate rows and coverage components from page-grounded evidence.
- [x] Set certificate enrollment to MATCH only for rows directly present in the paired certificate and preserve uncertain row interpretation as UNKNOWN.
- [x] Validate terms identity independently from edition applicability; preserve edition UNKNOWN where the supplied edition cannot be proved applicable.
- [x] Delete generic semantic facts whose cited clause does not directly support the assigned category.
- [x] Rebuild direct facts for payment trigger, definition, exclusion, waiting period, reduction, frequency, amount, renewal, required documents, termination, and cross-reference only where explicit clause evidence exists.
- [x] Require every fact and coverage mapping to carry physical page, source-clause, and digest lineage and remain executable=false.
- [x] Validate exact six-subject, 52-contract, 52-pair counts plus child-table closure and zero executable rows.

## Task 7: Apply to restored and real PostgreSQL safely

**Files:** External backup, dry-run report, confirmation manifest, and private package only.

- [x] Confirm target containers, migration head, available resources, household, operator, and active sessions without stopping unrelated work.
- [x] Create a mode-0600 custom PostgreSQL backup outside Git and validate it with pg_restore --list.
- [x] Run package validation and dry run; compare every entity and decision matrix with the independent 52-pair audit.
- [x] Restore the backup into a disposable database, migrate to head, apply/verify the package, apply/verify confirmations, and validate all six member counts.
- [x] Apply the exact approved package and confirmation report to the real database once.
- [x] Verify one current snapshot, six exact member bindings, 52 current contract confirmations, 52 paired terms assignments, all child closures, zero executable facts/mappings, and idempotent re-apply.

## Task 8: Rebuild runtime and complete end-to-end verification

**Files:** All changed production, test, contract, design, and plan files.

- [x] Run serial repository verification:

    python3 scripts/check_documentation.py
    python3 scripts/check_repository_safety.py
    corepack pnpm@11.22.0 web:check
    TMPDIR=/tmp uv run ruff format --check .
    TMPDIR=/tmp uv run ruff check .
    TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
    TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q
    TMPDIR=/tmp uv run python scripts/check_contracts.py
    TMPDIR=/tmp uv run python scripts/check_containers.py
    TMPDIR=/tmp uv run python scripts/check_workflows.py
    git diff --check

- [x] Rebuild and recreate only the FamilyCare API and Web services, preserving the database, Worker, sessions, and archives.
- [x] Verify readiness, six-member production query counts totaling 52, member isolation, no-store headers, and the primary catalog projection. No authenticated browser session was available, so the live counts were verified through the same production repository and the authenticated HTTP path through integration tests.
- [x] Use the authenticated browser session for visual acceptance if available; otherwise record that browser-only validation remains unverified. The available browser was at the login screen and Windows app control could not attach from the WSL workspace, so browser-only acceptance remains unverified.
- [x] Review the final diff for private material, prepare a Conventional Commit, and report any PR/CI/merge boundary separately.
