# Member-Scoped Review Integration Fixture

## Overview

The member-scoped policy review integration test now uses internally consistent synthetic document and Evidence hashes. This preserves the production integrity check while allowing the test to exercise cross-member filtering as intended.

## Context

- PR CI rejected the second synthetic member's candidate batch with `INVALID_POLICY_CANDIDATE_BATCH`.
- The production publisher requires each Evidence content hash to match its source document version.
- The second synthetic document version used one repeated hexadecimal character while its Evidence row used a different one, so the fixture failed before reaching the member-filter assertion.

## Changes Made

- Aligned the second synthetic Evidence hash with its synthetic document-version hash in `apps/api/tests/test_private_policy_confirmation_integration.py`.
- Kept the publisher's hash-integrity validation unchanged.
- No actual document, extracted content, identifier, or runtime value is present in the fixture.

## Code Example

```python
# Synthetic document and Evidence rows use the same 64-character fixture hash.
content_sha256 = "d" * 64
```

## Verification Results

```text
TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_private_policy_confirmation_integration.py::test_review_items_filter_to_selected_family_member_without_cross_member_leakage -q
1 passed

TMPDIR=/tmp uv run pytest -m integration -q
111 passed, 1255 deselected

TMPDIR=/tmp uv run python scripts/check_contracts.py
contract checks passed
```

The same disposable PostgreSQL 18.6 instance also completed the full Alembic `head -> base -> head` round trip and reported `0017_insurance_inventory (head)`.

## Next Steps

- Push the fix to PR #30 and require all seven CI checks to pass before merge.
