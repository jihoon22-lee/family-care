---
name: familycare-verify
description: Select and run FamilyCare checks for changed code, documentation, or configuration and record source-bound evidence. Use when validating a FamilyCare change or preparing its completion report, not for unrelated projects.
---

# FamilyCare verification

Run commands from the repository root. Read the changed paths and
[verification strategy](../../../docs/design/test-strategy.md#verification-by-change).
That document owns the command list; do not maintain a duplicate list here.

## Select the checks

- Classify the diff as read-only review, documentation/instruction changes,
  executable configuration, or product behavior. Use the union for mixed changes.
- Per commit, check changed-file syntax/format and alignment with the existing
  plan. Do not make full tests, integration, builds, or protected acceptance a
  commit gate. Write necessary tests alongside implementation; repeated RED/GREEN
  execution is not mandatory.
- Finish the PR's planned implementation, tests, and documentation before one
  focused detailed verification pass. During development, run a minimal targeted
  check only to resolve a concrete blocker or implementation decision.
- Use matching final-PR CI evidence for required checks instead of duplicating
  expensive suites locally. Run locally what CI does not cover or what is needed
  to diagnose a specific failure. Preserve every required CI check.
- A policy document that changes required CI or verification behavior must be
  reviewed against the actual workflow. Do not remove a required check to make
  the change pass.
- For new/edited skills, validate frontmatter and naming with the available
  skill-creator validator; also inspect links, invocation scope, and commands.
  Structural validation does not establish behavioral quality or automatic routing.

## Execute and report

Keep frontend, Python, and Docker verification serial, including across agents.
Inspect resources and container ownership before Docker work. Use `TMPDIR=/tmp`
for Python on WSL when Windows temporary paths interfere.

Record exact commands, exit/result, time, source SHA plus relevant uncommitted
changes, and environment/configuration. Compare with current inputs before using
prior evidence. After a fix, rerun only checks whose results the fix can affect.
Broaden only for a concrete regression concern; a new commit SHA, documentation
edit, interruption, or follow-up question alone does not invalidate passed code
checks. Do not repeatedly push intermediate changes just to restart full CI.

Distinguish default pytest (integration excluded), PostgreSQL integration, Web
unit/build, browser mock, real backend E2E, actual devices, external provider, and
protected data acceptance. Static container checks are not image builds.
Record skipped, failed, interrupted, unavailable, and passed checks separately.

Use wholly synthetic fixtures and dedicated test resources. Never inspect private
data, print runtime credentials, invoke an external AI, or reuse a shared database
as a shortcut to verification. Preserve required approvals for protected actions.
Keep one concise workthrough with actual evidence and remaining limitations.
