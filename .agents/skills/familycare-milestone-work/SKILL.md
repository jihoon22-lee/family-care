---
name: familycare-milestone-work
description: Start or continue FamilyCare milestone implementation by mapping the current issue, requirements, dependencies, and PR bundle to local work. Use for milestone-driven development, not standalone reviews or unrelated repositories.
---

# FamilyCare milestone work

Use the repository root as the base for commands. Read [AGENTS.md](../../../AGENTS.md)
and the [roadmap](../../../docs/plan/000-project-roadmap.md), then only the current
main issue, requirement sections, WP, and affected design documents.

## Resolve the next outcome

- Read current GitHub milestone/issue state through the available GitHub tools.
  If milestone collection access is unsupported, use authenticated `gh api` GET
  with pagination. Do not infer current progress from an old local plan.
- Identify the R/S requirements, dependency evidence, current PR bundle, owned
  files/contracts, and observable acceptance conditions. Reuse the existing plan;
  do not create a second backlog or one PR per checkbox.
- If GitHub is unavailable, distinguish the locally verified work from unknown
  remote status. Continue independent local work; do not claim remote completion.
- Treat v0.4 decision rules as the existing baseline. v0.5 changes their meaning
  in WP01 with failing tests, generated contracts, and a working local path.
  A documentation edit alone does not complete that transition.

## Execute within the request

Mark the current local task in progress, implement the agreed outcome with the
repository's test-first and verification rules, and keep design/contract changes
with their implementation. Read nested instructions before editing that area.
Use the root's bounded delegation rules only for independent work.

Preserve the distinction between plan registration, implementation, test evidence,
protected acceptance, and release. Record requirement → issue → actual PR (when
created) → source/data/schema version → environment/check → result/limitations.
Use one existing workthrough per coherent task; summarize evidence instead of
copying full logs or issue bodies.

GitHub comments, state changes, publishing, merging, real-data access, external
transmission, and runtime mutation require authorization covering that action.
The skill does not authorize them. Do not close a parent roadmap/specification
because one implementation PR passed; follow its final acceptance conditions.
