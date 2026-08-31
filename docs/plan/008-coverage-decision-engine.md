# Coverage Decision Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Status:** Complete — PR #19 merged as `0b51e1d2161040c5fa37971fdd82ffa8a6c1687f`; the later
private-knowledge v2 stream preserves this tri-state authority.

**Goal:** Evaluate structured MedicalEvent facts against actually subscribed Riders and executable CoverageRule versions with reproducible `MATCH`, `NO_MATCH`, and `UNKNOWN` results and complete Evidence lineage.

**Architecture:** Add a deterministic `familycare_api.decisions` module that reads scoped policy/Rider snapshots and executable rules through ports, evaluates allowlisted expressions without AI, persists immutable rule evaluations and a versioned decision run, and aggregates Rider candidates. The engine never infers facts, never calculates an unsupported amount, and never treats missing history or Evidence as a mismatch. Claim-history access is a protocol in this plan and the actual projection is added by the claim-workflow plan.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, direct `psycopg` 3.3 SQL, PostgreSQL 18, Alembic 1.19, JSON Schema Draft 2020-12, pytest, Ruff, and strict mypy.

**Spec:** `docs/design/coverage-decision-engine.md`, `docs/design/ai-document-analysis.md`, `docs/design/data-model.md`, `docs/design/event-result-pwa.md`, and `docs/design/v0.1-product.md`

## Global Constraints

- Migration `0007_coverage_decision_engine.py` revises `0006_rider_clause_rules`; it preserves all Phase 1 ingestion/queue contracts and all preceding policy/terms/rule tables.
- Use direct `psycopg` SQL and the existing row-mapping/repository pattern; do not add SQLAlchemy ORM models, SQLite substitutes, Redis, or a second queue/search service.
- All event, policy, Rider, rule, evaluation, and result reads use server-derived `HouseholdScope`; client-supplied household/member IDs are not authorization.
- MedicalEvent facts are normalized values with confirmation level and optional Evidence. Medical documents, receipt files, images, OCR text, diagnoses copied from private documents, and absolute paths are never stored.
- Only `AI_VERIFIED` and `USER_CONFIRMED` executable CoverageRule versions are read. AI cannot return or persist the tri-state or an amount.
- The only decision values are `MATCH`, `NO_MATCH`, and `UNKNOWN`.
- Missing facts, missing/conflicting renewal state, missing/stale Evidence, unsupported DSL, missing history, and conflicting contract rows produce `UNKNOWN`, not an exception, zero, or `NO_MATCH`.
- `NO_MATCH` requires a verified deterministic contradiction. `MATCH` requires all required facts/rules and Evidence; optional rules never override required results.
- Every RuleEvaluation stores exactly one tri-state, one rule version, reason code, input fact references, and Policy/Clause Evidence references.
- Updates require expected version; stale writes return `409 VERSION_CONFLICT`. Deletes are soft deletes with explicit trash/restore routes.
- CI and fixtures are wholly synthetic and provider-free. No OpenAI, Google Drive, real PDF, real medical data, API key, password, or private path is read or written.

## File Responsibility Map

```text
apps/api/migrations/versions/0007_coverage_decision_engine.py
  Creates medical_events, decision_runs, rule_evaluations,
  rule_evaluation_evidence, and claim_candidates.

apps/api/src/familycare_api/decisions/__init__.py
apps/api/src/familycare_api/decisions/domain.py
  Defines TriState, MedicalEvent, FactContext, RuleEvaluation, ClaimCandidate,
  DecisionRun, reason codes, and normalized value objects.

apps/api/src/familycare_api/decisions/facts.py
  Normalizes structured event facts and confirmation levels without guessing.

apps/api/src/familycare_api/decisions/operators.py
  Evaluates the allowlisted compiled expression against FactContext.

apps/api/src/familycare_api/decisions/rule_runtime.py
  Bridges executable CoverageRule versions to deterministic operator evaluation.

apps/api/src/familycare_api/decisions/engine.py
  Owns evaluation order, per-rule tri-state, Rider aggregation, questions, and
  stale-result detection.

apps/api/src/familycare_api/decisions/repository.py
  Owns scoped direct-psycopg persistence and immutable evaluation rows.

apps/api/src/familycare_api/decisions/service.py
apps/api/src/familycare_api/decisions/schemas.py
apps/api/src/familycare_api/decisions/router.py
apps/api/src/familycare_api/decisions/errors.py
  Own HTTP/event/analyze use cases and sanitized errors.

packages/contracts/schemas/coverage-decision.v1.schema.json
packages/contracts/examples/coverage-decision.v1.json
  Define strict event/result transport shapes and synthetic examples.

apps/api/tests/test_decision_migration.py
apps/api/tests/test_decision_engine.py
apps/api/tests/test_decision_api.py
apps/api/tests/test_decision_integration.py
apps/api/tests/test_decision_privacy.py
  Cover migration, decision tables, HTTP, PostgreSQL snapshots, and leakage.
```

