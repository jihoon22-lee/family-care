# Clause Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Structure synthetic and later private TermsEdition content into Evidence-backed Clause hierarchies and provide PostgreSQL full-text/trigram search without treating a search hit as an insurance decision.

**Architecture:** Extend the existing `familycare_api.clauses` module with a terms repository, Unicode normalization, PostgreSQL `simple` full-text search, and `pg_trgm` title relevance. Search returns bounded Clause excerpts and Evidence references; Rider subscription and CoverageRule publication remain separate use cases in `007-rider-clause-rules.md`. The migration is additive after policy/candidate review and preserves all Phase 1 document/extraction contracts.

**Tech Stack:** Python 3.14, FastAPI 0.141, Pydantic 2.13, direct `psycopg` 3.3 SQL, PostgreSQL 18 `simple` FTS and `pg_trgm`, Alembic 1.19, JSON Schema Draft 2020-12, pytest, Ruff, and strict mypy.

**Spec:** `docs/design/clause-linking-search.md`, `docs/design/data-model.md`, `docs/design/policy-ledger.md`, and `docs/design/v0.1-product.md`

## Global Constraints

- Migration `0005_clause_search.py` revises `0004_policy_candidate_review`; `0004` is the preceding approved candidate-review migration and remains unchanged by this plan.
- The migration preserves every Phase 1 document, DocumentVersion, Extraction, Evidence-coordinate, AnalysisJob state, JSON Schema, and generated document type contract.
- PostgreSQL is the only search engine in v0.1. Use built-in `simple` text search and `pg_trgm`; do not add Redis, Elasticsearch, a vector database, embeddings, or a second search service.
- Search normalizes Unicode NFC, whitespace, and punctuation deterministically. The normalization version is persisted with indexed content and query results.
- 목차 표시 page와 PDF physical page를 구분하고 Evidence에는 항상 1-based physical page를 저장합니다.
- Search is an investigation tool. A search hit never creates a Rider, CoverageRule, `MATCH`, `NO_MATCH`, or payment amount.
- Every Clause Evidence points to the exact `DocumentVersion`, content hash, physical page, and optional bbox. Missing or stale Evidence is surfaced, never silently substituted.
- All endpoints derive `HouseholdScope` on the server. The request cannot choose a household, policy, Rider, or TermsEdition outside that scope.
- soft delete is the default deletion behavior: default queries exclude soft-deleted TermsEdition/Clause rows, and trash/restore is explicit. Updates use an expected version and stale writes return `409 VERSION_CONFLICT`.
- AI candidates may suggest Clause links, but AI is non-authoritative; only the later deterministic validator/publisher can mark a link or rule usable.
- A missing or stale Clause Evidence, ambiguous applicability, or unsupported rule dependency is represented as downstream `UNKNOWN`; a search hit itself never becomes a decision.
- CI uses only wholly synthetic Korean/English Clause corpus and fake provider responses. No private document, extracted text, page image, API key, Google Drive ID, or real identifier is opened or committed.
- Logs and error responses do not include the raw query, full Clause text, source path, archive key, password, policy number, or medical input.

## File Responsibility Map

