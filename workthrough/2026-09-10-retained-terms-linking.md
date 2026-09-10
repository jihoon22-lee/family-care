# Retained terms classification and physical source flow

## Purpose and boundaries

For #62/#63/#69, metadata v10 recognizes covered-person conditional provisions
and product captions with bounded variant suffixes, while replaying physical
reading order independently of retained extractor ordinals. API classification,
Clause source assessment and semantic source layout use the stored revision.
Original source words, node IDs, spans, geometry and ordinal values stay intact.

The first overlap or unsupported physical region quarantines the remaining
metadata prefix. Reverse body flow cannot skip an unlocated same-layer passage,
including one emitted outside the two original endpoint ordinals. Complete word
lineage remains mandatory. Repeated headers or an operative sentence cannot
terminate an earlier explanation/example document boundary.

Schema `0072_metadata_physical_flow` admits the new validator and refuses a
history-losing downgrade. Existing v9 and earlier interpretation is preserved;
new classifications do not establish enrollment, applicability or payout.

## Verification

Source: base `6b96529743c35c5044b6ae08663b1099f20cf4bd` plus this task's uncommitted
API/Worker, generated contract, migration and synthetic test changes. Checks ran
on 2026-09-10 UTC with Python 3.14 and a task-specific external virtualenv.
PostgreSQL checks use the dedicated synthetic `familycare_retained_terms_test` DB.

- Tests first exposed missing conditional grammar, reversed physical body flow,
  unresolved metadata prefixes, caption variants, and the remaining semantic
  reader ordinal gate. The out-of-interval unlocated passage regression also
  failed before its Worker/API guard was added.
- Related conditional/flow/metadata tests: 131 passed before caption-variant and
  semantic-reader follow-ups. Later caption/prefix tests: 48 passed; bounded
  product suffix tests: 10 passed; body-flow safety tests: 6 passed.
- Initial v10 append and historical v9 PostgreSQL cases: 4 passed. The new durable
  physical-prefix source tests first failed in semantic source reading, then
  passed both cases after the stored-revision geometry fix.
- The first integration attempt used an unavailable example DB identity; the
  replacement task DB initially needed migration. These were setup failures,
  followed by the expected missing-v10 behavior failure after schema setup.
- Required completion: `corepack pnpm web:check` passed (249 tests, build),
  `ruff format --check .` passed (920 files), `ruff check .` passed, `mypy
  apps/api/src workers/analyzer/src scripts` passed (362 files), and `pytest
  apps/api/tests workers/analyzer/tests scripts/tests -q` passed (3,924 tests,
  867 integration tests deselected, 3 subtests; 43.54s). Initial style/type
  findings were corrected before these completed runs.
- `check_contracts.py`, `check_containers.py`, `check_workflows.py` and
  `git diff --check` passed. Container verification here is static; CI image
  builds and the final CI result remain pending.
- The final related PostgreSQL run passed 61 cases and failed one older compiler
  migration fixture because it created future v10 metadata before downgrading.
  That historical fixture now explicitly produces v9. Its rerun together with
  conditional-calculation and v10 append/publication/downgrade cases passed all
  five tests (9.17s). The preceding 61 successes remain valid for unchanged inputs.
  A wider historical-fixture audit reproduced the same issue in the activity
  migration test; pinning its metadata to v9 restored its own downgrade/history
  check (1 passed, 3.62s). No production constraint was relaxed.

## Protected diagnostics

Read-only counterfactuals use approved retained sources and a separately frozen
reader for the existing schema 0069 plan. They validate the approved full source
snapshot and generation lineage, load only the new pure metadata classifier,
and leave original and runtime databases unchanged. The one-source/150-page
checks recovered a supported body but still did not produce a terms component:
its inherited example context remains in force. These checks are not publication,
whole-corpus, edition-applicability or payout acceptance.

Two separately reserved development diagnostics sent only minimized short syntax
fragments using the previously approved Worker credential, no automatic retry,
`store=False`, and fixed output limits. The conditional fragment used 419 input /
297 output tokens; the product-caption fragment used 326 / 206. Their suggestions
were followed by synthetic regressions and independent source checks; model output
has no classification/publication authority. Private inputs and journals remain
outside the repository. No actual text, identifiers or credentials are recorded
here. The full approved-terms counterfactual completed in 115.292s with unchanged
retained source: terms classification improved for part of the corpus, but no
complete insurer/product identity was established. Actual metadata publication,
applicability and protected semantic acceptance remain pending.

PR #95's separate source-calculation work passed all seven CI checks and merged
as `ee732db952ec984d35989ec6ac7ac18afa6301b7`. This task does not create a release,
tag, deployment or protected-data publication. The final version remains v0.5.0.


## Integration and isolated schema 0072 acceptance

[PR #96](https://github.com/jihoon22-lee/family-care/pull/96), source
`d537c4065a94f903f3abc9ed81707d7a75d68930`, passed all seven required checks in
[CI 34434554492](https://github.com/jihoon22-lee/family-care/actions/runs/34434554492)
and merged as `e3de9f4ffac6779b838f3397b4907b5f3f059227`.
The matching [main CI](https://github.com/jihoon22-lee/family-care/actions/runs/34436945840)
also passed all seven checks.

On 2026-09-10 UTC, the same clean source created a separate development clone of
the already approved acceptance database. Schema 0069→0072 preserved every original
row and column; a fresh comparison also proved its parent clone unchanged (182.272s).
New API/Worker readiness rejected 0069 and accepted 0072. The initial disk preflight
used an unsupported BusyBox option and failed before creating or changing a database;
the corrected preflight and preparation then passed.
The previous clean source `2370761` API/Worker also rejected the new 0072 clone in
a separate read-only check. This covers both directions for those actual binaries.

Authenticated AI-off requests read the frozen existing events, original evidence,
claim history, and matching stored results. Every original row remained unchanged
afterward, with new result/session rows allowed. External HTTP and provider job
creation stayed zero. The new session was logged out.

A bounded metadata/edition phase used the unchanged approved full input identities,
appended v10 proposals and independently checked them in the API (300.498s).
New publications were deferred by existing component overlap; no new terms identity
was established. Original source, correction, claim and earlier publication rows
were preserved, while current derived component pointers were excluded from that
immutable-row comparison. The result remains PARTIAL. No tag, live schema change,
runtime switch, device acceptance or additional provider call occurred.
