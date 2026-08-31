# Rider-Clause Rules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Complete — PR #18 merged as `399f120a45d7c17bf623fd1348d6af9e9b653bc1` with required CI.

**Goal:** Connect verified subscribed Riders to applicable Terms Clauses and validate versioned, data-only CoverageRule candidates without executing arbitrary code or making a coverage decision.

**Architecture:** Extend `familycare_api.clauses` with Rider-Clause link repositories, an allowlist DSL validator, and a transactional CoverageRule publisher. This plan consumes the policy ledger, candidate-review status, TermsEdition hierarchy, and Evidence lineage; it produces only executable rule versions marked `AI_VERIFIED` or `USER_CONFIRMED`. The deterministic evaluation of a rule against MedicalEvent facts belongs to `008-coverage-decision-engine.md`.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, direct `psycopg` 3.3 SQL, PostgreSQL 18, Alembic 1.19, JSON Schema Draft 2020-12, pytest, Ruff, and strict mypy.

**Spec:** `docs/design/clause-linking-search.md`, `docs/design/ai-document-analysis.md`, `docs/design/coverage-decision-engine.md`, `docs/design/data-model.md`, and `docs/design/v0.1-product.md`

## Global Constraints

- Migration `0006_rider_clause_rules.py` revises `0005_clause_search`; it does not alter Phase 1 ingestion tables or contracts and does not duplicate Evidence, TermsEdition, Clause, Rider, or candidate-review tables.
- The persistence layer remains direct `psycopg` SQL with `dict_row`; no SQLAlchemy ORM model layer is introduced.
- Every link/rule query is server-scoped by `HouseholdScope`, excludes soft-deleted rows by default, and enforces expected-version optimistic concurrency with `409 VERSION_CONFLICT`.
- A Rider must be verified from policy Evidence before a Rider-Clause link can be confirmed. A Terms-only Clause can never create a subscribed Rider.
- A TermsEdition must apply to the contract date; a wrong or conflicting edition becomes a review item and is never silently selected.
- Every link and executable rule version has exact Clause/Policy Evidence with DocumentVersion, content hash, 1-based physical page, and bounded bbox lineage.
- AI may propose a link or rule candidate but is not authoritative. Only `AI_VERIFIED` and `USER_CONFIRMED` rules are executable; `NEEDS_REVIEW` and unsupported prose remain informational and cause a later decision `UNKNOWN`.
- The DSL is data-only JSON. No arbitrary Python, SQL, JavaScript, imports, reflection, shell command, or user-provided function is accepted.
- Supported operators and field paths are versioned allowlists. Unknown operator, field, unit, cross-reference, conflicting definition, or lost table evidence is rejected or remains non-executable.
- CI uses synthetic policy/terms/Rider values and fake AI responses only. No real PDF, extraction text, page image, insurer identifier, policy number, API key, or private path is used.
- Search results never establish subscription, and this plan never returns `MATCH`, `NO_MATCH`, `UNKNOWN`, or money; those outputs belong to the deterministic engine/calculation plans.

## File Responsibility Map

```text
apps/api/migrations/versions/0006_rider_clause_rules.py
  Creates rider_clause_links, rider_clause_link_evidence, coverage_rules,
  coverage_rule_versions, and coverage_rule_evidence.

apps/api/src/familycare_api/clauses/links.py
  Owns scoped link candidate/confirmation/rejection use cases.

apps/api/src/familycare_api/clauses/dsl.py
  Owns the versioned data-only operator, field-path, unit, and calculation
  allowlists. It validates but does not evaluate MedicalEvent facts.

apps/api/src/familycare_api/clauses/rules.py
  Owns CoverageRule version objects, executable-state checks, and publication.

apps/api/src/familycare_api/clauses/repository.py
  Adds direct-psycopg link/rule persistence to the Clause repositories.

apps/api/src/familycare_api/clauses/schemas.py
apps/api/src/familycare_api/clauses/router.py
  Add strict link/rule HTTP adapters and the published routes.

apps/api/src/familycare_api/clauses/errors.py
  Adds fixed link/rule validation and publication errors.

packages/contracts/schemas/rider-clause-rules.v1.schema.json
packages/contracts/examples/rider-clause-rules.v1.json
  Define link/rule/version transport contracts using only synthetic examples.

apps/api/tests/test_rider_clause_rules_migration.py
apps/api/tests/test_rule_dsl.py
apps/api/tests/test_rider_clause_rules.py
apps/api/tests/test_rider_clause_rules_api.py
apps/api/tests/test_rider_clause_rules_integration.py
apps/api/tests/test_rider_clause_rules_privacy.py
  Cover schema, DSL allowlist, applicability, publish state, HTTP, PostgreSQL,
  soft delete/version conflict, and privacy boundaries.
```