Root integration owns router registration, common error registry, OpenAPI regeneration, and the shared contract checker updates. The plan's code must remain usable without AI and without claim-history persistence until `0010_claim_workflow` is merged.

## Database, Python, HTTP, and JSON Interfaces

### Migration contract

```text
medical_events(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  family_member_id uuid references family_members(id),
  mode varchar(16) not null,
  event_date date null,
  visit_date date null,
  facts_json jsonb not null,
  confirmation_json jsonb not null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

decision_runs(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  medical_event_id uuid references medical_events(id),
  engine_version varchar(64) not null,
  rule_set_version varchar(64) not null,
  event_version integer not null,
  policy_snapshot_at timestamptz not null,
  status varchar(32) not null,
  stale boolean not null default false,
  created_at timestamptz not null
)

rule_evaluations(
  id uuid primary key,
  decision_run_id uuid references decision_runs(id),
  rider_id uuid references riders(id),
  coverage_rule_version_id uuid references coverage_rule_versions(id),
  result varchar(16) not null,
  required boolean not null,
  reason_code varchar(64) not null,
  facts_json jsonb not null,
  evidence_snapshot_json jsonb not null,
  missing_fields_json jsonb not null,
  conflicting_fields_json jsonb not null,
  evaluator_version varchar(64) not null,
  created_at timestamptz not null
)

rule_evaluation_evidence(
  rule_evaluation_id uuid references rule_evaluations(id),
  evidence_id uuid references evidence(id),
  primary key (rule_evaluation_id, evidence_id)
)

claim_candidates(
  id uuid primary key,
  decision_run_id uuid references decision_runs(id),
  rider_id uuid references riders(id),
  rider_type varchar(32) not null,
  aggregate_result varchar(16) not null,
  required_match_count integer not null,
  required_unknown_count integer not null,
  required_no_match_count integer not null,
  questions_json jsonb not null,
  hold_reason_codes_json jsonb not null,
  version integer not null default 1,
  created_at timestamptz not null
)
```

Use named checks for `mode = pre_visit|post_treatment`, tri-state values, non-negative counts, positive versions, valid run status, and no negative dates/counts. `claim_candidates` is not a ClaimCase and never records submission/payment state. Evidence is joined from `rule_evaluation_evidence`; an evaluation without required Evidence cannot be `MATCH`.

### Python interfaces

```python
TriState = Literal["MATCH", "NO_MATCH", "UNKNOWN"]


@dataclass(frozen=True)
class FactValue:
    value: object | None
    confirmation: Literal["user", "ai_structured", "unconfirmed", "conflicting"]
    evidence_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class FactContext:
    medical_event: Mapping[str, FactValue]
    policy: Mapping[str, FactValue]
    rider: Mapping[str, FactValue]
    claim_history: Mapping[str, FactValue]


@dataclass(frozen=True)
class DecisionRunResult:
    run_id: UUID
    event_version: int
    engine_version: str
    rule_set_version: str
    candidates: tuple[ClaimCandidate, ...]
    evaluations: tuple[RuleEvaluation, ...]
    stale: bool


@dataclass(frozen=True)
class ClaimHistoryFact:
    outcome: Literal["paid", "partially_paid", "denied"]
    counted_occurrence: bool
    payment_date: date | None


@dataclass(frozen=True)
class PolicySnapshot:
    policy_id: UUID
    rider_id: UUID
    effective_status: str
    evidence_ids: tuple[UUID, ...]


class ClaimHistoryReader(Protocol):
    def for_family_member(
        self, scope: HouseholdScope, family_member_id: UUID
    ) -> tuple[ClaimHistoryFact, ...]: ...


class PolicySnapshotReader(Protocol):
    def for_event_date(
        self, scope: HouseholdScope, family_member_id: UUID, event_date: date
    ) -> tuple[PolicySnapshot, ...]: ...


class RuleReader(Protocol):
    def executable_for_rider(
        self, scope: HouseholdScope, rider_id: UUID
    ) -> tuple[CoverageRuleVersion, ...]: ...


class EvidenceRepository(Protocol):
    def get_many(
        self, scope: HouseholdScope, evidence_ids: tuple[UUID, ...]
    ) -> tuple[EvidenceRef, ...]: ...


@dataclass(frozen=True)
class DecisionReaders:
    policy: PolicySnapshotReader
    rules: RuleReader
    evidence: EvidenceRepository
    history: ClaimHistoryReader


class CoverageDecisionEngine(Protocol):
    def evaluate(self, scope: HouseholdScope, event: MedicalEvent) -> DecisionRunResult: ...
```

