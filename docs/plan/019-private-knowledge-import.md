# Private Knowledge Import Implementation Plan

**Status:** Tasks 1–3 complete; Task 4 in progress; Tasks 5–8 pending

**Goal:** Import the externally reviewed insurance-analysis package into PostgreSQL as a lossless, immutable, household-scoped knowledge snapshot; make it safely queryable; and keep enrollment, terms applicability, semantic facts, and executable rules as separate authorities.

**Architecture:** Add a `private_knowledge_*` relational catalog beside the existing policy ledger. Preserve each validated source record as bounded JSON while indexing the fields required for reconciliation and queries. Bind family aliases, operational policies, Riders, and DocumentVersions only through explicit evidence-backed links. A read-only dry run produces an approved report digest and database-baseline digest; one atomic apply transaction rechecks both before selecting the new snapshot as current.

**Spec:** `docs/design/private-knowledge-catalog.md`, `docs/design/policy-ledger.md`, `docs/design/data-model.md`, and `docs/plan/018-insurance-document-inventory.md`

## Preconditions and fixed boundaries

- The supported input schema is `private-analysis-package.sol-v2` and the package remains outside the repository.
- The package reader accepts only an absolute external directory with mode `0700`, regular mode-`0600` files, a verified manifest, and no symlink traversal.
- Tests construct synthetic records such as `Family Member A`, `Sample Policy`, and `synthetic-policy-001`; no actual names, source aliases, amounts, paths, text, identifiers, or hashes enter Git, logs, snapshots, or fixtures.
- The certificate-backed enrollment decision, terms document identity, terms-edition applicability, coverage-to-section mapping, and current contract status remain independent tri-state fields.
- Imported facts and mappings are always `executable=false`. This plan does not publish a `CoverageRule`, calculate a benefit, or make a claim decision.
- `PrivateKnowledgeSubject` owns the package family alias and optional `FamilyMember` binding. Its decision remains `MATCH`/`NO_MATCH`/`UNKNOWN`, with conflict stored separately. Contract rows reference the subject instead of repeating or guessing member identity.
- Existing `PolicyContract`, `Rider`, `TermsEdition`, `Clause`, and `CoverageRule` rows are not overwritten by import. Optional bindings require exact internal Evidence or explicit later confirmation.
- Actual apply follows backup → dry run → expected-value comparison → disposable-database apply/verify → real apply → post-apply verification.

## Public Python contracts

The new module lives under `apps/api/src/familycare_api/private_knowledge/` and exposes these boundaries:

```python
def load_private_knowledge_package(
    root: Path,
    *,
    repository_root: Path,
) -> PrivateKnowledgePackage: ...

def canonical_package_digest(package: PrivateKnowledgePackage) -> str: ...

class PrivateKnowledgeRepository:
    def read_baseline(self, household_space_id: UUID) -> KnowledgeDatabaseBaseline: ...
    def apply_snapshot(
        self,
        package: PrivateKnowledgePackage,
        *,
        household_space_id: UUID,
        actor_id: UUID,
        approved_report: KnowledgeDryRunReport,
    ) -> AppliedKnowledgeSnapshot: ...
    def verify_current(self, household_space_id: UUID) -> KnowledgeSnapshotSummary: ...

def build_dry_run_report(
    package: PrivateKnowledgePackage,
    baseline: KnowledgeDatabaseBaseline,
) -> KnowledgeDryRunReport: ...
```

The CLI entry point is `familycare-private-knowledge`. It supports `validate`, `dry-run`, `apply`, and `verify`. Private paths, the database URL, actor ID, household ID, report path, and approval digest are environment-only inputs. Standard output contains only stable status/reason codes, opaque internal IDs, and counts.

## Task 1: Freeze the approved design and executable plan

**Files:** `docs/design/private-knowledge-catalog.md`, `docs/plan/019-private-knowledge-import.md`, documentation checks.

1. [x] Record the immutable-snapshot architecture, authority boundaries, package contract, validation rules, dry-run/apply lifecycle, query boundary, and operational apply gate.
2. [x] Separate package family aliases from contracts through an explicit subject binding in this plan; update the design before schema implementation.
3. [x] Run documentation and diff checks, inspect the rendered plan for private values or placeholders, and commit the plan as a standalone review unit.

