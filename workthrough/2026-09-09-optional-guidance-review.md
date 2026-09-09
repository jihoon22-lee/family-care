# Optional guidance review

B05 [#67](https://github.com/jihoon22-lee/family-care/issues/67) builds on B04 PR #79,
merge `05325888bb52b7b6c00365fd237d7473bef1dc20`. Implementation is in progress;
source packet retrieval and shared reservations are implemented; review validation/recalculation,
Worker execution and UI remain.

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
Full required checks, generated contracts, PostgreSQL queue/budget recovery, final review quality,
browser acceptance and PR/CI remain pending for the complete B05 change.
