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
- PR CI and protected application support for this guidance change remain pending.

No real-data access, provider request, runtime mutation, tag or deployment is part
of this implementation. Separate protected v11 metadata acceptance belongs to PR #97.

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
