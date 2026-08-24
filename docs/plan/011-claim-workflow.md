# Claim Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track insurer-specific claim preparation, manually recorded submission/payment outcomes, required-document checklist metadata, and future ClaimHistory without storing medical files or changing a historical result snapshot.

**Architecture:** Add a scoped `familycare_api.claims` module with an explicit ClaimStatus state machine, immutable ClaimCase snapshot rows, checklist metadata, status-transition audit events, and a minimal ClaimHistory projection consumed by the decision engine. ClaimCase is created from a MedicalEvent/ClaimCandidate but never submits to an insurer. A later reanalysis creates a comparison/result version; it cannot rewrite the snapshot captured at claim creation.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, direct `psycopg` 3.3 SQL, PostgreSQL 18, Alembic 1.19, JSON Schema Draft 2020-12, pytest, Ruff, and strict mypy.

**Spec:** `docs/design/claim-workflow.md`, `docs/design/data-model.md`, `docs/design/coverage-decision-engine.md`, `docs/design/event-result-pwa.md`, and `docs/design/v0.1-product.md`

## Global Constraints

- Migration `0010_claim_workflow.py` revises `0009_event_structuring`; it preserves all Phase 1 ingestion/extraction/job contracts and all preceding policy, terms, rule, decision, calculation, and event-structuring tables.
- Persistence remains direct `psycopg` SQL with row mappings; do not introduce SQLAlchemy ORM, insurer APIs, email/fax integrations, Redis, or a separate claim service.
- Every ClaimCase, checklist, history, snapshot, and transition query uses server-derived `HouseholdScope`; request body IDs are not authorization.
- ClaimCase stores result/rule/policy/Evidence snapshots and manually entered business metadata only. It never stores diagnosis/receipt/prescription files, images, OCR text, absolute paths, external document IDs, archive keys, or passwords.
- Candidate/analysis results and actual ClaimCase/payment outcomes are separate records. A ClaimCase does not keep a live pointer as its only record of the decision at submission time.
- Claim snapshots and status events are immutable append-only history. Corrections use an audit event or a new ClaimCase; a closed case is not reopened.
- Allowed statuses are exactly `preparing`, `submitted`, `supplementation_requested`, `paid`, `partially_paid`, `denied`, and `closed`.
- Status changes occur only through the transition endpoint with expected version and an allowed target; invalid transitions return `409 INVALID_CLAIM_TRANSITION`, stale writes return `409 VERSION_CONFLICT`.
- `submitted` records that the user submitted through an insurer channel; FamilyCare never directly submits anything.
- `paid` and `partially_paid` produce ClaimHistory facts for future frequency/first-payment rules. `denied` never automatically becomes a future `NO_MATCH`; missing/conflicting history produces `UNKNOWN` in the decision engine.
- Checklist rows contain document-kind requirement, required/conditional flag, prepared state, bounded note code, and source rule/Evidence IDs only. No file field exists.
- AI can explain verified requirements but cannot alter checklist requirement, snapshot, transition, paid amount, or future decision state.
- CI and fixtures use wholly synthetic parties, insurers, claim amounts, receipt numbers, and dates. Logs/responses/cache do not contain raw notes, medical input, receipt numbers, tokens, paths, or private identifiers.

## File Responsibility Map