## Task 2: Add the immutable relational catalog

**Files:** `apps/api/migrations/versions/0018_private_knowledge_catalog.py`, `apps/api/tests/test_private_knowledge_migration.py`, `apps/api/tests/test_private_knowledge_migration_integration.py`.

1. [x] Write RED migration tests for revision `0018_private_knowledge_catalog` with down revision `0017_insurance_inventory`.
2. [x] Require the migration to create these tables in dependency order:
   - `private_knowledge_import_runs`
   - `private_knowledge_subjects`
   - `private_knowledge_contracts`
   - `private_knowledge_coverages`
   - `private_knowledge_terms_assignments`
   - `private_knowledge_terms_assignment_sources`
   - `private_knowledge_terms_sections`
   - `private_knowledge_source_clauses`
   - `private_knowledge_facts`
   - `private_knowledge_fact_citations`
   - `private_knowledge_coverage_terms_mappings`
   - `private_knowledge_document_bindings`
3. [x] Test household-scoped foreign keys, actor provenance, optional exact bindings, immutable source keys, source-record digests, bounded JSON projections, UTC timestamps, and unique canonical identity per import run.
4. [x] Test database checks for tri-state decisions, contract/component/benefit classifications, import states, nonnegative amounts and pages, SHA-256 shape, page ranges, and `executable = false`.
5. [x] Test a partial unique index allowing exactly one current applied snapshot per household and a unique `(household_space_id, package_digest_sha256)` idempotency key.
6. [x] Test that child rows cannot be soft-deleted or republished as operational rules and that nullable bindings use `ON DELETE RESTRICT`.
7. [x] Implement the forward-only additive migration and a dependency-safe downgrade used only by synthetic tests.
8. [x] Run migration unit tests, upgrade a disposable PostgreSQL database from base to head, verify constraints/indexes with catalog queries, and downgrade/upgrade the disposable database once.

## Task 3: Validate and normalize the external package before DB access

**Files:** `apps/api/src/familycare_api/private_knowledge/__init__.py`, `models.py`, `package.py`, `errors.py`, `apps/api/tests/private_knowledge_fixtures.py`, `apps/api/tests/test_private_knowledge_package.py`, `apps/api/tests/test_private_knowledge_privacy.py`.

1. [x] Build a synthetic `private-analysis-package.sol-v2` fixture writer that creates the required eight role files, a canonical manifest, correct byte sizes and SHA-256 values, directory mode `0700`, and file mode `0600`.
2. [x] Write RED happy-path tests that load contracts, subjects, enrolled components, independent terms assignments, sections, clauses, facts, citations, and mappings without dropping any validated source field.
3. [x] Write RED failure tests for a repository-contained root, relative root, symlink root/file, nonregular file, wrong modes, duplicate or unexpected manifest entry, hash/size mismatch, schema drift, file/row/byte limit overflow, duplicate canonical key, broken reference, invalid enum/date/currency/amount/page/hash, and executable input.
4. [x] Write RED reconciliation tests proving declared file/record counts and cross-file hierarchy exactly equal the normalized package.
5. [x] Write RED privacy tests proving `PrivateKnowledgePackageError` contains only a stable reason code, file role, and row number; it must not echo a JSON value, source alias, path, hash, diagnosis, DSN, or statement.
6. [x] Implement strict Pydantic models with `extra="forbid"`, bounded strings/arrays/maps, finite numbers, and explicit `MATCH`/`NO_MATCH`/`UNKNOWN` semantics.
7. [x] Implement descriptor-safe reads: validate metadata before open, open without following symlinks, compare `fstat`, read within limits, verify bytes, and reject any post-validation replacement.
8. [x] Canonicalize each accepted record, calculate its row digest, preserve the bounded source record for lossless import, and calculate one deterministic package digest independent of filesystem ordering.
9. [x] Run the focused unit/privacy tests and static checks for this module.

## Task 4: Produce a read-only reconciliation report