```text
apps/api/migrations/versions/0005_clause_search.py
  Creates terms_editions, clauses, clause_evidence, and
  clause_search_synonyms; installs pg_trgm indexes without owning insurer logic.

apps/api/src/familycare_api/clauses/__init__.py
apps/api/src/familycare_api/clauses/domain.py
  Defines TermsEdition, Clause, ClauseEvidence, and bounded SearchHit values.

apps/api/src/familycare_api/clauses/normalization.py
  Defines versioned Unicode NFC, whitespace, punctuation, and query normalization.

apps/api/src/familycare_api/clauses/repository.py
  Owns scoped direct-psycopg writes/reads and PostgreSQL search statements.

apps/api/src/familycare_api/clauses/search.py
  Owns query parsing, filters, ranking, bounded excerpts, and search-version checks.

apps/api/src/familycare_api/clauses/service.py
  Owns TermsEdition/Clause use cases and Evidence scope validation.

apps/api/src/familycare_api/clauses/schemas.py
  Defines strict HTTP request/response adapters.

apps/api/src/familycare_api/clauses/router.py
  Defines TermsEdition, hierarchy, and search routes.

apps/api/src/familycare_api/clauses/errors.py
  Maps search/migration/domain failures to fixed sanitized error codes.

packages/contracts/schemas/clause-search.v1.schema.json
packages/contracts/examples/clause-search.v1.json
  Define the versioned language-neutral search response and synthetic example.

apps/api/tests/test_clause_search_migration.py
apps/api/tests/test_clause_normalization.py
apps/api/tests/test_clause_search.py
apps/api/tests/test_clause_search_integration.py
apps/api/tests/test_clause_privacy.py
  Cover migration shape, normalization/ranking, real PostgreSQL FTS/trigram,
  date/scope filters, and query/text redaction.
```

`apps/api/src/familycare_api/main.py`, `apps/api/src/familycare_api/errors.py`, `scripts/check_contracts.py`, and the committed OpenAPI artifact are root integration files. The feature implementation may provide the router and contract tests; the root agent registers and regenerates them after the focused feature suite passes.

## Database, Python, HTTP, and JSON Interfaces

### Migration contract

```text
terms_editions(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  document_version_id uuid references document_versions(id),
  insurer_display varchar(160) not null,
  insurer_key varchar(160) not null,
  product_display varchar(200) not null,
  product_key varchar(200) not null,
  applicability_start date null,
  applicability_end date null,
  content_sha256 varchar(64) not null,
  normalization_version varchar(32) not null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

clauses(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  terms_edition_id uuid references terms_editions(id),
  parent_clause_id uuid null references clauses(id),
  clause_type varchar(32) not null,
  label varchar(160) not null,
  normalized_title text not null,
  normalized_text text not null,
  search_vector tsvector not null,
  physical_page_start integer not null,
  physical_page_end integer not null,
  normalization_version varchar(32) not null,
  version integer not null default 1,
  created_at timestamptz not null,
  updated_at timestamptz not null,
  deleted_at timestamptz null
)

clause_evidence(
  clause_id uuid references clauses(id),
  evidence_id uuid references evidence(id),
  primary key (clause_id, evidence_id)
)

clause_search_synonyms(
  id uuid primary key,
  household_space_id uuid references household_spaces(id),
  synonym_key varchar(160) not null,
  replacement_text varchar(320) not null,
  dictionary_version varchar(32) not null,
  created_at timestamptz not null,
  created_by varchar(32) not null
)
```

Use named CHECK constraints for Clause types `chapter`, `section`, `article`, `paragraph`, `item`, `special_terms`, `definition`, `appendix`, `table`, page ranges `>= 1` and `start <= end`, hash shape, allowed normalization version, version `>= 1`, and non-empty labels. Create a GIN index on `search_vector`, a GIN/GiST trigram index on `normalized_title`, and scope/date indexes. Use `CREATE EXTENSION IF NOT EXISTS pg_trgm`; do not drop the shared extension on downgrade.

### Python interfaces

```python
NORMALIZATION_VERSION = "unicode-nfc-v1"


def normalize_clause_text(text: str) -> str: ...
def normalize_search_query(query: str) -> str: ...
def bounded_excerpt(text: str, *, max_chars: int = 320) -> str: ...


@dataclass(frozen=True)
class ClauseSearchFilters:
    terms_edition_id: UUID | None = None
    effective_on: date | None = None
    insurer_key: str | None = None
    product_key: str | None = None


@dataclass(frozen=True)
class ClauseSearchHit:
    clause_id: UUID
    label: str
    excerpt: str
    terms_edition_id: UUID
    physical_page_start: int
    physical_page_end: int
    evidence: tuple[EvidenceRef, ...]
    relevance: Decimal
    normalization_version: str


class ClauseSearchService(Protocol):
    def search(
        self, scope: HouseholdScope, query: str, filters: ClauseSearchFilters, *, limit: int = 20
    ) -> tuple[ClauseSearchHit, ...]: ...
```

