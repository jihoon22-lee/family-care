# Changelog-derived Release Notes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every FamilyCare GitHub Release derive its change list from `CHANGELOG.md`, publish future notes only after verified GHCR digests, and repair v0.1.0 through v0.3.2 with one format.

**Architecture:** A focused Python renderer extracts one Keep a Changelog version section and combines it with validated image evidence. The registry verifier can emit a bounded evidence JSON, and a final least-privilege workflow job passes a mode-0600 Markdown file to `gh release create|edit --notes-file`. Historical releases use the same merged tool after live digest verification.

**Tech Stack:** Python 3.14 standard library, pytest, GitHub Actions, GitHub CLI, GHCR OCI manifest API, existing repository policy scripts.

**Spec:** `docs/superpowers/specs/2026-08-31-changelog-release-notes-design.md`

**Status:** Implementation and historical repair complete in PR #49, merged as
`c418579fd6ffbfd1924d8998a4cb171a739be646`. The five public Release bodies were read back with the
common headings, real newlines, and three distinct image digests. A post-completion GitHub parser
regression in the job-level `runner.temp` context remains a blocker for the next tag and requires a
separate fix PR.

## Global Constraints

- Public release notes contain only repository CHANGELOG text and verified public release metadata.
- Actual insurance documents, extracted text, identifiers, credentials, private paths, and provider payloads never enter GitHub Release notes, tests, or logs.
- `packages: write` remains exclusive to the image publish job.
- `contents: write` is exclusive to the final GitHub Release job.
- All GitHub Release bodies use a real mode-0600 Markdown file and `--notes-file`; inline escaped bodies are forbidden.
- No new tag, image rebuild, live FamilyCare deployment, database mutation, or unrelated project change is part of this plan.

---

### Task 1: Extract and render changelog-derived notes

**Files:**
- Create: `scripts/release_notes.py`
- Create: `scripts/tests/test_release_notes.py`
- Modify: `CHANGELOG.md:93-210`

**Interfaces:**
- Produces: `extract_changelog_section(changelog: str, version: str) -> str`
- Produces: `render_release_notes(section: str, evidence: ReleaseNotesEvidence) -> str`
- Produces CLI: `python scripts/release_notes.py --version VERSION --commit-sha SHA --repository OWNER/REPO --workflow-url URL --image-evidence FILE --output FILE`

- [x] **Step 1: Write synthetic failing tests** for exact section extraction, missing/duplicate versions, empty categories, literal `\n`, exact three-image rendering, and a mode-0600 CLI output.
- [x] **Step 2: Run RED:** `TMPDIR=/tmp uv run pytest scripts/tests/test_release_notes.py -q`; expect import failure because `scripts.release_notes` does not exist.
- [x] **Step 3: Implement the parser and renderer** with semantic version, ISO date, allowed category, evidence schema, digest, commit, repository, and workflow URL validation.
- [x] **Step 4: Run GREEN:** `TMPDIR=/tmp uv run pytest scripts/tests/test_release_notes.py -q`; expect all release-note tests to pass.
- [x] **Step 5: Add a current-file test** that renders v0.1.0 through v0.3.2 and rejects the two empty v0.1.0 categories.
- [x] **Step 6: Run RED:** expect the current-file test to fail on v0.1.0 `Deprecated` or `Removed`.
- [x] **Step 7: Normalize v0.1.0 CHANGELOG** to concise user-impact Added/Changed/Fixed/Security entries while retaining detailed release evidence in `docs/release/v0.1.0-verification.md`.
- [x] **Step 8: Run GREEN and static checks:** `TMPDIR=/tmp uv run pytest scripts/tests/test_release_notes.py -q`, `TMPDIR=/tmp uv run ruff check scripts/release_notes.py scripts/tests/test_release_notes.py`, and `TMPDIR=/tmp uv run mypy scripts/release_notes.py`.

### Task 2: Emit verified image evidence

**Files:**
- Modify: `scripts/release_audit.py`
- Modify: `scripts/verify_release_images.py`
- Modify: `scripts/tests/test_release_audit.py`
- Modify: `scripts/tests/test_verify_release_images.py`

**Interfaces:**
- Produces: `ReleaseImageDigest(component: str, digest: str)`
- Produces: `inspect_image_digests(...) -> tuple[tuple[ReleaseImageDigest, ...], tuple[ReleaseFinding, ...]]`
- Preserves: `verify_image_digests(...) -> tuple[ReleaseFinding, ...]`
- Extends CLI: `--evidence-output ABSOLUTE_NEW_FILE`