`apps/api/src/familycare_api/main.py`, `apps/api/src/familycare_api/errors.py`, `scripts/check_contracts.py`, and the committed OpenAPI are root integration files. Register and regenerate them once the focused feature tests pass so multiple PR agents do not hand-merge generated JSON.

## Database, Python, HTTP, and JSON Interfaces

### Migration contract

```text
rider_clause_links(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  rider_id uuid references riders(id),
  terms_edition_id uuid references terms_editions(id),
  clause_id uuid references clauses(id),
  candidate_version_id uuid references analysis_candidate_versions(id),
  review_state varchar(32) not null,
  applicability_reason_code varchar(64) not null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

rider_clause_link_evidence(
  rider_clause_link_id uuid references rider_clause_links(id),
  evidence_id uuid references evidence(id),
  primary key (rider_clause_link_id, evidence_id)
)

coverage_rules(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  rider_clause_link_id uuid references rider_clause_links(id),
  rule_key varchar(160) not null,
  current_status varchar(32) not null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

coverage_rule_versions(
  id uuid primary key,
  coverage_rule_id uuid references coverage_rules(id),
  version_number integer not null,
  schema_version varchar(32) not null,
  rule_kind varchar(48) not null,
  required boolean not null,
  input_field_paths jsonb not null,
  expression_json jsonb not null,
  result_reason_code varchar(64) not null,
  review_state varchar(32) not null,
  executable boolean not null default false,
  generator_version varchar(64) not null,
  verifier_version varchar(64) not null,
  created_at timestamptz not null,
  published_at timestamptz null,
  unique (coverage_rule_id, version_number)
)

coverage_rule_evidence(
  coverage_rule_version_id uuid references coverage_rule_versions(id),
  evidence_id uuid references evidence(id),
  primary key (coverage_rule_version_id, evidence_id)
)
```

Named CHECK constraints must limit review states to `AI_VERIFIED`, `NEEDS_REVIEW`, `USER_CONFIRMED`, `generated`, and `rejected` as applicable; rule kinds and schema version are validated by the application contract. `executable = true` is allowed only for `AI_VERIFIED` or `USER_CONFIRMED` and only after evidence/DSL validation. Add scope/status/version indexes and active-row partial indexes.

### Python DSL interfaces

```python
RULE_SCHEMA_VERSION = "coverage-rule-v1"

RuleKind = Literal[
    "eligibility",
    "classification",
    "temporal",
    "exclusion",
    "frequency",
    "fixed_amount",
    "rate_amount",
    "indemnity_eligibility",
    "deductible",
    "limit",
    "required_document",
]

ExpressionOperator = Literal[
    "all",
    "any",
    "not",
    "present",
    "equals",
    "in",
    "range",
    "date_between",
    "days_since",
    "count_before",
]

CalculationOperator = Literal["add", "subtract", "multiply", "min", "max", "round"]


@dataclass(frozen=True)
class CompiledExpression:
    operator: ExpressionOperator
    operands: tuple[object, ...]
    referenced_fields: tuple[str, ...]


@dataclass(frozen=True)
class CompiledCalculation:
    operator: CalculationOperator
    operands: tuple[object, ...]
    rounding: str | None


def validate_field_path(path: str) -> None: ...
def validate_expression(value: Mapping[str, object]) -> CompiledExpression: ...
def validate_calculation(value: Mapping[str, object]) -> CompiledCalculation: ...
def validate_rule_document(
    value: Mapping[str, object], evidence_index: EvidenceIndex
) -> ValidatedRule: ...
```

The field registry initially permits only explicit paths such as `MedicalEvent.event_date`, `MedicalEvent.classification`, `MedicalEvent.admission_days`, `PolicyContract.contract_start`, `PolicyContract.contract_end`, `Rider.status`, `Rider.insured_amount`, and `ClaimHistory.counted_occurrence`. Unknown paths, dynamic indexing, URL/path fields, and raw text traversal fail validation.

### Python link/rule interfaces

```python
def confirm_rider_clause_link(
    scope: HouseholdScope,
    link_id: UUID,
    expected_version: int,
) -> RiderClauseLink: ...


def reject_rider_clause_link(
    scope: HouseholdScope,
    link_id: UUID,
    expected_version: int,
    reason_code: str,
) -> RiderClauseLink: ...


def publish_coverage_rule(
    scope: HouseholdScope,
    rule_id: UUID,
    version_id: UUID,
    expected_version: int,
) -> CoverageRuleVersion: ...
```

