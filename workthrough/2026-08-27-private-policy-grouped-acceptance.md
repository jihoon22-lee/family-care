# Grouped private policy acceptance

## Overview

The grouped Task 5 acceptance was completed after commit `bf6de78` on
`feat/private-policy-structuring`. The synthetic repository, Web, migration,
container, runtime, and session-preservation checks passed within their stated
boundaries. Task 5 items 1–4 are now checked in
[`docs/plan/017-private-policy-structuring.md`](../docs/plan/017-private-policy-structuring.md).

Actual Drive materialization, private insurance-document review, external
provider acceptance, and credentialed login action remain outside this
acceptance and are still pending as Task 5 item 5.

## Context

- The acceptance was grouped by work unit so the completed implementation could
  be reviewed with one verification record rather than repeated per-file checks.
- The current FamilyCare runtime was treated as user-owned. Credentials,
  passwords, authentication configuration, and existing session state were not
  changed.
- All database fixtures used for the disposable integration environment were
  synthetic. No actual insurance document, extracted text, OCR output, provider
  payload, source path, or Drive identifier was copied into the repository or
  logs.

## Acceptance scope

### Task 5 items completed

1. Document-kind controls and import-page status copy were present.
2. Password handling, no-store behavior, and current login/session behavior
   remained unchanged.
3. Grouped Web, Python, contract, container, workflow, migration, integration,
   repository-safety, and diff checks were executed.
4. API/Worker/Web images were rebuilt serially, migrations were applied, only
   FamilyCare-owned API/Worker/Web services were recreated, and the existing
   endpoints were revalidated without credential changes.

### Task 5 item still pending

5. Materializing approved Drive files outside Git and beginning root-owned
   family-by-family analysis was not performed in this acceptance.

## Verification results

### Repository and Python checks

```text
Documentation check: 45 passed
Repository safety: 525 paths checked
Ruff format: 360 files passed
Ruff check: passed
mypy: 165 sources passed
Default pytest: 1217 passed, 109 deselected, 3 subtests passed
Synthetic PostgreSQL integration pytest: 109 passed, 1217 deselected
Contract checks: passed
Container-definition checks: 3 images, 4 services
Workflow checks: passed
git diff --check: passed
```

### Web checks

The full Web invocation passed format, lint, and typecheck. Vitest was recorded
across two runs because the first invocation encountered an environmental
resource-contention worker-start timeout in `document-import.test.tsx` after
17 files and 92 tests had passed. The isolated retry passed the remaining one
file and 7 tests. Therefore the Web result is 18 files and 99 tests passed
across the two runs; it is not claimed as one uninterrupted invocation.

The production/PWA build also passed with 80 modules.

### Database and migration checks

A fresh disposable PostgreSQL database upgraded from `0001` to `0016`,
downgraded from `0016` to `0015`, and upgraded back to `0016`. The database
was removed after the acceptance. The removed container was unrecoverable but
contained only synthetic fixtures.

### Runtime and session checks

- API, Worker, and Web images were built one at a time.
- The current FamilyCare database migrated from `0013` to `0016`; the database
  was not recreated.
- Only the API, Worker, and Web services were recreated. All FamilyCare
  services were healthy afterward.
- Local and HTTPS `/healthz` returned `200`.
- API live and ready endpoints returned `200`.
- Local and HTTPS `/login` returned `200`.
- The database reported schema `0016`, one user, and two active sessions after
  the checks, confirming that existing sessions were preserved.
- Credentials, passwords, and authentication configuration were unchanged.

The `/login` checks were endpoint availability checks only. No credentialed
login action was performed.

## Boundary and unverified items

- Actual insurance PDFs, terms, policy text, OCR, and derived private data were
  not opened, materialized, analyzed, or stored in the repository.
- No external provider call was made for this acceptance.
- No credentialed login action was tested; only endpoint responses were checked.
- No PR was opened and nothing was pushed.
- Windows, mobile, other-device, production, and actual private-document
  acceptance remain unverified.

## Privacy and authority boundary

The acceptance used only synthetic fixtures for disposable integration checks.
No private source contents, personal values, authentication secrets, session
tokens, provider payloads, or Drive identifiers were recorded. Actual family
insurance-document classification, Evidence selection, policy interpretation,
and final ledger review remain root-owned and must stay outside Git.

## Next steps

- Keep Task 5 item 5 unchecked until the user-approved external source and
  output roots are explicitly ready.
- Perform actual family-by-family document review only outside the repository,
  recording sanitized aggregate outcomes and preserving the current privacy
  boundary.
