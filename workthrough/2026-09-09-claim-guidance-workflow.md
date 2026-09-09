# Guidance to claim preparation

B06 [#68](https://github.com/jihoon22-lee/family-care/issues/68) builds on B05
[PR #80](https://github.com/jihoon22-lee/family-care/pull/80), source `0c70e51`.
B05 CI/merge is still being monitored. This bundle is in progress.

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