- [x] **Step 1: Write failing tests** asserting ordered web/api/worker digest results, no evidence on findings, exact JSON schema, and mode `0600` output.
- [x] **Step 2: Run RED:** `TMPDIR=/tmp uv run pytest scripts/tests/test_release_audit.py scripts/tests/test_verify_release_images.py -q`; expect missing inspection interface or CLI option failure.
- [x] **Step 3: Refactor the existing six manifest checks** into `inspect_image_digests` and keep `verify_image_digests` as a compatibility wrapper.
- [x] **Step 4: Add bounded evidence output** only after zero findings; reject existing, relative, repository-contained, or symlink output paths.
- [x] **Step 5: Run GREEN and static checks** for the two focused test files, Ruff, and mypy.

### Task 3: Publish notes after verified images

**Files:**
- Modify: `.github/workflows/release.yml`
- Modify: `scripts/check_workflows.py`
- Create: `scripts/cleanup_release_files.py`
- Modify: `scripts/tests/test_release_workflow.py`
- Modify: `scripts/tests/test_workflows.py`
- Create: `scripts/tests/test_cleanup_release_files.py`

**Interfaces:**
- Produces workflow job: `publish-release`, `needs: verify-publication`
- Requires exact commands: evidence generation, note rendering, `gh release create|edit --notes-file`, and `if: always()` exact-file cleanup

- [x] **Step 1: Write failing workflow behavior tests** for dependency, scoped permissions, renderer use, no inline notes, and idempotent create/edit.
- [x] **Step 2: Run RED:** `TMPDIR=/tmp uv run pytest scripts/tests/test_release_workflow.py scripts/tests/test_workflows.py -q`; expect missing job and policy fragments.
- [x] **Step 3: Extend `validate_release`** so `packages: write` remains publish-only, `contents: write` is release-only, and packages read is limited to verification and release jobs.
- [x] **Step 4: Add `publish-release`** with checkout, uv, digest evidence, rendering, create/edit via `--notes-file`, and exact temporary-file cleanup.
- [x] **Step 5: Run GREEN:** focused workflow tests plus `TMPDIR=/tmp uv run python scripts/check_workflows.py`.

### Task 4: Document and verify the release contract

**Files:**
- Modify: `docs/design/project-foundation.md:291-310`
- Modify: `docs/guide.md:497-520`
- Modify: `docs/plan/016-v0.1-release.md`
- Modify: `CHANGELOG.md:5-7`

**Interfaces:**
- Documents CHANGELOG as the canonical change source, evidence appendices, least-privilege job, and historical repair.

- [x] **Step 1: Update the design, guide, and release plan** without claiming the PR, merge, or historical Release edits have happened before they do.
- [x] **Step 2: Add an Unreleased Fixed entry** describing the release-note source and literal-newline prevention.
- [x] **Step 3: Run documentation, safety, workflow, and diff checks.**

### Task 5: Deliver and repair public releases

**Files:**
- No additional tracked files unless CI review requires a focused correction.
- Temporary runtime files: one mode-0700 directory outside the repository, containing mode-0600 evidence and note files, deleted after verification.

**Interfaces:**
- GitHub Release mutation: `gh release edit TAG --title "FamilyCare TAG" --notes-file FILE`

- [x] **Step 1: Run the full required local verification** in the repository order, including Web and Python checks serially.
- [x] **Step 2: Review the complete diff, run repository safety, and commit with Conventional Commits.**
- [x] **Step 3: Push `fix/release-notes-changelog`, open a PR, wait for all required checks, merge, and verify post-merge main.**
- [x] **Step 4: For each v0.1.0 through v0.3.2**, resolve the immutable tag commit and successful workflow URL, query GHCR version/SHA digests, render notes from merged `main`, and update the existing Release via `--notes-file`.
- [x] **Step 5: Read every public body back through the GitHub API** and assert it contains its exact CHANGELOG section, three distinct image digests, real newlines, one privacy/deployment boundary, and no literal `\n`.
- [x] **Step 6: Delete only the task worktree, merged local/remote branch, and exact temporary directory after final verification; preserve tags, images, runtime containers, DB, and other projects.**
