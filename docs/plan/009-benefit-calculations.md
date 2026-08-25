# Benefit Calculations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Decimal-based fixed-benefit and indemnity calculation traces, manual receipt-line inputs, partial results, and multiple-indemnity handling without presenting an unsupported amount as guaranteed payment.

**Architecture:** Extend the existing `familycare_api.decisions` boundary after the tri-state engine. Persist only normalized manual `ReceiptLine` values and immutable `BenefitCalculation`/step rows; call pure fixed/indemnity calculators from the service; and attach results to `ClaimCandidate` without changing rule evaluation semantics. The calculation layer reads verified executable rules and confirmed facts, uses `Decimal`, and explicitly separates confirmed, additional, and excluded amounts. It never sums independent indemnity contracts when allocation is unknown.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, direct `psycopg` 3.3 SQL, PostgreSQL 18 `NUMERIC`, Alembic 1.19, JSON Schema Draft 2020-12, pytest, Ruff, and strict mypy.

**Spec:** `docs/design/coverage-decision-engine.md`, `docs/design/data-model.md`, `docs/design/event-result-pwa.md`, `docs/design/v0.1-product.md`, and `docs/design/test-strategy.md`

## Global Constraints

- Migration `0008_benefit_calculations.py` revises `0007_coverage_decision_engine`; all Phase 1 document/extraction/job tables, contracts, and states remain unchanged.
- Use direct `psycopg` SQL and PostgreSQL `NUMERIC`; do not introduce SQLAlchemy ORM, float arithmetic, SQLite monetary tests, Redis, or external calculation services.
- Every receipt/calculation query uses server-derived `HouseholdScope`; client-provided household IDs are not authoritative.
- Receipt lines are manual structured metadata only. No receipt image/PDF, medical document, OCR output, external file path, diagnosis text, or insurer file identifier is stored.
- Every calculation carries the applicable rule version and Policy/Clause `Evidence` references; missing or stale Evidence keeps the result conditional and never creates a guaranteed amount.
- Use `Decimal` for every amount and explicit currency/rounding rules. Negative amounts, overflow, unknown units, and currency mismatches are validation errors, not zero or `NO_MATCH`.
- Only executable `AI_VERIFIED`/`USER_CONFIRMED` CoverageRule versions can drive a calculation. AI never returns or overrides a calculation result.
- `MATCH`, `NO_MATCH`, and `UNKNOWN` remain the deterministic engine vocabulary. Missing calculation inputs produce a calculation `UNKNOWN`/hold state while preserving the underlying RuleEvaluation.
- Fixed-benefit and indemnity paths are separate. A calculation never mixes insured fixed amounts with receipt reimbursement.
- Partial indemnity results expose confirmed amount, additional-confirmation amount, excluded amount/reasons, deductible/rate/limit, and every intermediate step.
- Multiple indemnity contracts are shown independently with shared claim-review categories; their independent estimates are not summed and proportional allocation remains `UNKNOWN`.
- All changes use expected version and soft delete/restore where applicable. Stale writes return `409 VERSION_CONFLICT`.
- CI uses synthetic receipt lines and synthetic policy/rule values only. Logs and responses omit raw notes, full medical input, path, password, token, and private identifiers.

## File Responsibility Map

```text
apps/api/migrations/versions/0008_benefit_calculations.py
  Creates receipt_lines, benefit_calculations, and immutable
  benefit_calculation_steps.

apps/api/src/familycare_api/decisions/calculations.py
  Defines Decimal money, fixed-benefit, indemnity, partial-result, and
  multiple-indemnity pure calculation functions.

apps/api/src/familycare_api/decisions/calculation_validation.py
  Validates receipt line categories, currency, amounts, units, and rule inputs.

apps/api/src/familycare_api/decisions/calculation_repository.py
  Persists scoped receipt/calculation/step rows through direct psycopg SQL.

apps/api/src/familycare_api/decisions/calculation_service.py
  Coordinates candidate lookup, rule/evidence checks, calculation persistence,
  and version conflicts without owning the tri-state engine.

apps/api/src/familycare_api/decisions/calculation_schemas.py
  Defines strict HTTP and contract adapters for receipt/calculation data.

apps/api/src/familycare_api/decisions/router.py
  Adds receipt-line and calculation-result endpoints to the decision boundary.

packages/contracts/schemas/benefit-calculation.v1.schema.json
packages/contracts/examples/benefit-calculation.v1.json
  Define strict calculation transport objects and synthetic examples.

apps/api/tests/test_benefit_calculations_migration.py
apps/api/tests/test_benefit_calculations.py
apps/api/tests/test_receipt_lines_api.py
apps/api/tests/test_benefit_contracts.py
apps/api/tests/test_benefit_integration.py
apps/api/tests/test_benefit_privacy.py
  Cover schema, Decimal decision tables, HTTP, PostgreSQL persistence, and
  sensitive-data boundaries.
```

