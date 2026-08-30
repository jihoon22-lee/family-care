# Private Knowledge Decision Publication Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the complete private insurance catalog produce evidence-backed, deterministic multi-coverage event results, fixed conditional subtotals, and a separate indemnity status instead of an empty response, while providing LLM-assisted recommendations when a Worker provider is configured and citation-backed structured-search recommendations otherwise.

**Architecture:** Keep the immutable private-knowledge snapshot as document-analysis authority and add an append-only, user-approved publication package for exact-token fact normalizers, coverage dispositions, eligibility rules, calculations, and clause citations. Persist private-knowledge evaluations in additive tables and combine them with the existing operational Rider stream only at the v2 API boundary. Add a separate member-scoped recommendation stream: the API always stores an immediate structured-search result; a deduplicated Worker job may replace only that recommendation ordering/explanation with one strict-schema LLM response. Neither stream can alter verified decisions or calculations. Apply actual data only after protected dry-run and restored-database verification.

**Tech Stack:** FastAPI, Pydantic v2, psycopg/PostgreSQL, SQLAlchemy/Alembic, the existing data-only rule DSL, React 19, TypeScript 6, Vitest/Testing Library, JSON Schema/OpenAPI generation.

**Spec:** `docs/superpowers/specs/2026-08-30-private-knowledge-decision-publication-design.md`

## Global Constraints

- No actual insurance document, extracted text, page image, diagnosis, event, identifier, amount, Drive ID, package row, or acceptance value enters Git, tests, logs, or command output.
- Actual packages, reports, backups, and acceptance manifests remain outside the repository in mode-`0700` directories with mode-`0600` regular files.
- No external model API is used for actual document ingestion, rule publication, fact normalization authority, deterministic decision execution, or protected acceptance. Runtime event assistance may send only the bounded event and locally selected excerpts described by the spec.
- `OPENAI_API_KEY` remains Worker-environment-only. The API, browser, database, logs, tests, fixtures, generated files, and Git never receive it.
- All automated tests use fake providers and make zero network calls. A live smoke call is optional, synthetic-only, separately counted, and only run when necessary; this task's approved hard ceiling is ten calls and the implementation target is zero.
- One event version and local-candidate digest can produce at most one external call. There is no automatic provider retry; missing or failed provider leaves the structured-search recommendation usable.
- Certificate enrollment, current status, event-date status, terms identity, edition applicability, section mapping, rule publication, and benefit calculation remain independent authorities.
- Only `MATCH`, `NO_MATCH`, and `UNKNOWN` are eligibility decisions; missing or conflicting evidence is `UNKNOWN`, never inferred `NO_MATCH`.
- Private semantic facts and mappings remain `executable=false`; a separate user-approved publication version is the only execution authority.
- Fixed and indemnity calculations stay separate. No cross-currency or fixed-plus-indemnity grand total is produced.
- All runtime queries and foreign keys are scoped by server-derived `HouseholdSpace`; no source path, source alias, raw statement, DSN, SQL, or token is returned or logged.
- Feature and bug changes use strict RED → observed expected failure → minimal GREEN → refactor. Generated files are regenerated only after the source schema test is green.
- Frontend and Python verification run serially. WSL Python commands use `TMPDIR=/tmp`.

---

### Task 1: Complete the event-submit regression fix

**Status:** completed

**Files:**
- Modify: `apps/web/src/features/events/NewEventPage.tsx`
- Modify: `apps/web/src/features/events/EventComposer.tsx`
- Test: `apps/web/src/features/events/event-input.test.tsx`

**Interfaces:**
- Consumes: existing `MedicalEventUpdateRequest`, `updateMedicalEvent`, and `analyzeMedicalEvent` clients.
- Produces: `updateInput(event, draft)` that omits `structured_facts` for an empty draft and event actions that clear stale status before a new request.

- [x] **Step 1: Preserve the already-observed RED evidence**

The focused tests must name the two production breaks:

```ts
it("reaches analysis after saving an event with no structured facts", async () => {
  // PATCH rejects the request if an empty structured_facts array is sent.
  // The observable contract is that POST analyze is still reached once.
});

it("clears a stale success message when a later save fails", async () => {
  // The second failed save must not leave the first success live-region visible.
});
```

The first test was observed failing with `structured_facts: []` and zero analyze requests; the second was observed with both success and error visible.

- [x] **Step 2: Run the focused GREEN test**

Run:

```bash
corepack pnpm@11.22.0 --filter @familycare/web test -- src/features/events/event-input.test.tsx
```

Expected: all tests in `event-input.test.tsx` pass with no warning.

- [x] **Step 3: Review the minimal production change**

The implementation must remain equivalent to:

```ts
const update = {
  expected_version: event.version,
  ...(draft.facts.length > 0
    ? { structured_facts: draft.facts.map(({ field_id, value }) => ({ field_id, value })) }
    : {}),
};

async function action() {
  setStatus("");
  // execute request and set exactly one final success/error state
}
```

- [x] **Step 4: Run Web type and formatting checks for the changed files**

Run:

```bash
corepack pnpm@11.22.0 --filter @familycare/web format:check
corepack pnpm@11.22.0 --filter @familycare/web typecheck
git diff --check
```

Expected: all commands exit 0.

- [x] **Step 5: Commit the regression fix**

```bash
git add apps/web/src/features/events/EventComposer.tsx \
  apps/web/src/features/events/NewEventPage.tsx \
  apps/web/src/features/events/event-input.test.tsx
git commit -m "fix(events): reach analysis without structured facts"
```

### Task 2: Add publication and event-date status persistence

**Status:** completed

**Files:**
- Create: `apps/api/migrations/versions/0020_private_knowledge_publications.py`
- Create: `apps/api/tests/test_private_knowledge_publication_migration.py`
- Create: `apps/api/tests/test_private_knowledge_publication_migration_integration.py`

**Interfaces:**
- Consumes: revision `0019_private_confirmations` and the exact IDs in `private_knowledge_import_runs`, contracts, coverages, sections, clauses, facts, and AppUser.
- Produces: append-only status-interval and publication tables with same-run/same-household constraints.

- [x] **Step 1: Write the migration-shape RED tests**

The recording-operations test must require exactly these new tables in dependency order:

```python
EXPECTED_TABLES = [
    "private_knowledge_contract_status_intervals",
    "private_knowledge_rule_import_runs",
    "private_knowledge_coverage_execution_dispositions",
    "private_knowledge_fact_normalizer_publications",
    "private_knowledge_rule_publications",
    "private_knowledge_rule_citations",
    "private_knowledge_calculation_publications",
    "private_knowledge_calculation_citations",
]

assert migration.revision == "0020_private_publications"
assert migration.down_revision == "0019_private_confirmations"
assert list(operations.tables) == EXPECTED_TABLES
```

Require composite foreign keys back to the exact knowledge run and household, one current publication run per household, one disposition per coverage per publication run, unique rule/normalizer keys, user reviewer provenance, SHA-256 checks, JSON-object/array checks, bounded pages, and `ON DELETE RESTRICT`.

- [x] **Step 2: Run RED migration tests**