```text
apps/api/migrations/versions/0010_claim_workflow.py
  Creates claim_cases, claim_case_snapshots, claim_checklist_items,
  claim_status_events, and claim_history.

apps/api/src/familycare_api/claims/__init__.py
apps/api/src/familycare_api/claims/domain.py
  Defines ClaimCase, immutable snapshot, checklist, status event, history fact,
  and exact ClaimStatus values.

apps/api/src/familycare_api/claims/state_machine.py
  Defines allowed transitions and fixed transition errors.

apps/api/src/familycare_api/claims/snapshot.py
  Builds sanitized immutable result/rule/policy/Evidence snapshots and hashes.

apps/api/src/familycare_api/claims/repository.py
  Owns scoped direct-psycopg claim/checklist/history persistence.

apps/api/src/familycare_api/claims/service.py
  Owns create/update/transition/checklist/outcome/soft-delete/restore use cases.

apps/api/src/familycare_api/claims/schemas.py
apps/api/src/familycare_api/claims/router.py
apps/api/src/familycare_api/claims/errors.py
  Define strict HTTP adapters, routes, and sanitized errors.

packages/contracts/schemas/claim-workflow.v1.schema.json
packages/contracts/examples/claim-workflow.v1.json
  Define claim/status/checklist/history transport contracts and synthetic data.

apps/api/tests/test_claim_workflow_migration.py
apps/api/tests/test_claim_state_machine.py
apps/api/tests/test_claim_snapshot.py
apps/api/tests/test_claim_workflow_api.py
apps/api/tests/test_claim_workflow_integration.py
apps/api/tests/test_claim_privacy.py
  Cover migration, every transition, snapshot immutability, HTTP, PostgreSQL,
  checklist-only storage, history feedback, and leakage boundaries.
```

Root integration owns router registration, common errors, OpenAPI regeneration, and contract-checker registration. The claim module may import the public decision/calculation read interfaces, but it must not update another module's internal tables directly.

## Database, Python, HTTP, and JSON Interfaces

### Migration contract

```text
claim_cases(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  medical_event_id uuid references medical_events(id),
  family_member_id uuid references family_members(id),
  policy_contract_id uuid references policy_contracts(id),
  insurer_key varchar(160) not null,
  status varchar(32) not null,
  receipt_number varchar(160) null,
  submitted_at timestamptz null,
  claimed_amount numeric(18,2) null,
  paid_amount numeric(18,2) null,
  currency char(3) null,
  outcome_reason_code varchar(64) null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

claim_case_snapshots(
  id uuid primary key,
  claim_case_id uuid references claim_cases(id),
  snapshot_version integer not null,
  candidate_snapshot_json jsonb not null,
  rule_snapshot_json jsonb not null,
  policy_snapshot_json jsonb not null,
  evidence_snapshot_json jsonb not null,
  calculation_snapshot_json jsonb not null,
  snapshot_sha256 varchar(64) not null,
  created_at timestamptz not null,
  unique (claim_case_id, snapshot_version)
)

claim_checklist_items(
  id uuid primary key,
  claim_case_id uuid references claim_cases(id),
  document_kind varchar(64) not null,
  requirement_code varchar(64) not null,
  required boolean not null,
  conditional boolean not null,
  prepared boolean not null default false,
  note_code varchar(64) null,
  source_rule_version_id uuid references coverage_rule_versions(id),
  source_evidence_id uuid references evidence(id),
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null
)

claim_status_events(
  id uuid primary key,
  claim_case_id uuid references claim_cases(id),
  from_status varchar(32) null,
  to_status varchar(32) not null,
  occurred_at timestamptz not null,
  reason_code varchar(64) null,
  metadata_json jsonb not null,
  created_at timestamptz not null
)

claim_history(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  medical_event_id uuid references medical_events(id),
  family_member_id uuid references family_members(id),
  policy_contract_id uuid references policy_contracts(id),
  rider_id uuid references riders(id),
  outcome varchar(16) not null,
  payment_date date null,
  counted_occurrence boolean not null,
  amount numeric(18,2) null,
  currency char(3) null,
  reason_code varchar(64) null,
  created_at timestamptz not null
)
```

Named constraints enforce exact status/outcome values, non-negative amounts, valid currency shape, positive versions, one immutable snapshot version per case, and no checklist column named path/file/blob/text/image/OCR. Receipt number is user-entered business metadata and is never logged; it is not an external file identifier.

### Python interfaces

