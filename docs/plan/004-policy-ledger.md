# Policy Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a household-scoped, evidence-backed ledger of FamilyMember, PolicyContract, PolicyParty, and actually subscribed Rider records without changing the Phase 1 document-ingestion contract.

**Architecture:** Keep the FastAPI modular-monolith boundary and the existing direct `psycopg` repository pattern. Add a common server-derived `HouseholdScope`, a reusable Evidence lineage table, and policy repositories that publish only records whose source DocumentVersion and Evidence satisfy the policy invariants. Authentication is not implemented in this plan; tests inject a scope resolver and later authentication replaces that resolver without accepting a client-provided household ID.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, `psycopg` 3.3 direct SQL, PostgreSQL 18, Alembic 1.19, JSON Schema Draft 2020-12, pytest 9, Ruff 0.16, mypy strict mode.

**Spec:** `docs/design/policy-ledger.md`, `docs/design/data-model.md`, and `docs/design/v0.1-product.md`

## Global Constraints

- Migration `0003_policy_ledger.py` revises `0002_document_ingestion`; it does not alter or rename any Phase 1 table, state, route, JSON Schema, or generated document contract.
- The database layer uses direct `psycopg` SQL and row mappings; do not introduce SQLAlchemy ORM models or a second persistence pattern.
- Every business query uses a server-derived `HouseholdScope`; request bodies cannot choose an authoritative `household_space_id`.
- `Evidence` always stores a `document_version_id`, content hash, 1-based physical PDF page, review state, and optional PDF-point bbox; it never stores a source path, archive key, password, or document text.
- A Rider is publishable only from policy evidence; a Terms DocumentVersion alone can never create an actual subscribed Rider.
- `AI_VERIFIED`, `NEEDS_REVIEW`, and `USER_CONFIRMED` are candidate review states; AI is not authoritative for policy status or subscription.
- Updates require an expected integer version and return `409 VERSION_CONFLICT` for stale writes.
- Deletes are soft deletes. Default list/get queries exclude `deleted_at IS NOT NULL`; trash and restore are explicit operations.
- Tests, fixtures, logs, OpenAPI examples, and CI contain only wholly synthetic values such as `Admin A`, `Family Member A`, and `synthetic-policy-001`.
- The API must not return or log absolute paths, external archive keys, policy numbers, PDF text, passwords, tokens, or private identifiers.
- `MATCH`, `NO_MATCH`, and `UNKNOWN` remain decision-engine vocabulary owned by a later plan; this plan never derives a coverage decision from an insurer or terms name.
- Web, Python, and database checks run serially. No external AI, Google Drive, private PDF, or real credential is used in RED or GREEN verification.

## File Responsibility Map

```text
apps/api/migrations/versions/0003_policy_ledger.py
  Creates household_spaces, family_members, evidence, policy_contracts,
  policy_parties, riders, and policy_status_snapshots.

apps/api/src/familycare_api/common/scope.py
  Defines HouseholdScope and the dependency-injectable scope resolver.

apps/api/src/familycare_api/common/evidence.py
  Validates page, bbox, document-version, and content-hash lineage.

apps/api/src/familycare_api/common/versions.py
  Defines expected-version parsing and VERSION_CONFLICT mapping.

apps/api/src/familycare_api/policies/domain.py
  Defines policy ledger value objects and aggregate projections.

apps/api/src/familycare_api/policies/repository.py
  Owns direct PostgreSQL reads/writes for policy ledger tables.

apps/api/src/familycare_api/policies/service.py
  Owns household-scoped use cases, invariants, soft delete, restore, and
  optimistic concurrency.

apps/api/src/familycare_api/policies/schemas.py
  Defines strict HTTP request and response adapters.

apps/api/src/familycare_api/policies/router.py
  Defines the versioned family-member, policy, Rider, trash, and restore routes.

apps/api/src/familycare_api/policies/errors.py
  Defines sanitized policy-domain errors without request-value echoing.

packages/contracts/schemas/policy-ledger.v1.schema.json
packages/contracts/examples/policy-ledger.v1.json
  Define the language-neutral transport contract and one synthetic example.

apps/api/tests/test_policy_ledger_migration.py
apps/api/tests/test_policy_ledger_domain.py
apps/api/tests/test_policy_ledger_api.py
apps/api/tests/test_policy_ledger_integration.py
apps/api/tests/test_policy_ledger_privacy.py
  Cover migration shape, use cases, HTTP, PostgreSQL behavior, and leakage.
```