Run:

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_publication_migration.py -q
```

Expected: FAIL because revision `0020_private_publications` does not exist.

- [x] **Step 3: Implement the additive migration**

The important database checks must include:

```python
sa.CheckConstraint("decision IN ('MATCH', 'NO_MATCH', 'UNKNOWN')")
sa.CheckConstraint("disposition IN ('PUBLISHED', 'BLOCKED', 'NOT_APPLICABLE')")
sa.CheckConstraint("review_state = 'USER_CONFIRMED'")
sa.CheckConstraint("match_kind = 'EXACT_TOKEN_SEQUENCE'")
sa.CheckConstraint("calculation_kind IN ('FIXED', 'INDEMNITY', 'NONE', 'UNKNOWN')")
sa.CheckConstraint("page_start >= 1 AND page_end >= page_start")
sa.CheckConstraint("effective_through >= effective_from")
```

`private_knowledge_rule_import_runs` must store package/manifest/baseline/report/projection digests, state, counts, reviewer, timestamps, and current/superseded state. Rule and calculation JSON columns must be strict JSON objects; exact phrase values are allowed only in the private database table and never in public fixtures or output.

- [x] **Step 4: Write and run PostgreSQL constraint tests**

The integration test must insert a wholly synthetic current snapshot, then prove:

```python
with pytest.raises(psycopg.errors.ForeignKeyViolation):
    insert_rule_with_other_household_coverage(connection)

with pytest.raises(psycopg.errors.UniqueViolation):
    insert_second_disposition_for_same_coverage(connection)

with pytest.raises(psycopg.errors.CheckViolation):
    insert_executable_rule_without_user_review(connection)
```

Run the repository integration database harness with only the two new migration tests. Expected: upgrade → constraints → downgrade → upgrade all pass.

- [x] **Step 5: Run focused static checks and commit**

```bash
TMPDIR=/tmp uv run ruff format --check apps/api/migrations/versions/0020_private_knowledge_publications.py apps/api/tests/test_private_knowledge_publication_migration.py apps/api/tests/test_private_knowledge_publication_migration_integration.py
TMPDIR=/tmp uv run ruff check apps/api/migrations/versions/0020_private_knowledge_publications.py apps/api/tests/test_private_knowledge_publication_migration.py apps/api/tests/test_private_knowledge_publication_migration_integration.py
git diff --check
git add apps/api/migrations/versions/0020_private_knowledge_publications.py apps/api/tests/test_private_knowledge_publication_migration.py apps/api/tests/test_private_knowledge_publication_migration_integration.py
git commit -m "feat(private-knowledge): add rule publication schema"
```

### Task 3: Add private decision-result persistence

**Status:** completed

**Files:**
- Create: `apps/api/migrations/versions/0021_private_knowledge_decisions.py`
- Create: `apps/api/tests/test_private_knowledge_decision_migration.py`
- Create: `apps/api/tests/test_private_knowledge_decision_migration_integration.py`

**Interfaces:**
- Consumes: `decision_runs` and all tables from Task 2.
- Produces: knowledge snapshot identity on decision runs and additive evaluation, candidate, and calculation records.

- [x] **Step 1: Write RED tests for the exact result schema**

Require `decision_runs` columns:

```python
{
    "knowledge_import_run_id",
    "knowledge_rule_import_run_id",
    "knowledge_status_projection_digest",
    "event_fact_schema_version",
    "analysis_completeness",
    "source_failure_codes_json",
}
```

Require new tables:

```python
[
    "private_knowledge_rule_evaluations",
    "private_knowledge_claim_candidates",
    "private_knowledge_benefit_calculations",
    "private_knowledge_calculation_steps",
]
```

Evaluation rows reference one decision run, one publication, one knowledge coverage, and store tri-state, required flag, reason, fact path arrays, citation snapshot, and evaluator version. Candidate rows reference one contract and coverage and store benefit type, counts, questions, hold reasons, and safe label snapshots. Calculation amounts use `Numeric(20,4)` and never binary float.

- [x] **Step 2: Run RED tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_decision_migration.py -q
```

Expected: FAIL because revision `0021_private_knowledge_decisions` is missing.

- [x] **Step 3: Implement migration and run integration constraints**

Add `partial` to the decision-run status check while retaining `running`, `succeeded`, and `failed`. Require:

```sql
analysis_completeness IN ('COMPLETE', 'PARTIAL', 'UNAVAILABLE')
result IN ('MATCH', 'NO_MATCH', 'UNKNOWN')
calculation_status IN ('CALCULATED', 'UNKNOWN', 'NOT_APPLICABLE', 'FAILED')
benefit_type IN ('FIXED', 'INDEMNITY', 'UNKNOWN')
```

The integration test must prove same-household/same-run references, one candidate per run/coverage, one calculation per candidate, ordered unique steps, nonnegative amounts, rate bounds, and rollback-safe downgrade/upgrade.

- [x] **Step 4: Run focused migration verification and commit**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_decision_migration.py apps/api/tests/test_private_knowledge_decision_migration_integration.py -q
TMPDIR=/tmp uv run ruff format --check apps/api/migrations/versions/0021_private_knowledge_decisions.py apps/api/tests/test_private_knowledge_decision_migration.py apps/api/tests/test_private_knowledge_decision_migration_integration.py
TMPDIR=/tmp uv run ruff check apps/api/migrations/versions/0021_private_knowledge_decisions.py apps/api/tests/test_private_knowledge_decision_migration.py apps/api/tests/test_private_knowledge_decision_migration_integration.py
git diff --check
git add apps/api/migrations/versions/0021_private_knowledge_decisions.py apps/api/tests/test_private_knowledge_decision_migration.py apps/api/tests/test_private_knowledge_decision_migration_integration.py
git commit -m "feat(decisions): persist private knowledge results"
```

### Task 4: Validate the protected rule-publication package

**Status:** completed

**Files:**
- Create: `apps/api/src/familycare_api/private_knowledge/publication_models.py`
- Create: `apps/api/src/familycare_api/private_knowledge/publication_package.py`
- Create: `apps/api/tests/private_knowledge_publication_fixtures.py`
- Create: `apps/api/tests/test_private_knowledge_publication_package.py`
- Create: `apps/api/tests/test_private_knowledge_publication_privacy.py`
- Modify: `apps/api/src/familycare_api/private_knowledge/errors.py`

**Interfaces:**
- Produces:

```python
def load_rule_publication_package(
    root: Path,
    *,
    repository_root: Path,
) -> RulePublicationPackage: ...


def canonical_rule_publication_digest(package: RulePublicationPackage) -> str: ...
```

- Consumes: `validate_rule_document()` from the existing data-only DSL and exact external canonical coverage/section/clause/fact keys.

- [x] **Step 1: Write a wholly synthetic fixture writer and happy-path RED test**

The fixture must create these required role files plus a manifest:

```python
PUBLICATION_DATA_FILES = (
    "coverage-dispositions.jsonl",
    "contract-status-intervals.jsonl",
    "fact-normalizers.jsonl",
    "rule-publications.jsonl",
    "rule-citations.jsonl",
    "calculation-publications.jsonl",
    "calculation-citations.jsonl",
    "reconciliation.json",
)
```

The happy path uses `Family Member A`, `synthetic-policy-001`,
`synthetic-coverage-001`, `synthetic-section-001`, and a fabricated medical category. It asserts literal counts, exact references, `USER_CONFIRMED`, and a deterministic package digest.

- [x] **Step 2: Run RED package tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_publication_package.py -q
```