Core pure functions:

```python
def evaluate_expression(compiled: CompiledExpression, context: FactContext) -> OperatorOutcome: ...
def evaluate_rule(rule: CoverageRuleVersion, context: FactContext) -> RuleEvaluation: ...
def aggregate_required_results(evaluations: Sequence[RuleEvaluation]) -> TriState: ...
def build_follow_up_questions(evaluations: Sequence[RuleEvaluation]) -> tuple[Question, ...]: ...
def evaluate_event(
    scope: HouseholdScope, event: MedicalEvent, readers: DecisionReaders
) -> DecisionRunResult: ...
```

Evaluation order is fixed and tested:

```text
FamilyMember relationship
→ event date inside policy period
→ actual Rider subscription/status
→ disease/injury classification
→ waiting/reduction period
→ covered cause/appendix classification
→ exclusion/frequency/first-payment history
→ benefit calculation handoff
```

Aggregation is deterministic: any required `NO_MATCH` yields a decisive mismatch candidate; otherwise any required `UNKNOWN` yields an additional-confirmation candidate; only all required `MATCH` yields a claim-review candidate. Optional rules add questions/reasons but do not override required results.

### HTTP contract

```text
POST   /api/v1/medical-events
GET    /api/v1/medical-events/{id}
PATCH  /api/v1/medical-events/{id}
DELETE /api/v1/medical-events/{id}
GET    /api/v1/medical-events/trash
POST   /api/v1/medical-events/{id}/restore
POST   /api/v1/medical-events/{id}/analyze
GET    /api/v1/medical-events/{id}/results/{version}
```

Create/PATCH accepts structured facts, dates, mode, and confirmation levels only. It does not accept tri-state or amount fields from the client. Analyze returns a run ID, event version, engine/rule versions, Rider candidates, RuleEvaluations, questions, and Evidence references. It never says payment is guaranteed. `UNKNOWN` is a successful normal result, not HTTP failure.

All decision responses use `Cache-Control: no-store`. Every repository read derives the household scope from server-side request context; a client-supplied household or family-member scope is not an authorization input. The default scope resolver remains fail-closed until Phase 7 authentication is connected, so the routes are exercised in synthetic tests with an injected scope at this phase.

### JSON Schema contract

`coverage-decision.v1.schema.json` uses `additionalProperties: false`; `result` is an enum of exactly `MATCH`, `NO_MATCH`, `UNKNOWN`; every RuleEvaluation requires `rule_version_id`, `result`, `reason_code`, `evidence`, and `engine_version`; `amount` is absent from this plan's base result or explicitly represented as a `calculation_pending` state. Synthetic examples include a missing fact producing `UNKNOWN` and a deterministic exclusion producing `NO_MATCH` without medical document text.

## Tasks

## PR6 implementation checkpoint

The deterministic engine, migration, HTTP service, contract artifacts, and
synthetic PostgreSQL/privacy tests described below are implemented in the
current worktree. The local commit and shared PR gate are deliberately tracked
separately below; this plan does not claim a PR, CI result, or merge.

### Task 1: Define MedicalEvent, run, evaluation, and candidate migration

**Files:**
- Create: `apps/api/migrations/versions/0007_coverage_decision_engine.py`
- Create: `apps/api/tests/test_decision_migration.py`
- Test: `apps/api/tests/test_decision_migration.py`

**Interfaces:**
- Consumes: `0006_rider_clause_rules`, policy/Rider/rule/Evidence tables, and migration-spy conventions.
- Produces: `revision = "0007_coverage_decision_engine"`, `down_revision = "0006_rider_clause_rules"`, five tables, UUID FKs, tri-state/count/date checks, scope indexes, and reverse-order downgrade.

