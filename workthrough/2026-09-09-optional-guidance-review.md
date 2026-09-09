# Optional guidance review

B05 [#67](https://github.com/jihoon22-lee/family-care/issues/67) builds on B04 PR #79,
merge `05325888bb52b7b6c00365fd237d7473bef1dc20`. Implementation is complete through the Worker, API projection and Web result flow;
verification and review corrections completed; PR #80 is merged.

Explicit requests bind the saved local decision, event version, source digest, model and prompt
revision. Duplicate clicks reuse one job. Other households, mismatched runs and stale inputs are
rejected. Cancellation is retained across duplicate requests. Requests and reads do not contact
a provider or rewrite the original answer. Migration 0058 preserves request identity and rejects
downgrade while review history exists. The public request contains only the run and event version.

The provider adapter retains optional input/output/total/cached/reasoning usage and model/service
tier metadata, including known usage on refusal, incomplete output or validation failure. Missing
usage remains unknown. Raw SDK response accounting is validated before SDK numeric coercion;
the response body is not logged or retained by the accounting layer. Existing completion and
two-value payload helper contracts remain compatible. SDK retry stays zero.

The source reader starts with the enrolled coverage inventory, independently retrieves original
articles and reference closure, and records omitted coverage/regions and full inventory digests.
The local snapshot is capped at 512 KiB; this is not a provider-safe transmission allowance.
Migration 0060 binds that immutable inventory and the exact event snapshot to the request,
including privacy revision. Source or privacy changes make a historical review stale.

Migration 0059 keeps one immutable reservation per review phase and counts every participating
document once. Policy, terms and review reservations share the same lock and aggregate both
ledgers. Failed/unknown requests retain their quota; repeated phases cannot resend after response
loss. Review limits are two possible HTTP attempts, 32,768 conservative input tokens and 4,000
output tokens per call, with per-job sums of 65,536/8,000. Known late usage can settle after
cancellation without changing the cancelled result. All three reservation readers must deploy
together; older Worker code cannot account for the new ledger.

Semantic citations may name their actual review job. The existing Decimal engine retains
`GUIDANCE_REVIEW_PUBLICATION`/`GUIDANCE_REVIEW_JOB` provenance and rejects mixed global/review
citations. Null review identity preserves the global semantic-publication meaning.

A requested job now runs through the bounded Worker lane. Only native, citation-addressed sources
and permitted event facts enter the minimized request. Policy labels and local formulas without
an exact participating document identity are omitted and reported as partial. One outstanding
HTTP call per process is bounded by the job and call deadlines; late completion can settle usage
but cannot publish a result. Missing configuration makes no HTTP request.

Migrations 0061/0062 retain immutable proposals, actual review-publication IDs and separate result
JSON, and bind the live event, enrollment, rules, original sources and privacy context. The API
consumer verifies supplied citation addresses and source meaning, persists verified interpretations,
and invokes the existing Decimal engine with a review-scoped overlay. Contradictory sources are
rejected; unverified interpretations remain opinions. The original run and global terms knowledge
are preserved. Baseline ClaimCase creation rejects review-only authority; review-aware claim
preparation belongs to B06.

The generated response includes scope, exact source citations, before/after candidate differences,
separate reviewed guidance and known/unknown token usage. The Web shows partial retrieval and
partial interpretation separately, including expected/supplied/unsupplied source regions. Existing
local results and claim actions remain available after review failure or cancellation.

## Verification so far

2026-09-09 KST, locked Python 3.14.7/OpenAI 3.3.1 environment, synthetic inputs only.
Python commands used `TMPDIR=/tmp`, the task API/Worker source and repository root in `PYTHONPATH`,
and the existing locked `.venv` interpreter. Integration used the explicit disposable test DB,
`FAMILYCARE_ALLOW_DESTRUCTIVE_TEST_DB=true`; no runtime URL fallback or external provider.

- Provider subtask source `02be761` plus provider/new HTTP test, integrated as `1040840`:
  new `test_guidance_review_provider.py -q -x` first failed for missing metadata (1.91s).
  The first follow-up had 37 pass/1 fail from SDK numeric-string coercion; an intermediate raw
  response integration had 18 fail/52 pass/1 deselected because the wrapper exposes JSON via
  `http_response.json()`. Both were corrected. Final provider, policy provider, recommender,
  recommendation jobs and event pipeline tests: **70 passed / 1 deselected** (1.26s).
  These include 22 installed-SDK MockTransport cases and exactly one HTTP attempt on success,
  429, timeout, connection/server error, refusal and malformed output. Mypy provider and Ruff passed.
- Source `1040840` plus new review repository/model/0058 and request tests:
  `pytest apps/api/tests/test_guidance_review_request_integration.py -m integration -q -x`
  first failed because the review module did not exist (2.77s). An initial implementation had
  10 failures from an incorrect decision-run column assumption. After correction, 8 passed/2
  failed exposed the repeatable-read duplicate-click race. Bounded local transaction retry with
  a fresh snapshot resolved it: **10 passed** (16.47s). Empty 0058 downgrade/upgrade also passed.
- `pytest apps/api/tests/test_guidance_review_api.py -q -x`: first failed for the missing router
  (0.21s), then **5 passed** (0.59s). HTTP requests use the server household and no-store responses;
  extra client scope, source text, amount and model fields are rejected with sanitized errors.
- Related Ruff checks passed after formatting and line fixes. Mypy initially found an optional
  model value; explicit type validation corrected it: **5 source files passed**.
- Worker queue tests first failed for the missing queue module (3.15s). Scoped request and queue
  integration then passed together: **18 passed** (31.43s). Two workers claim only one job;
  cancellation, expired leases and changed events are terminal, with local JSON preserved.
  A member-change case exposed the original 0058 trigger rechecking historical membership on
  state updates (1 failed, 4.91s). The corrected trigger verifies scope on insertion and protects
  immutable identity thereafter; after reapplying the unpublished migration on the empty test DB,
  both member-change variants passed (**2 passed**, 3.99s). Related mypy **6 source files passed**.
- OpenAPI and generated Web types were regenerated. The first contract check identified the
  expected path inventory missing the three new routes. Updating that explicit inventory retained
  the existing per-route authentication checks; `scripts/check_contracts.py` then passed.

- Source retrieval subtask `1040840` plus two new files, integrated as `23802ac`: missing-module
  RED failed (0.26s). First PG run had 5 pass/7 fail (52.07s), from SQL text parameter casts and
  one superseded fixture. After correction, native/private 2 passed (10.91s) and remaining
  affected 5 passed (26.82s); together with the unchanged 5 scope cases, all 12 cases were covered.
  This was not one final 12-case run. Ruff, format, mypy and diff passed on the subtask source.
- Source `23802ac` plus 0059/0060, review quota/input tests and shared reader changes:
  budget RED failed for missing `provider_quota` (2.12s); immutable-input RED failed for the
  missing table (2.62s). The first combined run had **12 request cases passed / 12 budget cases
  failed** (41.43s): the budget fixture/guard incorrectly assumed documents owned household IDs.
  Scope now follows document versions to household evidence or terms editions. After an empty
  0060→0058→0060 migration round trip, **12 budget cases passed** (20.88s).
- `pytest apps/api/tests/test_guidance_review_calculation_provenance.py -q -x` first failed for
  the missing review authority field (0.51s). With that field and calculation provenance changes,
  the new tests plus `test_guidance_calculation_source.py`: **42 passed** (0.51s). Related mypy
  checked four source files successfully. The embedded claim schema was regenerated; decision
  schema/OpenAPI/Web updates remain with the final review DTO. These checks ran on 2026-09-09 KST
  before the next implementation commit.

No live key/configuration, private documents or provider calls were used in these checks.
Full required completion checks and PR/CI remain pending below; focused results are not
a complete release or real-provider acceptance.


## Integrated execution and projection evidence

Source `c4a08e4` plus the B05 projection/runner/migrations/generated response changes, 2026-09-09 KST,
in the same locked synthetic environment:

- Proposal persistence first failed for the missing method (2.65s), then 6 PG cases passed (11.46s).
  Worker input loading first failed for the missing method (2.92s), then request/budget/proposal
  integration passed 22 cases (44.91s). Rider-only status and linked-clause deletion regressions
  first failed to detect changed inputs; digest corrections passed the focused cases and empty
  0062 migration round trips. An intermediate SQL alias ambiguity was fixed before successful
  migration. These checks do not use the protected runtime database.
- Projection first failed for the missing module (5.87s), then 5 PG cases passed (32.19s), including
  a missing candidate recovered with amount 300, ignoring the proposed 999999, false citation and
  contradictory exception rejection, cancellation and original-run preservation. Semantic scope
  tests retain partial interpretation even when retrieval is complete: related PG 2 passed (13.35s)
  and pure partial-semantics plus Runner 8 passed (1.61s). Two discarded intermediate fixture
  variants failed to model the intended full-retrieval condition; the final test isolates that
  flag while retaining real source replay, compilation, persistence and calculation.
- Installed SDK 3.3.1 with actual PostgreSQL and MockTransport:
  `pytest workers/analyzer/tests/test_guidance_review_http_integration.py -m integration -q`
  passed 6 cases (39.11s): success, 429, timeout, lost settlement/restart, source change and cancel.
  Each retained one reservation and exactly one synthetic HTTP request, with known usage or NULL
  for unknown usage; duplicate/restarted execution did not resend and original JSON was unchanged.
- Consumer/factory wiring first failed 3 cases (5.64s), then 37 consumer/Worker health cases passed
  (1.93s). Baseline-claim review-authority guard first failed to raise (1.50s), then 4 provenance
  cases passed (0.47s). Related mypy passed 12 files before the later scope-count DTO addition.
- Worker minimization/quota subtask integrated through `c4a08e4`: 33 unit cases passed (1.80s),
  including canonical aliases, path filtering, participating document accounting and local partial
  comparison. Source/reassessment focused checks and UI checks from their earlier commits remain
  historical evidence; final combined checks below cover their integrated state.
- Initial UI integration `08118f2` with generated root DTO: focused 62 Web cases passed (6.11s),
  type/lint/format passed, and 2 Chromium mock flows passed (12.1s). Later scope-count copy changes
  require the final Web run. These are mock-browser tests, not Windows/mobile or protected acceptance.

## Completion verification

At 2026-09-09 02:14 UTC, source `c4a08e4bf31d81deab6e1cc8b6053a9187bd4f70`
plus the final B05 projection/runner/context/test/generated-contract changes:

- Combined `pytest -m integration -q` over the review sources, request, projection, reassessment,
  budget, jobs, proposal and HTTP integration modules passed **68 cases in 256.10s**. A preceding
  command used the wrong queue-test filename and collected no tests; it was corrected to
  `test_guidance_review_jobs_integration.py`.
- Independent read-only review identified missing terms-selection context and the reservation wait
  before the first HTTP call. USER_SELECTED terms detach first failed to invalidate loaded inputs
  (**1 failed**, 5.88s). Cancellation, stale input, stop and deadline during reservation each still
  transmitted (**4 failed**, 0.90s). The scope digest now includes policy terms selection,
  current applicability and effective change/source context; the Runner rechecks current inputs,
  stop and remaining wall time after reservation. Empty 0062 downgrade/upgrade passed.
  Runner **11 passed** (1.01s); final combined review PostgreSQL **70 passed** (268.64s), including
  the detach/zero-reservation regression and the no-suggestion control that leaves the known
  missing candidate unrecovered. The latter contrasts with the verified 300-amount improvement
  and rejected false-citation/contradictory-meaning cases; it is not a live-model quality estimate.
- Ruff format/check passed across 824 files after style fixes. Documentation **50 files passed**;
  repository safety **1029 paths passed** before this verification-record append.

B06 follow-up: equivalent guidance from a newly saved decision run currently reuses the original
review job, while the UI expects the displayed run ID. B06 must bind that reuse without another
paid call and support explicit review-result claim snapshots; existing local claim preparation
continues to use its original run. Terminal configuration/retry recovery and common evidence
browsing also belong to that next UI/API bundle.

The first full `corepack pnpm web:check` passed format/lint/type checks, then had
**219 tests passed / 1 failed** (42.53s): the partial-scope test expected the previous abbreviated
copy. Its expectation and browser expectation now identify unreviewed original packets explicitly;
the component fixture also verifies missing-region counts and incomplete interpretation. An
intermediate rerun stopped at Prettier for the edited test; that formatting was corrected.

Final Web completion on the same B05 source plus the scope-copy/test corrections:
`corepack pnpm web:check` passed format, ESLint, TypeScript, **220 tests in 27 files** (42.84s)
and production/PWA build. `corepack pnpm --filter @familycare/web test:e2e` passed all
**22 Chromium mock flows** (19.1s), including the two opt-in review flows. No real backend,
Windows/mobile, private document or external provider was used by these browser checks.
Full Python format and Ruff passed; `mypy apps/api/src workers/analyzer/src scripts`
passed **333 source files** in the locked Python 3.14.7 environment with task `PYTHONPATH`.

`pytest apps/api/tests workers/analyzer/tests scripts/tests -q` passed **3482 tests and 3 subtests**
with **714 integration tests deselected** (151.77s). This is the default unit suite, distinct from
the 70 related PostgreSQL cases. Contract, container-policy, workflow-policy and branch-convention
checks and `git diff --check` passed. Container policy is static; all three image builds and the
full PostgreSQL suite will run as required PR CI checks. No Dockerfile or workflow was changed.


## PR and merge

[PR #80](https://github.com/jihoon22-lee/family-care/pull/80) merged as
`c58fae77139bbe77caf3648b5d46e3eed74aaaf1` at 2026-09-09 02:41 UTC.
[CI 34302926708](https://github.com/jihoon22-lee/family-care/actions/runs/34302926708)
passed all seven required checks on source `0c70e515a5fc8d64d404a60c6d4a25c5c985e445`:
Python **3482 + 3 subtests** (42.43s), PostgreSQL **714** (956.86s) and empty
head→base→head at 0062, Web **220**/build, Chromium mock **22** (24.1s), three sequential
image builds, repository safety/contracts and conventions. The ten PR commit subjects passed
local convention checking. This adds CI evidence to the separate local results above.
No tag, deployment, live-provider review or protected-data acceptance was performed by B05.
#66/#67 code/synthetic acceptance is complete; #59/#60 remain open for B06–B08 and protected acceptance.