Before confirmation/publication, the service checks: Rider policy Evidence state, TermsEdition applicability on contract date, Clause parent DocumentVersion, exact Evidence page/bbox, candidate review state, and stale content hash. It never chooses the latest row merely because it has a larger ID or timestamp.

### HTTP contract

```text
GET  /api/v1/riders/{rider_id}/clause-links
POST /api/v1/rider-clause-links/{link_id}/confirm
POST /api/v1/rider-clause-links/{link_id}/reject
GET  /api/v1/coverage-rules/{rule_id}/versions
POST /api/v1/coverage-rules/{rule_id}/publish
GET  /api/v1/review-items?domain=rider_clause|coverage_rule
PATCH /api/v1/review-items/{review_id}/fields/{field_id}
```

Confirm/reject/publish request models use `extra="forbid"`, `frozen=True`, and `expected_version >= 1`. Publish never accepts an arbitrary DSL document from a client; it selects a stored candidate version after revalidation. Responses include bounded labels, status, version, Evidence IDs/pages, and reason codes, not raw Clause text or source path.

The review-item field endpoint accepts only generated field IDs and typed allowlist values for link selection, rule operator, fact field, unit, decimal/date boundary, and required/optional state. It creates a child candidate version and never accepts a free-form DSL string. A user-confirmed rule that still fails deterministic DSL/Evidence validation remains informational and non-executable.

### JSON Schema contract

`rider-clause-rules.v1.schema.json` defines strict objects for `RiderClauseLink`, `CoverageRuleVersion`, `RuleExpression`, `RuleEvidence`, and transition requests. Every object has `additionalProperties: false`. `RuleExpression` uses a discriminated allowlist for operators; no free-form executable string is permitted. The synthetic example has a policy Evidence page `1`, a terms Evidence page `2`, `rule_kind: "temporal"`, and a simple `date_between` expression with no private or actual values.

## Tasks

### Task 1: Define Rider-Clause and CoverageRule tables

**Status:** `completed`

**Files:**
- Create: `apps/api/migrations/versions/0006_rider_clause_rules.py`
- Create: `apps/api/tests/test_rider_clause_rules_migration.py`
- Test: `apps/api/tests/test_rider_clause_rules_migration.py`

**Interfaces:**
- Consumes: `0005_clause_search`, policy/Rider/Evidence tables, candidate versions, and Clause hierarchy.
- Produces: `revision = "0006_rider_clause_rules"`, `down_revision = "0005_clause_search"`, five new tables, FK/index/check constraints, and reverse-order downgrade.

- [x] **Step 1: Write failing migration tests.** Assert exact tables/columns, all UUID FKs, link/rule Evidence join uniqueness, version uniqueness, allowed review states, executable/status columns, and no duplicate Phase 1/Clause tables.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules_migration.py -q
  ```

  Expected: FAIL because `0006_rider_clause_rules.py` is absent.

- [x] **Step 3: Implement the additive Alembic migration.** Use direct `op.create_table` definitions, named constraints, FK delete behavior that preserves audit rows, and indexes on `(household_space_id, deleted_at)`, Rider, TermsEdition, Clause, and rule version.

- [x] **Step 4: Run migration tests and synthetic PostgreSQL upgrade.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules_migration.py -q
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  ```

  Expected: all shape tests pass and PostgreSQL reaches `0006_rider_clause_rules` without changing prior tables.

- [x] **Step 5: Commit the schema.**

  ```bash
  git add apps/api/migrations/versions/0006_rider_clause_rules.py apps/api/tests/test_rider_clause_rules_migration.py
  git commit -m "feat(db): add Rider clause rule schema"
  ```

### Task 2: Implement the data-only DSL validator

**Status:** `completed`

**Files:**
- Create: `apps/api/src/familycare_api/clauses/dsl.py`
- Create: `apps/api/tests/test_rule_dsl.py`
- Test: `apps/api/tests/test_rule_dsl.py`

**Interfaces:**
- Consumes: JSON Schema rule shape, Evidence index, and the fixed field/operator lists in this plan.
- Produces: `validate_field_path`, `validate_expression`, `validate_calculation`, `validate_rule_document`, `CompiledExpression`, `CompiledCalculation`, and stable validation reason codes.