`apps/api/src/familycare_api/main.py`, `apps/api/src/familycare_api/errors.py`, `scripts/check_contracts.py`, and generated OpenAPI are root integration files. The root agent registers additional routes and regenerates contracts after the calculation-focused tests pass.

## Database, Python, HTTP, and JSON Interfaces

### Migration contract

```text
receipt_lines(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  medical_event_id uuid references medical_events(id),
  category varchar(32) not null,
  coverage_category varchar(32) not null,
  amount numeric(18,2) not null,
  currency char(3) not null,
  confirmation_level varchar(32) not null,
  note_code varchar(64) null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

benefit_calculations(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  claim_candidate_id uuid references claim_candidates(id),
  calculation_kind varchar(16) not null,
  status varchar(16) not null,
  currency char(3) null,
  confirmed_amount numeric(18,2) null,
  additional_amount numeric(18,2) null,
  excluded_amount numeric(18,2) null,
  deductible_amount numeric(18,2) null,
  applied_rate numeric(9,6) null,
  applied_limit numeric(18,2) null,
  rounding_rule varchar(32) null,
  hold_reason_code varchar(64) null,
  excluded_reason_codes varchar(64)[] not null default '{}',
  rule_version_id uuid references coverage_rule_versions(id),
  engine_version varchar(64) not null,
  version integer not null default 1,
  created_at timestamptz not null
)

benefit_calculation_steps(
  id uuid primary key,
  benefit_calculation_id uuid references benefit_calculations(id),
  step_number integer not null,
  operation varchar(32) not null,
  input_amount numeric(18,6) null,
  input_currency char(3) null,
  output_amount numeric(18,6) null,
  output_currency char(3) null,
  rounding_rule varchar(32) null,
  reason_code varchar(64) not null,
  unique (benefit_calculation_id, step_number)
)
```

Named checks enforce `category = outpatient|inpatient|pharmacy`, `coverage_category = covered|possible_excluded|excluded|unknown`, `confirmation_level = user|ai_structured|unconfirmed`, `calculation_kind = fixed|indemnity`, `status = computed|partial|unknown`, positive versions, valid ISO currency/reason-code shapes, at most 16 exclusion reasons, and non-negative amounts. Calculation step rows are immutable; a reanalysis creates a new calculation row/version rather than updating historical steps.

### Python interfaces

```python
@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class ReceiptLine:
    line_id: UUID
    category: Literal["outpatient", "inpatient", "pharmacy"]
    coverage_category: Literal["covered", "possible_excluded", "excluded", "unknown"]
    amount: Money
    confirmation_level: Literal["user", "ai_structured", "unconfirmed"]
    note_code: str | None
    version: int


@dataclass(frozen=True)
class CalculationStep:
    step_number: int
    operation: str
    input_amount: Money | None
    output_amount: Money | None
    rounding_rule: str | None
    reason_code: str


@dataclass(frozen=True)
class BenefitCalculationResult:
    kind: Literal["fixed", "indemnity"]
    status: Literal["computed", "partial", "unknown"]
    confirmed: Money | None
    additional: Money | None
    excluded: Money | None
    deductible: Money | None
    applied_rate: Decimal | None
    applied_limit: Money | None
    steps: tuple[CalculationStep, ...]
    hold_reason_codes: tuple[str, ...]
```

Pure functions:

```python
def validate_receipt_line(line: ReceiptLine) -> None: ...
def calculate_fixed_benefit(
    candidate: ClaimCandidate, rule: CoverageRuleVersion, facts: FactContext
) -> BenefitCalculationResult: ...
def calculate_indemnity(
    candidate: ClaimCandidate,
    lines: Sequence[ReceiptLine],
    rule: CoverageRuleVersion,
    facts: FactContext,
) -> BenefitCalculationResult: ...
def split_confirmed_additional_excluded(lines: Sequence[ReceiptLine]) -> ReceiptBreakdown: ...
def detect_multiple_indemnity_contracts(
    candidates: Sequence[ClaimCandidate],
) -> MultipleIndemnityResult: ...
```

Fixed formula example, evaluated only after rule and Evidence validation:

```python
calculation = validate_and_compile_rule(rule.rule_document)
currency = confirmed_fact("Rider.currency")
intermediate = evaluate_decimal(calculation, confirmed_facts)
final = require_explicit_rounding(intermediate, calculation, currency)
```

Every intermediate value is recorded as a `CalculationStep`. Indemnity formula is similarly explicit:

```python
confirmed = sum_money(confirmed_lines)
eligible = max_money(Money(Decimal("0"), currency), confirmed - deductible)
reimbursed = eligible * applied_rate
limited = min_money(reimbursed, applied_limit)
final = round_money(limited, rounding_rule, currency)
```

If a line or condition is not confirmed, preserve it in `additional` or `excluded` rather than inventing a value. If more than one independent indemnity candidate exists, return `status="unknown"` for allocation and do not add per-contract estimates.

### HTTP contract

```text
POST   /api/v1/medical-events/{event_id}/receipt-lines
GET    /api/v1/medical-events/{event_id}/receipt-lines
PATCH  /api/v1/medical-events/{event_id}/receipt-lines/{line_id}
DELETE /api/v1/medical-events/{event_id}/receipt-lines/{line_id}
GET    /api/v1/medical-events/{event_id}/calculations
```

Request models use `ConfigDict(extra="forbid", frozen=True)`, Decimal string amounts, three-letter uppercase currency, explicit category/confirmation, bounded `note_code`, and expected version for update/delete. A client cannot submit `confirmed_amount`, `applied_rate`, or a rule version as authoritative calculation output. `GET .../receipt-lines` returns the active, versioned manual metadata needed to reopen the editor. `GET .../calculations` returns a `BenefitCalculationsResponse` envelope with Decimal-string Money objects, calculation IDs/version/created time, rule/evidence IDs, bounded calculation steps, hold/exclusion reason codes, and `Cache-Control: no-store`; neither endpoint returns receipt documents, free-form notes, or full medical text.

### JSON Schema contract

`benefit-calculation.v1.schema.json` is a strict `schema_version: "1"` envelope whose bounded `calculations` entries require `kind`, `status`, currency/amount objects, rule/evidence lineage, steps, and hold reasons with `additionalProperties: false`. Amounts and rates are Decimal strings matching fixed non-negative patterns; negative values, exponents, and numeric JSON amounts are rejected. `status` is exactly `computed`, `partial`, or `unknown`. The synthetic example includes one fixed calculation with intermediate steps and one partial indemnity calculation with confirmed/additional/excluded amounts; it does not contain a file, path, diagnosis, or raw note.

## Implementation status in this worktree

Tasks 1–6 are implemented and locally verified in the current branch: migration `0008`, Decimal primitives and validators, fixed/indemnity calculators, household-scoped PostgreSQL persistence, receipt-line/calculation HTTP routes, OpenAPI registration, the strict transport contract, synthetic PostgreSQL integration, and privacy assertions. The Focused Post-Merge Verification section remains intentionally unchecked; no PR, CI, merge, release, or real-data/device verification is implied here.

## Tasks

### Task 1: Define receipt/calculation tables and migration tests

**Files:**
- Create: `apps/api/migrations/versions/0008_benefit_calculations.py`
- Create: `apps/api/tests/test_benefit_calculations_migration.py`
- Test: `apps/api/tests/test_benefit_calculations_migration.py`

**Interfaces:**
- Consumes: `0007_coverage_decision_engine`, claim candidates, executable rule versions, and migration-spy conventions.
- Produces: `revision = "0008_benefit_calculations"`, `down_revision = "0007_coverage_decision_engine"`, three tables, Decimal columns/checks, immutable step uniqueness, and reverse-order downgrade.

- [x] **Step 1: Write failing migration tests.** Assert exact tables, FKs, numeric precision/scale, category/status checks, soft-delete/version fields, step uniqueness, no file/path/text columns, and preservation of Phase 1/decision tables.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations_migration.py -q
  ```

  Expected: FAIL because `0008_benefit_calculations.py` is absent.

- [x] **Step 3: Implement the additive Alembic migration.** Use PostgreSQL `NUMERIC(18,2)`/`NUMERIC(18,6)`, named constraints, no binary/path columns, and a unique `(benefit_calculation_id, step_number)` constraint.

- [x] **Step 4: Run migration tests and upgrade.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations_migration.py -q
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  ```

  Expected: shape tests pass and synthetic PostgreSQL reaches `0008_benefit_calculations`.