Expected: FAIL on missing module/function.

- [x] **Step 3: Implement strict Pydantic models and descriptor-safe loading**

Models must use `ConfigDict(extra="forbid", frozen=True, strict=True)`. Exact-token normalizers have this contract:

```python
class FactNormalizerRecord(StrictPublicationModel):
    normalizer_key: ShortText
    field_path: EventFieldPath
    match_kind: Literal["EXACT_TOKEN_SEQUENCE"]
    phrase: PrivatePhrase
    normalized_value: StrictStr | StrictBool
    priority: Annotated[int, Field(ge=0, le=1000)]
    review_state: Literal["USER_CONFIRMED"]
```

Status interval rows use exact contract keys and explicit dates:

```python
class ContractStatusIntervalRecord(StrictPublicationModel):
    canonical_policy_id: ShortText
    effective_from: date
    effective_through: date
    decision: Literal["MATCH", "NO_MATCH", "UNKNOWN"]
    confirmed_status: Literal["active", "inactive", "lapsed", "terminated", "unknown"]
    authority: Literal["USER_CONFIRMED_EVENT_DATE", "REVIEWED_STATUS_DOCUMENT"]
    reason_code: ReasonCode
```

The loader must reject relative/repository paths, wrong directory/file modes, symlinks, unexpected/missing files, hash/size mismatch, replacement after validation, invalid UTF-8/JSONL, duplicates, broken references, incomplete disposition closure, unsupported DSL, arbitrary executable strings, missing citations, and reconciliation drift.

- [x] **Step 4: Write failure and privacy tests**

Each error exposes only:

```python
PublicationPackageError(
    code=PublicationErrorCode.BROKEN_REFERENCE,
    file_role="rule-citations.jsonl",
    row_number=2,
)
```

Parameterized tests must prove the exception string excludes a synthetic private phrase, source key, path, JSON value, hash, event text, amount, DSN, and SQL fragment.

- [x] **Step 5: Run focused checks and commit**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_publication_package.py apps/api/tests/test_private_knowledge_publication_privacy.py -q
TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/private_knowledge/publication_models.py apps/api/src/familycare_api/private_knowledge/publication_package.py apps/api/tests/private_knowledge_publication_fixtures.py apps/api/tests/test_private_knowledge_publication_package.py apps/api/tests/test_private_knowledge_publication_privacy.py
TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/private_knowledge/publication_models.py apps/api/src/familycare_api/private_knowledge/publication_package.py apps/api/tests/private_knowledge_publication_fixtures.py apps/api/tests/test_private_knowledge_publication_package.py apps/api/tests/test_private_knowledge_publication_privacy.py
git diff --check
git add apps/api/src/familycare_api/private_knowledge/publication_models.py apps/api/src/familycare_api/private_knowledge/publication_package.py apps/api/src/familycare_api/private_knowledge/errors.py apps/api/tests/private_knowledge_publication_fixtures.py apps/api/tests/test_private_knowledge_publication_package.py apps/api/tests/test_private_knowledge_publication_privacy.py
git commit -m "feat(private-knowledge): validate rule packages"
```

### Task 5: Reconcile, dry-run, apply, and verify publication packages

**Status:** completed

**Files:**
- Create: `apps/api/src/familycare_api/private_knowledge/publication_reconciliation.py`
- Create: `apps/api/src/familycare_api/private_knowledge/publication_repository.py`
- Create: `apps/api/src/familycare_api/private_knowledge/publication_service.py`
- Modify: `apps/api/src/familycare_api/private_knowledge/cli.py`
- Modify: `apps/api/src/familycare_api/private_knowledge/__init__.py`
- Create: `apps/api/tests/test_private_knowledge_publication_reconciliation.py`
- Create: `apps/api/tests/test_private_knowledge_publication_service.py`
- Create: `apps/api/tests/test_private_knowledge_publication_repository_integration.py`
- Modify: `apps/api/tests/test_private_knowledge_cli.py`

**Interfaces:**

```python
class PostgresRulePublicationRepository:
    def read_baseline(self, household_space_id: UUID) -> PublicationDatabaseBaseline: ...
    def prepare_dry_run(
        self,
        package: RulePublicationPackage,
        *,
        household_space_id: UUID,
    ) -> RulePublicationDryRunReport: ...
    def apply(
        self,
        package: RulePublicationPackage,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report: RulePublicationDryRunReport,
    ) -> AppliedRulePublication: ...
    def verify_current(self, household_space_id: UUID) -> RulePublicationSummary: ...
```

- [x] **Step 1: Write RED count-only reconciliation tests**

The baseline digest includes current knowledge run/package/projection digests, coverage authority axes, current confirmations, referenced sections/clauses/facts, AppUser identity/version metadata, and current publication identity. Reports contain only schema version, package/snapshot/baseline/report digests, operation, and count/decision matrices.

Test first apply, same-current no-op, new snapshot/package supersede, historical digest block, stale knowledge snapshot, incomplete disposition matrix, missing current confirmation, non-MATCH mapping axes, citation mismatch, and changed baseline.

- [x] **Step 2: Run RED tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_publication_reconciliation.py apps/api/tests/test_private_knowledge_publication_service.py -q
```

Expected: FAIL on missing repository/service contracts.

- [x] **Step 3: Implement read-only dry-run and atomic service**

Use `REPEATABLE READ READ ONLY` for baseline/dry-run. Persist the report with atomic mode-`0600` replacement outside the repository and reread it before approval. Apply must advisory-lock the household publication scope, lock all baseline tables against DML, recompute the baseline, insert all rows, compare every count/matrix/digest, supersede the prior publication, and mark exactly one current run in one transaction.

- [x] **Step 4: Prove rollback and idempotency in PostgreSQL**

Integration tests inject a failure after each entity group and assert no partial run/child remains and the previous current run is unchanged. Apply the same digest twice and assert the second result is `NO_OP` with identical row counts. Mutate one clause digest and prove verify fails closed.

- [x] **Step 5: Add sanitized CLI commands**

Add:

```text
publication-validate
publication-dry-run
publication-apply
publication-verify
```

Private package/report path, household, actor, approval digest, and DB URL remain environment-only. stdout contains only status, opaque run ID, and aggregate counts. Extend CLI tests for invalid/missing environment, unapproved digest, sanitized errors, idempotent apply, and verify.