The root integration files `apps/api/src/familycare_api/main.py`, `apps/api/src/familycare_api/errors.py`, `packages/contracts/openapi/familycare.v1.json`, and `scripts/check_contracts.py` are modified only when the root agent registers the router and regenerates the committed contract. Their changes are part of this PR but are not delegated to an implementation subtask.

## Database and HTTP Interfaces

### Migration contract

The migration creates these tables in foreign-key order:

```text
household_spaces(
  id uuid primary key,
  space_key varchar(128) unique not null,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

family_members(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  display_name varchar(160) not null,
  internal_alias varchar(80) not null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

evidence(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  document_version_id uuid references document_versions(id),
  content_sha256 varchar(64) not null,
  page_number integer not null,
  bbox jsonb null,
  review_state varchar(32) not null,
  created_at timestamptz not null
)

policy_contracts(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  insurer_display varchar(160) not null,
  insurer_key varchar(160) not null,
  product_display varchar(200) not null,
  product_key varchar(200) not null,
  contract_start date null,
  contract_end date null,
  status varchar(32) not null,
  source_document_version_id uuid references document_versions(id),
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

policy_parties(
  id uuid primary key,
  policy_contract_id uuid references policy_contracts(id),
  family_member_id uuid references family_members(id),
  role varchar(32) not null,
  effective_from date null,
  effective_to date null,
  evidence_id uuid references evidence(id),
  version integer not null default 1
)

riders(
  id uuid primary key,
  policy_contract_id uuid references policy_contracts(id),
  display_name varchar(240) not null,
  normalized_key varchar(240) not null,
  rider_type varchar(32) not null,
  insured_amount numeric(18,2) null,
  currency char(3) null,
  payment_start date null,
  payment_end date null,
  coverage_start date null,
  coverage_end date null,
  renewable boolean null,
  status varchar(32) not null,
  status_checked_at timestamptz null,
  evidence_id uuid references evidence(id),
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

policy_status_snapshots(
  id uuid primary key,
  policy_contract_id uuid null references policy_contracts(id),
  rider_id uuid null references riders(id),
  status varchar(32) not null,
  effective_at timestamptz not null,
  evidence_id uuid references evidence(id),
  created_at timestamptz not null,
  check ((policy_contract_id is null) <> (rider_id is null))
)
```

The migration uses named CHECK constraints for non-empty keys, valid state values, non-negative versions/amounts, page number `>= 1`, lowercase SHA-256 format, and exactly one status-snapshot parent. It creates household/member/policy/Rider indexes for scope and active rows. It does not seed a real or default household in the migration.

### Python interfaces

```python
@dataclass(frozen=True)
class HouseholdScope:
    household_space_id: UUID


class HouseholdScopeResolver(Protocol):
    def resolve(self, request: Request) -> HouseholdScope: ...


@dataclass(frozen=True)
class EvidenceRef:
    evidence_id: UUID
    document_version_id: UUID
    content_sha256: str
    page_number: int
    bbox: tuple[Decimal, Decimal, Decimal, Decimal] | None


class PolicyRepository(Protocol):
    def create(self, scope: HouseholdScope, command: CreatePolicy) -> PolicyContract: ...
    def get(self, scope: HouseholdScope, policy_id: UUID) -> PolicyContract: ...
    def update(
        self, scope: HouseholdScope, policy_id: UUID, expected_version: int, command: UpdatePolicy
    ) -> PolicyContract: ...
    def soft_delete(
        self, scope: HouseholdScope, policy_id: UUID, expected_version: int
    ) -> None: ...
    def restore(
        self, scope: HouseholdScope, policy_id: UUID, expected_version: int
    ) -> PolicyContract: ...


class EvidenceRepository(Protocol):
    def validate_for_document(
        self, scope: HouseholdScope, evidence_id: UUID, document_version_id: UUID
    ) -> EvidenceRef: ...
```