- [x] **Step 5: Commit the migration.**

  ```bash
  git add apps/api/migrations/versions/0008_benefit_calculations.py apps/api/tests/test_benefit_calculations_migration.py
  git commit -m "feat(db): add benefit calculation schema"
  ```

### Task 2: Implement Decimal money and receipt validation

**Files:**
- Create: `apps/api/src/familycare_api/decisions/calculations.py`
- Create: `apps/api/src/familycare_api/decisions/calculation_validation.py`
- Create: `apps/api/tests/test_benefit_calculations.py`
- Test: `apps/api/tests/test_benefit_calculations.py`

**Interfaces:**
- Consumes: decision `ClaimCandidate`/`FactContext` types and rule units from `familycare_api.clauses.dsl`.
- Produces: `Money`, `ReceiptLine`, `CalculationStep`, `BenefitCalculationResult`, `validate_receipt_line`, `round_money`, and safe Decimal conversion.

- [x] **Step 1: Write failing Decimal/validation tests.** Cover string-to-Decimal conversion, precision/scale, negative/overflow, zero, currency case, mismatch, invalid category/confirmation, and explicit `ROUND_HALF_UP`/rule-selected rounding.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations.py -q
  ```

  Expected: FAIL because calculation types and Decimal validation functions are absent.

- [x] **Step 3: Implement Decimal-only primitives.** Reject float input, quantize only at the explicitly requested rounding boundary, preserve currency on every Money value, and raise stable validation codes.

  ```python
  def decimal_from_wire(value: str) -> Decimal:
      if not isinstance(value, str):
          raise InvalidAmount("AMOUNT_NOT_DECIMAL_STRING")
      parsed = Decimal(value)
      if not parsed.is_finite() or parsed < 0 or parsed.as_tuple().exponent < -6:
          raise InvalidAmount("INVALID_AMOUNT")
      return parsed
  ```

- [x] **Step 4: Run calculation unit tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions/calculations.py apps/api/src/familycare_api/decisions/calculation_validation.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: every Decimal/validation boundary passes without float arithmetic.

- [x] **Step 5: Commit the monetary primitives.**

  ```bash
  git add apps/api/src/familycare_api/decisions/calculations.py apps/api/src/familycare_api/decisions/calculation_validation.py apps/api/tests/test_benefit_calculations.py
  git commit -m "feat(calculations): add Decimal money primitives"
  ```

### Task 3: Implement fixed-benefit calculation with a complete trace

**Files:**
- Modify: `apps/api/src/familycare_api/decisions/calculations.py`
- Modify: `apps/api/tests/test_benefit_calculations.py`
- Test: `apps/api/tests/test_benefit_calculations.py`

**Interfaces:**
- Consumes: validated Decimal money, an executable fixed/rate rule, confirmed Rider facts, and a `MATCH` ClaimCandidate.
- Produces: `calculate_fixed_benefit` with validated DSL arithmetic, explicit rounding, ordered intermediate steps, and a final conditional estimate or value-free `UNKNOWN` reason.

- [x] **Step 1: Add failing fixed decision-table tests.** Cover nested rate and fixed-literal DSL formulas, `MATCH`/rule/fact guards, currency validation, explicit rounding, unsupported/overflow values, and complete ordered traces.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations.py -k fixed -q
  ```

  Expected: FAIL because `calculate_fixed_benefit` is not implemented.

- [x] **Step 3: Implement the fixed calculator.** Require a `MATCH` candidate, published executable approved rule/evidence, and confirmed required facts; evaluate only the allowlisted Decimal DSL and preserve every operation as an ordered `CalculationStep`; return value-free `UNKNOWN` for missing or unsafe inputs.

  ```python
  def calculate_fixed_benefit(candidate, rule, facts):
      if candidate.aggregate_result != "MATCH":
          return BenefitCalculationResult.unknown("CANDIDATE_NOT_MATCHED")
      calculation = validate_and_compile_rule(rule.rule_document)
      amount = evaluate_decimal(calculation, confirmed_facts=facts)
      return BenefitCalculationResult.computed_with_steps(amount)
  ```

