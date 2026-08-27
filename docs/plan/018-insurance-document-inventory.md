# Insurance Document Inventory Implementation Plan

**Status:** Planned after actual family document review

**Goal:** Show each FamilyMember's certificate-backed policies and document completeness while keeping terms-only and product-explanation-only materials outside the enrolled policy ledger.

**Architecture:** Preserve `PolicyContract` as the only certificate-backed insurance aggregate. Add an explicit product-explanation kind, a versioned household/member-scoped link between a published policy and imported supporting documents, and a derived inventory projection. Completeness is computed from current policy Evidence and active user-confirmed links rather than stored as mutable status.

**Spec:** `docs/design/insurance-document-inventory.md`, `docs/design/policy-ledger.md`, `docs/design/data-model.md`, and `docs/design/v0.1-product.md`

## Preconditions

- Root completes the remaining family-by-family source review and records unreadable or status-unknown boundaries outside Git.
- Actual source files, extracted text, screenshots, OCR output, identifiers, amounts, and paths remain outside the repository.
- The current login session, credentials, HTTPS port, archive key, and running private import behavior remain unchanged.
- Root owns the insurance semantics, migration invariants, final association review, and actual-data acceptance. Bounded synthetic tests or UI implementation may be delegated and are reviewed as one grouped unit.

## Global constraints

- A published `PolicyContract` with policy Evidence is the only registered insurance record.
- Terms and product explanations without a confirmed policy link are displayed as documents with `가입 확인 안 됨` and cannot publish Riders.
- `product_explanation` is a distinct document kind and never aliases `terms` or establishes enrollment authority.
- Completeness values are exactly `CERTIFICATE_AND_TERMS` and `CERTIFICATE_ONLY`; product explanation is an independent presence flag and count.
- Only active `USER_CONFIRMED` document links satisfy completeness. Suggested or conflicting matches remain review items.
- Missing, unreadable, duplicated, conflicting, or stale material is visible. Document role, processing, pairing, and duplicate state remain independent dimensions so one warning never hides another. None of these states becomes `NO_MATCH`, an active contract, or a fabricated product match.
- Every business query and mutation uses server-derived HouseholdSpace scope and validates the selected FamilyMember.
- The API and logs exclude source keys, absolute paths, archive keys, raw text, policy numbers, credentials, and provider payloads.
- Web/API/Worker validation and container builds run serially according to `AGENTS.md`.

## Task 1: Extend document classification

**Files:** migration after `0016_policy_structuring_jobs`, document batch contracts and generators, API batch models/repository, Worker batch validation, Web import controls, focused migration/API/Worker/Web tests.

1. [ ] Write RED tests that accept `product_explanation` in an authenticated private batch and reject unknown kinds.
2. [ ] Write RED tests proving a product explanation never enqueues a policy structuring job and cannot publish a PolicyContract or Rider.
3. [ ] Add the forward migration that replaces the bounded `documents` and `document_batch_items` kind constraints without weakening their other values.
4. [ ] Regenerate JSON Schema, OpenAPI, Python, and TypeScript contracts; do not hand-edit generated files.
5. [ ] Add `상품설명서` to the import selector and status display while preserving password and no-store behavior.

## Task 2: Persist reviewed policy-document links

**Files:** same forward migration, `policies` domain/repository/service/router modules, versioned transport schema/example, synthetic PostgreSQL tests.

1. [ ] Write RED migration tests for `policy_document_links`, foreign keys, household/member scope columns, match states, optimistic version, soft delete, confirmation metadata, and active uniqueness.
2. [ ] Add `policy_document_links` with document role `terms`, `product_explanation`, or `supporting` and match state `SUGGESTED`, `USER_CONFIRMED`, `CONFLICT`, or `REJECTED`.
3. [ ] Validate that policy, FamilyMember, batch item, DocumentVersion, Evidence, and current session all belong to the same HouseholdSpace and import context.
4. [ ] Permit one common terms document to link to multiple policies for the same FamilyMember, but reject cross-member and cross-household links.
5. [ ] Add create and soft-delete use cases with expected version. Do not rewrite historical extraction, batch, or candidate rows.

## Task 3: Build the member inventory projection

**Files:** inventory domain/projection repository/service/router, OpenAPI and JSON Schema contracts, API/privacy/integration tests.

1. [ ] Write RED projection tests for certificate+terms, certificate only, certificate+product explanation, terms only, product explanation only, supporting-only, and combinations with unreadable, conflict, or duplicate flags.
2. [ ] Implement `GET /api/v1/family-members/{member_id}/insurance-document-inventory` from active policies, batch items, immutable DocumentVersions, processing status, and confirmed links.
3. [ ] Deduplicate counts by content identity while retaining a warning for same-member duplicates and possible cross-member shared copies. Never change ownership automatically.
4. [ ] Count only policy-source Evidence as registered insurance and only active user-confirmed terms links as `CERTIFICATE_AND_TERMS`.
5. [ ] Return document role, processing state, pairing state, and duplicate state as separate bounded fields in a no-store projection with safe labels and internal IDs, excluding paths, raw text, policy numbers, external identifiers, and archive metadata.

## Task 4: Add the family insurance-document UI

**Files:** ledger API client, hook, summary and inventory components, ledger page styling, Vitest/Testing Library and Playwright tests.

1. [ ] Write RED component tests for summary counts, document chips, missing-document copy, product-explanation presence, and the visual separation between registered policies and unpaired documents.
2. [ ] Add `보험·문서 현황` to the current family ledger without changing the semantics of `확인된 계약` or candidate review.
3. [ ] Show `증권 근거 보험`, `증권+약관`, `증권만`, `미연결 약관`, `상품설명서`, and `판독 필요` summary counts.
4. [ ] Add same-member import navigation and document link/unlink controls with CSRF, expected version, and memory-only cache invalidation.
5. [ ] Verify keyboard navigation, narrow viewport layout, empty states, partial API failure, login expiry, and that the service worker never caches the inventory response.

## Task 5: Grouped verification and actual-data acceptance

1. [ ] Run focused migration, repository, API, contract, Web, and PostgreSQL integration tests after Tasks 1–4 are assembled.
2. [ ] Run the complete required repository verification serially and record exact results.
3. [ ] Review the whole diff once for enrollment authority, pairing state, household/member scope, duplicate handling, privacy, logs, and cache behavior.
4. [ ] Rebuild FamilyCare API, Worker, and Web images one at a time; apply the forward migration and restart only FamilyCare-owned services.
5. [ ] Revalidate the existing HTTPS login endpoint without changing credentials, sessions, port, or keys.
6. [ ] Root reviews and links the already analyzed family inventories in the private runtime. No actual values enter commits, tests, logs, or the completion document.
7. [ ] Compare every family summary against the root-owned external review, including certificate-backed policy count, paired terms, policy-only gaps, unpaired terms, product explanations, unreadable sources, duplicates, and status-unknown boundaries.

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
  -> independent product-explanation presence

unpaired or incomplete material
  -> terms only, product explanation only, unpublished policy,
     unreadable source, conflict, supporting material, or duplicate warning
```

No completion claim is made until root compares the runtime projection to the external family review and records all inaccessible visual/OCR and current-status checks as unverified rather than inferred.