- [x] **Step 1: Write failing decision-table tests.** Cover every allowed expression/calculation operator, unknown operator, unknown field, nested dynamic path, wrong type/unit, missing required field, unsupported cross-reference, conflicting definition, and arbitrary executable string.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rule_dsl.py -q
  ```

  Expected: FAIL because `familycare_api.clauses.dsl` and its compiler/validator symbols do not exist.

- [x] **Step 3: Implement the minimum recursive allowlist validator.** Reject keys outside the schema, require an operator at each expression node, validate operands against the field registry and unit registry, and return typed compiled data without evaluating a fact.

  ```python
  def validate_expression(value: Mapping[str, object]) -> CompiledExpression:
      if set(value) - {"op", "args", "field", "value", "unit"}:
          raise RuleValidationError("UNKNOWN_RULE_FIELD")
      operator = value.get("op")
      if operator not in EXPRESSION_OPERATORS:
          raise RuleValidationError("UNKNOWN_OPERATOR")
      fields = tuple(_collect_and_validate_fields(value))
      return CompiledExpression(
          operator=operator, operands=_compile_args(value), referenced_fields=fields
      )
  ```

- [x] **Step 4: Run DSL tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rule_dsl.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/clauses/dsl.py apps/api/tests/test_rule_dsl.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/clauses/dsl.py
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/clauses/dsl.py
  ```

  Expected: every allowlist row passes and every unsupported construct fails with a stable reason code.

- [x] **Step 5: Commit the validator.**

  ```bash
  git add apps/api/src/familycare_api/clauses/dsl.py apps/api/tests/test_rule_dsl.py
  git commit -m "feat(rules): validate coverage DSL allowlist"
  ```

### Task 3: Implement scoped Rider-Clause link validation

**Status:** `completed`

**Files:**
- Create: `apps/api/src/familycare_api/clauses/links.py`
- Modify: `apps/api/src/familycare_api/clauses/repository.py`
- Modify: `apps/api/src/familycare_api/clauses/service.py`
- Create: `apps/api/tests/test_rider_clause_rules.py`
- Test: `apps/api/tests/test_rider_clause_rules.py`

**Interfaces:**
- Consumes: policy/Rider repository, TermsEdition/Clause repository, Evidence validation, candidate review status, and `HouseholdScope`.
- Produces: `RiderClauseLink`, `list_rider_clause_links`, `confirm_rider_clause_link`, `reject_rider_clause_link`, and deterministic applicability/error codes.

- [x] **Step 1: Write failing link tests.** Cover verified policy Rider success, Terms-only Rider rejection, wrong contract-date TermsEdition, Clause from another DocumentVersion, missing/stale Evidence, conflicting common/special terms, cross-household denial, soft delete, and stale link version.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules.py -q
  ```

  Expected: FAIL because link domain/service functions are not implemented.

- [x] **Step 3: Implement validation before state transition.** Load all parents under the server scope, verify policy Evidence and TermsEdition applicability, validate every Evidence reference, and transition only after all invariants pass. Preserve a failed candidate as `NEEDS_REVIEW` with a reason code rather than selecting a different row.

- [x] **Step 4: Run link tests and direct SQL static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/clauses/links.py apps/api/src/familycare_api/clauses/repository.py apps/api/src/familycare_api/clauses/service.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/clauses
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/clauses
  ```

  Expected: link invariant tests pass and no untyped/unsafe SQL path remains.

- [x] **Step 5: Commit the link validator.**

  ```bash
  git add apps/api/src/familycare_api/clauses/links.py apps/api/src/familycare_api/clauses/repository.py apps/api/src/familycare_api/clauses/service.py apps/api/tests/test_rider_clause_rules.py
  git commit -m "feat(clauses): validate Rider clause links"
  ```

### Task 4: Implement CoverageRule versions and executable publication

**Status:** `completed`

**Files:**
- Create: `apps/api/src/familycare_api/clauses/rules.py`
- Modify: `apps/api/src/familycare_api/clauses/schemas.py`
- Modify: `apps/api/src/familycare_api/clauses/repository.py`
- Create: `apps/api/tests/test_rule_publication.py`
- Test: `apps/api/tests/test_rule_publication.py`

**Interfaces:**
- Consumes: validated DSL/compiler, Rider-Clause links, candidate versions, and Evidence index.
- Produces: `CoverageRule`, `CoverageRuleVersion`, `list_rule_versions`, `publish_coverage_rule`, and the invariant that only `AI_VERIFIED`/`USER_CONFIRMED` validated versions become executable.

