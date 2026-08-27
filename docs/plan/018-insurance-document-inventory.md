# Insurance Document Inventory Implementation Plan

**Status:** Task 5 grouped verification in progress after actual family document review

**Goal:** Show each FamilyMember's certificate-backed policies and document completeness while keeping terms-only and product-explanation-only materials outside the enrolled policy ledger.

**Architecture:** Preserve `PolicyContract` as the only certificate-backed insurance aggregate. Add explicit product-explanation/application kinds, reviewed page-range components for mixed PDFs, member-scoped insurance document sets that may exist without a registered contract, and a derived inventory projection. Completeness is computed from current policy Evidence and active user-confirmed set items rather than stored as mutable status.

**Spec:** `docs/design/insurance-document-inventory.md`, `docs/design/policy-ledger.md`, `docs/design/data-model.md`, and `docs/design/v0.1-product.md`

## Preconditions

- Root completes the remaining family-by-family source review and records unreadable or status-unknown boundaries outside Git.
- Actual source files, extracted text, screenshots, OCR output, identifiers, amounts, and paths remain outside the repository.
- The current login session, credentials, HTTPS port, archive key, and running private import behavior remain unchanged.
- Root owns the insurance semantics, migration invariants, final association review, and actual-data acceptance. Bounded synthetic tests or UI implementation may be delegated and are reviewed as one grouped unit.

## Global constraints

- A published `PolicyContract` with policy Evidence is the only registered insurance record.
- Terms, product explanations, and applications without a certificate-backed registered set are displayed with `가입 확인 안 됨` and cannot publish Riders.
- `product_explanation` is a distinct document kind and never aliases `terms` or establishes enrollment authority.
- `application` is distinct from a certificate and never establishes a published contract or current status.
- A source file can contain multiple reviewed page-range components for different insurance products and roles. Filename and source-level kind are never final role authority.
- Completeness values are exactly `CERTIFICATE_AND_TERMS` and `CERTIFICATE_ONLY`; product explanation is an independent presence flag and count.
- Only active `USER_CONFIRMED` document-set items satisfy completeness. Suggested or conflicting matches remain review items.
- Missing, unreadable, duplicated, conflicting, or stale material is visible. Document role, processing, pairing, and duplicate state remain independent dimensions so one warning never hides another. None of these states becomes `NO_MATCH`, an active contract, or a fabricated product match.
- Every business query and mutation uses server-derived HouseholdSpace scope and validates the selected FamilyMember.
- The API and logs exclude source keys, absolute paths, archive keys, raw text, policy numbers, credentials, and provider payloads.
- Successful private batch items pin the immutable processed DocumentVersion; password/OCR/failed items without a version remain path-free unreadable sources rather than fabricated components.
- Web/API/Worker validation and container builds run serially according to `AGENTS.md`.

## Task 1: Extend source and component classification

**Files:** migration after `0016_policy_structuring_jobs`, document batch contracts and generators, API batch/component models and repository, Worker batch validation, Web import/component review controls, focused migration/API/Worker/Web tests.

1. [x] Write RED tests that accept `product_explanation` and `application` in an authenticated private batch and reject unknown kinds.
2. [x] Write RED tests proving neither kind enqueues a policy structuring job or can publish a PolicyContract or Rider.
3. [x] Write RED migration/domain tests for `insurance_document_components`: immutable DocumentVersion, 1-based inclusive page range, role, Evidence, review state, optimistic version, soft delete, and same-role overlap conflict.
4. [x] Add the forward migration that replaces the bounded `documents` and `document_batch_items` kind constraints and creates components without weakening existing values.
5. [x] Support one source with two policy components, certificate+terms components, and explanation+application components. Keep source kind as immutable intake history.
6. [x] Regenerate JSON Schema, OpenAPI, Python, and TypeScript contracts; do not hand-edit generated files.
7. [x] Add `상품설명서` and `청약서` to import/review controls while preserving password and no-store behavior.

## Task 2: Persist reviewed insurance document sets

**Files:** same forward migration, `policies` domain/repository/service/router modules, versioned transport schema/example, synthetic PostgreSQL tests.