The repository query uses bound parameters and PostgreSQL operators only:

```sql
WHERE c.household_space_id = %(household_space_id)s
  AND c.deleted_at IS NULL
  AND plainto_tsquery('simple', %(normalized_query)s) @@ c.search_vector
  AND (%(terms_edition_id)s IS NULL OR c.terms_edition_id = %(terms_edition_id)s)
ORDER BY ts_rank_cd(c.search_vector, plainto_tsquery('simple', %(normalized_query)s)) DESC,
         similarity(c.normalized_title, %(normalized_query)s) DESC,
         c.physical_page_start,
         c.id
LIMIT %(limit)s
```

### HTTP contract

```text
GET /api/v1/terms-editions
GET /api/v1/terms-editions/{id}/clauses
POST /api/v1/clauses/search
```

`POST /clauses/search` accepts a no-store JSON body with `q`, optional `terms_edition_id`, `effective_on`, insurer/product filters, and bounded `limit`. POST is deliberate so the sensitive search phrase does not enter a URL, browser history, reverse-proxy access log, or referrer. Rider-specific filtering is added after `rider_clause_links` exists in `0006`; this search migration does not invent that relationship. It returns `schema_version`, `normalization_version`, `query_matched_count`, and `hits[]`; each hit has a bounded excerpt, Clause label, TermsEdition ID, page range, and Evidence. It never returns the full normalized Clause body. An empty or overlong query returns `422 INVALID_REQUEST` without echoing the value.

### JSON Schema contract

`clause-search.v1.schema.json` uses `additionalProperties: false`. A hit requires `clause_id`, `label`, `excerpt`, `terms_edition_id`, `physical_page_start`, `physical_page_end`, `evidence`, and `normalization_version`. `excerpt` has a maximum length of 320; `evidence.page_number` has minimum 1; UUIDs use `format: uuid`; `schema_version` is constant `"1"`. The example uses synthetic labels/text and contains no source path or private identifier.

## Tasks

### Task 1: Define the PostgreSQL terms/search migration

**Files:**
- Create: `apps/api/migrations/versions/0005_clause_search.py`
- Create: `apps/api/tests/test_clause_search_migration.py`
- Test: `apps/api/tests/test_clause_search_migration.py`

**Interfaces:**
- Consumes: `0004_policy_candidate_review`, `household_spaces`, `evidence`, `document_versions`, and the existing Alembic migration-spy conventions.
- Produces: `revision = "0005_clause_search"`, `down_revision = "0004_policy_candidate_review"`, four terms/search tables, pg_trgm indexes, named checks, and reverse dependency downgrade.

- [x] **Step 1: Write failing migration tests.** Assert the revision chain, exact table/column names, Clause parent FK, Evidence join FK, 1-based page checks, normalization version, tsvector column, GIN/trigram indexes, and preservation of all Phase 1 table names.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search_migration.py -q
  ```

  Expected: FAIL because `0005_clause_search.py` is absent and no terms/search tables exist.

- [x] **Step 3: Implement the minimum additive migration.** Use `op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")`, `sa.dialects.postgresql.TSVECTOR`, named indexes, and no destructive extension drop in downgrade.

  ```python
  revision = "0005_clause_search"
  down_revision = "0004_policy_candidate_review"

  op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
  op.create_index(
      "ix_clauses_search_vector",
      "clauses",
      ["search_vector"],
      postgresql_using="gin",
  )
  ```

