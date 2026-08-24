# Policy Candidate Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn synthetic document Evidence into versioned, reviewable policy candidates, publish only validated candidates to the policy ledger, and provide an accessible Web review queue that never treats a terms-only mention as an enrolled Rider.

**Architecture:** The Analyzer Worker owns provider calls and candidate structuring. A strict schema, an independent verifier, and a deterministic validator form the publication boundary. The API owns HouseholdSpace-scoped candidate versions, optimistic concurrency, publication, and review routes. The Web consumes generated OpenAPI types through a no-store client and an in-memory query cache; it displays bounded Evidence but never owns insurance rules or candidate authority.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, PostgreSQL 18, Alembic 1.19, React 19, TypeScript 6, Vite 8, Vitest 4, Testing Library, Playwright Chromium, OpenAI Responses API through a provider-neutral Worker adapter.

**Spec:** `docs/design/policy-ledger.md`, `docs/design/ai-document-analysis.md`, `docs/design/v0.1-product.md`, and `docs/plan/003-v0.1-implementation-index.md`

## Global Constraints

- Migration `0004_policy_candidate_review.py` has `down_revision = "0003_policy_ledger"` and preserves every Phase 1 and policy-ledger table/contract.
- Branch: `feat/policy-candidate-review`; commits use Conventional Commits and stay under 72 characters.
- Only synthetic fixtures, synthetic provider responses, and synthetic identifiers such as `synthetic-policy-001` may enter Git, tests, logs, screenshots, or CI artifacts.
- The Worker may receive only bounded extracted Evidence blocks and the minimum candidate context; it never receives PDF binary, page images, passwords, archive keys, local paths, cookies, or unrelated household data.
- `OPENAI_API_KEY` is injected only into the Worker runtime. Public CI uses a fake provider and makes no external AI request.
- The structurer model defaults to `gpt-5.6-luna` and the verifier model defaults to `gpt-5.6-terra`; both are non-secret configuration values and are not embedded in domain decisions.
- Candidate statuses are exactly `AI_VERIFIED`, `NEEDS_REVIEW`, `USER_CONFIRMED`, and `rejected` after the initial `generated` persistence state. Only the first three statuses are visible to the review projection; only `AI_VERIFIED` and `USER_CONFIRMED` are publishable.
- A verifier may approve, reject, or request review, but may not add a field, Evidence ID, date, amount, Rider, or contract fact absent from the structurer candidate and supplied Evidence.
- A Rider requires policy DocumentVersion Evidence. A candidate supported only by a terms DocumentVersion is never published as an actual Rider.
- User correction creates a new candidate version with parent/version/audit metadata. It never overwrites raw extraction, the original AI candidate, or historical Evidence.
- Missing, conflicting, stale, or unsupported Evidence creates `NEEDS_REVIEW`; it never becomes `NO_MATCH`, `MATCH`, a zero amount, or an active Rider by inference.
- The Web never sends or trusts a client-provided `household_space_id`, user ID, source path, archive key, policy number, or raw document text as an authorization input.
- Every API and Evidence response uses `Cache-Control: no-store`; the Web stores server state only in memory and never in localStorage, sessionStorage, or IndexedDB.
- The Web service worker remains app-shell-only. No API, review item, Evidence, PDF, or document URL is added to runtime caching.
- Browser E2E uses synthetic mocked API responses in this PR. Windows, real devices, Tailscale, private PDFs, and external AI remain separately unverified.

---

## File Responsibility Map

### Contracts and generation

- `packages/contracts/schemas/policy-candidate.v1.schema.json`: strict transport-neutral candidate, Evidence, review issue, and correction shapes.
- `packages/contracts/examples/policy-candidate.v1.json`: one wholly synthetic `AI_VERIFIED` candidate and one `NEEDS_REVIEW` candidate fixture.
- `scripts/check_policy_candidate_contract.py`: deterministic schema/example/privacy checks and forbidden-field checks.
- `scripts/generate_web_contract_types.py`: deterministic OpenAPI-to-TypeScript operation and schema alias generator; it never contains domain behavior.
- `apps/web/src/api/generated.ts`: checked-in generated TypeScript consumer; never hand-edit this file.
- `packages/contracts/openapi/familycare.v1.json`: regenerated canonical API contract for review-item and candidate-correction routes.
- `scripts/check_contracts.py`: invokes the candidate and Web generated-contract checks.

### Worker candidate pipeline

- `workers/analyzer/src/familycare_worker/ai/provider.py`: provider-neutral protocol, OpenAI Responses adapter, model configuration, and sanitized provider error classification.
- `workers/analyzer/src/familycare_worker/ai/schemas.py`: typed structurer/verifier payloads and candidate status values.
- `workers/analyzer/src/familycare_worker/ai/structurer.py`: bounded Evidence to strict candidate conversion.
- `workers/analyzer/src/familycare_worker/ai/verifier.py`: independent Evidence-supported verification with no fact invention.
- `workers/analyzer/src/familycare_worker/ai/validator.py`: schema, Evidence, unit, date, scope, and candidate invariant checks.
- `workers/analyzer/src/familycare_worker/ai/policy_pipeline.py`: structurer → verifier → deterministic validator → publisher orchestration.
- `workers/analyzer/src/familycare_worker/ai/repository.py`: candidate version persistence and publish transaction boundary.
- `workers/analyzer/tests/fixtures/policy_ai_responses.py`: synthetic provider response fixtures only.
- `workers/analyzer/tests/test_policy_ai_schemas.py`: strict schema and invented-field rejection tests.
- `workers/analyzer/tests/test_policy_ai_pipeline.py`: success, disagreement, missing Evidence, retry, and partial-failure tests.
- `workers/analyzer/tests/test_policy_ai_privacy.py`: request, database, exception, and log absence tests.