1. [x] Write RED migration tests for `insurance_document_sets` and `insurance_document_set_items`, including optional PolicyContract, household/member scope, match states, optimistic version, soft delete, confirmation metadata, and active uniqueness.
2. [x] Allow a document set without a PolicyContract so terms+product explanation can be shown together as `가입 확인 안 됨`.
3. [x] Add set items for `policy`, `terms`, `product_explanation`, `application`, or `supporting` components with `SUGGESTED`, `USER_CONFIRMED`, `CONFLICT`, or `REJECTED` match state.
4. [x] Validate that set, optional policy, FamilyMember, component, batch item, DocumentVersion, Evidence, and current session all belong to the same HouseholdSpace and import context.
5. [x] For a registered set, require the authoritative policy component to contain the PolicyContract Evidence page and match its source DocumentVersion.
6. [x] Permit one common terms component to appear in multiple sets for the same FamilyMember, but reject cross-member and cross-household links.
7. [x] Add create, item attach/detach, and soft-delete use cases with expected version. Do not rewrite historical extraction, batch, or candidate rows.

## Task 3: Build the member inventory projection

**Files:** inventory domain/projection repository/service/router, OpenAPI and JSON Schema contracts, API/privacy/integration tests.

1. [x] Write RED projection tests for certificate+terms, certificate only, certificate+product explanation, terms+explanation without certificate, product explanation only, application only, supporting-only, and combinations with unreadable, conflict, or duplicate flags.
2. [x] Add synthetic mixed-source cases: two certificates in one file, certificate+terms in one file, and a filename/source kind that differs from reviewed component roles.
3. [x] Implement `GET /api/v1/family-members/{member_id}/insurance-document-inventory` from active policies, document sets/items, components, batch items, immutable DocumentVersions, and processing status.
4. [x] Deduplicate physical source counts by content identity and component counts by content identity plus page range and role. Retain same-member and possible cross-member warnings without changing ownership.
5. [x] Count only PolicyContract source Evidence inside a confirmed policy component as registered insurance and only active user-confirmed terms set items as `CERTIFICATE_AND_TERMS`.
6. [x] Return source count, component count, page range, role, processing state, pairing state, and duplicate state as separate bounded fields in a no-store projection with safe labels and internal IDs, excluding paths, raw text, policy numbers, external identifiers, and archive metadata.

## Task 4: Add the family insurance-document UI

**Files:** ledger API client, hook, summary and inventory components, ledger page styling, Vitest/Testing Library and Playwright tests.

1. [x] Write RED component tests for summary counts, source/component chips, bundle labels, missing-document copy, product-explanation/application presence, and the visual separation between registered policies and unregistered document sets.
2. [x] Add `보험·문서 현황` to the current family ledger without changing the semantics of `확인된 계약` or candidate review.
3. [x] Show `증권 근거 보험`, `증권+약관`, `증권만`, `미연결 약관`, `상품설명서`, and `판독 필요` summary counts.
4. [x] Add same-member import navigation and document-set/component attach/detach controls with CSRF, expected version, and memory-only cache invalidation. Create a registered set on the first confirmed attachment when the PolicyContract does not have one yet.
5. [x] Verify keyboard navigation, narrow viewport layout, empty states, partial API failure, login expiry, and that the service worker never caches the inventory response.

## Task 5: Grouped verification and actual-data acceptance

1. [x] Run focused migration, repository, API, contract, Web, and PostgreSQL integration tests after Tasks 1–4 are assembled.
2. [x] Run the complete required repository verification serially and record exact results.
3. [x] Review the whole diff once for enrollment authority, mixed-source segmentation, set pairing state, household/member scope, duplicate handling, privacy, logs, and cache behavior.
4. [x] Rebuild FamilyCare API, Worker, and Web images one at a time; apply the forward migration and restart only FamilyCare-owned services.
5. [x] Revalidate the existing HTTPS login endpoint without changing credentials, sessions, port, or keys.
6. [ ] Root reviews and links the already analyzed family inventories in the private runtime. No actual values enter commits, tests, logs, or the completion document.
7. [ ] Compare every family summary against the root-owned external review, including certificate-backed policy count, paired terms, policy-only gaps, unregistered terms sets, product explanations, applications, unreadable sources, mixed bundles, duplicates, and status-unknown boundaries.

## Required verification

```bash
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
```

## Completion boundary

The feature is complete only when the private runtime shows every FamilyMember in two unambiguous groups:

```text
certificate-backed registered policies
  -> certificate + confirmed terms, or certificate only
  -> independent product-explanation and application presence

unregistered or incomplete document sets/components
  -> terms only, product explanation only, application only, unpublished policy,
     mixed source, unreadable source, conflict, supporting material, or duplicate warning
```

No completion claim is made until root compares the runtime projection to the external family review and records all inaccessible visual/OCR and current-status checks as unverified rather than inferred.
