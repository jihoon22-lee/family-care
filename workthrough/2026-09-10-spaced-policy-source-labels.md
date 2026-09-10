# Recognize spaced policy identity labels without losing their source

The #63 source follow-up found that known Korean insured/contract labels printed
with spaces were skipped. The Worker now recognizes those complete labels, while
the API independently resolves the same complete contract anchor. Names, policy
numbers, household/member checks and opaque source identity retain their existing
rules. Additional contract labels cannot be silently ignored as unrelated text.

Provider minimization recognizes the same labels before clipping source windows.
The minimization revision advances to v4; retained processing advances to v5 on
schema 0074. Old jobs, raw inputs, responses and publications remain intact; a new
explicit job has a fresh privacy fingerprint and cannot replay an old v4 packet.
The schema change admits v5 alongside old source revisions and blocks downgrade
when processing history would outlive the older privacy contract.

## Evidence and verification

Work uses base `a0b91d5f97ba29a1fe2d4b29f40119734d554b45` plus the Worker/API
label readers, minimizer, retained/runtime revision, migration, related tests and
this bundle's documentation. All implementation fixtures are wholly synthetic.

- On the preceding sealed source, a read-only label-only comparison inspected all
  43 approved policy sources and 703 original pages, verifying complete source
  snapshots before and after. It retained every old anchor and found additional
  table anchors in three sources. This is a parser diagnostic, not accepted new
  enrollment, member identity or provider quality.
- Initial unit fixture snapshots incorrectly used dataclass conversion on immutable
  mapping proxies. Switching to the structure's existing serializer allowed the
  behavioral RED: 14 failed / 46 passed (0.10s), on missing Worker/API recognition.
  After the label change, 137 related tests passed (0.15s).
- Privacy RED: three spaced-label cases left a synthetic identifier visible;
  14 controls passed. The label/source-window/minimization suites then passed
  177 tests (0.77s), retaining amount text and redacting a window cropped inside
  the private value. No real identifier was used or transmitted.
- The new migration tests first exposed absent v5 admission/idempotency/downgrade
  protection. One early fixture omitted its range plan; after preparing that plan,
  all three failed for the intended behavior (6.36s). All three now pass (7.34s):
  old v4 work/response preservation, separate idempotent v5 work, rejection of old
  replay, exact empty downgrade and nonempty-history refusal.
- Two native word-line/API/reimport PostgreSQL cases passed (4.38s), retaining
  the same contract/rider objects, original structure and both publication sources.
- The broader seven-module DB run passed 53 cases but failed six historical
  assertions and seven historical fixture setups (79.00s). The historical v4
  tests inherited the new v5 default argument, and the old migration assertions
  included the new privacy revision through `head`. They now specify their old
  producer explicitly and test the original migration with final restoration to
  current head. All 18 cases in the three affected modules passed (43.45s).
- One attempted follow-up DB command named a nonexistent source-test module and collected no
  tests. The corrected command uses the existing plural `sources` module.
- The corrected terms/review/job/projection/source/claim DB suite passed 61 tests
  (243.49s), including the three new revision tests after the historical fixture fixes.
- A complete-page comparison of actual local associations retained all 2,847 resolved
  policy nodes across the same three sources. The additional labels did not by
  themselves resolve another insured/contract association. All 43 sources/703 pages
  and their original role/node membership were checked without writes or provider
  calls. Early helper attempts stopped on a prohibited read-lock query, metadata-only
  page descriptors, and a JSON/JSONB union; the corrected read-only reader used the
  stored page fields and passed after a synthetic SQL check. This remains diagnostic
  support evidence, not new enrollment publication or final protected acceptance.
- Final Web format/lint/type, 250 tests (47.88s) and production build passed.
  Full Python passed 4,052 tests plus three subtests (40.08s), with 880 integration
  tests excluded. Whole-tree Ruff format/check passed (937 files), and mypy passed
  365 source files. Contract, static container/workflow and diff checks passed.
- PR CI on `328fd13` passed six jobs and 879 PostgreSQL cases; one historical
  replay/publication test failed because its default target had advanced to v5,
  which correctly rejects old privacy packets. The test now explicitly runs its
  original v4 producer/queue contract, retaining every verifier/publication
  assertion. That test plus all three v5 privacy/migration controls passed
  (4 tests, 10.32s). Complete replacement PR CI remains pending.
  Full Python after this test-only correction passed again: 4,052 tests and three
  subtests (31.61s), with 880 integration cases excluded. Ruff, documentation,
  repository safety and diff checks passed. Prior Web/type/contract/container
  results retain identical implementation/configuration inputs.
- The isolated 0073→0074 upgrade on clean `328fd13` preserved every old row/column
  and processing count. New API/Worker rejected 0073 and accepted 0074; the
  preceding clean `9019094` binaries rejected 0074. Authenticated evidence and
  claim-history reads, AI-off analysis and identical stored results passed for
  the same ten approved events, preserving every original row. The aggregate
  remained 34 candidates, 17 point estimates and 9 formulas; this establishes
  compatibility, not additional source linkage. External HTTP and new provider
  jobs were zero. No provider request, live runtime change, tag or deployment
  was performed. The subsequent changes affect only a historical test and this
  evidence record; production and migration inputs remain those of `328fd13`.

Commands used Python 3.14.7, Node 24.18.0 and locked pnpm 11.22.0 on 2026-09-10 UTC.
The dedicated disposable PostgreSQL 18 test database is separate from all protected
and fixed evaluation databases. Its wrapper supplied both test/runtime DSNs and
explicit destructive-test opt-in solely for that owned test database.

```bash
uv run pytest workers/analyzer/tests/test_policy_label_spacing.py workers/analyzer/tests/test_policy_source_association.py apps/api/tests/test_contract_source_locator.py workers/analyzer/tests/test_source_window_minimizer.py workers/analyzer/tests/test_policy_ai_minimization.py workers/analyzer/tests/test_resident_identifier_minimization.py -q --tb=short
python -m pytest -m integration workers/analyzer/tests/test_retained_label_spacing_revision.py -q --tb=short
```

`TMPDIR=/tmp` was set for Python commands; exact private helper paths and protected
receipts remain outside Git. PR #98's completed CI and isolated application receipt
are recorded in its existing workthrough, not repeated as checks of these changes.

Completion commands were `corepack pnpm web:check`, `uv run ruff format --check .`,
`uv run ruff check .`, `uv run mypy apps/api/src workers/analyzer/src scripts`,
`uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`, and
`uv run python scripts/check_contracts.py`, `check_containers.py` and
`check_workflows.py`. Documentation/safety/diff and branch/commit convention checks
are completed on the final documentation before publication. No product code was
changed after the successful full checks. The corrected additional DB command was:

```bash
python -m pytest -m integration workers/analyzer/tests/test_retained_label_spacing_revision.py workers/analyzer/tests/test_terms_semantic_jobs.py workers/analyzer/tests/test_guidance_review_proposal_integration.py workers/analyzer/tests/test_guidance_review_jobs_integration.py apps/api/tests/test_guidance_review_projection_integration.py apps/api/tests/test_guidance_review_claim_integration.py apps/api/tests/test_guidance_review_sources_integration.py -q --tb=short
```