### API persistence and review routes

- `apps/api/migrations/versions/0004_policy_candidate_review.py`: candidate version, field, Evidence reference, and review issue tables with optimistic version indexes.
- `apps/api/src/familycare_api/policies/candidate_models.py`: Pydantic request/response models derived from the generated contract.
- `apps/api/src/familycare_api/policies/candidate_repository.py`: HouseholdSpace-scoped reads/writes and version checks.
- `apps/api/src/familycare_api/policies/candidate_service.py`: confirmation, rejection, correction, and publication use cases.
- `apps/api/src/familycare_api/policies/candidate_router.py`: review-item and candidate-field HTTP boundary.
- `apps/api/tests/test_policy_candidate_migration.py`: PostgreSQL constraints and table boundary tests.
- `apps/api/tests/test_policy_candidate_api.py`: HTTP status, scope, response, and concurrency tests.
- `apps/api/tests/test_policy_candidate_integration.py`: synthetic Worker candidate to API review/publish flow.

### Web client and review UI

- `apps/web/src/api/http.ts`: same-origin fetch wrapper with `credentials: include`, `cache: no-store`, sanitized errors, and abort support.
- `apps/web/src/api/query-cache.ts`: memory-only resource cache and invalidation store.
- `apps/web/src/api/ledger.ts`: typed family, policy, Rider, review-item, confirmation, rejection, and correction calls.
- `apps/web/src/app/AppRoot.tsx`: providers and route boundary used by later Web plans.
- `apps/web/src/app/AppShell.tsx`: authenticated navigation, FamilyMember context, skip link, and route landmark.
- `apps/web/src/features/ledger/LedgerPage.tsx`: family-scoped read projection and review queue entry point.
- `apps/web/src/features/ledger/FamilyMemberPicker.tsx`: server-provided FamilyMember selection.
- `apps/web/src/features/ledger/PolicySummaryCard.tsx`: contract dates, status, and bounded source labels.
- `apps/web/src/features/ledger/RiderList.tsx`: actual enrolled Riders only.
- `apps/web/src/features/ledger/CandidateReviewQueue.tsx`: `NEEDS_REVIEW` exception list.
- `apps/web/src/features/ledger/CandidateReviewDialog.tsx`: accessible confirmation/rejection/correction dialog.
- `apps/web/src/features/ledger/CandidateFieldEditor.tsx`: typed editable candidate fields and Evidence selection.
- `apps/web/src/features/ledger/useLedger.ts`: query hooks and mutation invalidation.
- `apps/web/src/features/ledger/ledger.test.tsx`: read projection, status, and Rider invariants.
- `apps/web/src/features/ledger/candidate-review.test.tsx`: correction, dialog focus, conflict, and Evidence behavior.
- `apps/web/src/test/mockApi.ts`: deterministic in-memory fetch responses with synthetic payloads.
- `apps/web/src/test/renderWithProviders.tsx`: test wrapper for router, cache, and API providers.
- `apps/web/playwright.config.ts`: one-worker Chromium mock E2E configuration.
- `apps/web/e2e/ledger.spec.ts`: synthetic browser flow from ledger to candidate confirmation.

## Shared Interfaces

The following names and fields are the cross-task contract. Later tasks must use them rather than inventing parallel shapes.

```ts
export type CandidateStatus =
  | "AI_VERIFIED"
  | "NEEDS_REVIEW"
  | "USER_CONFIRMED"
  | "rejected";

export type PolicyCandidateFieldId =
  | "insurer"
  | "product_name"
  | "contract_start"
  | "contract_end"
  | "policy_status"
  | "rider_name"
  | "rider_key"
  | "benefit_type"
  | "sum_assured"
  | "currency"
  | "coverage_start"
  | "coverage_end"
  | "renewable"
  | "rider_status";

export interface EvidenceRef {
  evidence_id: string;
  document_version_id: string;
  document_label: string;
  page: number;
  bbox: [number, number, number, number] | null;
  bounded_excerpt: string;
}

export interface PolicyReviewItem {
  review_item_id: string;
  candidate_version_id: string;
  aggregate_id: string | null;
  candidate_kind: "policy_contract" | "policy_party" | "rider";
  status: CandidateStatus;
  fields: Array<{
    field_id: PolicyCandidateFieldId;
    value: string | number | boolean | null;
    evidence_ids: string[];
  }>;
  evidence: EvidenceRef[];
  issues: Array<{
    code:
      | "MISSING_EVIDENCE"
      | "CONFLICTING_EVIDENCE"
      | "TERMS_ONLY_RIDER"
      | "UNSUPPORTED_STRUCTURE"
      | "LOW_CONFIDENCE"
      | "INVALID_UNIT"
      | "INVALID_DATE";
    field_id: PolicyCandidateFieldId | null;
  }>;
  expected_version: number;
}

export interface CandidateCorrectionRequest {
  expected_version: number;
  field_id: PolicyCandidateFieldId;
  value: string | number | boolean | null;
  evidence_id: string;
}
```

