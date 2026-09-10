# Preserve conditional relevance for uncertain terms applicability

For the #63/#70 follow-up, an already approved operational rule could disappear
from local candidates because UNKNOWN terms applicability was folded into invalid
citation lineage. Source integrity and applicability now remain separate.

A bounded reader recomputes shared insurer/product-code evidence from current
source proofs, requires the current insufficiency-only assessment and existing
approved rule/link, and verifies the exact event/family/contract/rider/clause scope.
Stale/excluded/corrected sources, conflicting editions and change relations remain
barriers. It creates no new edition, link, approval or applicability MATCH.

Eligible relevance remains CONDITIONAL with TERMS_APPLICABILITY_UNRESOLVED.
Calculations retain only their source-validated formula; uncertain required/relevant
rules cannot contribute an amount, subtotal or planned-care scenario. The Web copy
identifies the missing contract-to-edition proof rather than asking for unrelated
medical-event facts. Public DTO/schema fields remain unchanged.

## Verification

Agent implementation used base `e3de9f4ffac6779b838f3397b4907b5f3f059227` plus
nine API guidance/test files. The root integrated v11 source `db04d0e` and main
merge `80c9cd0`, giving base `830cd2132c8956481bb18538fb066e7eb450a40d` before
this task's API/Web/documentation changes.

- Adapter RED: 1 failed, 9 passed (0.88s), showing a valid original citation was
  marked invalid by UNKNOWN applicability.
- Separate planned-scenario and source-binding bypass assertions failed before
  their fixes. The uncertainty guard now runs after calculation source binding.
- Before the session interruption, agent unit/related tests passed 212 cases,
  including 13 new cases; API mypy passed 213 files; scoped Ruff/diff passed.
- A host restart erased the temporary checkout. Committed v11 work remained intact;
  the nine uncommitted API files were restored from the original recorded patches
  into a persistent worktree. Earlier results are historical evidence, not a fresh
  verification of the recovered tree.
- New synthetic PostgreSQL tests now passed both cases (4.74s), exercising real
  metadata/applicability/rule/read/store behavior, later exclusion/correction and
  immutable old result/claim snapshots. Initial fixture failures used an unsupported
  persisted admission flag and requested the latest run when checking an older run;
  supported admission-days input and direct immutable-run lookup corrected them.
- Related local guidance/applicability/component/event/claim/code-scope PostgreSQL
  modules passed 23 additional cases (52.18s), on a dedicated owned synthetic DB.
- The Web copy RED failed the missing proof explanation before implementation, then
  the component suite passed 32 cases. Full Web format/lint/type, 250 tests (49.85s)
  and production build passed.
- Current full Python passed 4,021 tests and three subtests (39.68s), with 875
  integration cases excluded. Whole-tree Ruff format/check passed (933 files),
  mypy passed 365 source files, and generated/OpenAPI contract checks passed.
  Static container and workflow checks passed.
- `corepack pnpm --filter @familycare/web test:e2e`: 27 Chromium tests passed
  (27.3s), using mocked API responses. This is separate from the real PostgreSQL
  integration path and does not claim actual-device validation.
- [PR #98](https://github.com/jihoon22-lee/family-care/pull/98) passed all seven
  required checks in [CI 34477630281](https://github.com/jihoon22-lee/family-care/actions/runs/34477630281),
  including 875 PostgreSQL tests (1,604.18s; 4,021 deselected). It merged as
  `a0b91d5f97ba29a1fe2d4b29f40119734d554b45` on 2026-09-10 UTC.
  [Main CI 34480493504](https://github.com/jihoon22-lee/family-care/actions/runs/34480493504)
  also completed successfully. The separate protected application check is recorded below.

The implementation tests used synthetic data. Separate protected v11 metadata
acceptance belongs to PR #97; the authorized application check below reused its
isolated validation DB. No provider request, live runtime change, tag or deployment
was performed for this guidance change.

Current completion commands were `corepack pnpm web:check`,
`TMPDIR=/tmp uv run ruff format --check .`, `TMPDIR=/tmp uv run ruff check .`,
`TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts`,
`TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`,
and `TMPDIR=/tmp uv run python scripts/check_contracts.py` (plus the static
`check_containers.py`/`check_workflows.py` commands). The scoped DB wrapper supplied
only this task's disposable synthetic database and explicit destructive-test opt-in.
Its test commands were:

```bash
python -m pytest -m integration apps/api/tests/test_guidance_uncertain_terms_integration.py -q --tb=short
python -m pytest -m integration apps/api/tests/test_local_guidance_integration.py apps/api/tests/test_terms_applicability_consumers.py apps/api/tests/test_component_rule_sources.py apps/api/tests/test_event_terms_integration.py apps/api/tests/test_guidance_claim_restore_integration.py apps/api/tests/test_guidance_code_scope_integration.py -q --tb=short
```

Checks ran on 2026-09-10 UTC with Python 3.14.7, PostgreSQL 18.6, Node 24.18.0
and locked pnpm 11.22.0, on the base and uncommitted scope above. The new assessment
identity participates in the operational status digest, so changed support invalidates
current-result freshness while stored result and claim snapshots stay immutable.

Final documentation, repository safety and diff checks are run on the completed record before commit. No code changes followed the successful full suites.

## Isolated protected application acceptance

On 2026-09-10 UTC, clean source `9019094e61a450e98ad61a3ebec908aef176061a`
passed the reviewed `app_acceptance_guidance73.py` helper (SHA-256
`447369c916243ef31fce8d3a220a4cf2426ffbdec9962461008e5c1054672742`).
The existing separate schema-0073 validation DB was prepared under source
`db04d0e2a703d00c8599bf035d3d83566419f4e0`; that receipt was explicitly
checked as a data preparation receipt, independently of the current application SHA.

The helper authenticated a disposable session, read retained evidence and claim
history, analyzed the same ten approved baseline events with external HTTP blocked
and API keys removed, and read back identical stored guidance for all ten.
A fresh complete pre-query database baseline was preserved after the queries,
allowing only newly appended rows; logout returned 204. External HTTP requests
and provider jobs created were both zero. Fixed aggregate candidate/estimate counts
matched the preceding v11 application check, so this is compatibility/preservation
evidence, not a claim of increased real-document support or validated new links.

The command used the clean checkout's locked environment with `PYTHONPATH` set
to that checkout and the reviewed private helper directory, `TMPDIR=/tmp`,
`FAMILYCARE_EXPECTED_SOURCE_SHA=9019094e61a450e98ad61a3ebec908aef176061a`,
and `TRANSITION_PERF_BASELINE_ONLY=true`, then `uv run python` with the helper.
Private configuration, credentials and result payloads stayed outside Git.
This was direct authenticated ASGI/API execution, not a Windows/mobile browser
check or a switch of the live schema-0069 runtime.