**Files:** `apps/api/src/familycare_api/private_knowledge/reconciliation.py`, `repository.py`, `service.py`, `apps/api/tests/test_private_knowledge_reconciliation.py`, `apps/api/tests/test_private_knowledge_repository.py`, `apps/api/tests/test_private_knowledge_reconciliation_integration.py`.

1. [ ] Write RED tests for a `KnowledgeDatabaseBaseline` digest over the current snapshot plus scoped operational FamilyMember, PolicyContract, Rider, DocumentVersion, and Evidence identity/version metadata needed for exact binding.
2. [ ] Write RED cases for first import, same-digest idempotent no-op, new digest supersede, unbound subjects, candidate operational matches, exact document bindings, conflicts, and blocked executable facts.
3. [ ] Ensure product/insurer text similarity is reported only as a review candidate and never creates `MATCH`, `family_member_id`, `policy_contract_id`, `rider_id`, or `document_version_id`.
4. [ ] Implement the baseline read in a PostgreSQL `REPEATABLE READ READ ONLY` transaction scoped by `household_space_id`.
5. [ ] Build a count-only `KnowledgeDryRunReport` containing schema/package digest, baseline digest, create/no-op/supersede result, per-entity input/expected counts, independent decision matrices, binding counts, conflict/block counts, and expected post-apply current snapshot counts.
6. [ ] Canonicalize the report and calculate `report_digest_sha256`; exclude paths, source aliases, statements, display values, IDs other than opaque run IDs, SQL, DSN, and credentials.
7. [ ] Persist the report outside the repository with mode `0600` using atomic replacement, then reread and rehash it before approval.
8. [ ] Run focused unit and PostgreSQL integration tests, including an assertion that dry run performs no write.

## Task 5: Apply one snapshot atomically and verify it

**Files:** `apps/api/src/familycare_api/private_knowledge/repository.py`, `service.py`, `apps/api/tests/test_private_knowledge_apply.py`, `apps/api/tests/test_private_knowledge_integration.py`.

1. [ ] Write RED PostgreSQL tests proving apply rejects a changed package digest, report digest, database baseline, household, actor, entity count, unresolved conflict, executable row, and cross-household binding.
2. [ ] Write RED tests proving the same package digest returns the existing applied run without inserting children and a new package supersedes exactly one prior current run.
3. [ ] Write RED rollback tests that inject a failure after each entity group and prove the old current snapshot remains selected with no partial new snapshot.
4. [ ] Implement one transaction that advisory-locks the household import scope, recomputes the baseline, validates the approval, inserts import run/subjects/contracts/coverages/assignments/sections/clauses/facts/citations/mappings/bindings, compares persisted counts, supersedes the previous run, and selects the new run current.
5. [ ] Store package records only through parameterized SQL and validated JSON adapters. Never evaluate a stored condition, expression, template, SQL fragment, or provider payload.
6. [ ] Implement commit-uncertainty recovery by querying the household/package unique key; do not blindly retry a mutation.
7. [ ] Implement `verify_current` to re-count all child tables, validate parent/child referential closure, recompute decision matrices and row digests, confirm one current run, and return a bounded summary.
8. [ ] Run focused apply/rollback/idempotency tests twice against a disposable PostgreSQL database.

## Task 6: Add the private CLI and bounded read API

**Files:** `apps/api/src/familycare_api/private_knowledge/cli.py`, `schemas.py`, `router.py`, `apps/api/src/familycare_api/main.py`, `apps/api/pyproject.toml`, `apps/api/tests/test_private_knowledge_cli.py`, `apps/api/tests/test_private_knowledge_api.py`, `apps/api/tests/test_private_knowledge_contracts.py`, `packages/contracts/schemas/private-knowledge.v1.schema.json`, `packages/contracts/examples/private-knowledge.v1.json`, `packages/contracts/openapi/familycare.v1.json`, `apps/api/src/familycare_api/contracts/generated_business.py`, `apps/web/src/api/generated.ts`, `scripts/check_contracts.py`, `packages/contracts/README.md`.

