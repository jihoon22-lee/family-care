# Workthrough: Documentation and dependency maintenance

Date: 2026-09-01

## Overview

This maintenance pass brought the public documentation up to the `v0.3.2` product baseline and
resolved the two open Dependabot pull requests without rewriting their shared histories. The
dependency changes were reproduced on current `main`, passed the complete local gate and all seven
required GitHub checks, and were merged through replacement PRs. The stale bot PRs, remote branches,
local branches, and task worktrees were then removed.

The documentation now distinguishes four different facts that had drifted together:

- released product behavior through `v0.3.2`;
- unreleased release-note and development-dependency maintenance on `main`;
- protected package/database/browser acceptance performed outside Git; and
- actual document formats, devices, disaster recovery, and provider paths that remain unverified.

## Context

The repository was already beyond the status described by several living documents. Public tags
and GitHub Releases existed through `v0.3.2`, the private-knowledge catalog and advisory result stream
had merged, and historical release bodies had been normalized. Design headers and implementation
plans still described review, PR, release, or protected acceptance as pending.

Two Dependabot PRs also remained open:

- PR #46 updated `@testing-library/react` and `@vitejs/plugin-react` but was behind `main`.
- PR #41 updated Ruff in `pyproject.toml` but omitted the corresponding `uv.lock` change, causing its
  Repository safety check to fail.

GitHub's automatic branch update created a non-Conventional merge subject on PR #46. A forced rebase
would have rewritten a shared branch, so both updates were moved to clean replacement branches based
on current `main`.

## Changes Made

### Dependency pull requests

- PR #50 reproduced the PR #46 frontend development-tool updates on current `main`.
- PR #50 passed Repository safety, Web, Python, PostgreSQL integration, and all three container jobs,
  then merged as `d41ca0a6705a71c75802ed0f195aa9ea1f152810`.
- Original PR #46 was closed as superseded and its Dependabot branch was deleted.
- PR #51 reproduced the PR #41 Ruff update and synchronized `uv.lock` in the same atomic commit.
- PR #51 passed the same seven required jobs, then merged as
  `40484b306a361d747c81c4b8e479424f1d2262f3`.
- Original PR #41 was closed as superseded and its Dependabot branch was deleted.
- Both replacement remote/local branches and their dedicated worktrees were deleted after merge.
- Post-merge `main` CI for `40484b3` passed all seven jobs.

### Documentation refresh

- Updated README, security policy, contributor workflow, architecture, guide, glossary, contract
  inventory, roadmap, and changelog for the current release and maintenance state.
- Added current completion records to historical plans while preserving their original RED/GREEN
  procedures and explicit unresolved boundaries.
- Updated design and plan status headers for merged PRs #1 through #49 and releases `v0.1.0` through
  `v0.3.2`.
- Recorded the current OpenAPI review snapshot: version `0.3.2`, 69 paths, 81 operations, and 150
  component schemas.
- Verified all five public GitHub Release bodies have the common `Changes`, `Release evidence`, and
  `Privacy and deployment boundary` headings, real newlines, and three distinct image digests.

### Known release-workflow regression

The current `release.yml` uses `runner.temp` in job-level `env`. GitHub rejects that context at that
location, so ordinary pushes create an immediate workflow-file failure with no jobs. `actionlint`
independently reports the same two expression errors. This documentation-only task records the issue
but does not modify workflow code. No new tag should be created until a separate fix PR passes parser,
policy, PR/main CI, and an actual tag workflow.

## Code Examples

The updated status language separates delivery from unverified acceptance:

```markdown
- 현재 공개 버전: `v0.3.2`; 다음 버전 미지정
- Release workflow parser correction: pending before next tag
- Windows/mobile and full disaster recovery: unverified
```

The Python development dependency and lock now agree:

```toml
ruff==0.16.5
```

The contributor workflow now ends with recoverable cleanup only after merge confirmation:

```text
required CI passes -> merge confirmed -> delete completed branches and dedicated worktree
```

## Verification Results

### Dependency replacement PRs

- PR #50: seven of seven required GitHub checks passed.
- PR #51: seven of seven required GitHub checks passed.
- Post-merge `main` run `33449049384`: seven of seven jobs passed.
- `corepack pnpm@11.22.0 install --frozen-lockfile`: passed with the synchronized pnpm lockfile.
- `TMPDIR=/tmp uv sync --frozen --all-packages --group dev`: passed with Ruff 0.16.5.

### Documentation branch full local gate

- `python3 scripts/check_documentation.py`: passed, 48 required documents.
- `python3 scripts/check_repository_safety.py`: passed, 669 inspected paths.
- `corepack pnpm@11.22.0 web:check`: passed; 22 test files, 144 tests, and production PWA build.
- `TMPDIR=/tmp uv run ruff format --check .`: passed; 484 files already formatted.
- `TMPDIR=/tmp uv run ruff check .`: passed.
- `TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts`: passed, 208 source files.
- `TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`: passed;
  1,611 tests, 181 deselected integration tests, and 3 subtests.
- `TMPDIR=/tmp uv run python scripts/check_contracts.py`: passed.
- `TMPDIR=/tmp uv run python scripts/check_containers.py`: passed, three images and four Compose
  services.
- `TMPDIR=/tmp uv run python scripts/check_workflows.py`: passed the repository workflow policy.
- `git diff --check`: passed.
- `actionlint -oneline .github/workflows/release.yml .github/workflows/ci.yml`: failed on the two
  pre-existing job-level `runner.temp` expressions described above; this is the separately recorded
  next-tag blocker.

The local default pytest command intentionally did not run the 181 PostgreSQL integration tests.
Both dependency PRs ran and passed the PostgreSQL integration job in GitHub; the documentation PR is
expected to run that required job again.

## Privacy and security boundary

- No external actual insurance or medical document, extracted text, OCR image, personal identifier,
  private path, credential, package artifact, database, or protected acceptance artifact was opened
  or added. Existing sanitized repository records were reviewed in place.
- Documentation uses only public repository metadata, aggregate contract counts derived from the
  committed OpenAPI file, and previously recorded sanitized acceptance boundaries.
- No tag, image, runtime container, database, archive, deployment, or actual-data source was changed.

## Next Steps

1. Open the documentation PR, wait for all seven required checks, merge it, and remove its remote and
   local branch plus dedicated worktree.
2. Fix the release workflow parser regression in a separate non-document PR before creating another
   semantic-version tag.
3. Keep Windows/mobile, remaining document-format/OCR, actual provider, and full disaster-recovery
   checks explicitly unverified until they are run in separately approved scopes.
