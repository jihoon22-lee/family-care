# FamilyCare v0.2.0 Release Metadata

## Overview

FamilyCare Web, API, and Analyzer Worker product metadata now consistently reports `0.2.0`. The release changelog summarizes the public feature and reliability changes while preserving the repository's synthetic-only release boundary.

## Context

- The pre-change `release_audit.py --version 0.2.0` result contained the stable `version-mismatch` finding because the five authoritative product version fields still reported `0.1.0`.
- This change is limited to release identity metadata, mechanically derived lock data, health-test expectations, and public release notes.
- No private document, extracted content, personal identifier, credential, or runtime data was added to the repository.

## Changes Made

### Product identity

- Updated the API project and package versions in `apps/api/pyproject.toml` and `apps/api/src/familycare_api/__init__.py`.
- Updated the Worker project and package versions in `workers/analyzer/pyproject.toml` and `workers/analyzer/src/familycare_worker/__init__.py`.
- Updated the Web package version in `apps/web/package.json`.
- Refreshed only the two editable FamilyCare package entries in `uv.lock` with `uv lock`.

### Version assertions and release notes

- Aligned the synthetic API and Worker health-response expectations in `apps/api/tests/test_health.py` and `workers/analyzer/tests/test_health.py`.
- Added the dated `0.2.0` section to `CHANGELOG.md`, covering document inventory, policy structuring, member scoping, bounded PDF handling, and the private-data boundary.

### Canonical OpenAPI follow-up

- The full Python gate exposed deterministic OpenAPI drift after the API package version changed; all other tests in that run passed.
- Regenerated only `packages/contracts/openapi/familycare.v1.json` with the repository-owned `TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi` command.
- Reviewed the generated diff: only `info.version` and the health response version default changed from `0.1.0` to `0.2.0`; paths, schemas, examples, and error codes were unchanged.

## Code Example

All runtime health identities are derived from their package version rather than duplicating a release-only constant:

```python
# apps/api/src/familycare_api/__init__.py
__version__ = "0.2.0"

# workers/analyzer/src/familycare_worker/__init__.py
__version__ = "0.2.0"
```

## Verification Results

### Focused health tests

```text
TMPDIR=/tmp uv run pytest apps/api/tests/test_health.py workers/analyzer/tests/test_health.py -q
34 passed in 53.90s
```

### Release identity

```text
TMPDIR=/tmp uv run pytest scripts/tests/test_release_audit.py -q
5 passed in 0.70s

TMPDIR=/tmp uv run python scripts/release_audit.py --version 0.2.0 --commit-sha <full-current-sha>
release-identity-ok
```

### Formatting and diff safety

```text
TMPDIR=/tmp uv run ruff format --check <changed-python-files>
4 files already formatted

corepack pnpm@11.22.0 --dir apps/web exec prettier --check package.json ../../CHANGELOG.md
All matched files use Prettier code style!

git diff --check
passed
```

### Canonical OpenAPI follow-up

```text
TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_contracts.py::test_committed_openapi_has_no_contract_drift_and_policy_error_codes_are_fixed -q
1 passed in 6.22s

TMPDIR=/tmp uv run python scripts/check_contracts.py
contract checks passed
```

## Next Steps

- The root release flow still owns branch push, pull request checks, merge, tag creation, image publication, and post-release cleanup.