1. [ ] Write RED CLI tests for missing/invalid environment, validate, dry-run, apply, verify, stale approval, output-file mode, stable exit codes, sanitized stdout/stderr, and no command-line private path/DSN/actor arguments.
2. [ ] Add `familycare-private-knowledge = "familycare_api.private_knowledge.cli:main"` and implement commands that construct dependencies only after package validation.
3. [ ] Write RED authenticated API tests for:
   - `GET /api/v1/private-knowledge/current`
   - `GET /api/v1/private-knowledge/current/contracts`
   - `GET /api/v1/private-knowledge/current/contracts/{contract_id}`
4. [ ] Require HouseholdScope, bounded cursor pagination, stable internal IDs, independent decision fields, fact citations, `executable=false`, `Cache-Control: no-store`, and `404` for another household.
5. [ ] Exclude package paths, source/document aliases, raw source records, policy numbers, source hashes, full statements from list responses, database IDs for optional cross-system bindings, credentials, and provider payloads. Expose a bounded semantic statement only in authenticated contract detail with its citation metadata.
6. [ ] Add a versioned transport-neutral JSON Schema and wholly synthetic example, include the router in the app, regenerate OpenAPI/Python/TypeScript artifacts with repository generators, and extend contract/privacy drift checks.
7. [ ] Run focused CLI/API/contract/privacy tests and validate the committed generated artifacts are deterministic.

## Task 7: Complete synthetic and repository verification

**Files:** all files changed by Tasks 1–6, `docs/design/private-knowledge-catalog.md`, `docs/plan/019-private-knowledge-import.md`.

1. [ ] Run the complete synthetic package flow twice: validate → dry-run → approve exact digest → apply → verify → idempotent apply.
2. [ ] Review the full diff for enrollment authority, terms-edition independence, subject binding, household scope, row preservation, digest/stale checks, transaction rollback, query bounds, logging, cache, and absence of actual data.
3. [ ] Run the required repository verification serially:

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

4. [ ] Re-run the migration and private-knowledge PostgreSQL integration groups after the full suite so their latest evidence is explicit.
5. [ ] Commit the tested implementation in reviewable Conventional Commit units and request a final code review before operational apply.

## Task 8: Apply the reviewed package to the real FamilyCare database

**Files:** no actual package, report, backup, query output, or private values are added to the repository.

1. [ ] Reconfirm the target FamilyCare containers, PostgreSQL database, current migration, household, operator, free disk/memory, active sessions, and ownership without stopping any other session's process or container.
2. [ ] Create a timestamped custom-format PostgreSQL backup outside Git, set mode `0600`, calculate its SHA-256, and verify it with `pg_restore --list`.
3. [ ] Validate the actual package locally and record only sanitized aggregate counts and the validation result.
4. [ ] Run real DB dry-run, compare every package count, decision matrix, create/no-op/conflict/block count, baseline digest, and expected post-apply count against the externally reviewed reconciliation. Stop on any mismatch.
5. [ ] Restore the backup into a disposable database, upgrade it to migration head, apply the exact approved package/report, run `verify`, and compare every expected count.
6. [ ] Apply the same package and approved report to the real database once; if commit result is uncertain, identify the outcome by package digest rather than retrying.
7. [ ] Run post-apply verification for one current snapshot, all table/FK counts, all row digests, independent decision matrices, zero executable facts/mappings, zero unsafe operational bindings, API household isolation, and idempotent no-op.
8. [ ] Rebuild/restart only FamilyCare-owned services if the new API artifact requires it, preserve login credentials/session/archive configuration, and recheck HTTPS health and authenticated bounded queries.
9. [ ] Open a PR, wait for GitHub Actions, merge only after CI passes, and report the PR, merge commit, runtime migration, real-data verification, and every remaining unverified status/password/visual boundary. Do not tag or deploy a release without separate approval.

## Completion boundary

The work is complete only when the real private runtime can reproduce the approved external analysis as:

```text
validated immutable package snapshot
  -> subject aliases with explicit or UNKNOWN member binding
  -> certificate-backed contracts and enrolled components
  -> independent terms identity and edition assignments
  -> sections, source-clause lineage, semantic facts, and citations
  -> independent coverage-to-terms mappings
  -> zero executable rules and zero inferred operational enrollment links
```

Every imported row must belong to the approved package digest and HouseholdSpace, every dry-run expected count must equal the post-apply count, and applying the same digest again must be a no-op. Actual insurance material remains outside Git throughout.