- [x] **Step 6: Run focused verification and commit**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_publication_reconciliation.py apps/api/tests/test_private_knowledge_publication_service.py apps/api/tests/test_private_knowledge_publication_repository_integration.py apps/api/tests/test_private_knowledge_cli.py -q
TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/private_knowledge apps/api/tests/test_private_knowledge_publication_reconciliation.py apps/api/tests/test_private_knowledge_publication_service.py apps/api/tests/test_private_knowledge_publication_repository_integration.py apps/api/tests/test_private_knowledge_cli.py
TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/private_knowledge apps/api/tests/test_private_knowledge_publication_reconciliation.py apps/api/tests/test_private_knowledge_publication_service.py apps/api/tests/test_private_knowledge_publication_repository_integration.py apps/api/tests/test_private_knowledge_cli.py
git diff --check
git add apps/api/src/familycare_api/private_knowledge apps/api/tests/test_private_knowledge_publication_reconciliation.py apps/api/tests/test_private_knowledge_publication_service.py apps/api/tests/test_private_knowledge_publication_repository_integration.py apps/api/tests/test_private_knowledge_cli.py
git commit -m "feat(private-knowledge): publish reviewed rules"
```

### Task 6: Add trusted event facts and private rule runtime

**Status:** completed

**Files:**
- Create: `apps/api/src/familycare_api/decisions/knowledge_domain.py`
- Create: `apps/api/src/familycare_api/decisions/knowledge_facts.py`
- Create: `apps/api/src/familycare_api/decisions/knowledge_engine.py`
- Modify: `apps/api/src/familycare_api/clauses/dsl.py`
- Modify: `apps/api/src/familycare_api/decisions/operators.py`
- Modify: `apps/api/src/familycare_api/decisions/structuring_schemas.py`
- Modify: `apps/api/src/familycare_api/decisions/structuring_repository.py`
- Modify: `apps/api/src/familycare_api/decisions/schemas.py`
- Create: `apps/api/tests/test_private_knowledge_facts.py`
- Create: `apps/api/tests/test_private_knowledge_engine.py`
- Modify: `apps/api/tests/test_rule_dsl.py`
- Modify: `apps/api/tests/test_event_structuring_repository.py`
- Modify: `apps/api/tests/test_event_structuring_contracts.py`

**Interfaces:**

```python
KnowledgeFactProvenance = Literal[
    "USER_CONFIRMED",
    "DOCUMENT_REVIEWED",
    "DERIVED_CONFIRMED",
    "AI_SUGGESTED",
    "UNCONFIRMED",
    "CONFLICTING",
]


def normalize_private_event_facts(
    event: MedicalEvent,
    normalizers: tuple[KnowledgeFactNormalizer, ...],
) -> KnowledgeFactContext: ...


class DeterministicKnowledgeDecisionEngine:
    def evaluate(
        self,
        scope: HouseholdScope,
        event: MedicalEvent,
        context: KnowledgeDecisionContext,
        *,
        run_id: UUID,
    ) -> KnowledgeDecisionResult: ...
```

- [x] **Step 1: Write RED fact-normalization tests**

Use fabricated phrases unrelated to actual cases. Assert Unicode normalization plus exact token-sequence matching, deterministic priority, identical-value de-duplication, conflicting-value `CONFLICTING`, and no substring/fuzzy/regex match. Explicit user structured facts override a derived value and preserve conflict audit.

- [x] **Step 2: Write RED rule-engine tables**

Test one coverage each for all authority gates, required aggregation, missing/conflicting/AI-suggested fact, inactive/event-date status, exclusion, waiting, reduction, frequency history, invalid citation, fixed calculation, indemnity missing receipt data, mixed currency, and one-coverage failure isolation.

The two independent synthetic acceptance cases are:

```python
case_a = synthetic_event_with_two_matching_fixed_coverages()
assert [item.result for item in evaluate(case_a).candidates] == ["MATCH", "MATCH"]
assert evaluate(case_a).fixed_subtotals[0].amount == Decimal("300")

case_b = synthetic_event_with_four_matching_fixed_coverages_and_indemnity_gap()
assert len([item for item in evaluate(case_b).candidates if item.result == "MATCH"]) == 4
assert evaluate(case_b).fixed_subtotals[0].amount == Decimal("1000")
assert evaluate(case_b).indemnity_summary.status == "UNKNOWN"
```

The literal values are wholly synthetic and deliberately unrelated to actual data.

- [x] **Step 3: Run RED tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_facts.py apps/api/tests/test_private_knowledge_engine.py apps/api/tests/test_rule_dsl.py -q
```

Expected: new tests fail because the knowledge fact/runtime modules and v2 fields are absent.

- [x] **Step 4: Extend the field registry and implement exact normalization**

Add these qualified fields with strict kinds:

```python
PRIVATE_EVENT_FIELDS = {
    "MedicalEvent.diagnosis_code": "string",
    "MedicalEvent.procedure_code": "string",
    "MedicalEvent.anatomical_site_code": "string",
    "MedicalEvent.pathology_code": "string",
    "MedicalEvent.treatment_setting": "string",
    "MedicalEvent.treatment_context": "string",
    "MedicalEvent.separately_billed_treatment": "boolean",
    "Receipt.covered_amount": "decimal",
}
```

String operands must be bounded normalized codes, not raw diagnosis labels. Boolean literals must remain strict booleans. Only USER/DOCUMENT/DERIVED confirmed facts can satisfy a rule; AI-suggested, missing, stale, and conflicting facts produce `UNKNOWN`.

Extend `FactFieldId`, repository allowlists, request validation, and generated schema source with
`diagnosis_code`, `procedure_code`, `anatomical_site_code`, `pathology_code`,
`treatment_setting`, `treatment_context`, and `separately_billed_treatment`. User overrides are stored
as source `user`, state `confirmed`; AI/provider suggestions remain source `ai` until edited.

- [x] **Step 5: Implement deterministic knowledge evaluation and calculation**

Reuse the compiled data-only expression shape, but keep private citations as `KnowledgeCitation` rather than fabricating operational `Evidence`. Calculate each coverage independently using Decimal. Sum only `MATCH + CALCULATED` fixed results per currency, count unresolved candidates, and return indemnity separately. Runtime must not make network or model calls.

- [x] **Step 6: Run GREEN, mutation review, and commit**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_facts.py apps/api/tests/test_private_knowledge_engine.py apps/api/tests/test_rule_dsl.py apps/api/tests/test_decision_operators.py apps/api/tests/test_event_structuring_repository.py apps/api/tests/test_event_structuring_contracts.py -q
TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions/knowledge_domain.py apps/api/src/familycare_api/decisions/knowledge_facts.py apps/api/src/familycare_api/decisions/knowledge_engine.py apps/api/src/familycare_api/decisions/structuring_schemas.py apps/api/src/familycare_api/decisions/structuring_repository.py apps/api/src/familycare_api/decisions/schemas.py apps/api/src/familycare_api/clauses/dsl.py apps/api/src/familycare_api/decisions/operators.py apps/api/tests/test_private_knowledge_facts.py apps/api/tests/test_private_knowledge_engine.py apps/api/tests/test_rule_dsl.py apps/api/tests/test_event_structuring_repository.py apps/api/tests/test_event_structuring_contracts.py
TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions/knowledge_domain.py apps/api/src/familycare_api/decisions/knowledge_facts.py apps/api/src/familycare_api/decisions/knowledge_engine.py apps/api/src/familycare_api/decisions/structuring_schemas.py apps/api/src/familycare_api/decisions/structuring_repository.py apps/api/src/familycare_api/decisions/schemas.py apps/api/src/familycare_api/clauses/dsl.py apps/api/src/familycare_api/decisions/operators.py apps/api/tests/test_private_knowledge_facts.py apps/api/tests/test_private_knowledge_engine.py apps/api/tests/test_rule_dsl.py apps/api/tests/test_event_structuring_repository.py apps/api/tests/test_event_structuring_contracts.py
git diff --check
git add apps/api/src/familycare_api/decisions apps/api/src/familycare_api/clauses/dsl.py apps/api/tests/test_private_knowledge_facts.py apps/api/tests/test_private_knowledge_engine.py apps/api/tests/test_rule_dsl.py
git commit -m "feat(decisions): evaluate private knowledge rules"
```

### Task 7: Integrate private decisions with PostgreSQL analysis

**Status:** completed

**Files:**
- Create: `apps/api/src/familycare_api/decisions/knowledge_repository.py`
- Modify: `apps/api/src/familycare_api/decisions/domain.py`
- Modify: `apps/api/src/familycare_api/decisions/repository.py`
- Modify: `apps/api/src/familycare_api/decisions/service.py`
- Modify: `apps/api/src/familycare_api/decisions/calculation_repository.py`
- Create: `apps/api/tests/test_private_knowledge_decision_repository.py`
- Create: `apps/api/tests/test_private_knowledge_decision_integration.py`
- Modify: `apps/api/tests/test_decision_repository.py`

**Interfaces:**

```python
class PostgresKnowledgeDecisionRepository:
    def read_context(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
    ) -> KnowledgeDecisionContext: ...

    def persist_result(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        result: KnowledgeDecisionResult,
    ) -> None: ...