```python
ClaimStatus = Literal[
    "preparing",
    "submitted",
    "supplementation_requested",
    "paid",
    "partially_paid",
    "denied",
    "closed",
]

ClaimOutcome = Literal["paid", "partially_paid", "denied"]


ALLOWED_TRANSITIONS: Mapping[ClaimStatus, frozenset[ClaimStatus]] = {
    "preparing": frozenset({"submitted"}),
    "submitted": frozenset({"supplementation_requested", "paid", "partially_paid", "denied"}),
    "supplementation_requested": frozenset({"submitted", "paid", "partially_paid", "denied"}),
    "paid": frozenset({"closed"}),
    "partially_paid": frozenset({"closed"}),
    "denied": frozenset({"closed"}),
    "closed": frozenset(),
}


def allowed_claim_transitions(status: ClaimStatus) -> frozenset[ClaimStatus]: ...
def transition_claim(scope: HouseholdScope, claim_id: UUID, target: ClaimStatus, expected_version: int, occurred_at: datetime, metadata: Mapping[str, str]) -> ClaimCase: ...
def build_claim_snapshot(result: DecisionRunResult, calculation: BenefitCalculationResult | None) -> ClaimCaseSnapshot: ...
def create_claim_case(scope: HouseholdScope, event_id: UUID, insurer_key: str, policy_id: UUID) -> ClaimCase: ...
def record_claim_outcome(scope: HouseholdScope, claim_id: UUID, outcome: ClaimOutcome, amount: Money | None, payment_date: date | None) -> ClaimHistoryFact: ...
```

Snapshot builder includes only normalized candidate/rule/policy/Evidence/calculation data and a SHA-256 hash. It excludes natural-language medical input, receipt notes, document text, source path, and external IDs. `claim_history` records `paid`/`partially_paid` facts; a `denied` outcome is retained for audit but is not a future deterministic mismatch.

### HTTP contract

```text
POST   /api/v1/medical-events/{event_id}/claims
GET    /api/v1/claims?event_id={event_id}&status={status}
GET    /api/v1/claims/{id}
PATCH  /api/v1/claims/{id}
POST   /api/v1/claims/{id}/transitions
PATCH  /api/v1/claims/{id}/checklist/{item_id}
DELETE /api/v1/claims/{id}
POST   /api/v1/claims/{id}/restore
```

`POST /claims` creates `preparing` and captures the immutable initial snapshot. `GET /claims` is server-scoped and supports bounded event/status filters plus cursor pagination; it never accepts household scope. `PATCH /claims/{id}` can update only permitted business metadata using expected version; it cannot set status, rewrite snapshot JSON, or inject a file/path. `POST /transitions` accepts target status, expected version, occurred-at, and allowlisted metadata only. Paid/partial transitions require valid non-negative amount/currency; denied does not require a payment amount. Every response uses bounded reason codes and no raw user note.

### JSON Schema contract

`claim-workflow.v1.schema.json` uses `additionalProperties: false`, exact status/outcome enums, decimal string amount patterns, UUID formats, and checklist objects that contain only requirement metadata and Evidence/rule IDs. Snapshot objects are bounded structured maps with SHA-256 and version; they do not allow file/path/image/text/OCR keys. The synthetic example demonstrates preparing → submitted → supplementation_requested → partially_paid → closed and a separate denied history fact that does not imply future `NO_MATCH`.

## Tasks

### Task 1: Define claim tables and state-machine migration tests

**Files:**
- Create: `apps/api/migrations/versions/0010_claim_workflow.py`
- Create: `apps/api/tests/test_claim_workflow_migration.py`
- Create: `apps/api/src/familycare_api/claims/__init__.py`
- Create: `apps/api/src/familycare_api/claims/domain.py`
- Create: `apps/api/src/familycare_api/claims/state_machine.py`
- Create: `apps/api/tests/test_claim_state_machine.py`
- Test: `apps/api/tests/test_claim_workflow_migration.py`
- Test: `apps/api/tests/test_claim_state_machine.py`

**Interfaces:**
- Consumes: `0009_event_structuring`, `medical_events`, candidates, calculations, policy/member tables, and migration-spy conventions.
- Produces: `revision = "0010_claim_workflow"`, `down_revision = "0009_event_structuring"`, five tables, exact statuses/transitions, and immutable domain values.

- [ ] **Step 1: Write failing migration and state tests.** Assert exact tables/columns, no file/path/blob/OCR fields, UUID FKs, amount checks, snapshot uniqueness, all allowed transitions, every denied transition, closed terminal state, and denied outcome not mapped to a mismatch.