- [x] **Step 4: Run fixed tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations.py -k fixed -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions/calculations.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions/calculations.py
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions/calculations.py
  ```

  Expected: fixed formulas, missing facts, and trace rows pass.

- [x] **Step 5: Commit fixed-benefit behavior.**

  ```bash
  git add apps/api/src/familycare_api/decisions/calculations.py apps/api/tests/test_benefit_calculations.py
  git commit -m "feat(calculations): add fixed benefit calculator"
  ```

### Task 4: Implement partial indemnity and multiple-contract behavior

**Files:**
- Modify: `apps/api/src/familycare_api/decisions/calculations.py`
- Modify: `apps/api/src/familycare_api/decisions/calculation_validation.py`
- Modify: `apps/api/tests/test_benefit_calculations.py`
- Test: `apps/api/tests/test_benefit_calculations.py`

**Interfaces:**
- Consumes: validated `ReceiptLine`, indemnity eligibility/deductible/rate/limit rule, and multiple ClaimCandidates.
- Produces: `calculate_indemnity`, `split_confirmed_additional_excluded`, and `detect_multiple_indemnity_contracts` with partial/unknown results.

- [x] **Step 1: Add failing indemnity tests.** Cover no lines, confirmed covered lines, possible/unknown/unconfirmed additional amounts, excluded amounts, deductible/rate/limit, currency mismatch, partial data, validation errors, and two independent indemnity contracts without summing.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations.py -k indemnity -q
  ```

  Expected: FAIL because indemnity and multiple-contract functions are incomplete.

- [x] **Step 3: Implement the minimum partial calculator.** Separate line categories first, calculate only confirmed eligible amounts, preserve additional/excluded values, apply the explicit deductible/rate/limit/round sequence, and return `UNKNOWN` allocation when independent contracts coexist.

  ```python
  def detect_multiple_indemnity_contracts(candidates):
      indemnity = tuple(
          item
          for item in candidates
          if item.rider_type == "indemnity" and item.aggregate_result != "NO_MATCH"
      )
      if len(indemnity) > 1:
          return MultipleIndemnityResult(
              allocation="UNKNOWN", candidate_ids=tuple(item.id for item in indemnity)
          )
      return MultipleIndemnityResult(
          allocation="SINGLE" if indemnity else "NONE",
          candidate_ids=tuple(item.id for item in indemnity),
      )
  ```

- [x] **Step 4: Run indemnity tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations.py -k indemnity -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions/calculations.py apps/api/src/familycare_api/decisions/calculation_validation.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: partial confirmed/additional/excluded outputs pass and no contract amounts are summed across independent indemnity Riders.

- [x] **Step 5: Commit indemnity behavior.**

  ```bash
  git add apps/api/src/familycare_api/decisions/calculations.py apps/api/src/familycare_api/decisions/calculation_validation.py apps/api/tests/test_benefit_calculations.py
  git commit -m "feat(calculations): handle partial indemnity"
  ```

### Task 5: Persist receipt/calculation traces and expose HTTP contracts

**Files:**
- Create: `apps/api/src/familycare_api/decisions/calculation_repository.py`
- Create: `apps/api/src/familycare_api/decisions/calculation_service.py`
- Create: `apps/api/src/familycare_api/decisions/calculation_schemas.py`
- Modify: `apps/api/src/familycare_api/decisions/router.py`
- Create: `packages/contracts/schemas/benefit-calculation.v1.schema.json`
- Create: `packages/contracts/examples/benefit-calculation.v1.json`
- Create: `apps/api/tests/test_receipt_lines_api.py`
- Create: `apps/api/tests/test_benefit_contracts.py`
- Modify: `apps/api/src/familycare_api/main.py` through root integration
- Modify: `apps/api/src/familycare_api/errors.py` through root integration
- Modify: `scripts/check_contracts.py` through root integration
- Test: `apps/api/tests/test_receipt_lines_api.py`
- Test: `apps/api/tests/test_benefit_contracts.py`

**Interfaces:**
- Consumes: pure calculators, `medical_events`, `claim_candidates`, and `0008` tables.
- Produces: receipt-line CRUD, calculation retrieval, strict schemas, and the five HTTP operations in this plan.

- [x] **Step 1: Write failing HTTP/contract tests.** Assert extra fields are rejected, client cannot submit authoritative result fields, expected-version conflict is sanitized, Decimal wire values round-trip, response includes steps/hold reasons, and no file/path/raw note appears.