```

- [x] **Step 1: Write repository RED tests**

Prove the scoped SQL loads only exact member-bound current knowledge contracts, current user confirmations, event-date status intervals, complete dispositions, current publications, exact citations, receipt lines, and claim history. A catalog with coverages but no publication must produce `UNAVAILABLE` and a count, not an empty-success interpretation.

- [x] **Step 2: Write PostgreSQL end-to-end RED tests**

Seed wholly synthetic operational and knowledge sources in one household. Analyze once and assert both streams persist under one run. Seed a second household and prove it never appears. Make knowledge evaluation fail and assert legacy rows persist with run `partial`. Edit event/publication/status and assert the prior result becomes stale.

- [x] **Step 3: Run RED tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_decision_repository.py apps/api/tests/test_private_knowledge_decision_integration.py -q
```

Expected: FAIL on missing integration.

- [x] **Step 4: Implement one-transaction combined analysis**

`DecisionRepository.analyze_medical_event()` must lock the event at repeatable-read, evaluate legacy and knowledge streams independently, persist one run plus both child streams in one transaction, and set:

```python
status = "partial" if source_failure_codes else "succeeded"
stale = legacy_result.stale or knowledge_result.stale
analysis_completeness = knowledge_result.completeness
```

`get_decision_result()` must load both succeeded and partial runs and reconstruct the exact captured snapshot without consulting current rules for historical output.

- [x] **Step 5: Run focused regression and commit**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_decision_repository.py apps/api/tests/test_private_knowledge_decision_integration.py apps/api/tests/test_decision_repository.py apps/api/tests/test_decision_integration.py apps/api/tests/test_benefit_integration.py -q
TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions apps/api/tests/test_private_knowledge_decision_repository.py apps/api/tests/test_private_knowledge_decision_integration.py
TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions apps/api/tests/test_private_knowledge_decision_repository.py apps/api/tests/test_private_knowledge_decision_integration.py
git diff --check
git add apps/api/src/familycare_api/decisions apps/api/tests/test_private_knowledge_decision_repository.py apps/api/tests/test_private_knowledge_decision_integration.py apps/api/tests/test_decision_repository.py
git commit -m "feat(decisions): combine knowledge results"
```

### Task 8: Add member-scoped structured recommendations and job persistence `completed`

**Files:**
- Create: `apps/api/migrations/versions/0022_analysis_assistance.py`
- Create: `apps/api/src/familycare_api/decisions/assistance.py`
- Create: `apps/api/src/familycare_api/decisions/assistance_repository.py`
- Modify: `apps/api/src/familycare_api/decisions/repository.py`
- Create: `apps/api/tests/test_analysis_assistance_migration.py`
- Create: `apps/api/tests/test_analysis_assistance_migration_integration.py`
- Create: `apps/api/tests/test_analysis_assistance_search.py`
- Create: `apps/api/tests/test_analysis_assistance_repository.py`

**Interfaces:**

```python
class AnalysisAssistanceRepository:
    def create_search_projection(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        event: MedicalEvent,
        decision_run_id: UUID,
    ) -> AnalysisAssistance: ...

    def get_latest(
        self,
        connection: psycopg.Connection[dict[str, Any]],
        scope: HouseholdScope,
        decision_run_id: UUID,
    ) -> AnalysisAssistance: ...
```

- [x] **Step 1: Write migration RED tests**

Require revision `0022_analysis_assistance` after `0021_private_knowledge_decisions` and these tables:

```python
[
    "analysis_assistance_jobs",
    "analysis_assistance_runs",
    "analysis_recommendations",
]
```

The schema must enforce same-household/event/run foreign keys, job state
`QUEUED | RUNNING | SUCCEEDED`, mode `STRUCTURED_SEARCH | LLM_ASSISTED | NONE`, attempts `0..1`,
one job per event-version/candidate digest, immutable result runs, unique rank per run, bounded excerpts/pages,
and `ON DELETE RESTRICT`. It stores provider/model/config identifiers and sanitized outcome codes but no key,
prompt, response, query, situation, fact value, raw statement, source path, or private source alias.

- [x] **Step 2: Run the migration RED tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_analysis_assistance_migration.py -q
```

Expected: FAIL because revision `0022_analysis_assistance` is absent.

- [x] **Step 3: Implement migration and PostgreSQL constraint tests**

Write integration tests proving duplicate digests, attempt `2`, cross-household coverage/section/citation, and
recommendations without a same-run enrolled coverage are rejected. Exercise upgrade → downgrade → upgrade on
the disposable PostgreSQL harness.

- [x] **Step 4: Write structured-search RED tests**

Seed wholly synthetic current private knowledge for two members and two households. Require search to use only:

- server-scoped current import run;
- the event's exact FamilyMember subject binding;
- certificate enrollment `MATCH` coverages;
- same-run coverage mappings and cited section/fact/clause rows;
- normalized event/fact tokens with deterministic rank and tie-breaks.

Assert a relevant mapped section is returned with a bounded excerpt and page citation, while another member,
another household, stale snapshot, unenrolled coverage, unmapped section, and zero-token query are absent.
The result type has no eligibility, amount, or claim-ready field.

- [x] **Step 5: Run RED search tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_analysis_assistance_search.py apps/api/tests/test_analysis_assistance_repository.py -q
```

Expected: FAIL on the missing domain/repository and analyze integration.

- [x] **Step 6: Implement immediate search and deduplicated job creation**

`DecisionRepository.analyze_medical_event()` stores deterministic results first, then creates one immutable
`STRUCTURED_SEARCH` assistance run and one deduplicated queued job in the same transaction. Repeated result GET
does not mutate or enqueue. Re-analyzing the same event/candidate digest reuses the job; an edited event gets a
new digest and job. Search parameters and private values must never be logged.

- [x] **Step 7: Run focused checks and commit**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_analysis_assistance_migration.py apps/api/tests/test_analysis_assistance_migration_integration.py apps/api/tests/test_analysis_assistance_search.py apps/api/tests/test_analysis_assistance_repository.py apps/api/tests/test_private_knowledge_decision_integration.py -q
TMPDIR=/tmp uv run ruff format --check apps/api/migrations/versions/0022_analysis_assistance.py apps/api/src/familycare_api/decisions apps/api/tests/test_analysis_assistance_*.py
TMPDIR=/tmp uv run ruff check apps/api/migrations/versions/0022_analysis_assistance.py apps/api/src/familycare_api/decisions apps/api/tests/test_analysis_assistance_*.py
git diff --check
git add apps/api/migrations/versions/0022_analysis_assistance.py apps/api/src/familycare_api/decisions apps/api/tests/test_analysis_assistance_*.py
git commit -m "feat(decisions): add structured recommendations"
```

