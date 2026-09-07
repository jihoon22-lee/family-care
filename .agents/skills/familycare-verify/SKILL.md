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
- During implementation run the test that exposes the missing behavior, then
  related tests. At PR completion run the required suite and the additional
  integration checks selected by the changed boundaries.
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
prior evidence. Rerun affected checks after changes, failures, or unresolved risks;
do not repeat an unchanged successful suite just to answer a follow-up question.

Distinguish default pytest (integration excluded), PostgreSQL integration, Web
unit/build, browser mock, real backend E2E, actual devices, external provider, and
protected data acceptance. Static container checks are not image builds.
Record skipped, failed, interrupted, unavailable, and passed checks separately.

Use wholly synthetic fixtures and dedicated test resources. Never inspect private
data, print runtime credentials, invoke an external AI, or reuse a shared database
as a shortcut to verification. Preserve required approvals for protected actions.
Keep one concise workthrough with actual evidence and remaining limitations.
