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

UI integration, combined PostgreSQL coverage and full B06 completion checks are still pending.