### Task 9: Add one-call LLM assistance with zero-retry fallback `completed`

**Files:**
- Create: `workers/analyzer/src/familycare_worker/ai/recommender.py`
- Create: `workers/analyzer/src/familycare_worker/recommendation_jobs.py`
- Modify: `workers/analyzer/src/familycare_worker/ai/provider.py`
- Modify: `workers/analyzer/src/familycare_worker/__main__.py`
- Modify: `workers/analyzer/src/familycare_worker/runner.py`
- Create: `workers/analyzer/tests/test_recommender.py`
- Create: `workers/analyzer/tests/test_recommendation_jobs.py`
- Create: `workers/analyzer/tests/test_recommendation_privacy.py`
- Modify: `workers/analyzer/tests/test_policy_ai_provider.py`

**Interfaces:**

```python
RECOMMENDER_SCHEMA_NAME = "event_clause_recommendations_v1"


def recommend_clauses(
    *,
    request: RecommendationRequest,
    provider: AiProvider,
    model: str,
) -> RecommendationResult: ...
```

- [x] **Step 1: Write strict-schema and privacy RED tests**

Require at most 12 request-local candidate tokens, a 240-character maximum excerpt, bounded event situation,
and only safe labels/page/citation kind. Reject DB UUIDs, unknown tokens, invented citations, duplicate ranks,
decision fields, payable amounts, claim readiness, raw provider content, and extra keys. Prove result `repr`,
persisted rows, errors, and logs contain none of the event text, excerpt markers, provider payload, API key, path,
policy number, household/member ID, or raw response.

- [x] **Step 2: Write provider cost-boundary RED tests**

Extend `OpenAiResponsesAdapter` with a validated per-schema output-token limit. Existing structurer/verifier
schemas retain current limits; the recommender schema defaults to `1_200` and refuses values above `4_000`.
Assert `store=False`, one request, bounded timeout, strict JSON schema, and no automatic retry.

- [x] **Step 3: Write job-routing RED tests**

Test all paths with fakes only:

```text
no OPENAI_API_KEY -> provider calls 0 -> base STRUCTURED_SEARCH run remains current
configured fake provider -> calls 1 -> append LLM_ASSISTED run -> supplied tokens only
timeout/rate/auth/invalid response -> calls 1 -> no retry -> base search remains current
same claimed job again -> calls remain 1
event/digest changed -> a different job may call once
```

- [x] **Step 4: Run RED worker tests**

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_recommender.py workers/analyzer/tests/test_recommendation_jobs.py workers/analyzer/tests/test_recommendation_privacy.py workers/analyzer/tests/test_policy_ai_provider.py -q
```

Expected: FAIL because the recommender and queue runner are absent.

- [x] **Step 5: Implement one-call Worker refinement**

The Worker loads only the already-selected local candidates, assigns request-local opaque tokens, calls the
configured provider once, validates the strict response, and appends a sanitized `LLM_ASSISTED` run. Missing
configuration or every provider error completes the job with the existing search run and a stable fallback code.
Do not retry, and do not make provider success a prerequisite for deterministic decision or result retrieval.

- [x] **Step 6: Integrate the fair runner without starving existing queues**

Register recommendation jobs as a bounded fair-runner lane. Existing document, event-structuring, import, and
policy jobs retain their current behavior. `FAMILYCARE_AI_ASSISTANCE_MODEL` is runtime configuration; its value
is stored only as a bounded model label. Never read or expose the key outside `OpenAiResponsesAdapter`.

- [x] **Step 7: Run focused checks and commit**

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_recommender.py workers/analyzer/tests/test_recommendation_jobs.py workers/analyzer/tests/test_recommendation_privacy.py workers/analyzer/tests/test_policy_ai_provider.py workers/analyzer/tests/test_event_structuring_pipeline.py workers/analyzer/tests/test_policy_structuring_runner.py -q
TMPDIR=/tmp uv run ruff format --check workers/analyzer/src workers/analyzer/tests/test_recommend*.py workers/analyzer/tests/test_policy_ai_provider.py
TMPDIR=/tmp uv run ruff check workers/analyzer/src workers/analyzer/tests/test_recommend*.py workers/analyzer/tests/test_policy_ai_provider.py
git diff --check
git add workers/analyzer/src workers/analyzer/tests/test_recommend*.py workers/analyzer/tests/test_policy_ai_provider.py
git commit -m "feat(analyzer): add bounded llm recommendations"
```

No live provider call is part of this task. If all fake-provider and adapter-boundary tests pass, do not spend
the user's API balance merely to duplicate existing adapter acceptance.

### Task 10: Publish the v2 API and generated contracts `completed`

**Files:**
- Modify: `apps/api/src/familycare_api/decisions/schemas.py`
- Modify: `apps/api/src/familycare_api/decisions/router.py`
- Modify: `apps/api/src/familycare_api/decisions/assistance.py`
- Modify: `apps/api/src/familycare_api/decisions/assistance_repository.py`
- Create: `packages/contracts/schemas/coverage-decision.v2.schema.json`
- Create: `packages/contracts/examples/coverage-decision.v2.json`
- Modify: `scripts/check_contracts.py`
- Modify: `apps/api/tests/test_decision_api.py`
- Modify: `apps/api/tests/test_decision_contracts.py`
- Modify: `apps/api/tests/test_decision_privacy.py`
- Regenerate: `packages/contracts/openapi/familycare.v1.json`
- Regenerate: `apps/api/src/familycare_api/contracts/generated_business.py`
- Regenerate: `apps/web/src/api/generated.ts`

**Interfaces:**
- Produces `CoverageDecisionResponse.schema_version == "2"` and a discriminated candidate/evaluation union.

- [x] **Step 1: Write RED HTTP and schema tests**

Require literal envelope fields:

```python
{
    "schema_version",
    "run_id",
    "medical_event_id",
    "event_version",
    "engine_version",
    "rule_set_version",
    "knowledge_snapshot_version",
    "policy_snapshot_at",
    "stale",
    "analysis_completeness",
    "catalog_coverage",
    "candidates",
    "evaluations",
    "conditional_fixed_subtotals",
    "indemnity_summary",
    "source_failure_codes",
    "assistance",
}
```

Candidate `source.kind` is `OPERATIONAL_RIDER` with `rider_id` or
`PRIVATE_KNOWLEDGE_COVERAGE` with `knowledge_contract_id` and
`knowledge_coverage_id`. Require `claim_start_ready=false` for private candidates, bounded citations, decimal strings, currency-separated totals, no-store, 404 isolation, and no raw statement/path/alias/hash/actual identifiers.