- [x] **Step 2: Run the focused RED tests.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_receipt_lines_api.py apps/api/tests/test_benefit_contracts.py -q
  ```

  Expected: FAIL because calculation repository/service/schemas/routes and contract artifacts are absent.

- [x] **Step 3: Implement scoped persistence and strict adapters.** Use `SELECT ... FOR UPDATE` for receipt updates, create immutable calculation/step rows per run, and accept only manual receipt metadata. Return `unknown` calculation state rather than silently calculating from incomplete data.

- [x] **Step 4: Run HTTP, contract, and API static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_receipt_lines_api.py apps/api/tests/test_benefit_contracts.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: strict API/schema tests and static checks pass; OpenAPI has no calculation route drift.

- [x] **Step 5: Commit the calculation boundary.**

  ```bash
  git add apps/api/src/familycare_api/decisions/calculation_repository.py apps/api/src/familycare_api/decisions/calculation_service.py apps/api/src/familycare_api/decisions/calculation_schemas.py apps/api/src/familycare_api/decisions/router.py packages/contracts/schemas/benefit-calculation.v1.schema.json packages/contracts/examples/benefit-calculation.v1.json apps/api/tests/test_receipt_lines_api.py apps/api/tests/test_benefit_contracts.py
  git commit -m "feat(api): expose benefit calculation traces"
  ```

### Task 6: Verify PostgreSQL persistence, partial results, and privacy

**Files:**
- Create: `apps/api/tests/test_benefit_integration.py`
- Create: `apps/api/tests/test_benefit_privacy.py`
- Modify: `apps/api/tests/test_benefit_calculations.py` for any integration-discovered deterministic edge case
- Test: `apps/api/tests/test_benefit_integration.py`
- Test: `apps/api/tests/test_benefit_privacy.py`

**Interfaces:**
- Consumes: all calculation primitives, repositories, HTTP contract, and `0008` migration.
- Produces: PostgreSQL proof of immutable steps, partial confirmed/additional/excluded results, multiple-indemnity unknown allocation, scope/version isolation, and no medical-document/log/cache leakage.

- [x] **Step 1: Write failing integration/privacy assertions.** Insert synthetic event/candidates/rules/receipt lines, calculate fixed and partial indemnity results, re-run with a changed rule, and assert the old trace remains immutable and multi-contract allocation is `UNKNOWN`.

- [x] **Step 2: Run the focused RED integration command.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_benefit_integration.py -q
  ```

  Expected: FAIL until real PostgreSQL transactions, immutable step storage, and calculation result persistence are complete.

- [x] **Step 3: Implement only missing transaction/redaction paths.** Keep receipt update/delete scoped and versioned; write calculation header and all steps in one transaction; omit raw notes/diagnoses/files from logs and response exceptions.

- [x] **Step 4: Run the complete focused feature suite.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations_migration.py apps/api/tests/test_benefit_calculations.py apps/api/tests/test_receipt_lines_api.py apps/api/tests/test_benefit_contracts.py apps/api/tests/test_benefit_privacy.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_benefit_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions apps/api/tests/test_benefit_calculations_migration.py apps/api/tests/test_benefit_calculations.py apps/api/tests/test_receipt_lines_api.py apps/api/tests/test_benefit_contracts.py apps/api/tests/test_benefit_integration.py apps/api/tests/test_benefit_privacy.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions apps/api/tests
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: all Decimal, fixed, indemnity, partial, multiple-contract, PostgreSQL, contract, privacy, and static checks pass without external providers.

- [x] **Step 5: Commit the complete calculation acceptance.**

  ```bash
  git add apps/api/tests/test_benefit_integration.py apps/api/tests/test_benefit_privacy.py apps/api/tests/test_benefit_calculations.py
  git commit -m "test(calculations): verify monetary result boundaries"
  ```

## Focused Post-Merge Verification

- [ ] **Step 1: Verify migration and Decimal decision tables on merged `main`.**

  ```bash
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_calculations_migration.py apps/api/tests/test_benefit_calculations.py apps/api/tests/test_receipt_lines_api.py apps/api/tests/test_benefit_contracts.py -q
  ```

  Expected: the chain reaches `0008_benefit_calculations` or a later descendant and all monetary outputs retain explicit Decimal/currency/rounding details.

- [ ] **Step 2: Verify PostgreSQL partial/multiple indemnity and privacy.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_benefit_integration.py -q
  TMPDIR=/tmp uv run pytest apps/api/tests/test_benefit_privacy.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: partial results preserve confirmed/additional/excluded amounts, independent indemnity estimates are not summed, allocation is `UNKNOWN`, and sensitive values are absent from logs/responses.

- [ ] **Step 3: Apply the shared Root PR gate.** Follow `docs/plan/003-v0.1-implementation-index.md`: inspect the whole diff once immediately before push, run its serial repository gate, wait for required CI, merge, and record PR URL, merge commit, Actions result, and unverified real-data/device boundaries.