Use direct SQL with `%s` parameters and `psycopg.rows.dict_row`. Never interpolate display names, aliases, identifiers, or dates into query strings.

### HTTP contract

```text
GET/POST       /api/v1/family-members
GET/PATCH/DELETE /api/v1/family-members/{id}
POST            /api/v1/family-members/{id}/restore
GET/POST        /api/v1/policies
GET/PATCH/DELETE /api/v1/policies/{id}
POST            /api/v1/policies/{id}/restore
GET             /api/v1/policies/{id}/riders
```

`POST` and `PATCH` request models use `ConfigDict(extra="forbid", frozen=True)`. PATCH includes `expected_version: int`. Responses include internal UUID, display/normalized fields, status, version, and bounded Evidence references only. They never include `source_key`, absolute paths, policy numbers, archive keys, document text, or passwords. Error envelopes use `INVALID_REQUEST`, `POLICY_NOT_FOUND`, `FAMILY_MEMBER_NOT_FOUND`, `EVIDENCE_INVALID`, `VERSION_CONFLICT`, and `POLICY_STATE_CONFLICT`.

### JSON Schema contract

`policy-ledger.v1.schema.json` has `additionalProperties: false` for every request and response object. The synthetic example contains `schema_version: "1"`, `family_member_id`, `policy_id`, `rider_id`, status/version values, and an Evidence object with a UUID, synthetic SHA-256, physical page `1`, and no source path. It has no actual insurer, policy number, name, or document text.

## Tasks

### Task 1: Define the policy migration and migration contract tests

**Files:**
- Create: `apps/api/migrations/versions/0003_policy_ledger.py`
- Create: `apps/api/tests/test_policy_ledger_migration.py`
- Modify: `apps/api/tests/test_document_ingestion_migration.py` only if the existing test needs an explicit assertion that Phase 1 remains unchanged
- Test: `apps/api/tests/test_policy_ledger_migration.py`

**Interfaces:**
- Consumes: `0002_document_ingestion` tables and the existing `RecordingOperations` migration-test pattern.
- Produces: `0003_policy_ledger`, `down_revision = "0002_document_ingestion"`, seven named tables, UUID foreign keys, scope indexes, state/amount/page/version CHECK constraints, and reverse-order downgrade.

- [x] **Step 1: Write the failing migration-shape tests.** Add tests for the revision chain, exact seven new table names, no missing Phase 1 table, UUID primary/foreign keys, household scope columns, soft-delete/version columns, Evidence page/hash constraints, status snapshot XOR constraint, and downgrade order.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_migration.py -q
  ```

  Expected: FAIL because `apps/api/migrations/versions/0003_policy_ledger.py` is absent and the policy migration module cannot be loaded.

- [x] **Step 3: Implement the minimum Alembic migration.** Use `op.create_table`, `sa.UUID(as_uuid=True)`, timezone-aware timestamps, named constraints, and indexes. Set `revision = "0003_policy_ledger"`, `down_revision = "0002_document_ingestion"`; do not modify `0002_document_ingestion.py`.

  ```python
  revision = "0003_policy_ledger"
  down_revision = "0002_document_ingestion"

  op.create_table(
      "family_members",
      _uuid("id", primary_key=True),
      sa.Column(
          "household_space_id",
          sa.UUID(as_uuid=True),
          sa.ForeignKey("household_spaces.id"),
          nullable=False,
      ),
      sa.Column("display_name", sa.String(160), nullable=False),
      sa.Column("internal_alias", sa.String(80), nullable=False),
      sa.Column("version", sa.Integer, nullable=False, server_default=sa.text("1")),
      _created_at(),
      _updated_at(),
      sa.Column("deleted_at", sa.DateTime(timezone=True)),
      sa.CheckConstraint("version >= 1", name="ck_family_members_version"),
  )
  ```

- [x] **Step 4: Run the migration tests and a synthetic PostgreSQL migration.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_migration.py -q
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  ```

  Expected: the migration spy tests pass; the configured synthetic PostgreSQL reaches `0003_policy_ledger` without changing the eight Phase 1 table shapes.

