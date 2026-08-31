# Release workflow parser fix

## Overview

The semantic-tag release workflow could not be loaded by GitHub because the `publish-release` job
used the step-only `runner` context in a job-level environment. This change moves each temporary
path to the exact steps that consume it and extends repository policy checks so the invalid form
cannot silently return.

## Context

`scripts/check_workflows.py` originally passed while `actionlint` reported two expression errors for
the job-level `RELEASE_EVIDENCE` and `RELEASE_NOTES` values. Existing public releases and images were
not affected, but a future tag could not start the workflow until the YAML expression scope was
corrected.

The repair does not create a Git tag, publish an image, edit a GitHub Release, deploy a service, or
read private insurance material.

## Changes made

### Release workflow

- Removed the job-level `env` mapping from `.github/workflows/release.yml`.
- Added `RELEASE_EVIDENCE` only to the digest-recording, note-rendering, and cleanup steps.
- Added `RELEASE_NOTES` only to the note-rendering, release-publication, and cleanup steps.
- Kept exact no-follow cleanup, automatic GitHub-token use, and existing job permissions unchanged.

```yaml
- name: Render changelog-derived release notes
  env:
    RELEASE_EVIDENCE: ${{ runner.temp }}/familycare-release-image-evidence.json
    RELEASE_NOTES: ${{ runner.temp }}/familycare-release-notes.md
```

### Regression policy and tests

- Added `_job_level_env_blocks` to `scripts/check_workflows.py` to inspect only four-space
  job-level environment mappings.
- Made release validation reject a `runner.*` expression in those mappings.
- Added a synthetic mutation test in `scripts/tests/test_release_workflow.py` that recreates the
  invalid job-level form and requires the stable policy error.

```python
if any(re.search(r"\$\{\{\s*runner\.", block) for block in _job_level_env_blocks(content)):
    errors.append(f"{relative}: job-level env cannot use the runner context")
```

### Documentation

Updated the changelog, README, architecture, guide, foundation/release plans, and related design
records to replace the parser-blocker warning with the fixed boundary. Every document records that
no new tag was created and that the next deliberate tag run still needs its own release evidence.

## Verification results

The new focused test failed before implementation because the repository validator returned no
job-level context error. After the repair:

```text
pytest scripts/tests/test_release_workflow.py scripts/tests/test_workflows.py
37 passed

actionlint -oneline .github/workflows/*.yml
exit 0, no findings

python scripts/check_workflows.py
workflow policy passed
```

The complete local repository gate also passed:

- Documentation contract: 48 required files.
- Repository safety: 670 paths before this workthrough was added.
- Web: formatting, lint, typecheck, 22 test files and 144 tests, production PWA build.
- Python: 485 files formatted, Ruff clean, mypy clean across 208 source files.
- Python tests: 1,612 passed, 181 deselected, and 3 subtests passed.
- Generated contracts: no drift.
- Container definitions: 3 images and 4 Compose services.
- Workflow policy, `actionlint`, and `git diff --check`: passed.

## Remaining boundary

No semantic-version tag was pushed, so this change does not claim a new release workflow run or
artifact publication. The next explicitly approved release must retain its normal tag-run, digest,
and GitHub Release verification.