- [x] **Step 4: Run migration-shape and PostgreSQL migration checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search_migration.py -q
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  ```

  Expected: migration-shape tests pass and a synthetic PostgreSQL reaches `0005_clause_search` with `pg_trgm` available.

- [x] **Step 5: Commit the migration.**

  ```bash
  git add apps/api/migrations/versions/0005_clause_search.py apps/api/tests/test_clause_search_migration.py
  git commit -m "feat(db): add terms clause search schema"
  ```

### Task 2: Implement versioned text normalization and synthetic corpus fixtures

**Files:**
- Create: `apps/api/src/familycare_api/clauses/__init__.py`
- Create: `apps/api/src/familycare_api/clauses/domain.py`
- Create: `apps/api/src/familycare_api/clauses/normalization.py`
- Create: `apps/api/tests/test_clause_normalization.py`
- Create: `fixtures/synthetic/terms-search-corpus.json`
- Test: `apps/api/tests/test_clause_normalization.py`

**Interfaces:**
- Consumes: `EvidenceRef` and `HouseholdScope` from `familycare_api.common`.
- Produces: `NORMALIZATION_VERSION`, `normalize_clause_text`, `normalize_search_query`, `bounded_excerpt`, `TermsEdition`, `Clause`, `ClauseSearchFilters`, `ClauseSearchHit`, and a fully synthetic Korean/English corpus.

- [x] **Step 1: Write failing normalization tests.** Cover NFC composition, repeated whitespace, punctuation boundaries, Korean synthetic terms, empty/overlong queries, deterministic bounded excerpts, and rejection of non-string values.

- [x] **Step 2: Run the focused RED test.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_normalization.py -q
  ```

  Expected: FAIL because the normalization module and versioned functions do not exist.

- [x] **Step 3: Implement the smallest deterministic normalizer.** Do not use external dictionaries or private document text. Preserve meaningful Unicode characters, collapse whitespace, normalize punctuation separators, and keep the normalization version explicit.

  ```python
  def normalize_clause_text(text: str) -> str:
      if not isinstance(text, str):
          raise TypeError("clause text must be text")
      normalized = unicodedata.normalize("NFC", text)
      normalized = re.sub(r"[\\t\\r\\n\\f\\v]+", " ", normalized)
      normalized = re.sub(r"\\s{2,}", " ", normalized)
      return normalized.strip()
  ```

- [x] **Step 4: Run normalization and safety checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_normalization.py -q
  TMPDIR=/tmp uv run python scripts/check_repository_safety.py
  ```

  Expected: all normalization cases pass and the corpus is accepted as wholly synthetic.

- [x] **Step 5: Commit the normalization boundary.**

  ```bash
  git add apps/api/src/familycare_api/clauses/__init__.py apps/api/src/familycare_api/clauses/domain.py apps/api/src/familycare_api/clauses/normalization.py apps/api/tests/test_clause_normalization.py fixtures/synthetic/terms-search-corpus.json
  git commit -m "feat(clauses): add versioned text normalization"
  ```

### Task 3: Add scoped repositories and PostgreSQL FTS/trigram search

**Files:**
- Create: `apps/api/src/familycare_api/clauses/repository.py`
- Create: `apps/api/src/familycare_api/clauses/search.py`
- Create: `apps/api/src/familycare_api/clauses/service.py`
- Create: `apps/api/tests/test_clause_search.py`
- Create: `apps/api/tests/test_clause_search_integration.py`
- Test: `apps/api/tests/test_clause_search.py`
- Test: `apps/api/tests/test_clause_search_integration.py`

**Interfaces:**
- Consumes: normalized domain types and migration indexes from Tasks 1–2.
- Produces: `TermsEditionRepository.list/get/create`, `ClauseRepository.create_tree/get_hierarchy`, `ClauseSearchService.search`, date/scope filters, bounded excerpts, and deterministic ranking.

- [x] **Step 1: Write failing repository and search tests.** Unit-test parameter construction and ranking tie-breakers. Integration-test a synthetic corpus for exact phrase-ish matches, whitespace variants, Korean query normalization, terms-date boundaries, insurer/product filters, scope exclusion, and same-title different-definition separation.

- [x] **Step 2: Run the focused RED tests.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search.py apps/api/tests/test_clause_search_integration.py -q
  ```

  Expected: FAIL because repository/search modules and PostgreSQL Clause rows are not implemented.