The API service accepts `RequestScope` from authentication middleware in the next authentication plan; until then the policy-ledger scope object supplied by Plan 004 is mandatory. The client never constructs that scope.

## Task 1: Define candidate contracts and generated Web consumers

**Files:**

- Create: `packages/contracts/schemas/policy-candidate.v1.schema.json`
- Create: `packages/contracts/examples/policy-candidate.v1.json`
- Create: `scripts/check_policy_candidate_contract.py`
- Create: `scripts/generate_web_contract_types.py`
- Create: `apps/web/src/api/generated.ts`
- Test: `scripts/tests/test_policy_candidate_contract.py`
- Modify: `packages/contracts/openapi/familycare.v1.json`
- Modify: `scripts/check_contracts.py`
- Modify: `packages/contracts/README.md`

**Interfaces:**

- Consumes: Plan 004 policy ledger IDs, DocumentVersion Evidence references, and the canonical FastAPI OpenAPI output.
- Produces: strict `PolicyReviewItem`, `CandidateCorrectionRequest`, `CandidateConfirmationRequest`, `CandidateRejectionRequest`, `VERSION_CONFLICT`, `REVIEW_ITEM_NOT_FOUND`, and `INVALID_CANDIDATE_CORRECTION` contracts.
- The schema rejects `source_path`, `absolute_path`, `policy_number`, `raw_pdf`, `password`, `archive_key`, `household_space_id`, `prompt`, and `raw_provider_response` at every request/response object level.

- [x] **Step 1: Write the failing schema and generated-consumer tests**

```python
def test_candidate_example_requires_policy_evidence_and_bounded_excerpt() -> None:
    candidate = load_json("examples/policy-candidate.v1.json")
    schema = load_json("schemas/policy-candidate.v1.schema.json")

    assert validate_schema_instance(schema, candidate) is False
    assert candidate["status"] == "AI_VERIFIED"
    assert candidate["evidence"][0]["page"] == 1
    assert len(candidate["evidence"][0]["bounded_excerpt"]) <= 240
    assert "source_path" not in candidate
    assert "policy_number" not in candidate


def test_web_generated_types_are_checked_in_and_not_stale() -> None:
    generated = ROOT / "apps/web/src/api/generated.ts"
    assert generated.is_file()
    text = generated.read_text(encoding="utf-8")
    assert "PolicyReviewItem" in text
    assert "CandidateCorrectionRequest" in text
    assert "VERSION_CONFLICT" in text
```

Run:

```bash
TMPDIR=/tmp uv run pytest scripts/tests/test_policy_candidate_contract.py -q
```

Expected: FAIL because the candidate schema, checker, generated TypeScript consumer, and OpenAPI operations do not exist.

- [x] **Step 2: Add the strict candidate schema and synthetic example**

Define one versioned JSON object with `schema_version: "1"`, the exact candidate status enum, candidate kind enum, fixed field ID enum, typed values, Evidence references, bounded excerpts, and issue code enum. Set `additionalProperties: false` on the root and every nested object. Require Evidence for every `AI_VERIFIED` and `USER_CONFIRMED` field. Add one `AI_VERIFIED` rider backed by a synthetic policy DocumentVersion and one `NEEDS_REVIEW` rider with `TERMS_ONLY_RIDER` so the example demonstrates that terms presence is not enrollment.

Use only values such as `synthetic-policy-001`, `Sample Policy`, `Family Member A`, and UUID-shaped synthetic IDs. Do not add a field that can carry raw provider messages, full extracted text, absolute paths, passwords, or private external identifiers.

- [x] **Step 3: Add deterministic contract and TypeScript generation checks**

Implement `check_policy_candidate_contract.py` with the same standard-library schema validator used by the existing document contract tests. Validate the examples, forbidden field list, page/bbox bounds, excerpt length, exact status values, and the requirement that `TERMS_ONLY_RIDER` cannot have `AI_VERIFIED` status.

Implement `generate_web_contract_types.py` as a deterministic standard-library generator that reads the canonical OpenAPI JSON, emits sorted `paths`, `operations`, `components`, and error-code aliases, and writes only `apps/web/src/api/generated.ts`. Generated output begins with `// GENERATED FILE: do not edit; source packages/contracts/openapi/familycare.v1.json`. Add a temporary-directory regeneration comparison to `check_contracts.py`; do not use hand-written API interfaces in Web features.

Run:

```bash
TMPDIR=/tmp uv run python scripts/check_policy_candidate_contract.py
TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py --check
```

Expected: PASS after the schema, examples, and generator are present; a byte change in the generated file fails the check.

- [x] **Step 4: Add the review operations to OpenAPI and checker expectations**

Add these operations with strict request and response schemas:

```text
GET  /api/v1/review-items?domain=policy&status=NEEDS_REVIEW
GET  /api/v1/review-items/{review_item_id}
PATCH /api/v1/policies/{policy_id}/candidate-fields/{field_id}
PATCH /api/v1/review-items/{review_item_id}/candidate-fields/{field_id}
POST /api/v1/review-items/{review_item_id}/confirm
POST /api/v1/review-items/{review_item_id}/reject
```

The correction body contains only `expected_version`, `field_id`, `value`, and `evidence_id`. Confirmation and rejection contain `expected_version`; rejection additionally contains an enum `reason_code`. Define `409 VERSION_CONFLICT`, `404 REVIEW_ITEM_NOT_FOUND`, and `422 INVALID_CANDIDATE_CORRECTION` without echoing raw values. Regenerate OpenAPI with the application factory and ensure the committed contract matches byte-for-byte.

- [x] **Step 5: Run the contract slice and commit it**

```bash
TMPDIR=/tmp uv run pytest scripts/tests/test_policy_candidate_contract.py -q
TMPDIR=/tmp uv run python scripts/check_policy_candidate_contract.py
TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py --check
TMPDIR=/tmp uv run python scripts/check_contracts.py
git diff --check
git add packages/contracts scripts apps/web/src/api/generated.ts
git commit -m "feat(contracts): define policy candidate review"
```

Expected: all commands exit 0 and the commit contains only contract/checker/generated-consumer work.

## Task 2: Implement the provider-neutral AI candidate pipeline

**Files:**

- Create: `workers/analyzer/src/familycare_worker/ai/__init__.py`
- Create: `workers/analyzer/src/familycare_worker/ai/provider.py`
- Create: `workers/analyzer/src/familycare_worker/ai/schemas.py`
- Create: `workers/analyzer/src/familycare_worker/ai/structurer.py`
- Create: `workers/analyzer/src/familycare_worker/ai/verifier.py`
- Create: `workers/analyzer/src/familycare_worker/ai/validator.py`
- Create: `workers/analyzer/src/familycare_worker/ai/policy_pipeline.py`
- Create: `workers/analyzer/tests/fixtures/policy_ai_responses.py`
- Test: `workers/analyzer/tests/test_policy_ai_schemas.py`
- Test: `workers/analyzer/tests/test_policy_ai_pipeline.py`
- Test: `workers/analyzer/tests/test_policy_ai_privacy.py`
- Modify: `workers/analyzer/src/familycare_worker/runner.py`

**Interfaces:**

- Consumes: `EvidenceSlice` records from the existing extraction repository and the generated `policy-candidate.v1` shape.
- Produces: `PolicyCandidateBatch`, `CandidatePipelineResult`, and one sanitized error classification from `SUCCESS`, `NEEDS_REVIEW`, `RETRYABLE_PROVIDER_ERROR`, `CONFIGURATION_ERROR`, or `VALIDATION_ERROR`.

```python
class AiProvider(Protocol):
    def complete(
        self,
        *,
        model: str,
        schema_name: str,
        system_instruction: str,
        input_payload: Mapping[str, object],
    ) -> ProviderResponse: ...


@dataclass(frozen=True)
class EvidenceSlice:
    evidence_id: UUID
    document_version_id: UUID
    page: int
    text: str
    bbox: tuple[float, float, float, float] | None


def run_policy_pipeline(
    *,
    evidence: Sequence[EvidenceSlice],
    provider: AiProvider,
    structurer_model: str,
    verifier_model: str,
    schema_version: str = "1",
) -> PolicyCandidateBatch: ...
```

- [x] **Step 1: Write the failing pipeline decision-table tests**

```python
def test_two_valid_ai_stages_and_validator_publish_ai_verified() -> None:
    result = run_policy_pipeline(
        evidence=synthetic_policy_evidence(),
        provider=FakeProvider(structurer=VALID_STRUCTURED, verifier=VALID_VERIFIED),
        structurer_model="gpt-5.6-luna",
        verifier_model="gpt-5.6-terra",
    )
    assert result.candidates[0].status == "AI_VERIFIED"


def test_verifier_cannot_invent_a_rider_or_evidence() -> None:
    result = run_policy_pipeline(
        evidence=synthetic_policy_evidence(),
        provider=FakeProvider(structurer=VALID_STRUCTURED, verifier=INVENTED_FACT),
        structurer_model="gpt-5.6-luna",
        verifier_model="gpt-5.6-terra",
    )
    assert result.candidates[0].status == "NEEDS_REVIEW"
    assert "INVENTED_EVIDENCE" in result.candidates[0].issue_codes
```