- [x] **Step 5: Commit the migration contract.**

  ```bash
  git add apps/api/migrations/versions/0003_policy_ledger.py apps/api/tests/test_policy_ledger_migration.py apps/api/tests/test_document_ingestion_migration.py
  git commit -m "feat(db): add policy ledger schema"
  ```

### Task 2: Add server scope, Evidence validation, and domain repositories

**Files:**
- Create: `apps/api/src/familycare_api/common/scope.py`
- Create: `apps/api/src/familycare_api/common/evidence.py`
- Create: `apps/api/src/familycare_api/common/versions.py`
- Create: `apps/api/src/familycare_api/policies/__init__.py`
- Create: `apps/api/src/familycare_api/policies/domain.py`
- Create: `apps/api/src/familycare_api/policies/repository.py`
- Create: `apps/api/src/familycare_api/policies/errors.py`
- Create: `apps/api/tests/test_policy_ledger_domain.py`
- Test: `apps/api/tests/test_policy_ledger_domain.py`

**Interfaces:**
- Consumes: migration tables and `ApiBoundaryError` behavior from `apps/api/src/familycare_api/errors.py`.
- Produces: `HouseholdScope`, `HouseholdScopeResolver`, `EvidenceRef`, `EvidenceRepository.validate_for_document`, policy/family dataclasses, and direct-`psycopg` repository methods with scope predicates.

- [x] **Step 1: Write failing domain tests.** Test that a resolver returns a server-owned `HouseholdScope`, a repository query cannot read another household, Evidence rejects page `0`, wrong content hash, stale DocumentVersion, and out-of-bounds bbox, and a stale expected version raises `VersionConflict` without changing the row.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_domain.py -q
  ```

  Expected: FAIL with missing `familycare_api.common.scope`, `familycare_api.common.evidence`, or `familycare_api.policies` symbols.

- [x] **Step 3: Implement the minimum scope, Evidence, and repository interfaces.** Keep the test resolver injectable; never read `household_space_id` from a request model. Every SQL query must contain the server scope predicate and `deleted_at IS NULL` unless the method is explicitly trash/restore.

  ```python
  @dataclass(frozen=True)
  class HouseholdScope:
      household_space_id: UUID


  def require_expected_version(expected_version: int) -> int:
      if isinstance(expected_version, bool) or expected_version < 1:
          raise InvalidVersion
      return expected_version


  def validate_evidence_page(page_number: int) -> int:
      if page_number < 1:
          raise EvidenceInvalid
      return page_number
  ```

- [x] **Step 4: Run domain tests and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_domain.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/common apps/api/src/familycare_api/policies apps/api/tests/test_policy_ledger_domain.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/common apps/api/src/familycare_api/policies
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/common apps/api/src/familycare_api/policies
  ```

  Expected: all domain tests pass, direct SQL is formatted/linted, and strict mypy reports no errors.

- [x] **Step 5: Commit the scoped domain layer.**

  ```bash
  git add apps/api/src/familycare_api/common apps/api/src/familycare_api/policies apps/api/tests/test_policy_ledger_domain.py
  git commit -m "feat(api): add scoped policy ledger domain"
  ```

### Task 3: Add family-member and policy use cases with soft delete and concurrency

**Files:**
- Modify: `apps/api/src/familycare_api/policies/repository.py`
- Create: `apps/api/src/familycare_api/policies/service.py`
- Create: `apps/api/src/familycare_api/policies/schemas.py`
- Create: `apps/api/src/familycare_api/policies/router.py`
- Create: `apps/api/tests/test_policy_ledger_api.py`
- Test: `apps/api/tests/test_policy_ledger_api.py`

**Interfaces:**
- Consumes: `HouseholdScope`, repository methods, and domain errors from Task 2.
- Produces: `create_family_member`, `update_family_member`, `delete_family_member`, `restore_family_member`, `create_policy`, `update_policy`, `delete_policy`, `restore_policy`, and `list_policy_riders`; strict Pydantic HTTP models and the routes in this plan.