- [ ] **Step 2: Run the focused RED tests.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_workflow_migration.py apps/api/tests/test_claim_state_machine.py -q
  ```

  Expected: FAIL because `0010_claim_workflow.py` and the claim state machine are absent.

- [ ] **Step 3: Implement the migration and pure transition table.** Use direct Alembic tables/constraints, append-only status events, immutable snapshot rows, and the exact mapping in this plan.

  ```python
  def transition_target(source: ClaimStatus, target: ClaimStatus) -> None:
      if target not in ALLOWED_TRANSITIONS[source]:
          raise InvalidClaimTransition
  ```

- [ ] **Step 4: Run migration/state tests and upgrade.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_workflow_migration.py apps/api/tests/test_claim_state_machine.py -q
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  ```

  Expected: all migration/state cases pass and synthetic PostgreSQL reaches `0010_claim_workflow`.

- [ ] **Step 5: Commit the schema/state foundation.**

  ```bash
  git add apps/api/migrations/versions/0010_claim_workflow.py apps/api/src/familycare_api/claims apps/api/tests/test_claim_workflow_migration.py apps/api/tests/test_claim_state_machine.py
  git commit -m "feat(claims): add claim state schema"
  ```

### Task 2: Implement immutable result snapshots and ClaimCase creation

**Files:**
- Create: `apps/api/src/familycare_api/claims/snapshot.py`
- Modify: `apps/api/src/familycare_api/claims/domain.py`
- Create: `apps/api/tests/test_claim_snapshot.py`
- Test: `apps/api/tests/test_claim_snapshot.py`

**Interfaces:**
- Consumes: public decision `DecisionRunResult`, benefit `BenefitCalculationResult`, policy/Rider/Evidence versions, and `HouseholdScope`.
- Produces: `build_claim_snapshot`, `ClaimCaseSnapshot`, deterministic snapshot hash, and a create command that stores an immutable initial snapshot.

- [ ] **Step 1: Write failing snapshot tests.** Assert candidate/rule/policy/Evidence/calculation versions are present, snapshot hash is deterministic, raw medical text/receipt note/path/file keys are absent, later rule changes do not mutate the initial snapshot, and a second snapshot version is append-only.

- [ ] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_snapshot.py -q
  ```

  Expected: FAIL because snapshot builder and immutable storage adapter are absent.

- [ ] **Step 3: Implement sanitized snapshot construction.** Canonicalize JSON with sorted keys/separators, include only normalized IDs/versions/reason codes/Evidence, hash the canonical bytes, and reject forbidden keys before persistence.

  ```python
  def snapshot_sha256(payload: Mapping[str, object]) -> str:
      encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
      return hashlib.sha256(encoded).hexdigest()
  ```

- [ ] **Step 4: Run snapshot tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_snapshot.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/claims/snapshot.py apps/api/tests/test_claim_snapshot.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/claims
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/claims
  ```

  Expected: immutable snapshot and forbidden-key tests pass.

- [ ] **Step 5: Commit snapshot behavior.**

  ```bash
  git add apps/api/src/familycare_api/claims/snapshot.py apps/api/src/familycare_api/claims/domain.py apps/api/tests/test_claim_snapshot.py
  git commit -m "feat(claims): preserve immutable result snapshots"
  ```

### Task 3: Add checklist metadata and ClaimHistory projection

**Files:**
- Modify: `apps/api/src/familycare_api/claims/domain.py`
- Create: `apps/api/src/familycare_api/claims/repository.py`
- Create: `apps/api/src/familycare_api/claims/service.py`
- Create: `apps/api/tests/test_claim_history.py`
- Modify: `apps/api/src/familycare_api/decisions/service.py` to consume the public history protocol
- Test: `apps/api/tests/test_claim_history.py`

