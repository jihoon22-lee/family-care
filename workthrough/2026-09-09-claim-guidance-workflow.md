# Guidance to claim preparation

B06 [#68](https://github.com/jihoon22-lee/family-care/issues/68) builds on B05
[PR #80](https://github.com/jihoon22-lee/family-care/pull/80), source `0c70e51`.
B05 CI 34302926708 passed 7/7 and merged as `c58fae7`. This bundle is in progress.

Equivalent saved local runs reuse the original review job without another reservation or provider
call. The response keeps its original `decision_run_id` and adds the server-verified
`matched_decision_run_id` for the displayed run. A GET by run only finds existing work; GET/cancel
by job may bind a requested run. Stale or mismatched source context cannot establish that alias.
The shared resolver distinguishes current guidance used for a new claim from exact historical
references used for optional evidence reading.

## Verification so far

2026-09-09 UTC, source `0c70e51` plus review binding/repository/routes/models/tests and generated
OpenAPI/Web changes. Locked Python 3.14.7, task API/Worker/root `PYTHONPATH`, `TMPDIR=/tmp` and the
existing locked `.venv`; PostgreSQL uses only the explicit disposable synthetic test DB and
`FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true`. No external provider or protected document access.

- `pytest apps/api/tests/test_guidance_review_reuse_integration.py -m integration -q -x` first
  failed for missing read-only lookup (**1 failed**, 3.00s). Implemented reuse plus existing request
  integration: **16 passed** (33.27s). Equivalent new runs preserve one job and zero reservations;
  changed context at the same event version cannot bind a review.
- Shared current-versus-historical guidance resolver: **2 PG cases passed** (3.65s).
- `pytest apps/api/tests/test_guidance_review_api.py -q`: **6 passed** (0.55s), including no-store,
  scoped current lookup and alias-bound GET/cancel without enqueue.
- Related Ruff passed after formatting. Mypy first found optional model annotations; assigning
  the validated string corrected them, and all **10 review source files passed**. OpenAPI and
  generated Web types were regenerated for the new route and matched-run field.

Common evidence reading, review-result claim snapshots, UI integration and completion checks remain
in progress. These focused tests are not the full B06 acceptance or a protected runtime test.


## Common evidence and reviewed preparation

Common evidence disclosure resolves an exact reference in the saved selected candidate before
reading its source. Retained extraction and semantic source spans are originals; private summaries
are labeled summaries. An unavailable original does not remove successful evidence. Text is
bounded to 2048 characters and carries page/document/edition identity plus truncation state.
The authenticated POST remains no-store and takes no filesystem path.

`GuidanceClaimSelection.review_job_id` explicitly selects the persisted reviewed guidance. The shared
resolver rechecks current input and run equivalence; baseline creation continues to reject review-only
authority. Reviewed preparation preserves the selected run, original run, review job, source digest,
result digest and exact program candidate. Migration 0063 adds the review FK and validates that
snapshot against the retained result. Existing audit immutability remains, and downgrade refuses
review claim history. Reusing an existing claim does not overwrite its snapshot or create payment facts.

Evidence subtask `f8c3df1`, integrated as `a2c4567`, used the same synthetic Python/PG environment:
missing-module RED failed at collection (0.32s); initial API 5 passed (0.37s), final API **6 passed**
(0.41s, including authenticated/no-store boundary), Ruff and mypy **8 source files passed**.
The PG run had **12 passed / 1 failed** (27.76s) from misplaced test assertions; after correction,
the two affected cases passed (10.20s). All 13 cases were exercised, not a single final 13-case run.
Only adjacent SQL literal wrapping/import formatting followed the successful relevant PG checks.

Review claim source `a2c4567` plus 0063/claim builder/selection/tests/generated changes:
`pytest apps/api/tests/test_guidance_review_claim_integration.py -m integration -q -x`
first failed for the missing `review_job_id` parameter (8.85s). After implementation, **4 passed /
1 failed** (46.03s): creation assertions passed but the test expected CheckViolation instead of the
existing immutable-audit RaiseException. The corrected positive case plus candidate amount,
original-run and missing-review-FK tamper guards passed (12.41s). These cover current/new-run reuse,
source preservation, absent candidate/stale/other-household rejection and zero payment history.
An empty 0063→0062→0063 migration round trip passed.

Existing claim API, local snapshot, contract and review-provenance unit checks: **34 passed** (3.11s).
A preceding command referenced two incorrect test paths and collected no tests; it was corrected
to the existing claim API and `unit/claims` paths. Ruff and mypy **12 source files passed** after
format/import/one SQL-line fixes. Claim schema, OpenAPI and Web types were regenerated. Independent
read-only review found no further actionable claim-source/immutability issue; this was static review,
not additional dynamic verification. Root also reviewed the common evidence source-to-disclosure path.

The combined PostgreSQL run passed **38 tests** (130.14s) on 2026-09-09 UTC at `c5d7b6a`:
`pytest apps/api/tests/test_guidance_review_reuse_integration.py
apps/api/tests/test_guidance_evidence_integration.py
apps/api/tests/test_guidance_review_claim_integration.py
apps/api/tests/test_guidance_claim_concurrency_integration.py
apps/api/tests/test_guidance_claim_restore_integration.py
apps/api/tests/test_guidance_claim_reimport_integration.py -m integration -q --maxfail=3`.
This includes all new evidence/review claim cases and existing concurrency, restore and reimport
boundaries on synthetic PostgreSQL schema 0063.

The unpublished integration merge initially failed commit-title conventions. Only that message and
descendant commit identities were corrected; all file trees were checked equal, with no working-tree
change. Equivalent final source is `b1d3fa8`; `check_git_conventions.py --range c58fae7..HEAD`
passed all four subjects. No shared history was rewritten.

UI integration and full B06 completion checks are still pending.

## Integrated browser behavior and completion checks

UI source `8a68098`, integrated as `c466327`, adds exact family/event/source context, saved event URLs,
optional question PATCH and reanalysis, review lookup by matched run, read-only polling recovery,
common evidence pagination with individual retry, abort-on-logout and reviewed claim provenance.
Focused frontend coverage passed **120 tests** (15.41s), TypeScript and ESLint passed in its isolated
checkout. Additional browser scenarios and partial receipt-save recovery are still being completed.

At integrated source `c466327` on 2026-09-09 UTC, default Python completion passed **3489 tests and
3 subtests**, with **738 integration tests deselected** (152.77s). During review, a combined explicit
admission/day answer was found to lose its days in the repository. A PostgreSQL regression first
gave **3 failed / 1 passed** (1.60s). Preserving the explicit answer and rejecting contradictory
no-admission/positive-day answers atomically then passed the entire event-structuring module:
**8 passed** (3.08s). The related decision repository/API, event-structuring repository and review
API checks passed **48 tests** (1.44s). One earlier related-test command used a nonexistent directory
and collected no tests before the corrected explicit file list.

With that repository correction and test plus this record uncommitted, mypy passed **342 source
files**. Full Ruff initially reported one long pre-existing-in-this-bundle model-selection line;
formatting it corrected both reports, after which format **840 files** and lint passed. Contracts,
documentation **50**, repository safety **1053**, container definitions, workflow policy and diff
checks passed. Static container checks do not establish image builds; full final Web/browser and
PR CI evidence remain pending. All runs use the synthetic, provider-free environment above.

Final UI follow-up `96b6b83`, integrated as `c98d8c4`, preserves each successful receipt create/update
and deletion before retrying later failures. Identical saved lines are not PATCHed again; optional
question retry uses its latest known event version. The three new behavioral RED cases reproduced
duplicate POST, repeated DELETE/404 and stale saved-version reuse; after correction the related
suite passed **23 tests** (5.91s), including both explicit admission answers.

At 2026-09-09 03:15 UTC, `corepack pnpm web:check` on the isolated UI checkout passed all
format/lint/TypeScript checks, **238 tests in 31 files** (49.87s), Vite build and PWA generation.
At 03:16:43 UTC, the complete Chromium mock suite against that built preview passed **27 tests**
(20.1s). The five new browser cases cover 320px/keyboard, 18 mixed evidence references with partial
failure/retry, stale drawer responses and session expiry, network GET recovery with focus retention,
equivalent-run review reuse and reviewed claim provenance, and Korean saved-draft reload/back plus
optional question version advancement. No actual backend, provider, private data or device is implied.
Root checked all Web files, generated contracts and root lock/config inputs byte-identical to the
integrated checkout, so these final results apply without repeating an unchanged successful suite.

Local B06 completion checks are complete. [PR #81](https://github.com/jihoon22-lee/family-care/pull/81),
source `3625860`, passed all seven checks in [CI 34306662287](https://github.com/jihoon22-lee/family-care/actions/runs/34306662287)
and merged as `f59c8a9e989ea2822ec6e57174a1a7055948828b` at 2026-09-09 03:46:27 UTC.
CI confirms Python **3489 + 3 subtests** (43.11s), PostgreSQL **742** (1491.22s) with empty
head/base/head migration, Web **238/build**, Chromium mock **27** (26.7s), and all three serial
image builds. WP08 #68 is complete in the code/synthetic boundary; parents and protected acceptance
remain open for B07/B08. The exact local browser command was
`pnpm_config_verify_deps_before_run=false corepack pnpm --filter @familycare/web exec playwright test`
against the task-owned built preview on 127.0.0.1:4173, which was stopped after verification.