Run:

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_policy_ai_schemas.py workers/analyzer/tests/test_policy_ai_pipeline.py -q
```

Expected: FAIL because the provider protocol, strict payload models, and pipeline do not exist.

- [x] **Step 2: Implement strict structurer and verifier payloads**

Use frozen Pydantic models or equivalent typed validation with `extra="forbid"`. The structurer accepts only the bounded `EvidenceSlice` batch and returns fixed candidate kinds and field IDs. The verifier receives the structurer candidate plus the same Evidence IDs and returns `approved`, `needs_review`, or `rejected` with issue codes; it has no field for adding facts.

Reject unknown fields, unknown enum values, missing Evidence IDs, Evidence IDs from another DocumentVersion, invalid page numbers, bboxes outside page bounds, negative amounts, invalid currency, end-before-start dates, and terms-only Rider candidates marked as enrolled.

- [x] **Step 3: Implement provider adapter and sanitized retry classification**

Define `OpenAiResponsesAdapter` behind `AiProvider`. Read the API key only inside the Worker call path, send strict JSON schema instructions, and retain only a provider request ID in the candidate audit record. Do not log prompts, responses, Evidence text, model input, API key, or exception bodies.

Classify timeout and rate-limit responses as retryable once within the job retry budget. Classify authentication and configuration errors as terminal. Invalid JSON, schema mismatch, invented Evidence, and unsupported candidate structure become `NEEDS_REVIEW` without retrying the same invalid response.

- [x] **Step 4: Implement pipeline ordering and partial-failure behavior**

Run structurer, verifier, and deterministic validator in that order. A verifier failure preserves the candidate as `NEEDS_REVIEW`; it does not erase other valid candidates. A provider failure leaves the existing published candidate version unchanged. The runner publishes only the validated batch and never calls the decision engine or computes `MATCH`, `NO_MATCH`, or money.

- [x] **Step 5: Add privacy tests and commit the pipeline**

```bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_policy_ai_schemas.py workers/analyzer/tests/test_policy_ai_pipeline.py workers/analyzer/tests/test_policy_ai_privacy.py -q
TMPDIR=/tmp uv run ruff format --check workers/analyzer
TMPDIR=/tmp uv run ruff check workers/analyzer
TMPDIR=/tmp uv run mypy workers/analyzer/src
git diff --check
git add workers/analyzer/src/familycare_worker/ai workers/analyzer/src/familycare_worker/runner.py workers/analyzer/tests
git commit -m "feat(ai): validate policy candidates"
```

Expected: synthetic pipeline tests pass; logs and provider payload assertions contain none of the forbidden values.

## Task 3: Persist candidate versions and expose review use cases

**Files:**

- Create: `apps/api/migrations/versions/0004_policy_candidate_review.py`
- Create: `apps/api/src/familycare_api/policies/candidate_models.py`
- Create: `apps/api/src/familycare_api/policies/candidate_repository.py`
- Create: `apps/api/src/familycare_api/policies/candidate_service.py`
- Create: `apps/api/src/familycare_api/policies/candidate_router.py`
- Test: `apps/api/tests/test_policy_candidate_migration.py`
- Test: `apps/api/tests/test_policy_candidate_api.py`
- Test: `apps/api/tests/test_policy_candidate_integration.py`
- Modify: `apps/api/src/familycare_api/main.py`
- Modify: `apps/api/src/familycare_api/errors.py`
- Modify: `apps/api/src/familycare_api/policies/__init__.py`

**Interfaces:**

- Consumes: Plan 004 `HouseholdSpace` request scope, `PolicyContract`/`Rider` tables, `DocumentVersion` Evidence, and Worker `PolicyCandidateBatch`.
- Produces: `CandidateReviewService.list_review_items`, `.get_review_item`, `.correct_field`, `.confirm`, and `.reject`; all writes require `expected_version`.

```python
class CandidateReviewService:
    def list_review_items(
        self,
        *,
        scope: RequestScope,
        status: Literal["NEEDS_REVIEW", "AI_VERIFIED", "USER_CONFIRMED"] = "NEEDS_REVIEW",
    ) -> list[PolicyReviewItem]: ...

    def correct_field(
        self,
        *,
        scope: RequestScope,
        review_item_id: UUID,
        request: CandidateCorrectionRequest,
        actor_id: UUID,
    ) -> PolicyReviewItem: ...
```

- [x] **Step 1: Write failing migration and use-case tests**

```python
def test_candidate_correction_creates_a_child_version_without_overwriting_parent(
    database_url: str,
) -> None:
    original = seed_candidate(status="NEEDS_REVIEW", version=1)
    corrected = service.correct_field(
        scope=synthetic_scope(),
        review_item_id=original.review_item_id,
        request=CandidateCorrectionRequest(
            expected_version=1,
            field_id="rider_name",
            value="Sample Rider Corrected",
            evidence_id=original.evidence[0].evidence_id,
        ),
        actor_id=SYNTHETIC_ADMIN_ID,
    )
    assert corrected.expected_version == 2
    assert load_candidate_version(original.candidate_version_id).status == "NEEDS_REVIEW"
