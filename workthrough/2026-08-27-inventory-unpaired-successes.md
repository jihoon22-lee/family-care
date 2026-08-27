# Include processed batch items without reviewed components

## Overview

The member insurance-document inventory now keeps succeeded, processed private batch items visible even when no reviewed `insurance_document_components` row exists yet. The repository derives a path-free, full-document suggested component in memory so users can see material awaiting review without creating a component row or treating it as enrolled coverage.

## Context

- The inventory read model previously queried only component rows for `unpaired_components`.
- A successful batch item with a pinned `processed_document_version_id` but no active component was therefore absent from the member inventory.
- The fallback must retain the immutable intake `document_kind`, cover pages `1..page_count`, remain `SUGGESTED` and `READY`, and preserve duplicate warnings.
- An item already represented by an active component, or whose version is the source of an active member policy, must not be emitted a second time.

## Changes Made

### 1. Added an in-memory synthetic component projection

File: `apps/api/src/familycare_api/insurance_documents/repository.py`

- Added `_synthetic_component`, which assigns `id=None`, the existing batch item ID, the pinned immutable version, intake `document_kind`, and the complete version page range.
- The synthetic state is always `review_state="SUGGESTED"` and `processing_state="READY"`; duplicate state is derived from succeeded processed identities scoped to the household/member.
- Added a metadata-only query for succeeded items joined to their processed version. It excludes items with an active component and items whose version already backs an active policy for the same member.
- No insert or update is performed by the inventory read.

### 2. Added synthetic API and PostgreSQL coverage

Files:

- `apps/api/tests/test_insurance_document_inventory_repository.py`
- `apps/api/tests/test_insurance_document_inventory_api.py`
- `apps/api/tests/test_insurance_document_inventory_integration.py`

The tests cover API serialization of a component with no component ID, full page ranges, suggested/ready states, same-member/cross-member duplicate state, active-component exclusion, active-policy-source exclusion, and verification that no component row is persisted for synthetic items. Fixtures contain only synthetic IDs and labels.

## Key code example

```python
return InventoryComponent(
    id=None,
    document_batch_item_id=cast(UUID, row["document_batch_item_id"]),
    document_version_id=cast(UUID, row["document_version_id"]),
    content_sha256=cast(str, row["content_sha256"]),
    role=cast(DocumentRole, row["document_kind"]),
    page_start=1,
    page_end=int(row["page_count"]),
    review_state="SUGGESTED",
    processing_state="READY",
    duplicate_state=_duplicate_state(row),
)
```

## Verification Results

### Test-first evidence

```text
Initial RED: ImportError because _synthetic_component did not exist.
Focused GREEN: 16 passed in 7.01s.
Root focused unit/API review: 5 passed in 5.68s.
Root PostgreSQL RED: the new same-version active-policy copy correctly changed
the existing source from UNIQUE to SAME_MEMBER_DUPLICATE, exposing a stale test
expectation.
Root PostgreSQL GREEN after correcting the expectation: 1 passed in 4.94s.
```

### Focused checks

```text
TMPDIR=/tmp uv run ruff format --check <focused files>: passed
TMPDIR=/tmp uv run ruff check <focused files>: passed
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts: passed
git diff --check: passed
```

The root ran the integration case against a temporary isolated PostgreSQL 18.6
container, applied migrations through `0017_insurance_inventory`, and removed
the temporary container after the test passed.

### Full repository verification

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

The Web suite ran with one worker because concurrent WSL filesystem load made
fork startup slow. It completed without worker timeout or assertion failure.

## Privacy and authority boundary

- No actual/private documents, extracted text, OCR output, paths, source keys, archive keys, credentials, or runtime data were accessed or recorded.
- The API exposes only fields already present in the inventory response contract; the synthetic component ID is `null` and the existing internal batch item ID is retained for review navigation.
- Synthetic state cannot confirm enrollment, pair a document set, publish a policy, or create a database row.

## Next Steps

- Rebuild and restart only the FamilyCare API, then compare the deployed
  member inventories with the root-owned private runtime review.
