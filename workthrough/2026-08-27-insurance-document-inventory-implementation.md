# Family insurance-document inventory implementation

## Overview

FamilyCare now exposes and renders a FamilyMember-scoped insurance-document inventory. Certificate-backed `PolicyContract` records remain the only registered insurance authority, while terms-only, product-explanation-only, application-only, unreadable, conflicting, supporting, and duplicate materials remain visibly separate as unconfirmed documents.

The private runtime was migrated to schema `0017_insurance_inventory`, and the API, Worker, and Web images were rebuilt and restarted without changing credentials, sessions, HTTPS port, or archive keys. Root-owned actual family-document linking remains a separate private acceptance step because the runtime did not yet contain family, policy, or batch rows at deployment time.

## Context

- Root had already completed the family-by-family document review outside Git and established which password-protected sources remained unreadable.
- The existing ledger could show published policies and Riders but could not express whether a certificate had matching terms, only a certificate, a product explanation, an application, or an unreadable source.
- One physical PDF can contain multiple insurance products or document roles, so source filename and intake kind cannot be final classification authority.
- Actual documents, extracted text, OCR, identifiers, amounts, source paths, passwords, and Drive identifiers must remain outside the repository and ordinary logs.

## Changes made

### 1. Added immutable document-version and reviewed-component persistence

Files include:

- `apps/api/migrations/versions/0017_insurance_document_inventory.py`
- `workers/analyzer/src/familycare_worker/repository.py`
- `apps/api/src/familycare_api/documents/batch_repository.py`

The migration and Worker now:

- distinguish `product_explanation` and `application` from policy and terms sources;
- pin every successful private batch item to its processed immutable `DocumentVersion`;
- backfill an older successful item only from a structuring job or an archive that was active at the item completion time;
- create reviewed page-range components, member-scoped document sets, and versioned set items;
- preserve soft deletion, confirmation actor/time, optimistic versioning, same-member scope, and page bounds;
- convert new batch/document kinds to `supporting` before a downgrade restores the older constraints.

Policy structuring remains explicitly limited to `document_kind == "policy"`; product explanations and applications cannot enqueue or publish policy candidates.

### 2. Added the household-scoped inventory domain and API

Files include:

- `apps/api/src/familycare_api/insurance_documents/domain.py`
- `apps/api/src/familycare_api/insurance_documents/repository.py`
- `apps/api/src/familycare_api/insurance_documents/schemas.py`
- `apps/api/src/familycare_api/insurance_documents/service.py`
- `apps/api/src/familycare_api/insurance_documents/router.py`

The read model derives:

- certificate-backed policy count;
- `CERTIFICATE_AND_TERMS` versus `CERTIFICATE_ONLY`;
- independent product-explanation and application presence;
- unregistered terms, explanations, applications, policy components, and supporting material;
- conflict and duplicate warnings;
- path-free unreadable sources with only an internal item ID, generalized role label, and bounded processing state.

Duplicate checks now join the exact `processed_document_version_id`. A reissued version of the same logical Document is therefore not misreported as a shared family copy merely because an older version has the same content identity.

The API provides member inventory, component creation, document-set creation, set-item attachment/detachment, and set soft deletion. All mutations use authenticated server-derived household scope, CSRF protection, expected versions, and validated member/import lineage.

### 3. Added the family insurance-document UI

Files include:

- `apps/web/src/features/ledger/InsuranceDocumentInventory.tsx`
- `apps/web/src/features/ledger/useInsuranceDocumentInventory.ts`
- `apps/web/src/api/insurance-document-inventory.ts`
- `apps/web/src/features/ledger/LedgerPage.tsx`
- `apps/web/src/features/documents/ImportPage.tsx`
- `apps/web/src/styles.css`

The ledger now shows:

- six summary cards for certificate-backed policies, certificate+terms, certificate only, unpaired terms, product explanations, and unreadable sources;
- a clear visual split between registered insurance and `가입 확인 안 됨` material;
- source counts, reviewed page-range counts, bundled-source labels, processing/review/duplicate states, and missing-terms copy;
- same-member import navigation;
- confirmed component attachment and set-item detachment controls;
- automatic creation of a registered document set on the first attachment to a PolicyContract;
- a safe error boundary in which an invalid inventory success payload becomes `INVALID_RESPONSE` and never removes the existing ledger.

Suggested, conflicted, or rejected components do not expose a confirmed-attachment action. The Web cache is memory-only, every request uses `cache: "no-store"`, and the service worker still caches only the application shell.

### 4. Kept generated contracts formatter-stable

The OpenAPI, Python TypedDict, and TypeScript consumers were regenerated. The document and Web generators were adjusted to emit Ruff/Prettier-stable wrapping for the longer source-kind and inventory unions, so a clean generation check and formatting check agree without hand-editing generated files.

## Key decisions

1. `PolicyContract` and its certificate Evidence remain the sole enrollment authority.
2. Terms, product explanations, and applications cannot establish enrollment or publish Riders.
3. Completeness has only two values: certificate+confirmed terms or certificate only.
4. A successful batch item pins its processed immutable version; arbitrary latest-version selection is prohibited.
5. Password/OCR/failed items without a version are unreadable sources, not fabricated components.
6. Physical source count, reviewed component count, pairing state, processing state, and duplicate state remain independent.
7. The UI creates a registered document set lazily on first attachment so a valid PolicyContract without prior inventory metadata remains usable.

## Verification results

### Focused and integration checks

```text
Inventory/domain/API/migration and document-kind tests: 47 passed
Inventory PostgreSQL integration: 1 passed
Worker processed-version PostgreSQL integration: 1 passed
Inventory/candidate Web regression: 17 passed
Disposable PostgreSQL 0017 downgrade to 0016 and upgrade to head: passed
```

### Complete repository checks

```text
Web: format, lint, typecheck, 20 files / 111 tests, and production PWA build passed
Ruff format: 377 files passed
Ruff check: passed
mypy: 171 source files passed
Default pytest: 1248 passed, 110 deselected, 3 subtests passed
Contract checks: passed
Container definitions: 3 images, 4 Compose services passed
Workflow policy: passed
Repository safety: passed
git diff --check: passed
```

### Runtime checks

```text
API, Worker, and Web images: built one at a time
Runtime schema: 0017_insurance_inventory
FamilyCare API, Worker, Web, and database: healthy
Local /healthz: 200
HTTPS :8443 /healthz: 200
HTTPS :8443 /login: 200
Inventory API without a session: 401
Runtime user/session row counts after restart: 1 / 4, unchanged from before restart
```

The disposable PostgreSQL container used only synthetic fixtures and was removed after verification.

## Privacy and authority boundary

- No actual insurance document, text extraction, OCR output, screenshot, page image, identifier, amount, filename, source path, password, archive key, token, or Drive identifier was added to Git or normal logs.
- All committed examples and integration fixtures are synthetic.
- Actual contract status remains `UNKNOWN` unless independently verified; document completeness never changes coverage eligibility.
- Credentials, passwords, current sessions, port 8443, and archive keys were not changed.
- The HTTPS checks verified endpoint availability only and did not perform a credentialed login.

## Remaining work

- Materialize/import the already reviewed family documents into the private runtime without adding private data to Git.
- Root creates or confirms the family, policy, Rider, reviewed component, and document-set associations.
- Compare every member inventory with the external root-owned review, including unreadable and document-only gaps.
- Keep key rotation last, after private runtime acceptance is complete.