```

Run:

```bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_candidate_migration.py apps/api/tests/test_policy_candidate_api.py -q
```

Expected: FAIL because revision `0004_policy_candidate_review`, repository, service, and routes do not exist.

- [x] **Step 2: Add the candidate version migration**

Create `analysis_candidate_versions` with UUID ID, `household_space_id`, candidate kind, aggregate ID, parent version ID, integer version, status, generator/verifier/schema versions, provider request ID, issue JSON, actor/timestamps, and soft-delete timestamp. Create `analysis_candidate_fields` with candidate version ID, fixed field ID, JSON scalar value, and unique `(candidate_version_id, field_id)`. Create `analysis_candidate_evidence` with candidate version ID, field ID, DocumentVersion ID, Evidence ID, physical page, bounded excerpt, optional bbox, and unique `(candidate_version_id, field_id, evidence_id)`. Create indexes for household/status and review-item lookup.

Do not create columns for source paths, passwords, raw provider response, PDF body, policy numbers, or user free-text copies. Add foreign keys to Plan 004 policy and Phase 1 DocumentVersion records. Add a unique `(aggregate_id, version)` constraint and a check that status is in the exact candidate enum.

- [x] **Step 3: Implement repository scope and optimistic concurrency**

Every SELECT, UPDATE, and INSERT receives server-derived `RequestScope.household_space_id`; ignore any household/user ID in request JSON or query parameters. `expected_version` is checked in the same transaction as the write. A mismatch returns `VERSION_CONFLICT`; missing or soft-deleted rows return `REVIEW_ITEM_NOT_FOUND` without revealing whether another household owns the ID.

- [x] **Step 4: Implement publish, correction, confirmation, and rejection**

`correct_field` validates field type, Evidence membership, and DocumentVersion lineage, then inserts a child candidate version and audit event. `confirm` marks the current version `USER_CONFIRMED` and publishes only aggregate values with policy Evidence. `reject` marks the version `rejected` with a bounded reason code. A terms-only Rider can be confirmed as an informational candidate but cannot create or activate a Rider projection. Existing published versions remain unchanged until the replacement passes all invariants in one transaction.

- [x] **Step 5: Add API routes and run focused tests**

Register the router only in the authenticated application path. Add `Cache-Control: no-store` to every review response and use the existing sanitized error envelope. Add integration coverage for AI candidate persistence, review, correction, confirmation, publication, terms-only rejection, object scope, and stale version conflicts.

```bash
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_candidate_migration.py apps/api/tests/test_policy_candidate_api.py apps/api/tests/test_policy_candidate_integration.py -q
TMPDIR=/tmp uv run mypy apps/api/src
git diff --check
git add apps/api/migrations/versions/0004_policy_candidate_review.py apps/api/src/familycare_api/policies apps/api/src/familycare_api/main.py apps/api/src/familycare_api/errors.py apps/api/tests
git commit -m "feat(api): add policy candidate review"
```

Expected: the migration reaches head on synthetic PostgreSQL; all focused API tests pass with no path, document, password, or policy-number leakage.

## Task 4: Add the no-store Web client and read-only ledger projection

**Files:**

- Create: `apps/web/src/api/http.ts`
- Create: `apps/web/src/api/errors.ts`
- Create: `apps/web/src/api/query-cache.ts`
- Create: `apps/web/src/api/ledger.ts`
- Create: `apps/web/src/app/AppRoot.tsx`
- Create: `apps/web/src/app/AppShell.tsx`
- Create: `apps/web/src/features/ledger/LedgerPage.tsx`
- Create: `apps/web/src/features/ledger/FamilyMemberPicker.tsx`
- Create: `apps/web/src/features/ledger/PolicySummaryCard.tsx`
- Create: `apps/web/src/features/ledger/RiderList.tsx`
- Create: `apps/web/src/features/ledger/useLedger.ts`
- Create: `apps/web/src/test/mockApi.ts`
- Create: `apps/web/src/test/renderWithProviders.tsx`
- Test: `apps/web/src/features/ledger/ledger.test.tsx`
- Modify: `apps/web/src/App.tsx`
- Modify: `apps/web/src/styles.css`
- Modify: `apps/web/package.json`
- Modify: `pnpm-lock.yaml`

**Interfaces:**

```ts
export async function apiRequest<T>(
  path: string,
  init?: RequestInit & { csrfToken?: string },
): Promise<T>;

export function useResource<T>(
  key: string,
  loader: (signal: AbortSignal) => Promise<T>,
): { data: T | undefined; loading: boolean; error: ApiError | undefined };

export function useLedger(memberId: string): {
  familyMember: FamilyMember | undefined;
  policies: PolicySummary[];
  reviewCount: number;
  loading: boolean;
  error: ApiError | undefined;
};
```

- [x] **Step 1: Write failing HTTP/cache and ledger tests**

```tsx
it("uses same-origin credentials and does not persist ledger responses", async () => {
  const fetchMock = vi.fn().mockResolvedValue(jsonResponse(SYNTHETIC_LEDGER));
  vi.stubGlobal("fetch", fetchMock);

  renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);
  expect(await screen.findByRole("heading", { name: "Sample Policy" })).toBeInTheDocument();
  expect(fetchMock.mock.calls[0][1]).toMatchObject({
    credentials: "include",
    cache: "no-store",
  });
  expect(localStorage.setItem).not.toHaveBeenCalled();
});
```

Run:

```bash
corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
  src/features/ledger/ledger.test.tsx
