# Advisory Coverage Calculation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Activate every reviewed certificate coverage as an advisory search result and automatically provide a conditional fixed-benefit estimate from either a cited reviewed formula or, for rule-matched fixed coverage only, the certificate insured amount.

**Architecture:** Add `ADVISORY` beside the strict `PUBLISHED` execution state, preserve append-only publication history, and let the deterministic engine compute conditional amounts without converting unresolved eligibility to `MATCH`. Persist and expose advisory counts through the decision snapshot and render the distinction in the Web result page.

**Tech Stack:** PostgreSQL 18, Alembic, FastAPI, Pydantic, Python decimal calculation engine, JSON Schema/OpenAPI generation, React/TypeScript, Vitest.

**Spec:** `docs/superpowers/specs/2026-08-31-advisory-coverage-calculation-design.md`

**Status:** Complete — PR #42 merged the advisory publication and protected acceptance; PR #43
released the baseline as `v0.3.0`. PR #44/#45 and PR #47/#48 hardened relevant conditional results
and released `v0.3.1`/`v0.3.2`. Protected values and acceptance artifacts remain outside Git.

## Global Constraints

- Keep actual insurance and medical material outside Git and logs.
- Do not weaken the exact-rule and citation requirements for `PUBLISHED` coverage.
- Do not treat a conditional amount as confirmed eligibility. Include it only in the explicitly named
  conditional fixed subtotal, with `confirmed_amount` left null.
- A rule-matched fixed coverage may use the certificate insured amount only as a conditional
  estimate with `calculation_publication_id` and `confirmed_amount` left null and an explicit trace.
- Do not estimate indemnity payment without receipt and cost-sharing inputs.
- Apply private data only through backup, dry run, restored-database verification, and one real apply.

---

### Task 1: Add the advisory persistence and contract state

**Files:**
- Create: `apps/api/migrations/versions/0023_advisory_coverage_disposition.py`
- Modify: private publication and decision domain/schema/repository modules
- Modify: generated OpenAPI and JSON Schema contracts
- Test: migration, package, reconciliation, repository, and decision contract tests

- [x] Add RED tests requiring `ADVISORY`, advisory counts, and backward-compatible legacy blocked counts.
- [x] Run focused tests and confirm they fail because the state and columns are absent.
- [x] Add the migration and minimal domain/repository/contract implementation.
- [x] Regenerate contracts and run focused tests to GREEN.
- [x] Add the v2 publication-scoped enrollment authority, confirming-actor lineage, and
  user-confirmed reconciliation count without rewriting the immutable certificate snapshot.
- [x] Prove the complete authority matrix and raw-unknown advisory recommendation persistence in
  PostgreSQL.

### Task 2: Calculate conditional fixed benefits without asserting eligibility

**Files:**
- Modify: `apps/api/src/familycare_api/decisions/knowledge_engine.py`
- Modify: decision domain and response schemas
- Test: `apps/api/tests/test_private_knowledge_engine.py` and decision integration tests

- [x] Add RED tests for reviewed conditional calculation, decisive no-match suppression, indemnity suppression, catalog-only suppression, and conditional subtotal behavior.
- [x] Run the focused engine tests and confirm the expected failures.
- [x] Implement conditional execution of reviewed fixed calculations while keeping the candidate `UNKNOWN` and `confirmed_amount` null.
- [x] Add the user-approved certificate fixed-amount estimate fallback only after a reviewed rule
  matches; exclude catalog-only, decisive no-match, and indemnity rows.
- [x] Keep every AI-suggested rule outcome non-authoritative, retain AI dates as candidate facts,
  and preserve conditional fixed estimates when only the event date is missing.
- [x] Attach the bound certificate document alias and evidence pages to amount calculations.
- [x] Run focused and decision integration tests to GREEN.

### Task 3: Present advisory and conditional results clearly

**Files:**
- Modify: `apps/web/src/features/results/AnalysisCompleteness.tsx`
- Modify: `apps/web/src/features/results/ClaimCandidateCard.tsx`
- Modify: generated Web contracts and result fixtures
- Test: result-page and API client tests

- [x] Add RED component tests for advisory coverage counts and conditional amount wording.
- [x] Run the focused Vitest tests and confirm the expected failures.
- [x] Implement the labels without changing candidate authority.
- [x] Name the automatically evaluated coverages, hide catalog-only event cards, and label the
  certificate fallback as an estimate rather than a confirmed calculation.
- [x] Make automatic structuring a single bounded attempt, keep retryable polling non-terminal,
  and persist an event-level attempted marker so failed or empty runs do not spend another call.
- [x] Run focused Web tests and `web:check` to GREEN.

### Task 4: Supersede the private publication and verify live behavior

**Files:** Protected external package and reports only; nothing private is committed.

- [x] Create a new protected publication that maps reviewed legacy blocked rows to `ADVISORY`,
  declares certificate or user-confirmed enrollment authority, and updates all digests and
  reconciliation counts.
- [x] Back up the current database and validate the backup.
- [x] Dry-run, restore into a disposable database, migrate, apply, and verify the new publication.
- [x] Apply once to the real database and verify zero current ordinary blocked rows.
- [x] Rebuild the local runtime and verify authenticated result generation and conditional calculations.

### Task 5: Integrate and release

**Files:** Version metadata, changelog, release evidence, and roadmap only after the feature PR merges.

- [x] Run the complete serial repository gate and privacy review.
- [x] Push the feature branch, create the PR, wait for CI, and merge.
- [x] Create `release/v0-3-1`, update all product versions and release records, run the complete gate, open a release PR, wait for CI, and merge.
- [x] Tag the release merge as `v0.3.1`, verify the release workflow and all three version/SHA image digests.
- [x] Back up the current Compose database, stop only the FamilyCare stack, deploy the digest-pinned v0.3.1 images, migrate, and verify health, data counts, and the result path. The later `v0.3.2` acceptance supersedes this runtime baseline.