- [x] **Step 1: Write failing publication tests.** Assert `NEEDS_REVIEW`, unsupported DSL, invented/mismatched Evidence, wrong schema version, stale candidate, and missing Clause Evidence all remain non-executable; assert two-stage verified candidate plus exact Evidence publishes atomically.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rule_publication.py -q
  ```

  Expected: FAIL because rule repository/publisher functions are absent.

- [x] **Step 3: Implement the minimum transactional publisher.** Revalidate the stored candidate and Evidence inside one `SELECT ... FOR UPDATE` transaction, write a new immutable version, and set `executable = true` only for the two approved review states.

  ```python
  APPROVED_STATES = frozenset({"AI_VERIFIED", "USER_CONFIRMED"})


  def can_execute(review_state: str, validation: ValidationOutcome) -> bool:
      return review_state in APPROVED_STATES and validation.valid and validation.evidence_complete
  ```

- [x] **Step 4: Run publication tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rule_publication.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/clauses/rules.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/clauses
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/clauses
  ```

  Expected: executable-state and atomic-version tests pass; unsupported rules remain informational.

- [x] **Step 5: Commit rule publication.**

  ```bash
  git add apps/api/src/familycare_api/clauses/rules.py apps/api/src/familycare_api/clauses/schemas.py apps/api/src/familycare_api/clauses/repository.py apps/api/tests/test_rule_publication.py
  git commit -m "feat(rules): publish verified coverage versions"
  ```

### Task 5: Add HTTP contracts, integration tests, and privacy checks

**Status:** `completed`

**Files:**
- Modify: `apps/api/src/familycare_api/clauses/router.py`
- Modify: `apps/api/src/familycare_api/clauses/schemas.py`
- Modify: `apps/api/src/familycare_api/clauses/errors.py`
- Modify: `apps/api/src/familycare_api/policies/candidate_service.py`
- Modify: `apps/api/src/familycare_api/policies/candidate_router.py`
- Create: `packages/contracts/schemas/rider-clause-rules.v1.schema.json`
- Create: `packages/contracts/examples/rider-clause-rules.v1.json`
- Create: `apps/api/tests/test_rider_clause_rules_api.py`
- Create: `apps/api/tests/test_rider_clause_rules_integration.py`
- Create: `apps/api/tests/test_rider_clause_rules_privacy.py`
- Modify: `scripts/check_contracts.py` through the root integration step
- Test: `apps/api/tests/test_rider_clause_rules_api.py`
- Test: `apps/api/tests/test_rider_clause_rules_integration.py`
- Test: `apps/api/tests/test_rider_clause_rules_privacy.py`

**Interfaces:**
- Consumes: link/rule services and publisher from Tasks 3–4.
- Produces: strict link/rule routes, `rider-clause-rules.v1`, PostgreSQL transaction proof, and response/log redaction.

- [x] **Step 1: Write failing API/contract/integration tests.** Assert route status/error envelopes, expected version handling, generic review domains and typed child-version correction, link/rule Evidence fields, no raw DSL/path/text output, synthetic PostgreSQL publish atomicity, and inability to access another household.

- [x] **Step 2: Run the focused RED commands.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules_api.py apps/api/tests/test_rider_clause_rules_integration.py -q
  ```

  Expected: FAIL because the routes, schema artifact, and integration transactions are incomplete.

- [x] **Step 3: Implement strict route adapters and the JSON Schema.** Confirm/reject/publish endpoints accept only expected version and bounded reason metadata. Extend the generic candidate review service with the two new domains and generated typed field IDs; corrections always create child versions. The client cannot submit a new authoritative rule body or household ID.

- [x] **Step 4: Run the complete focused suite.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules_migration.py apps/api/tests/test_rule_dsl.py apps/api/tests/test_rider_clause_rules.py apps/api/tests/test_rule_publication.py apps/api/tests/test_rider_clause_rules_api.py apps/api/tests/test_rider_clause_rules_privacy.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_rider_clause_rules_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/clauses apps/api/tests/test_rider_clause_rules_migration.py apps/api/tests/test_rule_dsl.py apps/api/tests/test_rider_clause_rules.py apps/api/tests/test_rule_publication.py apps/api/tests/test_rider_clause_rules_api.py apps/api/tests/test_rider_clause_rules_integration.py apps/api/tests/test_rider_clause_rules_privacy.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/clauses apps/api/tests
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/clauses
  ```

  Expected: migration, DSL, link, publish, HTTP, PostgreSQL, contract, and privacy checks pass with no external AI.