```

Expected: FAIL because the client, query cache, route shell, and ledger components do not exist.

- [x] **Step 2: Implement the fetch boundary and memory-only query cache**

Set `credentials: "include"`, `cache: "no-store"`, and `Accept: "application/json"` for every request. Add JSON content type only for JSON bodies. Convert non-2xx responses to stable `ApiError` codes without returning raw response text or request bodies. Use `AbortController` for unmounts. Implement query subscriptions with `useSyncExternalStore`; `clear()` removes all server data on logout and session expiry. Do not import or call any Web Storage API.

- [x] **Step 3: Implement accessible shell and read-only ledger**

Use `/app/members/:memberId/ledger` under the existing SPA fallback. Render one `h1`, a FamilyMember picker sourced from the API, policy summary cards, actual Rider rows, and a visible `NEEDS_REVIEW` count. Do not render candidate fields as enrolled Riders. Add a skip link, semantic `main`, list/table semantics, text labels for every status, and a route heading focus target.

- [x] **Step 4: Add interaction support and safe empty/error states**

Render empty family, no policy, partial API failure, and unauthorized states separately. Do not echo API detail, IDs, source paths, policy numbers, or raw document text. Set `aria-live="polite"` for loading completion and `role="alert"` for safe errors. Add `@testing-library/user-event` as a pinned dev dependency for keyboard tests.

- [x] **Step 5: Run the Web slice and commit**

```bash
corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 src/features/ledger/ledger.test.tsx
corepack pnpm@11.22.0 --filter @familycare/web format:check
corepack pnpm@11.22.0 --filter @familycare/web lint
corepack pnpm@11.22.0 --filter @familycare/web typecheck
git diff --check
git add apps/web pnpm-lock.yaml
git commit -m "feat(web): show policy ledger"
```

Expected: the targeted ledger tests and Web static checks pass; API data exists only in React memory.

## Task 5: Add candidate review UI, Evidence disclosure, and corrections

**Files:**

- Create: `apps/web/src/features/ledger/CandidateReviewQueue.tsx`
- Create: `apps/web/src/features/ledger/CandidateReviewDialog.tsx`
- Create: `apps/web/src/features/ledger/CandidateFieldEditor.tsx`
- Create: `apps/web/src/components/EvidenceDrawer.tsx`
- Create: `apps/web/playwright.config.ts`
- Test: `apps/web/src/features/ledger/candidate-review.test.tsx`
- Test: `apps/web/e2e/ledger.spec.ts`
- Modify: `apps/web/src/api/ledger.ts`
- Modify: `apps/web/src/features/ledger/LedgerPage.tsx`
- Modify: `apps/web/package.json`
- Modify: `pnpm-lock.yaml`

**Interfaces:**

```ts
export function CandidateReviewDialog(props: {
  item: PolicyReviewItem;
  onClose: () => void;
  onConfirmed: () => void;
}): JSX.Element;

export function EvidenceDrawer(props: {
  evidence: EvidenceRef[];
  open: boolean;
  unavailable?: boolean;
  onClose: () => void;
}): JSX.Element;
```

- [x] **Step 1: Write failing review interaction tests**

```tsx
it("keeps a terms-only candidate out of enrolled Riders and exposes its review reason", async () => {
  renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);

  expect(screen.queryByText("Terms-only Rider")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "검토 필요 항목 보기" }));
  expect(screen.getByText("TERMS_ONLY_RIDER")).toBeInTheDocument();
});