- [x] **Step 1: Write failing migration tests.** Assert exact table/column sets, event mode checks, tri-state checks, evaluation/candidate FKs, Evidence join uniqueness, soft-delete/version fields, and absence of any medical-file/path column.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_migration.py -q
  ```

  Expected: FAIL because `0007_coverage_decision_engine.py` is absent.

- [x] **Step 3: Implement the additive migration.** Store normalized facts/confirmation as JSONB with no document binary/text fields, use exact tri-state CHECK constraints, and separate Evidence join rows from evaluations. Each evaluation also stores an immutable Evidence metadata/hash snapshot for historical result reconstruction.

- [x] **Step 4: Run migration tests and upgrade.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_migration.py -q
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  ```

  Expected: migration shape passes and synthetic PostgreSQL reaches `0007_coverage_decision_engine` without changing Phase 1 or prior domain tables.

- [x] **Step 5: Commit the decision schema.**

  ```bash
  git add apps/api/migrations/versions/0007_coverage_decision_engine.py apps/api/tests/test_decision_migration.py
  git commit -m "feat(db): add deterministic decision schema"
  ```

### Task 2: Implement normalized facts, operator runtime, and history protocol

**Files:**
- Create: `apps/api/src/familycare_api/decisions/__init__.py`
- Create: `apps/api/src/familycare_api/decisions/domain.py`
- Create: `apps/api/src/familycare_api/decisions/facts.py`
- Create: `apps/api/src/familycare_api/decisions/operators.py`
- Create: `apps/api/src/familycare_api/decisions/rule_runtime.py`
- Create: `apps/api/tests/test_decision_facts.py`
- Create: `apps/api/tests/test_decision_operators.py`
- Test: `apps/api/tests/test_decision_facts.py`
- Test: `apps/api/tests/test_decision_operators.py`

**Interfaces:**
- Consumes: compiled allowlist expressions from `familycare_api.clauses.dsl` and the exact `ClaimHistoryReader`/`PolicySnapshotReader`/`RuleReader` protocols in this plan.
- Produces: `FactValue`, `FactContext`, deterministic operator outcomes, date/range/count evaluation, and no-AI engine inputs.

- [x] **Step 1: Write failing fact/operator tests.** Cover missing/null, user-confirmed vs unconfirmed/conflicting values, date boundary, `all/any/not`, `present/equals/in/range`, `date_between`, `days_since`, `count_before`, decimal comparisons, and absent history.

- [x] **Step 2: Run the focused RED tests.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_facts.py apps/api/tests/test_decision_operators.py -q
  ```

  Expected: FAIL because facts/operators/runtime modules do not exist.

- [x] **Step 3: Implement pure deterministic operators.** Treat missing, unconfirmed, conflicting, and stale Evidence values as `UNKNOWN`; raise only structural validation errors such as an invalid decimal/unit, never convert them to `NO_MATCH`.

  ```python
  def compare_required(value: FactValue | None, expected: object) -> OperatorOutcome:
      if value is None or value.value is None or value.confirmation == "conflicting":
          return OperatorOutcome("UNKNOWN", "MISSING_OR_CONFLICTING_FACT")
      if value.confirmation not in {"user", "ai_structured"}:
          return OperatorOutcome("UNKNOWN", "UNCONFIRMED_FACT")
      return (
          OperatorOutcome("MATCH", "FACT_EQUALS")
          if value.value == expected
          else OperatorOutcome("NO_MATCH", "DETERMINISTIC_VALUE_MISMATCH")
      )
  ```

- [x] **Step 4: Run operator/fact tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_facts.py apps/api/tests/test_decision_operators.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions/facts.py apps/api/src/familycare_api/decisions/operators.py apps/api/src/familycare_api/decisions/rule_runtime.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: all operator decision-table rows pass and strict static checks pass.

- [x] **Step 5: Commit the deterministic runtime primitives.**

  ```bash
  git add apps/api/src/familycare_api/decisions/__init__.py apps/api/src/familycare_api/decisions/domain.py apps/api/src/familycare_api/decisions/facts.py apps/api/src/familycare_api/decisions/operators.py apps/api/src/familycare_api/decisions/rule_runtime.py apps/api/tests/test_decision_facts.py apps/api/tests/test_decision_operators.py
  git commit -m "feat(decisions): add deterministic rule runtime"
  ```

### Task 3: Implement evaluation order, tri-state aggregation, and Evidence output

**Files:**
- Create: `apps/api/src/familycare_api/decisions/engine.py`
- Create: `apps/api/tests/test_decision_engine.py`
- Test: `apps/api/tests/test_decision_engine.py`

**Interfaces:**
- Consumes: facts/operators/protocols from Task 2, policy snapshot/Rider state, executable rule versions, Evidence repository, and a fake empty ClaimHistoryReader.
- Produces: `CoverageDecisionEngine.evaluate`, `evaluate_rule`, `evaluate_event`, `aggregate_required_results`, follow-up questions, and immutable run/candidate objects.

- [x] **Step 1: Write failing engine decision-table tests.** Include event/policy boundaries, non-insured member, inactive/unconfirmed Rider, waiting/reduction period, decisive exclusion, missing renewal state, missing history, stale Evidence, unsupported/non-executable rule, multiple Rider partial failure, and optional-rule aggregation.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_engine.py -q
  ```

  Expected: FAIL because `CoverageDecisionEngine` and aggregation functions are absent.