**Interfaces:**
- Consumes: claim tables, snapshot builder, `ClaimHistoryReader` protocol from `008-coverage-decision-engine.md`, and verified rule/Evidence IDs.
- Produces: `create_checklist_items`, `update_checklist_item(expected_version)`, `record_claim_outcome`, `ClaimHistoryReader.for_family_member`, and checklist-only metadata validation.

- [ ] **Step 1: Write failing checklist/history tests.** Assert required/conditional/prepared fields and source IDs work, file/path/binary/OCR/medical text keys are rejected, paid/partial create counted history, denied is retained but not a future `NO_MATCH`, and missing/conflicting history returns `UNKNOWN` through the decision protocol.

- [ ] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_history.py -q
  ```

  Expected: FAIL because checklist/history repository and service methods are absent.

- [ ] **Step 3: Implement checklist/history persistence.** Store only allowlisted metadata and reason codes; use `SELECT ... FOR UPDATE` for expected-version checklist changes; create history in the same transaction as a valid paid/partial outcome; leave denied as an audit fact without feeding mismatch semantics.

  ```python
  def history_to_fact(row: Mapping[str, object]) -> ClaimHistoryFact:
      return ClaimHistoryFact(
          outcome=row["outcome"],
          counted_occurrence=bool(row["counted_occurrence"]),
          payment_date=row["payment_date"],
      )
  ```

- [ ] **Step 4: Run history/checklist tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_history.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py
  ```

  Expected: checklist/history tests pass and the decision history protocol has no claim-specific table access outside its public adapter.

- [ ] **Step 5: Commit checklist/history feedback.**

  ```bash
  git add apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py apps/api/tests/test_claim_history.py
  git commit -m "feat(claims): add checklist and history projection"
  ```

### Task 4: Expose ClaimCase, transition, checklist, and outcome HTTP contracts

**Files:**
- Create: `apps/api/src/familycare_api/claims/schemas.py`
- Create: `apps/api/src/familycare_api/claims/router.py`
- Create: `apps/api/src/familycare_api/claims/errors.py`
- Create: `packages/contracts/schemas/claim-workflow.v1.schema.json`
- Create: `packages/contracts/examples/claim-workflow.v1.json`
- Create: `apps/api/tests/test_claim_workflow_api.py`
- Create: `apps/api/tests/test_claim_workflow_contracts.py`
- Modify: `apps/api/src/familycare_api/main.py` through root integration
- Modify: `apps/api/src/familycare_api/errors.py` through root integration
- Modify: `scripts/check_contracts.py` through root integration
- Test: `apps/api/tests/test_claim_workflow_api.py`
- Test: `apps/api/tests/test_claim_workflow_contracts.py`

**Interfaces:**
- Consumes: ClaimCase service/state machine/snapshot/history from Tasks 1–3.
- Produces: the seven claim routes, strict request/response models, exact transition/error envelopes, and `claim-workflow.v1` schema/example.

- [ ] **Step 1: Write failing API/contract tests.** Assert claim creation captures a snapshot and begins `preparing`, direct status PATCH is rejected, allowed/denied transitions map correctly, expected-version conflicts are sanitized, checklist accepts metadata only, paid/partial amount/currency validation works, and response contains no medical document/file/path field.