- [x] **Step 1: Write failing HTTP tests.** Add TestClient tests for create/list/get/update/delete/restore, another-scope denial, missing aggregate, stale `expected_version`, invalid Evidence, and response absence of `source_key`, path, password, policy number, and raw text.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_api.py -q
  ```

  Expected: FAIL because the policy router, service, and strict request/response models do not exist.

- [x] **Step 3: Implement the minimum service and router.** Use dependency injection for the scope resolver and repository. Never accept a household ID in `FamilyMemberCreateRequest` or `PolicyCreateRequest`. Map stale writes to a sanitized `409 VERSION_CONFLICT` envelope.

  ```python
  class PolicyUpdateRequest(BaseModel):
      model_config = ConfigDict(extra="forbid", frozen=True)
      expected_version: int = Field(ge=1)
      status: PolicyStatus | None = None
      contract_end: date | None = None


  @router.patch("/policies/{policy_id}", response_model=PolicyResponse)
  def patch_policy(
      policy_id: UUID, request: PolicyUpdateRequest, service: PolicyService = Depends(...)
  ) -> PolicyResponse:
      return PolicyResponse.from_domain(
          service.update_policy(policy_id, request.expected_version, request.to_command())
      )
  ```

- [x] **Step 4: Run HTTP tests and route contract validation.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_api.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: all synthetic HTTP behavior passes; OpenAPI is regenerated from FastAPI and the contract checker reports no drift.

- [x] **Step 5: Commit the policy use cases.**

  ```bash
  git add apps/api/src/familycare_api/policies
  git commit -m "feat(api): expose policy ledger use cases"
  ```

### Task 4: Add the language-neutral ledger contract and API registration

**Files:**
- Create: `packages/contracts/schemas/policy-ledger.v1.schema.json`
- Create: `packages/contracts/examples/policy-ledger.v1.json`
- Modify: `apps/api/src/familycare_api/main.py`
- Modify: `apps/api/src/familycare_api/errors.py`
- Modify: `scripts/check_contracts.py`
- Create: `apps/api/src/familycare_api/contracts/generated_business.py` as the deterministic output of the business-contract generator
- Create: `apps/api/tests/test_policy_ledger_contracts.py`
- Test: `apps/api/tests/test_policy_ledger_contracts.py`

**Interfaces:**
- Consumes: policy router and strict models from Task 3.
- Produces: `policy-ledger.v1` JSON Schema, synthetic example validation, OpenAPI paths for the policy routes, and sanitized policy error codes.

- [x] **Step 1: Write failing schema and registration tests.** Assert `additionalProperties: false`, required UUID/version/status fields, no forbidden path/password/text field names, exact route presence, and schema/example validation.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_contracts.py -q
  ```

  Expected: FAIL because the policy schema/example and router registration are absent.

- [x] **Step 3: Implement the contract and registration.** Include only synthetic examples; extend the checker through a deterministic loader rather than relaxing the existing document contract. Register the router explicitly in `create_app` and add fixed error codes without echoing request values.

  ```json
  {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "additionalProperties": false,
    "required": ["schema_version", "family_member_id", "policy_id", "version"],
    "properties": {
      "schema_version": {"const": "1"},
      "family_member_id": {"format": "uuid", "type": "string"},
      "policy_id": {"format": "uuid", "type": "string"},
      "version": {"minimum": 1, "type": "integer"}
    },
    "type": "object"
  }
  ```