`assistance` must use the closed state/mode vocabulary and contain only bounded recommendation cards and
citation projections. It must not contain provider request IDs, prompts, raw responses, provider error detail,
eligibility, amount, claim readiness, or another member's candidates. Repeated GET returns the latest immutable
assistance run and never enqueues or calls a provider.

- [x] **Step 2: Run RED contract tests**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_api.py apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_privacy.py -q
```

Expected: FAIL because v2 fields and schema are absent.

- [x] **Step 3: Implement strict Pydantic adapters and transport schema**

Retain the v1 schema/example as a historical artifact. Change the live endpoint and generated OpenAPI atomically to v2. Every nested object uses `extra="forbid"`; response collections have explicit max lengths; money is a decimal string.

- [x] **Step 4: Regenerate and verify contracts**

```bash
TMPDIR=/tmp uv run python scripts/generate_business_contract_types.py
TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_api.py apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_privacy.py -q
```

Expected: generated files are deterministic and all checks pass.

- [x] **Step 5: Commit the API boundary**

```bash
git diff --check
git add apps/api/src/familycare_api/decisions/schemas.py apps/api/src/familycare_api/decisions/router.py packages/contracts/schemas/coverage-decision.v2.schema.json packages/contracts/examples/coverage-decision.v2.json packages/contracts/openapi/familycare.v1.json apps/api/src/familycare_api/contracts/generated_business.py apps/web/src/api/generated.ts scripts/check_contracts.py apps/api/tests/test_decision_api.py apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_privacy.py
git commit -m "feat(contracts): publish coverage decision v2"
```

### Task 11: Render complete and non-empty result states in Web

**Status:** completed

**Files:**
- Modify: `apps/web/src/features/results/ActionFirstResult.tsx`
- Modify: `apps/web/src/features/results/ResultGroup.tsx`
- Modify: `apps/web/src/features/results/ClaimCandidateCard.tsx`
- Create: `apps/web/src/features/results/AnalysisCompleteness.tsx`
- Create: `apps/web/src/features/results/BenefitSummaries.tsx`
- Create: `apps/web/src/features/results/RelatedClauseRecommendations.tsx`
- Modify: `apps/web/src/features/results/EventResultPage.tsx`
- Modify: `apps/web/src/features/results/Results.module.css`
- Modify: `apps/web/src/features/results/result-page.test.tsx`
- Modify: `apps/web/src/api/results.test.ts`
- Modify: `apps/web/src/features/events/EventComposer.tsx`
- Modify: `apps/web/src/features/events/StructuredFactEditor.tsx`
- Modify: `apps/web/src/features/events/event-input.test.tsx`

**Interfaces:**
- Consumes: generated `CoverageDecisionResponse` v2 discriminated unions.
- Produces: accessible catalog-completeness, fixed subtotal, indemnity, knowledge candidate, Evidence, and claim-readiness UI.

- [x] **Step 1: Write RED component tests**

Test the following observable behavior with complete synthetic v2 fixtures:

- two and four matching private coverage cards are all rendered, not de-duplicated by label;
- currency-specific `조건부 정액 합계` shows only MATCH+CALCULATED values;
- indemnity status appears in a separate section and never changes the fixed subtotal;
- `PARTIAL`/`UNAVAILABLE` with enrolled coverages and zero published rules says rules are not ready, never “no insurance”;
- private candidates have no claim-start button; operational candidates retain it;
- `UNKNOWN` questions and clause/page citations are keyboard accessible;
- stale and partial source failures remain visible without hiding valid cards.
- event editor can add and user-confirm every new v2 fact field, while AI suggestions remain visibly
  unconfirmed until edited.
- `STRUCTURED_SEARCH` and `LLM_ASSISTED` modes have distinct labels and every recommendation states that it is
  a review candidate, not a payment decision;
- `LLM_PENDING` keeps verified results and immediate DB recommendations visible, then polling updates only the
  recommendation section; provider fallback remains usable and never becomes a full-page error.

- [x] **Step 2: Run RED Web tests**

```bash
corepack pnpm@11.22.0 --filter @familycare/web test -- src/features/results/result-page.test.tsx src/api/results.test.ts
```

Expected: FAIL on the missing v2 UI and private source handling.

- [x] **Step 3: Implement action-first v2 UI**

Use source discriminators rather than nullable-ID guessing:

```ts
function candidateKey(candidate: CoverageCandidateResponse): string {
  return candidate.source.kind === "OPERATIONAL_RIDER"
    ? candidate.source.rider_id
    : candidate.source.knowledge_coverage_id;
}
```

Group by aggregate result, not source. Display safe contract/coverage labels, calculation trace, reason codes translated through a bounded local dictionary, and citation buttons. Do not render raw internal errors or payment-guarantee wording.

Render `assistance.recommendations` outside every `MATCH | UNKNOWN | NO_MATCH` group. Do not infer a verified
candidate from a matching label or shared citation. Result polling uses `cache: "no-store"`, stops on
`SEARCH_READY | LLM_READY`, and has a bounded timeout without creating another analysis request.

- [x] **Step 4: Run GREEN and complete Web checks**

```bash
corepack pnpm@11.22.0 --filter @familycare/web test -- src/features/events/event-input.test.tsx src/features/results/result-page.test.tsx src/api/results.test.ts
corepack pnpm@11.22.0 web:check
git diff --check
```

Expected: all Web format, lint, typecheck, unit tests, and build pass.

- [x] **Step 5: Commit the result experience**

```bash
git add apps/web/src/features/results apps/web/src/api/results.test.ts
git commit -m "feat(web): show complete insurance decisions"
```

### Task 12: Complete synthetic end-to-end and repository verification

**Files:**
- Create: `apps/api/tests/test_private_knowledge_decision_acceptance.py`
- Modify: `docs/design/coverage-decision-engine.md`
- Modify: `docs/design/event-result-pwa.md`
- Modify: `docs/design/private-knowledge-catalog.md`
- Modify: `docs/design/ai-document-analysis.md`
- Modify: `docs/design/security-privacy.md`
- Modify: `docs/superpowers/specs/2026-08-30-private-knowledge-decision-publication-design.md`
- Modify: `docs/superpowers/plans/2026-08-30-private-knowledge-decision-publication.md`

**Interfaces:**
- Consumes: complete synthetic catalog, publication package, confirmations, status intervals, event, receipts, analyze/result API, and Web contract.
- Produces: one reproducible no-external-service acceptance proof and current documentation.

- [ ] **Step 1: Write the acceptance RED test before the final wiring change**

The test performs:

```text
knowledge package apply
  -> confirmation apply
  -> rule publication apply
  -> event create/update
  -> combined analyze
  -> result reload
```

Assert two fixed candidates and a literal subtotal in one event; four fixed candidates plus separate indemnity UNKNOWN in another; exact citations on every evaluation; idempotent publication apply; and no cross-household rows.

The same synthetic flow must prove both assistance paths without a network request: no provider returns scoped
`STRUCTURED_SEARCH` recommendations; a fake strict provider reorders only supplied tokens into
`LLM_ASSISTED`; provider failure retains the same DB recommendations. Verified candidates, calculations, and
subtotals must be byte-for-byte equal across assistance modes.

- [ ] **Step 2: Run acceptance and fix only the missing wiring**

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_decision_acceptance.py -q
```