- [ ] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_workflow_api.py apps/api/tests/test_claim_workflow_contracts.py -q
  ```

  Expected: FAIL because claim router, strict schemas, and contract artifacts are absent.

- [ ] **Step 3: Implement the routes and schema.** Status transitions call `transition_claim`; `PATCH /claims/{id}` cannot set status or snapshot; transition metadata is an allowlisted mapping; checklist payload rejects extra file/path/text keys; paid/partial requires non-negative Decimal/currency.

  ```python
  class ClaimTransitionRequest(BaseModel):
      model_config = ConfigDict(extra="forbid", frozen=True)
      target_status: ClaimStatus
      expected_version: int = Field(ge=1)
      occurred_at: datetime
      metadata: dict[str, str] = Field(default_factory=dict, max_length=8)
  ```

- [ ] **Step 4: Run API/contract checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_workflow_api.py apps/api/tests/test_claim_workflow_contracts.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: HTTP transitions, strict JSON Schema, and generated OpenAPI pass without raw note/status echo.

- [ ] **Step 5: Commit the claim HTTP boundary.**

  ```bash
  git add apps/api/src/familycare_api/claims/schemas.py apps/api/src/familycare_api/claims/router.py apps/api/src/familycare_api/claims/errors.py packages/contracts/schemas/claim-workflow.v1.schema.json packages/contracts/examples/claim-workflow.v1.json apps/api/tests/test_claim_workflow_api.py apps/api/tests/test_claim_workflow_contracts.py
  git commit -m "feat(api): expose claim workflow"
  ```

### Task 5: Verify PostgreSQL snapshots, transitions, history, and privacy

**Files:**
- Create: `apps/api/tests/test_claim_workflow_integration.py`
- Create: `apps/api/tests/test_claim_privacy.py`
- Modify: `apps/api/tests/test_claim_state_machine.py` for any transition edge case found in PostgreSQL integration
- Modify: `apps/api/tests/test_claim_snapshot.py` for any immutable snapshot regression
- Test: `apps/api/tests/test_claim_workflow_integration.py`
- Test: `apps/api/tests/test_claim_privacy.py`

**Interfaces:**
- Consumes: complete `0009` migration, ClaimCase service/router, snapshot/checklist/history layers, and public decision/calculation results.
- Produces: synthetic PostgreSQL proof of independent insurer ClaimCases, immutable snapshots, all allowed/denied transitions, soft delete/restore, history feedback, and no medical-document/cache/log leakage.

- [ ] **Step 1: Write failing end-to-end/privacy assertions.** Create one MedicalEvent with two synthetic insurers/policies, capture separate snapshots, transition one through partial payment and the other through denial, close both, reanalyze with a changed rule, and assert snapshots remain unchanged and denial does not produce future `NO_MATCH`.

- [ ] **Step 2: Run the focused RED integration command.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_claim_workflow_integration.py -q
  ```

  Expected: FAIL until real PostgreSQL transaction boundaries, immutable snapshots, history projection, and independent ClaimCase scope are complete.

- [ ] **Step 3: Implement only missing transaction/redaction paths.** Lock one ClaimCase per transition, append a status event, update its version, and insert payment history atomically. Never update another insurer's ClaimCase or rewrite its snapshot.