- [x] **Step 3: Implement the fixed evaluation order and tri-state aggregation.** Filter to actual subscribed Riders first, evaluate each required rule exactly once, preserve every evaluation/reason/Evidence, and return an `UNKNOWN` candidate whenever required information is absent or conflicting.

  ```python
  def aggregate_required_results(evaluations: Sequence[RuleEvaluation]) -> TriState:
      required = [item for item in evaluations if item.required]
      if any(item.result == "NO_MATCH" for item in required):
          return "NO_MATCH"
      if any(item.result == "UNKNOWN" for item in required):
          return "UNKNOWN"
      return "MATCH"
  ```

- [x] **Step 4: Run engine tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_engine.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions/engine.py apps/api/tests/test_decision_engine.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: every tri-state and evidence-chain case passes without AI/provider calls.

- [x] **Step 5: Commit the engine.**

  ```bash
  git add apps/api/src/familycare_api/decisions/engine.py apps/api/tests/test_decision_engine.py
  git commit -m "feat(decisions): evaluate evidence-backed tri-state"
  ```

### Task 4: Persist MedicalEvent/runs and expose deterministic analysis HTTP

**Files:**
- Create: `apps/api/src/familycare_api/decisions/repository.py`
- Create: `apps/api/src/familycare_api/decisions/service.py`
- Create: `apps/api/src/familycare_api/decisions/schemas.py`
- Create: `apps/api/src/familycare_api/decisions/router.py`
- Create: `apps/api/src/familycare_api/decisions/errors.py`
- Create: `apps/api/tests/test_decision_api.py`
- Test: `apps/api/tests/test_decision_api.py`

**Interfaces:**
- Consumes: engine result and direct-psycopg migration tables.
- Produces: the versioned MedicalEvent lifecycle, analysis/result operations, strict schemas, and the routes in this plan.

- [x] **Step 1: Write failing HTTP tests.** Assert pre/post event create/update, structured facts only, scope denial, stale update `409`, analysis returns `UNKNOWN` normally, no amount/guarantee wording, and Evidence/result version fields.