Expected RED: the first still-unwired boundary fails. Implement only that boundary and rerun until GREEN.

- [ ] **Step 3: Run all focused groups serially**

```bash
corepack pnpm@11.22.0 --filter @familycare/web test -- src/features/events/event-input.test.tsx src/features/results/result-page.test.tsx
TMPDIR=/tmp uv run pytest apps/api/tests/test_private_knowledge_publication_migration.py apps/api/tests/test_private_knowledge_publication_package.py apps/api/tests/test_private_knowledge_publication_reconciliation.py apps/api/tests/test_private_knowledge_engine.py apps/api/tests/test_private_knowledge_decision_integration.py apps/api/tests/test_analysis_assistance_search.py apps/api/tests/test_private_knowledge_decision_acceptance.py workers/analyzer/tests/test_recommendation_jobs.py -q
```

- [ ] **Step 4: Run the full required repository gate**

```bash
python3 scripts/check_documentation.py
python3 scripts/check_repository_safety.py
corepack pnpm@11.22.0 web:check
TMPDIR=/tmp uv run ruff format --check .
TMPDIR=/tmp uv run ruff check .
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run python scripts/check_containers.py
TMPDIR=/tmp uv run python scripts/check_workflows.py
git diff --check
```

Expected: every command exits 0. A skipped real-browser or external-data check is recorded separately and is not converted into a pass.

- [ ] **Step 5: Review privacy and commit synthetic completion**

Inspect the full diff for actual names, event phrases, diagnoses, amounts, source aliases, paths, IDs, hashes, DSNs, SQL output, and extracted text. Then:

```bash
git add apps/api/tests/test_private_knowledge_decision_acceptance.py docs/design docs/superpowers/specs/2026-08-30-private-knowledge-decision-publication-design.md docs/superpowers/plans/2026-08-30-private-knowledge-decision-publication.md
git commit -m "test(decisions): prove multi-coverage acceptance"
```

### Task 13: Build and audit the actual protected publication package

**Files:** External mode-`0700` package, mode-`0600` files, count-only reports, and temporary per-task extraction directories only. No actual artifact is added to Git.

**Interfaces:**
- Consumes: the current external private-analysis package and certificate/terms source set already authorized by the user.
- Produces: one exact `private-knowledge-rule-publication.sol-v1` package and one protected actual-acceptance manifest.

- [ ] **Step 1: Reconfirm the protected source inventory without outputting private values**

Compare current Drive/object inventory, external package manifest, current DB snapshot digest, paired certificate/terms counts, coverage count, section/clause/fact closure, subject binding count, and current confirmation count. Output only aggregate counts and stable mismatch codes.

- [ ] **Step 2: Create per-coverage disposition closure**

For every current coverage component, record exactly one `PUBLISHED`, `BLOCKED`, or `NOT_APPLICABLE` disposition. `PUBLISHED` requires certificate enrollment MATCH, current confirmation MATCH, supported benefit type, exact terms identity/edition/mapping MATCH, and direct clause citations. Never promote a generic semantic fact or label similarity.

- [ ] **Step 3: Directly review and publish exact rules/calculations**

Read the paired certificate and terms locally, without a model API. Create eligibility, definition, exclusion, temporal, frequency, and calculation rules only when directly supported. Add exact-token fact normalizers for reviewed user vocabulary without committing or logging phrases. Preserve every unresolved cross-reference, status interval, receipt dependency, and coordination issue as BLOCKED/UNKNOWN.

- [ ] **Step 4: Validate both actual acceptance cases outside Git**

Use the user-provided historical outcomes only in a protected acceptance manifest. Compare candidate count, per-coverage result, conditional fixed subtotal by currency, separate indemnity status, and exact clause/page citations. Do not print or commit names, event text, diagnosis, products, source aliases, IDs, or amounts; report only pass/fail and mismatch reason codes.

- [ ] **Step 5: Run actual package validate and zero-write dry-run**

Use environment-only paths and IDs:

```bash
familycare-private-knowledge publication-validate
familycare-private-knowledge publication-dry-run
```

Verify mode/hash/counts, exact knowledge snapshot digest, complete disposition matrix, zero conflicts, zero executable imported facts, and expected publication counts. Preserve the exact report digest for approval/apply without echoing it in chat or logs.

### Task 14: Back up, restore-test, apply, and verify the actual runtime

**Files:** External backup/report/package only; runtime containers and database in the already-authorized FamilyCare scope.

**Interfaces:**
- Consumes: exact approved package/report digest from Task 13.
- Produces: migrated real PostgreSQL, current publication run, rebuilt FamilyCare API/Web, and live acceptance evidence.

- [ ] **Step 1: Resolve exact runtime targets and impact**

Read-only checks must identify FamilyCare DB/API/Web containers, migration head, ports, active sessions, free disk, memory, swap, package digest, household, and actor. Do not stop or alter unrelated processes, containers, volumes, sessions, archives, or services.

- [ ] **Step 2: Create and validate a recoverable backup**

Create a timestamped custom-format PostgreSQL dump outside Git with mode `0600`, compute its SHA-256 without printing secrets, and validate it with `pg_restore --list`. Record only backup success, size, and entry count.

- [ ] **Step 3: Restore and apply in a disposable database**

Restore the exact backup into a safely named disposable integration database, migrate through revisions
0020/0021/0022, apply the exact publication report, and run `publication-verify`. Compare all counts, digests,
disposition matrices, FK closure, rule/citation/calculation closure, assistance-table isolation, and idempotent
second apply.

- [ ] **Step 4: Apply once to the real DB**

Re-run the actual dry-run and compare its full expected values to the approved report. If unchanged, run `publication-apply` once. On uncertain commit outcome, query by package digest; never blindly retry. Do not physically delete or rewrite prior snapshots/publications.

- [ ] **Step 5: Rebuild only FamilyCare API and Web and verify runtime**

Rebuild/recreate API, Worker, then Web serially while preserving DB, sessions, archives, and unrelated
containers. Verify readiness, migration head, current snapshot/publication uniqueness, member isolation,
no-store headers, result v2, provider-disabled structured-search fallback, two protected acceptance cases,
explicit partial states, and absence of sensitive logs. Do not spend a live provider call on protected data;
the provider-on path is covered by synthetic fake-provider acceptance unless a separate minimal smoke is needed.

- [ ] **Step 6: Perform browser acceptance and final repository verification**

With an authenticated browser session, reproduce event create/save/analyze/result for synthetic and protected actual acceptance without taking or committing private screenshots. Verify keyboard/focus behavior and that the result is non-empty. If browser attachment is unavailable, report it as unverified rather than inferring success from API tests.

Re-run the complete repository gate from Task 12 in the same final turn.

- [ ] **Step 7: Final review and branch completion**

Use `superpowers:verification-before-completion`, then `superpowers:finishing-a-development-branch`. Report purpose, user impact, files, migration/API/package contracts, exact verification results, actual-data/privacy boundary, backup/restore/apply outcome, runtime/browser status, commits, PR/CI/merge state, and explicitly that no tag or deployment beyond the approved local runtime was performed.