- [ ] **Step 4: Run the complete focused suite.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_workflow_migration.py apps/api/tests/test_claim_state_machine.py apps/api/tests/test_claim_snapshot.py apps/api/tests/test_claim_history.py apps/api/tests/test_claim_workflow_api.py apps/api/tests/test_claim_workflow_contracts.py apps/api/tests/test_claim_privacy.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_claim_workflow_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py apps/api/tests/test_claim_workflow_migration.py apps/api/tests/test_claim_state_machine.py apps/api/tests/test_claim_snapshot.py apps/api/tests/test_claim_history.py apps/api/tests/test_claim_workflow_api.py apps/api/tests/test_claim_workflow_contracts.py apps/api/tests/test_claim_workflow_integration.py apps/api/tests/test_claim_privacy.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py apps/api/tests
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/claims apps/api/src/familycare_api/decisions/service.py
  ```

  Expected: all migration, transition, snapshot, checklist, history, HTTP, PostgreSQL, contract, privacy, and static checks pass with synthetic values only.

- [ ] **Step 5: Commit the complete claim acceptance.**

  ```bash
  git add apps/api/tests/test_claim_workflow_integration.py apps/api/tests/test_claim_privacy.py apps/api/tests/test_claim_state_machine.py apps/api/tests/test_claim_snapshot.py
  git commit -m "test(claims): verify immutable claim outcomes"
  ```

### Task 6: Add the ClaimCase Web workflow

**Files:**
- Create: `apps/web/src/api/claims.ts`
- Create: `apps/web/src/features/claims/ClaimListPage.tsx`
- Create: `apps/web/src/features/claims/ClaimCasePage.tsx`
- Create: `apps/web/src/features/claims/ClaimStatusStepper.tsx`
- Create: `apps/web/src/features/claims/ChecklistEditor.tsx`
- Create: `apps/web/src/features/claims/ClaimOutcomeForm.tsx`
- Create: `apps/web/src/features/claims/claims.test.tsx`
- Create: `apps/web/e2e/claims.spec.ts`
- Modify: `apps/web/src/app/AppRoutes.tsx`
- Modify: `apps/web/src/features/results/ClaimCandidateCard.tsx`
- Modify: `apps/web/src/styles.css`

**Interfaces:**
- Consumes: generated claim create/read/update/transition/checklist/delete/restore operations through the shared no-store client.
- Produces: `/app/claims`, `/app/claims/{claimId}`, and result-card actions that create independent insurer/policy ClaimCases from one MedicalEvent.
- The Web records checklist state, receipt reference, submitted/paid dates, claimed/paid decimal amounts, currency, and bounded reason codes only. It has no medical-document upload, insurer submission, free-form raw-document field, or persistent browser draft.

- [ ] **Step 1: Write failing state, amount, conflict, and privacy tests.** Cover two independent ClaimCases for one event, allowed transition buttons only, `INVALID_CLAIM_TRANSITION`, optimistic `VERSION_CONFLICT` with draft preservation, metadata-only checklist, negative/currency validation, partial/denied outcomes, soft-delete/restore, immutable snapshot display, and zero Web Storage/service-worker writes.

  ```bash
  corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
    src/features/claims/claims.test.tsx
  ```

  Expected: FAIL because the generated claim client and screens do not exist.

- [ ] **Step 2: Implement the generated client and accessible ClaimCase screens.** Derive available actions from the server's `allowed_transitions`; never let `PATCH /claims/{id}` set status. Use decimal string inputs, ISO date controls, semantic checklist rows, text status, and safe reason-code copy. Clear in-memory form values after mutation, logout, 401, close, or unmount; never place claim values in a URL or console.

- [ ] **Step 3: Run GREEN and the synthetic browser flow.**

  ```bash
  corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
    src/features/claims/claims.test.tsx
  corepack pnpm@11.22.0 --filter @familycare/web exec playwright test \
    --workers=1 e2e/claims.spec.ts
  corepack pnpm@11.22.0 web:check
  ```

  The browser flow runs result card → two insurer cases → checklist → submitted → supplementation → partial/denied → closed with synthetic data. It asserts no file input, insurer network request, persistent sensitive cache, URL value, or raw error output.

- [ ] **Step 4: Commit the ClaimCase Web slice.**

  ```bash
  git add apps/web/src/api/claims.ts apps/web/src/features/claims \
    apps/web/src/features/results/ClaimCandidateCard.tsx \
    apps/web/src/app/AppRoutes.tsx apps/web/src/styles.css \
    apps/web/e2e/claims.spec.ts
  git commit -m "feat(web): add claim tracking screens"
  ```

## Focused Post-Merge Verification

- [ ] **Step 1: Verify the migration and state machine on merged `main`.**

  ```bash
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_workflow_migration.py apps/api/tests/test_claim_state_machine.py apps/api/tests/test_claim_snapshot.py apps/api/tests/test_claim_history.py apps/api/tests/test_claim_workflow_api.py apps/api/tests/test_claim_workflow_contracts.py -q
  ```

  Expected: the chain reaches `0010_claim_workflow` or a later descendant; every allowed transition and terminal close rule passes.

- [ ] **Step 2: Verify PostgreSQL/history/privacy after merge.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_claim_workflow_integration.py -q
  TMPDIR=/tmp uv run pytest apps/api/tests/test_claim_privacy.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: snapshots are immutable, insurer ClaimCases are independent, checklist is metadata-only, paid/partial history feeds the protocol, denied does not become future `NO_MATCH`, and sensitive values remain absent from logs/responses/cache.

- [ ] **Step 3: Apply the shared Root PR gate.** Follow `docs/plan/003-v0.1-implementation-index.md`: inspect the full diff once immediately before push, execute its serial repository gate, wait for required CI, merge, and record PR URL, merge commit, Actions result, and unverified real-data/device boundaries.