it("returns focus to the opener after a correction conflict", async () => {
  const user = userEvent.setup();
  renderWithProviders(<LedgerPage memberId="synthetic-member-a" />);
  const opener = screen.getByRole("button", { name: "후보 검토" });
  await user.click(opener);
  await user.click(screen.getByRole("button", { name: "수정 저장" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("다른 변경이 먼저 저장되었습니다");
  await user.keyboard("{Escape}");
  expect(opener).toHaveFocus();
});
```

Run:

```bash
corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
  src/features/ledger/candidate-review.test.tsx
```

Expected: FAIL because review queue, dialog, Evidence drawer, and mutation handlers do not exist.

- [x] **Step 2: Implement status-grouped review queue and safe terminology**

Show `AI_VERIFIED` records in the ledger, `NEEDS_REVIEW` records in the exception queue, and `USER_CONFIRMED` records with the confirmation actor/time. Use the Korean UI terms `청구 검토`, `추가 확인 필요`, and `조건 불일치` only where applicable; never use `지급 확정`. A review reason is a fixed code mapped to safe copy, not provider prose.

- [x] **Step 3: Implement the keyboard-safe dialog and Evidence drawer**

Use `role="dialog"`, `aria-modal="true"`, a labelled heading, Escape close, focus on the heading or first invalid field, and focus restoration to the opener. Evidence shows document label, physical page, bounded excerpt, and optional coordinates. It never shows path, archive object key, password, policy number, or full extracted text. If Evidence fetch fails or hash is stale, show a stale warning and disable confirmation.

- [x] **Step 4: Implement typed correction and optimistic mutation behavior**

The editor renders only the generated `PolicyCandidateFieldId` enum. Field-level validation blocks invalid dates, negative sums, unsupported currencies, and missing Evidence. Submit `expected_version`, typed value, and selected Evidence ID. On `409 VERSION_CONFLICT`, preserve the unsaved draft, refetch the item, and give a safe retry action. On success, invalidate the ledger and review-item query keys.

- [x] **Step 5: Add mock browser E2E and run the focused suite**

Configure one Chromium worker with `webServer` running `pnpm build && pnpm preview --host 127.0.0.1`, `baseURL` defaulting to `http://127.0.0.1:4173`, and `page.route("**/api/v1/**")` returning synthetic JSON only. The E2E must cover 320px layout, keyboard dialog close, terms-only exclusion, confirmation, and absence of Web Storage writes.

```bash
corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
  src/features/ledger/ledger.test.tsx src/features/ledger/candidate-review.test.tsx
corepack pnpm@11.22.0 --filter @familycare/web exec playwright install --with-deps chromium
corepack pnpm@11.22.0 --filter @familycare/web exec playwright test --workers=1 e2e/ledger.spec.ts
```

Expected: component tests and the synthetic browser flow pass; no network request leaves the test process.

## Task 6: Complete the concentrated PR gate

**Files:**

- Modify only files implicated by a failing focused check or a directly observed contract/privacy defect.
- Do not modify unrelated foundation, clause, decision, claim, or authentication files.
- Test: `scripts/tests/test_policy_candidate_contract.py`, `workers/analyzer/tests/test_policy_ai_pipeline.py`, `apps/api/tests/test_policy_candidate_integration.py`, `apps/web/src/features/ledger/candidate-review.test.tsx`, `apps/web/e2e/ledger.spec.ts`

**Interfaces:**

- Consumes: all Task 1–5 outputs and the root agent's current `main` comparison.
- Produces: one reviewable branch, one PR, passing required checks, and a post-merge focused verification record.

- [ ] **Step 1: Run the complete feature checks serially**

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

Expected: every command exits 0; no command is skipped, interrupted, or replaced by a retry-only result.

- [x] **Step 2: Inspect the complete diff once before push**

Trace candidate input → provider payload → validator → database → API response → UI. Confirm that scope, Evidence lineage, terms-only behavior, status transitions, generated types, no-store headers, memory-only cache, logs, and service-worker output match this plan. Resolve all actionable findings before the push; do not substitute repetitive per-file reviews for this gate.

- [ ] **Step 3: Commit and invoke the shared root PR gate**

```bash
git add \
  packages/contracts/schemas/policy-candidate.v1.schema.json \
  packages/contracts/examples/policy-candidate.v1.json \
  packages/contracts/openapi/familycare.v1.json \
  packages/contracts/README.md \
  scripts/check_policy_candidate_contract.py scripts/generate_web_contract_types.py \
  scripts/check_contracts.py scripts/tests/test_policy_candidate_contract.py \
  workers/analyzer/src/familycare_worker/ai \
  workers/analyzer/src/familycare_worker/runner.py \
  workers/analyzer/tests/fixtures/policy_ai_responses.py \
  workers/analyzer/tests/test_policy_ai_schemas.py \
  workers/analyzer/tests/test_policy_ai_pipeline.py \
  workers/analyzer/tests/test_policy_ai_privacy.py \
  apps/api/migrations/versions/0004_policy_candidate_review.py \
  apps/api/src/familycare_api/policies \
  apps/api/src/familycare_api/main.py apps/api/src/familycare_api/errors.py \
  apps/api/tests/test_policy_candidate_migration.py \
  apps/api/tests/test_policy_candidate_api.py \
  apps/api/tests/test_policy_candidate_integration.py \
  apps/web/src/api apps/web/src/app apps/web/src/components/EvidenceDrawer.tsx \
  apps/web/src/features/ledger apps/web/src/test apps/web/src/App.tsx \
  apps/web/src/styles.css apps/web/package.json apps/web/playwright.config.ts \
  apps/web/e2e/ledger.spec.ts pnpm-lock.yaml
git diff --cached --check
git commit -m "feat: add policy candidate review"
```

Then use the shared Root PR gate in `docs/plan/003-v0.1-implementation-index.md`: verify branch and commit range, push `feat/policy-candidate-review`, open one PR, wait for every required check, merge with a merge commit, fetch `main`, and rerun the focused policy-candidate contract/API/Web tests. Record the PR URL, Actions result, merge commit, and any inaccessible browser/private-data checks. Do not create a release tag.

## Acceptance Matrix

| Requirement | Evidence | Test command |
|---|---|---|
| AI structurer/verifier are separate | fake provider call order and request-shape assertions | `TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_policy_ai_pipeline.py -q` |
| Verifier cannot invent facts/Evidence | rejected synthetic response | `TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_policy_ai_schemas.py -q` |
| Terms-only Rider never publishes | API and UI exclusion tests | `TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_candidate_api.py -q` and targeted Vitest |
| User correction preserves raw/current history | child-version migration and API tests | `TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_candidate_integration.py -q` |
| Household scope is server-derived | cross-scope request tests | `TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_candidate_api.py -q` |
| API/Web contract is generated | checker and regeneration comparison | `TMPDIR=/tmp uv run python scripts/check_contracts.py` |
| Browser state is not persistent | fetch/storage and Playwright assertions | `corepack pnpm@11.22.0 --filter @familycare/web exec playwright test --workers=1 e2e/ledger.spec.ts` |
| 320px and keyboard behavior | Chromium viewport/focus assertions | same Playwright command |

## Deferred but explicitly unverified

- Real OpenAI requests, actual policies, private PDF extraction, and real medical or policy identifiers are not used.
- Windows browser installation, mobile PWA installation, Tailscale access, and production ingress are not proven by this plan.
- Clause search, CoverageRule execution, MedicalEvent decision semantics, claim state transitions, authentication, archive encryption, OCR, and release publishing are consumed by later plans and are not implemented here.