- [x] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_api.py -q
  ```

  Expected: FAIL because decisions router/service/schemas are absent.

- [x] **Step 3: Implement scoped service and routes.** Persist normalized facts and confirmation levels, invoke only the deterministic engine, store a new decision run/evaluation set transactionally, and map missing facts to HTTP 200 with `UNKNOWN` results. The boundary also provides explicit soft-delete/trash/restore operations and optimistic version checks.

  ```python
  class MedicalEventCreateRequest(BaseModel):
      model_config = ConfigDict(extra="forbid", frozen=True)
      family_member_id: UUID
      mode: Literal["pre_visit", "post_treatment"]
      event_date: date | None = None
      visit_date: date | None = None
      facts: dict[str, FactInput]
  ```

- [x] **Step 4: Run API tests and route contract generation.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_api.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: deterministic analysis responses and OpenAPI paths pass; no raw event description or internal SQL error is echoed.

- [x] **Step 5: Commit the decision HTTP boundary.**

  ```bash
  git add apps/api/src/familycare_api/decisions/repository.py apps/api/src/familycare_api/decisions/service.py apps/api/src/familycare_api/decisions/schemas.py apps/api/src/familycare_api/decisions/router.py apps/api/src/familycare_api/decisions/errors.py apps/api/tests/test_decision_api.py
  git commit -m "feat(api): expose coverage decision analysis"
  ```

### Task 5: Add decision contract, PostgreSQL integration, and privacy proof

**Files:**
- Create: `packages/contracts/schemas/coverage-decision.v1.schema.json`
- Create: `packages/contracts/examples/coverage-decision.v1.json`
- Create: `apps/api/tests/test_decision_contracts.py`
- Create: `apps/api/tests/test_decision_integration.py`
- Create: `apps/api/tests/test_decision_privacy.py`
- Modify: `scripts/check_contracts.py` through the root integration step
- Test: `apps/api/tests/test_decision_contracts.py`
- Test: `apps/api/tests/test_decision_integration.py`
- Test: `apps/api/tests/test_decision_privacy.py`

**Interfaces:**
- Consumes: complete deterministic engine/service/router and previous migrations.
- Produces: strict `coverage-decision.v1`, synthetic PostgreSQL proof of reproducibility/immutability, and log/response/cache-safe boundaries.

- [x] **Step 1: Write failing schema/integration/privacy tests.** Assert exact tri-state enum, required rule/Evidence/version fields, immutable prior run after reanalysis, one failed Rider not hiding another, stale Evidence warning, no medical document field, and no raw fact/log output.

- [x] **Step 2: Run the focused RED commands.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_integration.py apps/api/tests/test_decision_privacy.py -q
  ```

  Expected: FAIL because contract artifacts and PostgreSQL persistence/integration are incomplete.

- [x] **Step 3: Implement the strict schema and transactional persistence.** The schema rejects client-provided tri-state/amount fields and disallows extra properties; the repository writes event version, decision run, evaluations, Evidence joins, and candidates in one transaction after the engine returns. Evidence metadata and content hashes are snapshotted with each evaluation so later source-row changes do not rewrite an already persisted result.

- [x] **Step 4: Run the complete focused suite.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_migration.py apps/api/tests/test_decision_facts.py apps/api/tests/test_decision_operators.py apps/api/tests/test_decision_engine.py apps/api/tests/test_decision_api.py apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_privacy.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_decision_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/decisions apps/api/tests/test_decision_migration.py apps/api/tests/test_decision_facts.py apps/api/tests/test_decision_operators.py apps/api/tests/test_decision_engine.py apps/api/tests/test_decision_api.py apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_integration.py apps/api/tests/test_decision_privacy.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/decisions apps/api/tests
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/decisions
  ```

  Expected: all deterministic decision, PostgreSQL, contract, privacy, and static checks pass without OpenAI or private files.

- [x] **Step 5: Commit the complete engine acceptance.**

  ```bash
  git add apps/api/src/familycare_api/decisions packages/contracts/schemas/coverage-decision.v1.schema.json packages/contracts/examples/coverage-decision.v1.json apps/api/tests/test_decision_contracts.py apps/api/tests/test_decision_integration.py apps/api/tests/test_decision_privacy.py
  git commit -m "feat(decisions): add reproducible decision contract"
  ```

## Focused Post-Merge Verification

- [ ] **Step 1: Verify migration and deterministic decision tables on merged `main`.**

  ```bash
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_migration.py apps/api/tests/test_decision_facts.py apps/api/tests/test_decision_operators.py apps/api/tests/test_decision_engine.py apps/api/tests/test_decision_api.py apps/api/tests/test_decision_contracts.py -q
  ```

  Expected: the chain reaches `0007_coverage_decision_engine` or a later descendant; all outputs remain exactly tri-state.

- [ ] **Step 2: Verify PostgreSQL, history protocol, and privacy.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_decision_integration.py -q
  TMPDIR=/tmp uv run pytest apps/api/tests/test_decision_privacy.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: reproducibility, stale Evidence, missing history → `UNKNOWN`, scope isolation, soft delete/version conflict, and redaction all pass.

- [ ] **Step 3: Apply the shared Root PR gate.** Follow `docs/plan/003-v0.1-implementation-index.md`: inspect the entire diff once immediately before push, execute its serial repository checks, wait for required CI, merge, and record PR URL, merge commit, Actions result, and unverified external/device boundaries.