- [x] **Step 4: Run contract and privacy checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_contracts.py apps/api/tests/test_policy_ledger_privacy.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run python scripts/check_repository_safety.py
  ```

  Expected: the committed OpenAPI, JSON Schema, generated business types, and privacy checks agree.

- [x] **Step 5: Commit the contract boundary.**

  ```bash
  git add packages/contracts/schemas/policy-ledger.v1.schema.json packages/contracts/examples/policy-ledger.v1.json apps/api/src/familycare_api/main.py apps/api/src/familycare_api/errors.py scripts/check_contracts.py apps/api/tests/test_policy_ledger_contracts.py apps/api/src/familycare_api/contracts/generated_business.py
  git commit -m "feat(contracts): define policy ledger boundary"
  ```

### Task 5: Verify PostgreSQL integration, privacy, and focused acceptance

**Files:**
- Create: `apps/api/tests/test_policy_ledger_integration.py`
- Create: `apps/api/tests/test_policy_ledger_privacy.py`
- Modify: `apps/api/tests/test_policy_ledger_domain.py` for any regression cases discovered by the integration test
- Modify: `apps/api/tests/test_policy_ledger_api.py` for route-level regression cases discovered by the integration test
- Test: `apps/api/tests/test_policy_ledger_integration.py`
- Test: `apps/api/tests/test_policy_ledger_privacy.py`

**Interfaces:**
- Consumes: the complete `0003` migration, policy repositories/services/router, and `policy-ledger.v1` contract.
- Produces: a synthetic PostgreSQL proof of household isolation, Evidence validation, policy/Rider lifecycle, soft delete/restore, optimistic concurrency, and response/log redaction.

- [x] **Step 1: Write the failing end-to-end and privacy assertions.** Exercise synthetic DocumentVersion/Evidence rows through family member → policy → party → Rider, then assert a second scope cannot read them, stale updates return `409`, deleted rows disappear from default queries, and `caplog`/responses contain no source path, password, policy number, or text.

- [x] **Step 2: Run the focused RED integration command.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_policy_ledger_integration.py -q
  ```

  Expected: FAIL until the real PostgreSQL repository transaction and scope predicates are complete. The test must not inspect any private filesystem root.

- [x] **Step 3: Implement only the missing transaction and redaction paths.** Use short `psycopg.connect` transactions, atomic `UPDATE ... WHERE version = expected_version` predicates, explicit `deleted_at` predicates, and fixed public error messages.

- [x] **Step 4: Run the complete focused feature suite.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_migration.py apps/api/tests/test_policy_ledger_domain.py apps/api/tests/test_policy_ledger_api.py apps/api/tests/test_policy_ledger_contracts.py apps/api/tests/test_policy_ledger_privacy.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_policy_ledger_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/common apps/api/src/familycare_api/policies apps/api/tests/test_policy_ledger_migration.py apps/api/tests/test_policy_ledger_domain.py apps/api/tests/test_policy_ledger_api.py apps/api/tests/test_policy_ledger_contracts.py apps/api/tests/test_policy_ledger_privacy.py apps/api/tests/test_policy_ledger_integration.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/common apps/api/src/familycare_api/policies apps/api/tests
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/common apps/api/src/familycare_api/policies
  ```

  Expected: all focused tests and static checks pass; no external provider or private data is accessed.

- [x] **Step 5: Commit the verified feature and record the PR gate.**

  ```bash
  git add apps/api/tests/test_policy_ledger_integration.py apps/api/tests/test_policy_ledger_privacy.py apps/api/tests/test_policy_ledger_domain.py apps/api/tests/test_policy_ledger_api.py
  git commit -m "test(policy): verify scoped ledger lifecycle"
  ```

## Focused Post-Merge Verification

- [ ] **Step 1: Verify the merged migration and policy contract on `main`.**

  ```bash
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_ledger_migration.py apps/api/tests/test_policy_ledger_domain.py apps/api/tests/test_policy_ledger_api.py apps/api/tests/test_policy_ledger_contracts.py -q
  ```

  Expected: the current main migration chain reaches `0003_policy_ledger` or a later descendant and all policy-focused tests pass.

- [ ] **Step 2: Run the PostgreSQL scope and privacy proof after merge.**

  ```bash
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_policy_ledger_integration.py apps/api/tests/test_policy_ledger_privacy.py -q
  ```

  Expected: household isolation, Evidence lineage, soft-delete/restore, version conflict, and redaction all pass with synthetic values.

- [ ] **Step 3: Apply the shared Root PR gate.** Follow `docs/plan/003-v0.1-implementation-index.md` exactly: inspect branch/diff, run the serial repository gate, review the complete diff once immediately before push, wait for required CI, merge, and record PR URL, merge commit, Actions result, and unverified real-data/device boundaries.