- [x] **Step 5: Commit the complete rule boundary.**

  ```bash
  git add apps/api/src/familycare_api/clauses packages/contracts/schemas/rider-clause-rules.v1.schema.json packages/contracts/examples/rider-clause-rules.v1.json apps/api/tests/test_rider_clause_rules_api.py apps/api/tests/test_rider_clause_rules_integration.py apps/api/tests/test_rider_clause_rules_privacy.py
  git commit -m "feat(clauses): add Rider rule publication boundary"
  ```

### Task 6: Add Rider-Clause and CoverageRule review screens

**Status:** `completed`

**Files:**
- Create: `apps/web/src/api/rules.ts`
- Create: `apps/web/src/features/clauses/RuleReviewPage.tsx`
- Create: `apps/web/src/features/clauses/RiderClauseReviewDialog.tsx`
- Create: `apps/web/src/features/clauses/CoverageRuleReviewDialog.tsx`
- Create: `apps/web/src/features/clauses/RuleExpressionEditor.tsx`
- Create: `apps/web/src/features/clauses/rule-review.test.tsx`
- Create: `apps/web/e2e/rule-review.spec.ts`
- Modify: `apps/web/src/app/AppRoutes.tsx`
- Modify: `apps/web/src/styles.css`

**Interfaces:**
- Consumes: generated generic review-item, link confirm/reject, typed correction, rule version, and publish operations.
- Produces: `/app/clauses/review`, separate exception queues for Rider-Clause links and CoverageRules, exact Evidence disclosure, typed correction, confirmation/rejection, and publication state.
- The UI does not offer a textarea or code editor for DSL; it renders only generated field/operator/unit options and never marks an unsupported user-confirmed rule executable.

- [x] **Step 1: Write failing review, conflict, and accessibility tests.** Cover `AI_VERIFIED` stored-version publication eligibility, `NEEDS_REVIEW` queue visibility, terms-only Rider rejection, wrong edition, stale Evidence, typed correction as a child version, `409 VERSION_CONFLICT` draft preservation, unsupported DSL remaining informational, dialog focus, and no raw Clause/provider/path output.

  ```bash
  corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
    src/features/clauses/rule-review.test.tsx
  ```

  Expected: FAIL because the generated rule client and review components do not exist.

- [x] **Step 2: Implement the no-store generated client and typed review UI.** Reuse the Evidence drawer and memory-only query cache from Plan 005. Invalidate only the affected review/link/rule keys after mutations. Map stable reason codes to safe copy, preserve unsaved typed values on conflict, and require fresh Evidence before enabling confirm/publish.

- [x] **Step 3: Run GREEN and the synthetic browser flow.**

  ```bash
  corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
    src/features/clauses/rule-review.test.tsx
  corepack pnpm@11.22.0 --filter @familycare/web exec playwright test \
    --workers=1 e2e/rule-review.spec.ts
  corepack pnpm@11.22.0 web:check
  ```

  Expected: typed correction, conflict recovery, Evidence/focus behavior, and browser privacy checks pass with wholly synthetic data.

- [x] **Step 4: Commit the review UI.**

  ```bash
  git add apps/web/src/api/rules.ts apps/web/src/features/clauses \
    apps/web/src/app/AppRoutes.tsx apps/web/src/styles.css \
    apps/web/e2e/rule-review.spec.ts
  git commit -m "feat(web): review Rider clause rules"
  ```

## Focused Post-Merge Verification

- [ ] **Step 1: Verify the migration chain and rule tables on merged `main`.**

  ```bash
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules_migration.py apps/api/tests/test_rule_dsl.py apps/api/tests/test_rider_clause_rules.py apps/api/tests/test_rule_publication.py -q
  ```

  Expected: the chain reaches `0006_rider_clause_rules` or a later descendant and only approved validated versions are executable.

- [ ] **Step 2: Verify PostgreSQL, contracts, and privacy after merge.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_rider_clause_rules_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run pytest apps/api/tests/test_rider_clause_rules_api.py apps/api/tests/test_rider_clause_rules_privacy.py -q
  ```

  Expected: wrong edition, wrong Evidence, unsupported DSL, cross-scope access, stale version, and raw-text/path leakage tests all pass.

- [ ] **Step 3: Apply the shared Root PR gate.** Follow `docs/plan/003-v0.1-implementation-index.md`: inspect the full diff once immediately before push, run its serial repository checks, wait for required CI, merge, and record PR URL, merge commit, Actions result, and all inaccessible real-data/device checks.