- [x] **Step 3: Implement direct-psycopg repository and search service.** Use only bound parameters, derive all scope filters from `HouseholdScope`, select no full body in search responses, use `simple` FTS plus trigram rank, and preserve existing index version when a rebuild is in progress.

  ```python
  def search(
      self,
      scope: HouseholdScope,
      query: str,
      filters: ClauseSearchFilters,
      *,
      limit: int = 20,
  ) -> tuple[ClauseSearchHit, ...]:
      normalized = normalize_search_query(query)
      if not normalized or len(normalized) > 160:
          raise InvalidSearchQuery
      rows = self._connection.execute(
          SEARCH_SQL,
          {
              "household_space_id": scope.household_space_id,
              "normalized_query": normalized,
              "terms_edition_id": filters.terms_edition_id,
              "effective_on": filters.effective_on,
              "limit": min(limit, 50),
          },
      ).fetchall()
      return tuple(_row_to_hit(row) for row in rows)
  ```

- [x] **Step 4: Run unit, PostgreSQL, and static checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_clause_search_integration.py -q
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/clauses
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/clauses
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/clauses
  ```

  Expected: unit and real PostgreSQL FTS/trigram tests pass; no SQLite substitute is accepted.

- [x] **Step 5: Commit the search implementation.**

  ```bash
  git add apps/api/src/familycare_api/clauses/repository.py apps/api/src/familycare_api/clauses/search.py apps/api/src/familycare_api/clauses/service.py apps/api/tests/test_clause_search.py apps/api/tests/test_clause_search_integration.py
  git commit -m "feat(api): add PostgreSQL clause search"
  ```

### Task 4: Expose terms and search HTTP contracts

**Files:**
- Create: `apps/api/src/familycare_api/clauses/schemas.py`
- Create: `apps/api/src/familycare_api/clauses/router.py`
- Create: `apps/api/src/familycare_api/clauses/errors.py`
- Create: `packages/contracts/schemas/clause-search.v1.schema.json`
- Create: `packages/contracts/examples/clause-search.v1.json`
- Create: `apps/api/tests/test_clause_search_api.py`
- Create: `apps/api/tests/test_clause_search_contracts.py`
- Modify: `apps/api/src/familycare_api/main.py` through the root integration step
- Modify: `apps/api/src/familycare_api/errors.py` through the root integration step
- Modify: `scripts/check_contracts.py` through the root integration step
- Test: `apps/api/tests/test_clause_search_api.py`
- Test: `apps/api/tests/test_clause_search_contracts.py`

**Interfaces:**
- Consumes: `ClauseSearchService.search` and terms repositories from Task 3.
- Produces: strict `TermsEditionResponse`, `ClauseHierarchyResponse`, `ClauseSearchResponse`, the three GET routes, and the `clause-search.v1` schema/example.

- [ ] **Step 1: Write failing HTTP and contract tests.** Assert exact route status codes, scope filtering, bounded excerpts, Evidence page numbering, no full Clause body, `additionalProperties: false`, synthetic example validation, and invalid query errors without query echo.

- [ ] **Step 2: Run the focused RED tests.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search_api.py apps/api/tests/test_clause_search_contracts.py -q
  ```

  Expected: FAIL because the router, response models, schema, and contract checker registration are absent.

- [ ] **Step 3: Implement the strict HTTP adapters and schema.** Map service errors to stable fixed messages, enforce `limit <= 50`, return bounded excerpts only, and include `normalization_version` so stale search indexes are visible.

  ```python
  class ClauseSearchQuery(BaseModel):
      model_config = ConfigDict(extra="forbid", frozen=True)
      q: str = Field(min_length=1, max_length=160)
      terms_edition_id: UUID | None = None
      effective_on: date | None = None
      limit: int = Field(default=20, ge=1, le=50)
  ```

