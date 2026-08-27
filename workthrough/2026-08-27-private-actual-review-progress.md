# Aggregate actual-review progress

## Overview

This workthrough records sanitized aggregate progress from the root-owned review
of actual family insurance sources. The source review remains outside Git and
this document contains no source content, extracted text, OCR output, document
identifiers, names, amounts, or source paths. Accessible-source review now
covers all six family aliases, but the repository plan remains open because
password/font/raw-visual boundaries and current/renewal verification have not
yet changed any result from `UNKNOWN`.

## Context

- Actual family insurance documents are handled only in the approved external
  runtime boundary and are not repository fixtures.
- Aggregate counts document review progress; they do not establish coverage
  eligibility or a final policy status.
- The insurance decision boundary remains evidence-first: all reviewed statuses
  are `UNKNOWN` pending current/renewal verification.

## Changes Made

### 1. Updated the implementation plan

File: `docs/plan/017-private-policy-structuring.md`

- Kept Task 5 item 5 unchecked so the overall actual-acceptance task is not
  represented as complete.
- Recorded completed accessible-source aggregate review for Families A through
  F.
- Kept password/font/raw-visual review and current/renewal verification as
  remaining work.
- Preserved the rule that actual source material and private derivatives stay
  outside Git.

### 2. Added a sanitized progress record

File: `workthrough/2026-08-27-private-actual-review-progress.md`

- Captured only family labels, readable/unreadable source counts, policy and
  coverage aggregates, and review-state boundaries supplied for this update.
- Excluded the nonfinal Family F product explanation from the policy aggregate.
- Recorded no raw document or personal values.

## Aggregate review status

| Aggregate | Source-review progress | Policy/coverage aggregate | Status boundary |
| --- | --- | --- | --- |
| Family E | 6 readable PDFs reviewed | 3 policies / 94 coverages | All `UNKNOWN` pending current/renewal verification |
| Family F | 8 readable PDFs reviewed; 1 nonfinal product explanation excluded | 4 certificate-backed policies / 160 coverages | All `UNKNOWN` pending current/renewal verification |
| Family C | 17 PDFs + 1 image; 15 readable / 3 password-required | 5 readable certificate-backed policies / 39 coverages; 3 encrypted policy candidates unpublished | All `UNKNOWN` pending current/renewal verification |
| Family B | 18 PDFs; 16 readable / 2 password-required; one legacy-font label boundary | 7 readable certificate-backed policies / 97 coverages; 2 encrypted policy candidates unpublished | All `UNKNOWN` pending current/renewal verification |
| Family D | 21 readable PDFs; previously inaccessible supporting material classified separately | 6 certificate-backed policies / 109 coverages | All `UNKNOWN` pending current/renewal verification |
| Family A | 37 readable PDFs; mixed-role and duplicate sources reviewed | 15 certificate-backed policies / 163 coverages | All `UNKNOWN` pending current/renewal verification |

The root-verified six-digit birth-date candidates were tried only after the
empty-password check and only through memory or a temporary workspace outside
Git. The final cross-family pass used five unique verified candidates covering
all six members and opened two additional policy sources where policyholder and
insured boundaries differed. Five sources remain password-required after every
verified candidate failed. Worker plaintext workspaces were removed by the
normal cleanup path.

The two recovered sources were independently checked for certificate authority,
field-to-page Evidence, date normalization, duplicate policy identity, and
member scope. Two contracts were published with status `UNKNOWN`; one source's
date range was retained after deterministic normalization and the other source's
unreliable dates were omitted. No rider was published without conclusive enrolled
coverage Evidence.

The user then clarified a Samsung Fire issuer convention: the title
`상품설명서` on these Samsung Fire sources denotes the issued policy certificate
rather than a standalone product explanation. Root review found seven sources
inside that exact issuer-and-title boundary. Two were already registered as
policies; the remaining five were independently checked for certificate
authority, insurer and product Evidence, duplicate identity, date reliability,
and member scope before correction. All five were published as certificate-only
contracts with status `UNKNOWN`; four unreliable date ranges were omitted, one
previously validated date range was retained, and no newly proposed rider was
published. Two earlier product-explanation components, their set items, and
their unregistered sets were soft-deleted after replacement policy sets were
successfully created, preserving the audit history. One non-Samsung source with
the same title was deliberately left outside the convention.

The current runtime ledger is 18 policies and 314 riders. All seven Samsung Fire
convention sources now have one active policy component and one active registered
set, with no active product-explanation component. This runtime ledger is
deliberately reported separately from the earlier external review aggregate of
40 policies and 662 coverages because the two datasets have not yet been fully
reconciled family by family.

## Code Examples

The actual-source correction changed only private runtime projections and audit
rows outside Git. No source document, extracted content, identifier, or private
field value was added to application or test code.

## Verification Results

The full required repository checks were run serially after the runtime review
and related inventory-read changes:

```text
python3 scripts/check_documentation.py: passed (48 files)
python3 scripts/check_repository_safety.py: passed (554 paths)
corepack pnpm@11.22.0 web:check: passed (20 files / 112 tests; production PWA build passed)
TMPDIR=/tmp uv run ruff format --check .: passed (382 files)
TMPDIR=/tmp uv run ruff check .: passed
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts: passed (171 files)
TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q:
  passed (1255 tests, 111 deselected, 3 subtests)
TMPDIR=/tmp uv run python scripts/check_contracts.py: passed
TMPDIR=/tmp uv run python scripts/check_containers.py: passed
TMPDIR=/tmp uv run python scripts/check_workflows.py: passed
git diff --check: passed
```

## Privacy and authority boundary

- Actual source review remains root-owned and outside Git.
- No raw PDFs, extracted text, OCR output, screenshots, embeddings, source
  paths, document IDs, names, amounts, credentials, or provider payloads were
  added to the repository.
- One earlier diagnostic emitted an insufficiently redacted private line in
  ephemeral tool output. It was not written to a file, database artifact, Git,
  or application log; subsequent diagnostics were restricted to ordinals,
  counts, booleans, and hashes.
- The aggregate counts are progress metadata only. They do not promote any
  result beyond `UNKNOWN` and do not make claim or payment decisions.

## Next Steps

- Complete the five remaining password-required certificate candidates and
  the legacy-font/raw-visual boundaries outside the repository.
- Reconcile the 18-policy / 314-rider runtime ledger with the earlier external
  review aggregate without inferring enrollment from terms-only material.
- Verify current and renewal status before revisiting any `UNKNOWN` result.
- Keep the Task 5 actual-acceptance item open until those boundaries are met.