- [ ] **Step 4: Run API/contract/privacy checks.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search_api.py apps/api/tests/test_clause_search_contracts.py apps/api/tests/test_clause_privacy.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py --write-openapi
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  ```

  Expected: HTTP and JSON Schema tests pass; committed OpenAPI matches FastAPI; no raw query or full text appears in responses/errors.

- [ ] **Step 5: Commit the HTTP contract.**

  ```bash
  git add apps/api/src/familycare_api/clauses/schemas.py apps/api/src/familycare_api/clauses/router.py apps/api/src/familycare_api/clauses/errors.py packages/contracts/schemas/clause-search.v1.schema.json packages/contracts/examples/clause-search.v1.json apps/api/tests/test_clause_search_api.py apps/api/tests/test_clause_search_contracts.py
  git commit -m "feat(contracts): expose clause search"
  ```

### Task 5: Add the private no-store Clause search Web page

**Files:**
- Create: `apps/web/src/api/clauses.ts`
- Create: `apps/web/src/features/clauses/ClauseSearchPage.tsx`
- Create: `apps/web/src/features/clauses/ClauseSearchFilters.tsx`
- Create: `apps/web/src/features/clauses/ClauseSearchResults.tsx`
- Create: `apps/web/src/features/clauses/ClauseHierarchy.tsx`
- Create: `apps/web/src/features/clauses/clause-search.test.tsx`
- Create: `apps/web/e2e/clause-search.spec.ts`
- Modify: `apps/web/src/app/AppRoutes.tsx`
- Modify: `apps/web/src/styles.css`

**Interfaces:**
- Consumes: generated terms-edition, hierarchy, and `POST /api/v1/clauses/search` contracts through the shared no-store client.
- Produces: `/app/clauses/search` with an in-memory search phrase, date/edition filters, bounded results, hierarchy context, and Evidence actions.
- Search text is never placed in the URL, browser history, Web Storage, IndexedDB, service-worker cache, console, analytics, or error copy.

- [ ] **Step 1: Write failing component and privacy tests.** Cover keyboard submit, JSON-body search, no query parameter, filter reset, empty/invalid state, bounded excerpts, exact physical page/Evidence label, stale-index warning, abort of an obsolete request, and zero persistent storage writes.

  ```bash
  corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
    src/features/clauses/clause-search.test.tsx
  ```

  Expected: FAIL because the generated client, route, and Clause components do not exist.

- [ ] **Step 2: Implement the generated client and accessible page.** Send the phrase in a POST JSON body through `apiRequest`, keep it in component memory, and clear it on logout/session expiry. Render `<form role="search">`, labelled filters, semantic result lists, text status, and an Evidence button. Never render the full normalized body or raw server error.

- [ ] **Step 3: Run GREEN and a synthetic Playwright flow.** The browser stub accepts only POST, rejects a URL containing `q`, and returns wholly synthetic Korean/English Clause snippets.

  ```bash
  corepack pnpm@11.22.0 --filter @familycare/web exec vitest run --maxWorkers=1 \
    src/features/clauses/clause-search.test.tsx
  corepack pnpm@11.22.0 --filter @familycare/web exec playwright test \
    --workers=1 e2e/clause-search.spec.ts
  corepack pnpm@11.22.0 web:check
  ```

  Expected: search, filters, Evidence navigation, focus, 320 CSS px layout, and browser privacy assertions pass.

- [ ] **Step 4: Commit the Web search slice.**

  ```bash
  git add apps/web/src/api/clauses.ts apps/web/src/features/clauses \
    apps/web/src/app/AppRoutes.tsx apps/web/src/styles.css \
    apps/web/e2e/clause-search.spec.ts
  git commit -m "feat(web): add private clause search"
  ```

### Task 6: Verify stale-index, privacy, and search acceptance

**Files:**
- Create: `apps/api/tests/test_clause_privacy.py`
- Modify: `apps/api/tests/test_clause_search.py` for stale normalization/index cases
- Modify: `apps/api/tests/test_clause_search_integration.py` for atomic rebuild and partial Clause parse cases
- Test: `apps/api/tests/test_clause_privacy.py`
- Test: `apps/api/tests/test_clause_search_integration.py`

**Interfaces:**
- Consumes: complete migration, normalization, repository/search service, HTTP contract, and Evidence repository.
- Produces: synthetic proof that searchable Clauses survive an individual parse failure, old index versions remain available until atomic swap, wrong scope/date editions are excluded, and logs/responses contain no query or full text.

- [ ] **Step 1: Write failing stale/partial/privacy tests.** Assert one malformed synthetic Clause does not remove other rows, normalization-version mismatch is explicit rather than silent fallback, and captured logs omit raw query/result text/path.

- [ ] **Step 2: Run the focused RED command.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_privacy.py apps/api/tests/test_clause_search.py apps/api/tests/test_clause_search_integration.py -q
  ```

  Expected: FAIL until stale-version checks, partial rebuild behavior, and redaction are implemented.

- [ ] **Step 3: Implement the minimum stale/index and redaction behavior.** Keep the previous normalized/search version readable until the new rows and indexes are complete; return a stable `SEARCH_INDEX_VERSION_MISMATCH` warning/error without exposing SQL or document text.

- [ ] **Step 4: Run the complete focused feature suite.**

  ```bash
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search_migration.py apps/api/tests/test_clause_normalization.py apps/api/tests/test_clause_search.py apps/api/tests/test_clause_search_api.py apps/api/tests/test_clause_search_contracts.py apps/api/tests/test_clause_privacy.py -q
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_clause_search_integration.py -q
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run ruff format --check apps/api/src/familycare_api/clauses apps/api/tests/test_clause_search_migration.py apps/api/tests/test_clause_normalization.py apps/api/tests/test_clause_search.py apps/api/tests/test_clause_search_api.py apps/api/tests/test_clause_search_contracts.py apps/api/tests/test_clause_search_integration.py apps/api/tests/test_clause_privacy.py
  TMPDIR=/tmp uv run ruff check apps/api/src/familycare_api/clauses apps/api/tests
  TMPDIR=/tmp uv run mypy apps/api/src/familycare_api/clauses
  ```

  Expected: all focused tests and static checks pass with a wholly synthetic corpus and no external service.

- [ ] **Step 5: Commit the verified search acceptance.**

  ```bash
  git add apps/api/tests/test_clause_privacy.py apps/api/tests/test_clause_search.py apps/api/tests/test_clause_search_integration.py
  git commit -m "test(clauses): verify scoped search boundaries"
  ```

## Focused Post-Merge Verification

- [ ] **Step 1: Verify the migration chain and PostgreSQL search after merge.**

  ```bash
  TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
  TMPDIR=/tmp uv run pytest -m integration apps/api/tests/test_clause_search_integration.py -q
  ```

  Expected: the merged chain reaches `0005_clause_search` or a later descendant, `pg_trgm` exists, and FTS/trigram/date/scope tests pass.

- [ ] **Step 2: Verify contracts and privacy.**

  ```bash
  TMPDIR=/tmp uv run python scripts/check_contracts.py
  TMPDIR=/tmp uv run pytest apps/api/tests/test_clause_search_api.py apps/api/tests/test_clause_search_contracts.py apps/api/tests/test_clause_privacy.py -q
  ```

  Expected: committed OpenAPI/schema artifacts match and no raw query, full Clause text, path, or private identifier appears in response/log assertions.

- [ ] **Step 3: Apply the shared Root PR gate.** Follow `docs/plan/003-v0.1-implementation-index.md`: inspect the complete diff once immediately before push, run the serial repository gate, wait for required CI, merge, and record the PR URL, merge commit, Actions result, and unverified real-data/device boundaries.
