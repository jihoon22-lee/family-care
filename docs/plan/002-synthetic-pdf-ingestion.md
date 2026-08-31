# Synthetic PDF Ingestion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Status:** Complete — implementation PR #8~#12 and completion record PR #13 merged with the
synthetic ingestion regression retained in CI. Explicit private-data boundaries below remain valid.

**Goal:** Build a synthetic-only, evidence-preserving PDF intake and asynchronous analysis path with explicit path, parser, resource, password, lease, and cleanup boundaries.

**Architecture:** The API accepts a relative source key and creates an AnalysisJob in PostgreSQL. The Analyzer Worker resolves that key below an absolute document root, validates the local PDF, and invokes pdfplumber in a dedicated child process with pypdf structural checks and fixed resource limits. Extraction results are stored in the minimum document model; no Policy Ledger or insurance decision rule is introduced.

**Tech Stack:** Python 3.14, FastAPI 0.141.1, PostgreSQL 18, SQLAlchemy 2.0.52, pypdf 6.16.2, pdfplumber 0.11.10, reportlab 5.0.1, uv 0.12.x, pytest, Ruff, mypy, and the existing pnpm 11.22.0 Web workspace.

**Spec:** docs/design/pdf-ingestion.md and docs/design/data-model.md

## Global Constraints

- Phase 1 implementation and CI are synthetic-only; never open a real PDF or private external root.
- Tests copy wholly synthetic reportlab fixtures into a temporary root outside the checkout before setting FAMILYCARE_DOCUMENT_ROOT.
- The parser stack is pdfplumber 0.11.10 for text/word/coordinate/table extraction, pypdf 6.16.2 for structural/page/encryption validation, and reportlab 5.0.1 for deterministic fixtures.
- PyMuPDF is rejected for now because its AGPL/commercial dual-license model conflicts with the current no-license/proprietary-distribution posture; this is an engineering decision, not legal advice.
- Input is at most 25 MiB and 500 pages; the parent wall timeout is 120 seconds, child CPU is 90 seconds, child address space is 1536 MiB, child file size (`RLIMIT_FSIZE`) is 64 MiB, output files are at most 64 MiB, and open descriptors are at most 64.
- Work directories use mode 0700 and files use mode 0600.
- FAMILYCARE_DOCUMENT_ROOT is an absolute root. Requests and jobs carry only relative source_key values. The resolved file must be a regular file below the root, must not traverse symlinks, and must begin with PDF magic %PDF-.
- Intake performs component checks, then opens the final source once with descriptor-based no-follow semantics. Magic, size, pypdf structure, and SHA-256 use that opened descriptor; the validated path is never reopened. Linux parser children receive an inherited or duplicated read-only descriptor, not a reopenable path. Source keys and paths are sanitized out of logs.
- SHA-256 is streamed in 1 MiB chunks.
- The parser child receives only a local read-only descriptor and canonical JSON settings capped at 64 KiB of UTF-8. It applies resource limits before lazy-importing the parser, has no network client or external URL resolution, and applies `RLIMIT_FSIZE=64 MiB`. OS egress enforcement is production hardening; private-data acceptance stays blocked until the approved runtime boundary exists. Windows descriptor passing and `RLIMIT_*` behavior are unverified.
- Coordinates are PDF points with a top-left origin, rounded to 3 decimals. pdfplumber extract_words x0/top/x1/bottom values are bounds-checked directly; no page-height subtraction or bottom-origin conversion is used. Page numbers are 1-based and reading_order starts at 0 for each page. Words are TextBlock records; table and cell candidates retain bounding boxes; each page result is appended/serialized before page.close(). The extractor does not persist directly to DB.
- quality-v1 classifies a page as OCR_REQUIRED when non-whitespace chars are below 20, alphanumeric ratio is below 0.25, replacement-character ratio is above 0.05, or maximum repeated-character run is above 20. Otherwise it is TEXT_SUFFICIENT. OCR execution is outside Phase 1.
- Passwords never enter the database, job payload, or logs. The asynchronous API accepts unencrypted PDFs only; encrypted input returns PASSWORD_REQUIRED. A parser adapter has direct one-shot runtime-password tests for PASSWORD_INVALID, but queued password transport is not built.
- Phase 1 owns only Document, DocumentVersion, Extraction, ExtractionPage, ExtractionBlock, ExtractionTable, ExtractionCell, AnalysisJob, and Evidence coordinates. Policy Ledger remains Phase 2.
- Physical tables are exactly `documents`, `document_versions`, `extractions`, `extraction_pages`, `extraction_blocks`, `extraction_tables`, `extraction_cells`, and `analysis_jobs`.
- `documents` has one active row per source_key. `document_versions` uniquely stores both `(document_id, version_number)` and `(document_id, content_sha256)`. `extractions` has a partial unique `(document_version_id, extractor_config_hash) WHERE status = 'succeeded'`. DocumentVersion represents content hash, so these keys jointly enforce one succeeded extraction for the same document content/config without a redundant Extraction content_sha256 column or an impossible cross-table constraint. Page number, block reading order, and table-cell coordinates also have parent-scoped uniqueness. AnalysisJob states are queued, running, succeeded, retryable_failed, permanently_failed, and cancelled, with availability time, lease, heartbeat, and attempts.
- Authentication provider remains Phase 7. Later business records must be HouseholdSpace-scoped. Phase 1 endpoints are local synthetic-only development endpoints and are not production-safe.
- Synthetic ingestion routes are enabled only when FAMILYCARE_ENV=development and FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true. The default is false; when disabled, the router is not registered and the route returns 404. Endpoint tests explicitly opt in.
- Each approved branch is independently testable. The root agent reviews the complete branch once before its push; each PR must pass GitHub CI, merge through a merge commit, and verify post-merge main. No tag or production deployment is part of this plan.
- A THIRD_PARTY_NOTICES.md file is created only after the parser dependencies land in uv.lock. It is an implementation artifact of the dependency PR, not part of this planning change.

---

## File Responsibility Map

### Contracts and physical model

- packages/contracts/schemas/document-ingestion.v1.schema.json: request, source, status, and error contract.
- packages/contracts/schemas/extraction-result.v1.schema.json: versioned page, TextBlock, table, cell, quality, and Evidence contract.
- packages/contracts/schemas/analysis-job.v1.schema.json: replace the Foundation future placeholder with the actual password-free queue envelope; source_key and canonical settings are present before intake, while content_sha256 is not.
- packages/contracts/examples/document-ingestion.v1.json: synthetic request and status examples.
- packages/contracts/examples/extraction-result.v1.json: synthetic extraction result example.
- packages/contracts/examples/analysis-job.v1.json: update the synthetic example to the actual pre-intake queue envelope.
- scripts/generate_document_contract_types.py: deterministic generator from the two JSON Schemas to API and Worker typed contract modules.
- scripts/check_document_contracts.py: generator drift, examples, enum, limit, and forbidden-field checks.
- apps/api/src/familycare_api/documents/generated_contracts.py: generated API contract types; never edit manually.
- workers/analyzer/src/familycare_worker/generated_contracts.py: generated Worker contract types; never edit manually.
- apps/api/migrations/versions/0002_document_ingestion.py: physical tables `documents`, `document_versions`, `extractions`, `extraction_pages`, `extraction_blocks`, `extraction_tables`, `extraction_cells`, and `analysis_jobs`, with keys, enums, indexes, and uniqueness constraints.
- apps/api/tests/test_document_contracts.py: schema, generator, and migration contract tests.
- workers/analyzer/tests/test_document_contracts.py: Worker-side generated type and envelope tests.
- THIRD_PARTY_NOTICES.md: dependency names, exact versions, source licenses, and repository distribution boundary; create in the dependency PR after uv.lock contains all three parser dependencies.

### Intake safety and isolation

- workers/analyzer/src/familycare_worker/pdf/errors.py: stable error codes and retry classification.
- workers/analyzer/src/familycare_worker/pdf/limits.py: exact byte, page, timeout, CPU, address-space, `RLIMIT_FSIZE`, output, and descriptor constants plus OS limit application.
- workers/analyzer/src/familycare_worker/pdf/intake.py: absolute-root loading, descriptor-based no-follow source opening, relative source-key resolution, regular-file and magic validation, pypdf structural validation, and 1 MiB SHA-256 through the opened handle.
- workers/analyzer/src/familycare_worker/pdf/workspace.py: 0700 work directory and 0600 file lifecycle with cleanup reporting.
- workers/analyzer/src/familycare_worker/pdf/isolation.py: parent supervisor and dedicated parser child contract.
- workers/analyzer/tests/test_pdf_intake.py: source-key, symlink, magic, size, page, hash, and password-required cases.
- workers/analyzer/tests/test_pdf_isolation.py: child process limits, timeout, output, descriptor, permissions, and cleanup cases.

### Synthetic extraction

- workers/analyzer/src/familycare_worker/pdf/coordinates.py: PDF-point top-left coordinate normalization and 3-decimal rounding.
- workers/analyzer/src/familycare_worker/pdf/quality.py: quality-v1 metrics and TEXT_SUFFICIENT/OCR_REQUIRED classification.
- workers/analyzer/src/familycare_worker/pdf/extractor.py: pdfplumber word, page, table, and cell extraction with page-cache closure.
- workers/analyzer/tests/synthetic_pdf_factory.py: reportlab-only deterministic PDF builders that write to a caller-provided temporary path.
- workers/analyzer/tests/test_pdf_extraction.py: text/table/coordinate/quality regression corpus.
- workers/analyzer/tests/test_pdf_passwords.py: direct one-shot runtime-password classification without queued transport.
- workers/analyzer/pyproject.toml, root pyproject.toml, and uv.lock: pypdf 6.16.2 and pdfplumber 0.11.10 Worker runtime dependencies plus reportlab 5.0.1 development/test fixtures.

### Analysis job worker

- workers/analyzer/src/familycare_worker/jobs.py: PostgreSQL queue claim, lease, heartbeat, attempts, cancellation, and state transitions.
- workers/analyzer/src/familycare_worker/repository.py: transactional persistence for the eight Phase 1 tables and Evidence coordinates.
- workers/analyzer/src/familycare_worker/runner.py: job lifecycle, isolated parser invocation, idempotency, retry mapping, and cleanup.
- workers/analyzer/src/familycare_worker/__main__.py: Worker polling and bounded shutdown integration.
- workers/analyzer/tests/test_analysis_job_queue.py: concurrent claim, lease expiry, heartbeat, attempts, and state transitions.
- workers/analyzer/tests/test_analysis_job_runner.py: success, retryable failure, permanent failure, cancellation, cleanup, and duplicate-success behavior.

### Document analysis API

- apps/api/src/familycare_api/documents/router.py: local synthetic-only POST and status GET endpoints.
- apps/api/src/familycare_api/documents/service.py: source-key validation, job creation, and status projection.
- apps/api/src/familycare_api/errors.py: explicit FastAPI RequestValidationError handler returning the stable INVALID_REQUEST envelope.
- apps/api/src/familycare_api/documents/generated_contracts.py: generated request/response types consumed by the router.
- apps/api/src/familycare_api/main.py: feature-gated document router registration without changing Foundation health contracts.
- apps/api/tests/test_document_analysis_api.py: disabled 404, enabled 202 enqueue, status polling, invalid source key, unknown-job ANALYSIS_JOB_NOT_FOUND, and no-password payload cases.
- apps/api/tests/test_document_analysis_e2e.py: PostgreSQL/temp-root POST → Worker run → GET succeeded and encrypted → PASSWORD_REQUIRED integration cases.
- .env.example: default-disabled FAMILYCARE_ENABLE_SYNTHETIC_INGESTION configuration.
- packages/contracts/openapi/familycare.v1.json: regenerated OpenAPI contract after the document endpoints land.
- scripts/check_contracts.py: extend the existing contract checker for the document endpoint and status schemas.

---

## Approved PR sequence

| Order | Branch | Independently testable deliverable | Merge prerequisite |
|---|---|---|---|
| 1 | feat/document-ingestion-contracts | JSON Schemas, generated types, eight-table migration, and contract checks | Foundation main and fresh local checks |
| 2 | feat/pdf-intake-safety | Root/source-key safety, pypdf validation, hashing, workspace, and child limits | PR 1 merge and PostgreSQL migration |
| 3 | feat/synthetic-pdf-extraction | pdfplumber extraction, reportlab fixtures, coordinates, tables, and quality-v1 | PR 2 merge and intake test suite |
| 4 | feat/analysis-job-worker | PostgreSQL queue, leases, heartbeats, attempts, isolated runner, and persistence | PR 3 merge and extraction suite |
| 5 | feat/document-analysis-api | Async local API, status polling, contract regeneration, and no-password boundary | PR 4 merge and end-to-end synthetic path |

Each branch uses a merge commit. Before each push, the root agent performs one review of the complete branch diff and verifies that unrelated worktrees and processes remain untouched. After each merge, fetch main, rerun the affected post-merge checks, and record the merge commit before starting the next branch.

---

### Task 1: Define document ingestion contracts and the minimum physical model

**Branch:** feat/document-ingestion-contracts

**Files:**

- Create: packages/contracts/schemas/document-ingestion.v1.schema.json
- Create: packages/contracts/schemas/extraction-result.v1.schema.json
- Create: packages/contracts/examples/document-ingestion.v1.json
- Create: packages/contracts/examples/extraction-result.v1.json
- Modify: packages/contracts/schemas/analysis-job.v1.schema.json
- Modify: packages/contracts/examples/analysis-job.v1.json
- Create: scripts/generate_document_contract_types.py
- Create: scripts/check_document_contracts.py
- Create: apps/api/src/familycare_api/documents/generated_contracts.py
- Create: workers/analyzer/src/familycare_worker/generated_contracts.py
- Create: apps/api/migrations/versions/0002_document_ingestion.py
- Create: apps/api/tests/test_document_contracts.py
- Create: workers/analyzer/tests/test_document_contracts.py
- Modify: packages/contracts/README.md
- Modify: scripts/check_contracts.py only to call the new deterministic document contract checker

**Interfaces:**

- Consumes: the Foundation OpenAPI/JSON Schema layout, PostgreSQL 18, and the data-model entity names.
- Produces: generated API and Worker types, schema_version 1 envelopes, physical tables `documents`, `document_versions`, `extractions`, `extraction_pages`, `extraction_blocks`, `extraction_tables`, `extraction_cells`, and `analysis_jobs`, the exact AnalysisJob state enum, Evidence coordinate fields, unique document-version identity keys, and a partial `(document_version_id, extractor_config_hash) WHERE status = 'succeeded'` unique constraint on `extractions`.
- Request contract: source_key is a non-empty relative string and extractor_config is server-canonicalized; the request has no password, authoritative config hash, absolute path, raw PDF bytes, or external identifier fields.
- Queue contract: analysis-job.v1 contains schema_version, job_id, document_id, relative source_key, canonical settings, and server-computed extractor_config_hash. It cannot contain content_sha256 because intake has not run yet.
- Extraction contract: each page has page_number >= 1; each block has PDF-point x0/y0/x1/y1 rounded to three decimals and reading_order >= 0; tables and cells require bounding boxes; quality rule version is quality-v1.

- [x] **Step 1: Write the schema and migration tests first**

Add tests that load the examples, assert the required keys and fixed enum values, and inspect the migration SQL metadata for exactly these physical tables: documents, document_versions, extractions, extraction_pages, extraction_blocks, extraction_tables, extraction_cells, analysis_jobs. Assert that request and queue schemas reject a password and an absolute source key, that the request rejects a client-supplied extractor_config_hash, and that the pre-intake analysis-job envelope has no content_sha256. Assert the uniqueness rules separately and document that DocumentVersion is the sole content-hash representative after intake.

~~~python
def test_document_request_has_relative_source_key_only() -> None:
    request = load_example("document-ingestion.v1.json")
    assert request["source_key"] == "synthetic/policy-001.pdf"
    assert "password" not in request
    assert "absolute_path" not in request


def test_extraction_result_uses_versioned_quality_and_evidence_coordinates() -> None:
    result = load_example("extraction-result.v1.json")
    assert result["quality_rule_version"] == "quality-v1"
    assert result["pages"][0]["page_number"] == 1
    assert result["pages"][0]["blocks"][0]["reading_order"] == 0
    assert result["pages"][0]["blocks"][0]["bbox"] == [10.0, 20.0, 30.0, 40.0]
~~~

Run:

~~~bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_document_contracts.py workers/analyzer/tests/test_document_contracts.py -q
~~~

Expected: FAIL because the schema files, generated modules, and migration revision do not yet exist.

- [x] **Step 2: Add the two versioned JSON Schemas and synthetic examples**

Define document-ingestion.v1 with request fields source_key, document_kind, and extractor_config plus a status/error projection. The server canonicalizes extractor_config JSON and computes extractor_config_hash; clients cannot supply the authoritative hash. Define extraction-result.v1 with content_sha256, extractor_name, extractor_version, extractor_config_hash, quality_rule_version, pages, blocks, tables, cells, and evidence. Replace the Foundation placeholder analysis-job.v1 shape with the actual pre-intake queue envelope: schema_version, job_id, document_id, relative source_key, canonical settings, and server-computed extractor_config_hash. It must not require or expose content_sha256 before the Worker runs. Use only synthetic values such as synthetic-policy-001 and Sample Policy. Do not add a password property, absolute path property, raw PDF property, external URL, or client-controlled config-hash property.

Set AnalysisJob status values exactly to queued, running, succeeded, retryable_failed, permanently_failed, and cancelled. Set error codes to the values in docs/design/pdf-ingestion.md. Make page number, bbox, and reading_order constraints explicit in the schema rather than relying only on Python tests.

- [x] **Step 3: Generate typed consumers and add drift checking**

Implement scripts/generate_document_contract_types.py as a deterministic standard-library generator. It reads the schemas, sorts fields and enum members, and emits the API and Worker modules. Implement scripts/check_document_contracts.py to regenerate into a temporary directory, compare bytes, validate both examples, reject forbidden fields, and check the exact safety-limit constants. The generated modules include no business logic and are not hand-edited.

Run:

~~~bash
TMPDIR=/tmp uv run python scripts/check_document_contracts.py
~~~

Expected: PASS for the checked-in schema/examples/generated outputs after the generator is implemented.

- [x] **Step 4: Create the minimum migration**

Create revision 0002_document_ingestion with foreign keys and indexes for the eight tables. Include:

- Document in `documents`: UUID primary key, relative source_key, document_kind, nullable media_type/byte_size/page_count until Worker intake, content status, created_at, updated_at, deleted_at, and one active source_key enforced by a partial unique index.
- DocumentVersion in `document_versions`: UUID primary key, document_id, version_number integer, content_sha256, byte_size, page_count, created_at, unique `(document_id, version_number)`, and unique `(document_id, content_sha256)`.
- Extraction in `extractions`: UUID primary key, document_version_id, extractor_name, extractor_version, extractor_config_hash, quality_rule_version, status, succeeded_at, created_at, and partial unique `(document_version_id, extractor_config_hash) WHERE status = 'succeeded'`.
- ExtractionPage in `extraction_pages`: UUID primary key, extraction_id, page_number, width_points, height_points, quality metrics, classification, warning_codes, and unique `(extraction_id, page_number)`.
- ExtractionBlock in `extraction_blocks`: UUID primary key, page_id, text, bbox JSON, reading_order, and unique `(page_id, reading_order)`.
- ExtractionTable in `extraction_tables`: UUID primary key, page_id, bbox JSON, review_state.
- ExtractionCell in `extraction_cells`: UUID primary key, table_id, row_index, column_index, text, bbox JSON, review_state, and unique `(table_id, row_index, column_index)`.
- AnalysisJob in `analysis_jobs`: UUID primary key, document_id, source_key, settings_json, extractor_config_hash, state, available_at, lease_owner, lease_expires_at, heartbeat_at, attempts, max_attempts, error_code, created_at, updated_at.

Do not create PolicyContract, PolicyParty, Rider, Clause, CoverageRule, MedicalEvent, ClaimCandidate, ClaimCase, AppUser, or HouseholdSpace tables in this migration. Phase 2 adds HouseholdSpace-scoped business records after this minimum model is verified.

Run against a repository-owned PostgreSQL 18 service:

~~~bash
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini current
~~~

Expected: revision 0002_document_ingestion is head and the eight named physical tables exist; no unrelated domain table exists. The extraction table does not contain a redundant content_sha256 column.

- [x] **Step 5: Run the complete branch test set**

~~~bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_document_contracts.py workers/analyzer/tests/test_document_contracts.py -q
TMPDIR=/tmp uv run python scripts/check_document_contracts.py
TMPDIR=/tmp uv run python scripts/check_contracts.py
git diff --check
~~~

Expected: all commands exit 0 and no generated file differs from its schema source.

- [x] **Step 6: Commit the independently reviewable branch**

~~~bash
git add packages/contracts scripts apps/api/migrations apps/api/tests/test_document_contracts.py workers/analyzer/tests/test_document_contracts.py
git commit -m "feat(contracts): define document ingestion schemas"
~~~

- [x] **Step 7: Complete the PR checkpoint**

The root agent reviews the full feat/document-ingestion-contracts diff once, including generated output, migration constraints, privacy fields, and test evidence. Then push the branch, create the PR, wait for all seven GitHub CI jobs, merge with a merge commit, fetch main, verify the migration and contract checks on post-merge main, and record the merge commit before beginning feat/pdf-intake-safety. Do not create THIRD_PARTY_NOTICES.md in this branch because parser dependencies have not landed.

---

### Task 2: Enforce intake path, content, workspace, and parser-process safety

**Branch:** feat/pdf-intake-safety

**Files:**

- Create: workers/analyzer/src/familycare_worker/pdf/__init__.py
- Create: workers/analyzer/src/familycare_worker/pdf/errors.py
- Create: workers/analyzer/src/familycare_worker/pdf/limits.py
- Create: workers/analyzer/src/familycare_worker/pdf/intake.py
- Create: workers/analyzer/src/familycare_worker/pdf/workspace.py
- Create: workers/analyzer/src/familycare_worker/pdf/isolation.py
- Create: workers/analyzer/tests/test_pdf_intake.py
- Create: workers/analyzer/tests/test_pdf_isolation.py
- Modify: workers/analyzer/pyproject.toml to add pypdf==6.16.2
- Modify: uv.lock

**Interfaces:**

- Consumes: the v1 generated contract, the 0002_document_ingestion migration, and an absolute FAMILYCARE_DOCUMENT_ROOT.
- Produces: open_source(root: Path, source_key: str) -> OpenedSource, validate_pdf(source: OpenedSource) -> ValidatedPdf, stream_sha256(handle: BinaryIO, chunk_size: int = 1048576, on_read: Callable[[int], None] | None = None) -> str, create_workspace(root: Path) -> Workspace, and run_isolated_parser(source_fd: int, settings_json: str, wall_timeout_seconds: int = 120) -> ParseOutcome.
- OpenedSource owns one read-only descriptor opened after component checks with no-follow semantics. All validation, hashing, and child parsing use that descriptor; no validated path is reopened.
- ValidatedPdf includes only safe metadata: media_type, byte_size, page_count, encrypted, and content_sha256. It does not carry a password or absolute path into the job payload.
- ParseOutcome contains success or one stable error code and sanitized metadata; it never contains the original source path in a log message.

- [x] **Step 1: Write failing intake and isolation tests**

Use pytest tmp_path or a context-managed tempfile.TemporaryDirectory in every test; both are created outside the checkout by the TMPDIR=/tmp test command. Build only synthetic bytes in that temporary root. Test a valid relative source key, absolute source key rejection, parent traversal rejection, symlink component rejection, regular-file rejection, wrong magic rejection, 25 MiB boundary, 500-page boundary through a generated synthetic PDF, observed one MiB read chunks, 0700/0600 modes, child wall timeout, child CPU/address-space/file-size/descriptor limit configuration, and output-file cap.

~~~python
def test_open_source_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "synthetic-outside.pdf"
    outside.write_bytes(b"%PDF-1.4\nsynthetic\n")
    (root / "link.pdf").symlink_to(outside)
    with pytest.raises(DocumentPathEscape):
        open_source(root, "link.pdf")


def test_stream_sha256_reads_one_mib_chunks(tmp_path: Path) -> None:
    path = tmp_path / "synthetic.bin"
    path.write_bytes(b"synthetic-" * 200000)
    observed_sizes: list[int] = []
    with path.open("rb") as handle:
        digest = stream_sha256(handle, chunk_size=1_048_576, on_read=observed_sizes.append)
    assert digest == hashlib.sha256(path.read_bytes()).hexdigest()
    assert observed_sizes[:-1] == [1_048_576] * (len(observed_sizes) - 1)
    assert 0 < observed_sizes[-1] <= 1_048_576
~~~

Run:

~~~bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_intake.py workers/analyzer/tests/test_pdf_isolation.py -q
~~~

Expected: FAIL because the pdf safety modules and their exported functions do not yet exist.

- [x] **Step 2: Implement relative-root resolution and intake validation**

Load FAMILYCARE_DOCUMENT_ROOT once per job and require an absolute directory. Reject source_key values beginning with a path separator, containing NUL, or containing a parent component. Open the root directory descriptor, then traverse each relative component with chained `openat`/`dir_fd`, `O_NOFOLLOW`, and directory-only flags; retain the directory descriptors until the final read-only file descriptor is open. This makes containment part of the open operation instead of a check that can race a later reopen. Require final `fstat` regular-file metadata, check the first five bytes for %PDF-, enforce the 25 MiB size limit, and run pypdf structural/page/encryption validation through duplicates of the opened descriptor. Return PASSWORD_REQUIRED for encrypted input. Compute content_sha256 by reading exactly 1 MiB chunks from the same opened file identity and reset/duplicate offsets between consumers. Close every descriptor in all exit paths. Do not log source_key or any path.

Keep errors in errors.py as an enum/string union. Map path escape, unsupported type, size, page, encryption, corruption, timeout, and resource failures to the contract's stable codes. Unit tests assert that exceptions and logs contain no absolute path, file name, document text, or password.

- [x] **Step 3: Implement the workspace lifecycle**

Create a random job directory below the configured work root with mode 0700. Create every output file with mode 0600 using exclusive creation. Expose close_and_cleanup() that attempts cleanup in a finally path and returns a boolean cleanup result plus a sanitized TEMP_CLEANUP_FAILED error when deletion fails. Do not include source_key or family labels in directory names.

- [x] **Step 4: Implement parser supervision and limits**

Use a dedicated child process for parser execution. On Linux the supervisor passes only the inherited or duplicated read-only source descriptor and canonical JSON settings; it never passes a reopenable path. Immediately after fork, close inherited application file and socket descriptors other than the source and supervision pipes. It waits at most 120 seconds, terminates and joins the child on timeout, and caps serialized result handling at 64 MiB. Child results must be JSON values encoded as canonical UTF-8 JSON; the parent must not unpickle child-controlled objects. In the child, apply RLIMIT_CPU=90, RLIMIT_AS=1536 MiB, RLIMIT_FSIZE=64 MiB, and RLIMIT_NOFILE=64 before lazy-importing or calling the parser. The child has no HTTP client, URL resolver, subprocess launcher, or embedded-file execution path. Keep the parser callable injectable so this branch can test limits without a PDF parser implementation. Windows descriptor passing and RLIMIT behavior are unverified.

Run:

~~~bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_intake.py workers/analyzer/tests/test_pdf_isolation.py -q
~~~

Expected: all intake and isolation tests pass, including the exact limit constants and permissions.

- [x] **Step 5: Run static checks for the branch**

~~~bash
TMPDIR=/tmp uv run ruff format --check workers/analyzer
TMPDIR=/tmp uv run ruff check workers/analyzer
TMPDIR=/tmp uv run mypy workers/analyzer/src
git diff --check
~~~

Expected: all commands exit 0 and the test suite reports no real/private path access.

- [x] **Step 6: Commit the independently reviewable branch**

~~~bash
git add workers/analyzer/pyproject.toml workers/analyzer/src/familycare_worker/pdf workers/analyzer/tests/test_pdf_intake.py workers/analyzer/tests/test_pdf_isolation.py uv.lock
git commit -m "feat(pdf): enforce intake safety"
~~~

- [x] **Step 7: Complete the PR checkpoint**

The root agent reviews the full feat/pdf-intake-safety diff once, checking path source-to-sink behavior, symlink handling, limit application before parsing, sanitized errors, permissions, and synthetic-only tests. Then push, create the PR, wait for all seven GitHub CI jobs, merge with a merge commit, fetch main, rerun the intake and repository safety checks on post-merge main, and record the merge commit before beginning feat/synthetic-pdf-extraction.

Completed in PR #9 at merge commit `523bd68be3d951e37a9f4ba19b858d9ac9bdcfcc`; all seven PR and post-merge `main` checks passed, and the post-merge intake, mypy, documentation, and repository-safety checks passed locally.

---

### Task 3: Add deterministic synthetic extraction, tables, coordinates, and quality-v1

**Branch:** feat/synthetic-pdf-extraction

**Files:**

- Create: workers/analyzer/src/familycare_worker/pdf/coordinates.py
- Create: workers/analyzer/src/familycare_worker/pdf/quality.py
- Create: workers/analyzer/src/familycare_worker/pdf/extractor.py
- Create: workers/analyzer/tests/synthetic_pdf_factory.py
- Create: workers/analyzer/tests/test_pdf_extraction.py
- Create: workers/analyzer/tests/test_pdf_passwords.py
- Create: workers/analyzer/tests/test_pdf_quality.py
- Create: workers/analyzer/tests/test_synthetic_pdf_factory.py
- Create: THIRD_PARTY_NOTICES.md after uv.lock contains all three parser dependencies
- Modify: workers/analyzer/pyproject.toml to add pdfplumber==0.11.10
- Modify: root pyproject.toml to add reportlab==5.0.1 to the development/test group only
- Modify: uv.lock
- Modify: packages/contracts/schemas/extraction-result.v1.schema.json and its example to finalize the required TextBlock page_number
- Regenerate: API and Worker document contract types
- Modify: scripts/check_document_contracts.py and Worker contract tests to reject page-number drift
- Modify: workers/analyzer/src/familycare_worker/pdf/errors.py to add the sanitized PASSWORD_INVALID exception

**Interfaces:**

- Consumes: the validated read-only source descriptor and canonical settings from feat/pdf-intake-safety.
- Produces: PdfPlumberExtractor.extract(source_fd: int, settings: ExtractionSettings) -> ExtractionResult, normalize_bbox(x0: float, top: float, x1: float, bottom: float) -> list[float], classify_page_quality(text: str, rule_version: Literal["quality-v1"]) -> PageQuality, and deterministic reportlab fixture builders.
- `ExtractionSettings` is a Worker-internal post-intake shape containing only document_version_id, content_sha256, extractor_config_hash, quality_rule_version, and table_strategy. It is serialized as exact canonical JSON for the child and is not the client or queued AnalysisJob settings contract.
- Every extracted word becomes one TextBlock. Each TextBlock has page_number, text, bbox, and reading_order. Table and cell candidates retain page-relative bounding boxes. `extract_words` x0/top/x1/bottom values are bounds-checked directly, rounded, appended or serialized before page.close(), and never converted from bottom-origin coordinates. The extractor does not persist to DB.
- The parser adapter exposes a one-shot password argument only in a direct function used by test_password_invalid; no queued schema or job field is added.

- [x] **Step 1: Write failing extraction and quality tests**

Build text, table, low-quality, and encrypted PDFs with reportlab in pytest tmp_path or a context-managed TemporaryDirectory. Copy each wholly synthetic fixture used by intake into a second checkout-external root before opening it. Exercise replacement-character and repeated-character threshold boundaries directly against the deterministic quality function. The expected assertions use synthetic strings only.

~~~python
def test_words_are_text_blocks_with_pdf_point_contract(tmp_path: Path) -> None:
    source = make_text_pdf(tmp_path / "synthetic-text.pdf")
    with source.open("rb") as handle:
        result = extractor.extract(handle.fileno(), settings)
    first = result.pages[0].blocks[0]
    assert first.text == "Synthetic"
    assert first.page_number == 1
    assert first.reading_order == 0
    assert first.bbox == [72.0, 72.0, 120.0, 84.0]


def test_quality_v1_uses_or_thresholds() -> None:
    assert classify_page_quality("a" * 19, "quality-v1").classification == "OCR_REQUIRED"
    assert classify_page_quality("A1 " * 20, "quality-v1").classification == "TEXT_SUFFICIENT"
~~~

Run:

~~~bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_extraction.py workers/analyzer/tests/test_pdf_passwords.py -q
~~~

Expected: FAIL because pdfplumber, reportlab, extractor, coordinate, quality, and password adapter implementations are absent.

- [x] **Step 2: Add the exact parser dependencies and notices**

Add pdfplumber 0.11.10 to the Worker runtime dependency set and reportlab 5.0.1 to the root development/test group only, regenerate uv.lock, and run package metadata inspection to record each direct and relevant transitive package in THIRD_PARTY_NOTICES.md. Include exact package version, project source URL, declared license text or SPDX identifier, and the statement that this repository has no LICENSE and does not grant reuse permission by default. Do not copy license text beyond the amount allowed by the package terms.

This is the first step allowed to create THIRD_PARTY_NOTICES.md. The file is not created in the planning branch before dependencies land.

- [x] **Step 3: Implement coordinate normalization and quality-v1**

Use pdfplumber's x0/top/x1/bottom values directly as top-left PDF points; bounds-check 0 <= x0 <= x1 <= page.width and 0 <= top <= bottom <= page.height, then round the four values to three decimal places. Do not subtract from page height or describe a bottom-origin conversion. Preserve page width/height in points. Assign reading_order by the stable word sequence returned for each page, starting at zero. Implement quality-v1 metrics exactly:

~~~python
def classify_page_quality(text: str, rule_version: Literal["quality-v1"]) -> PageQuality:
    non_whitespace = sum(not char.isspace() for char in text)
    alphanumeric = sum(char.isalnum() for char in text)
    alphanumeric_ratio = alphanumeric / max(non_whitespace, 1)
    replacement_ratio = text.count("\ufffd") / max(len(text), 1)
    maximum_run = max_repeated_character_run(text)
    classification = (
        "OCR_REQUIRED"
        if non_whitespace < 20
        or alphanumeric_ratio < 0.25
        or replacement_ratio > 0.05
        or maximum_run > 20
        else "TEXT_SUFFICIENT"
    )
    return PageQuality(
        rule_version=rule_version,
        classification=classification,
        non_whitespace_chars=non_whitespace,
        alphanumeric_ratio=alphanumeric_ratio,
        replacement_character_ratio=replacement_ratio,
        maximum_repeated_character_run=maximum_run,
    )
~~~

The implementation must return every metric and rule_version so a future threshold change is distinguishable. It must not execute OCR.

- [x] **Step 4: Implement pdfplumber word/table extraction and page-cache closure**

Open a duplicate of the inherited read-only descriptor as a file-like handle in the child, construct a pdfplumber PDF, iterate pages one at a time, call extract_words for TextBlock records, and detect table/cell candidates with their bboxes. Append or serialize each page result before closing that page's cache, then explicitly close the page and release references. The repository layer persists after extraction returns; the extractor does not write DB rows. Close the overall PDF in a finally block. Reject network URLs, embedded-file actions, and parser calls that do not originate from the local intake descriptor.

For encrypted PDFs, the direct adapter accepts a one-shot runtime password only when called directly by the password test. A wrong runtime password maps to PASSWORD_INVALID. The queued AnalysisJob JSON remains password-free.

- [x] **Step 5: Run extraction, password, and static checks**

~~~bash
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_extraction.py workers/analyzer/tests/test_pdf_passwords.py -q
TMPDIR=/tmp uv run ruff format --check workers/analyzer
TMPDIR=/tmp uv run ruff check workers/analyzer
TMPDIR=/tmp uv run mypy workers/analyzer/src
git diff --check
~~~

Expected: all commands exit 0; tests cover text, table, cell, coordinate rounding, page numbering, reading order, all quality-v1 boundaries, direct PASSWORD_INVALID, and no OCR execution.

- [x] **Step 6: Commit the independently reviewable branch**

~~~bash
git add workers/analyzer/pyproject.toml workers/analyzer/src/familycare_worker/pdf workers/analyzer/tests THIRD_PARTY_NOTICES.md uv.lock packages/contracts/examples/extraction-result.v1.json
git commit -m "feat(pdf): extract synthetic pages and tables"
~~~

- [x] **Step 7: Complete the PR checkpoint**

The root agent reviews the full feat/synthetic-pdf-extraction diff once, checking the exact parser versions and notices, source-to-sink descriptor boundary, coordinate normalization, page closure, quality thresholds, deterministic fixture authorship, and password tests. Then push, create the PR, wait for all seven GitHub CI jobs, merge with a merge commit, fetch main, rerun the complete extraction suite on post-merge main, and record the merge commit before beginning feat/analysis-job-worker.

Completed in PR #10 at merge commit `eac98171fd72604c7ff0c641f7c80f02c99d145a`; all seven PR and post-merge `main` checks passed, and the local post-merge extraction checks passed. Task 4 is recorded below as the next completed checkpoint.

---

### Task 4: Implement AnalysisJob queue, lease, heartbeat, runner, and persistence

**Branch:** feat/analysis-job-worker

**Files:**

- Create: workers/analyzer/src/familycare_worker/jobs.py
- Create: workers/analyzer/src/familycare_worker/repository.py
- Create: workers/analyzer/src/familycare_worker/runner.py
- Create: workers/analyzer/tests/test_analysis_job_queue.py
- Create: workers/analyzer/tests/test_analysis_job_runner.py
- Modify: workers/analyzer/src/familycare_worker/__main__.py
- Modify: workers/analyzer/src/familycare_worker/health.py to add the queue repository readiness probe
- Modify: workers/analyzer/src/familycare_worker/pdf/isolation.py to expose bounded heartbeat/cancellation progress
- Modify: workers/analyzer/tests/test_health.py for the queue readiness contract
- Modify: workers/analyzer/tests/test_pdf_isolation.py for progress cancellation and child reaping

**Interfaces:**

- Consumes: the eight physical tables, validated ExtractionResult, and the isolated descriptor-based parser runner.
- Produces: `JobQueue.claim_next_job`, `heartbeat`, `fail_job`, `cancel_job`; `ExtractionRepository.prepare_document_version`, `complete_with_existing`, `persist_success`; and `AnalysisJobRunner.run_once`.
- Job claim uses PostgreSQL FOR UPDATE SKIP LOCKED, increments attempts once per claim, sets running/lease/heartbeat atomically, and refuses a claim after max attempts.
- The production Worker lease is 180 seconds, heartbeat interval is 30 seconds, and retry backoff is exponential from `2 ** attempts` seconds with a 300-second cap. Queue tests may request shorter explicit leases.
- Intake prepares or reuses `document_versions` in a short transaction before the parser child starts because the child Evidence contract requires the DocumentVersion UUID. A failed parse can therefore leave a valid content-identity row but cannot leave a partial Extraction. Successful persistence writes `extractions`, `extraction_pages`, `extraction_blocks`, `extraction_tables`, `extraction_cells`, Evidence coordinates, and the job success transition in one transaction. It enforces `document_versions(document_id, content_sha256)` and `extractions(document_version_id, extractor_config_hash) WHERE status = 'succeeded'`; DocumentVersion represents the content hash and Extraction has no redundant content_sha256 column.
- Retryable failures are limited to parser timeout, resource limit, and transient database errors, with bounded backoff through `available_at`. Path, magic, page, password, corruption, contract, and temporary-cleanup errors are permanently failed. `TEMP_CLEANUP_FAILED` additionally emits a sanitized security event and is never retried automatically.

- [x] **Step 1: Write failing queue and runner tests**

Add PostgreSQL-marked tests for two concurrent workers claiming distinct queued jobs, lease-expired job recovery, heartbeat extension by the lease owner only, attempt increment and max-attempt handling, cancellation, and exact state transition rejection. Add runner tests for success, duplicate-success idempotency, PASSWORD_REQUIRED, PASSWORD_INVALID from direct adapter only, retryable timeout, permanently failed corruption, cleanup in every path, and no password in serialized payload.

~~~python
def test_claim_uses_lease_and_increments_attempts(db) -> None:
    job = seed_queued_job(source_key="synthetic/policy-001.pdf")
    claimed = claim_next_job(db, "worker-a", lease_seconds=30)
    assert claimed.id == job.id
    assert claimed.state == "running"
    assert claimed.attempts == 1
    assert claimed.lease_owner == "worker-a"
    assert "password" not in claimed.settings_json
~~~

Run:

~~~bash
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_analysis_job_queue.py workers/analyzer/tests/test_analysis_job_runner.py -m integration -q
~~~

Expected: FAIL because queue repository, runner, and persistence functions do not yet exist.

- [x] **Step 2: Implement transactional queue operations**

Implement claim_next_job with a single transaction: make expired running jobs eligible for recovery, select one queued or due retryable job (`available_at <= now()`) with FOR UPDATE SKIP LOCKED, set running, lease_owner, lease_expires_at, heartbeat_at, and attempts, and commit. Jobs at max attempts become permanently_failed instead of being reclaimed. heartbeat must require the current lease owner and a non-expired lease. cancel_job must reject succeeded jobs and be idempotent for cancelled jobs. State transitions must be represented by the exact six enum values and no free-form strings.

- [x] **Step 3: Implement extraction persistence and idempotency**

Persist or reuse one DocumentVersion by `(document_id, content_sha256)` and one Extraction by `(document_version_id, extractor_config_hash)` transactionally. Store TextBlock words, table/cell bounding boxes, page quality metrics, warning codes, extractor name/version, extractor config hash, and quality-v1. Before insert, look up a succeeded extraction by document version and extractor_config_hash; return the existing extraction without creating a second succeeded row. Never store original bytes, password, or absolute source path.

- [x] **Step 4: Implement runner, retries, cleanup, and bounded shutdown**

run_job claims the source key from the database, opens the final source descriptor through the intake module, creates the mode-0700 workspace, invokes run_isolated_parser with the descriptor, stores results, updates succeeded, and cleans up in finally. It sends heartbeat at a bounded interval shorter than the lease, exits on cancellation, and maps each error code to retryable_failed or permanently_failed. It must not print document text, source key, absolute path, password, or serialized settings. The idle Worker process handles SIGTERM/SIGINT without starting a second job.

- [x] **Step 5: Run the complete Worker integration and static checks**

~~~bash
FAMILYCARE_DATABASE_URL=postgresql+psycopg://postgres:ci-only-password@127.0.0.1:55432/postgres TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_analysis_job_queue.py workers/analyzer/tests/test_analysis_job_runner.py -m integration -q
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_intake.py workers/analyzer/tests/test_pdf_isolation.py workers/analyzer/tests/test_pdf_extraction.py workers/analyzer/tests/test_pdf_passwords.py -q
TMPDIR=/tmp uv run ruff format --check workers/analyzer
TMPDIR=/tmp uv run ruff check workers/analyzer
TMPDIR=/tmp uv run mypy workers/analyzer/src
git diff --check
~~~

Expected: PostgreSQL queue and synthetic extraction suites pass serially, static checks pass, and no private path or password appears in captured logs.

Local pre-PR evidence: 159 non-integration tests and 24 PostgreSQL integration tests passed; Ruff format/lint, mypy, contracts, container/workflow policies, documentation, repository safety, Web checks, and `git diff --check` passed. The Worker image built successfully, ran as UID/GID 10002, and reported ready against a migrated synthetic PostgreSQL database. The first manual image command used the wrong Dockerfile path and the first runtime probe passed `--health` as the executable; both failed before creating a persistent container and were corrected with the repository Dockerfile and explicit `familycare-worker --health` invocation.

- [x] **Step 6: Commit the independently reviewable branch**

~~~bash
git add workers/analyzer/src/familycare_worker workers/analyzer/tests workers/analyzer/pyproject.toml
git commit -m "feat(worker): process analysis jobs with leases"
~~~

- [x] **Step 7: Complete the PR checkpoint**

The root agent reviews the full feat/analysis-job-worker diff once, checking SQL transaction boundaries, SKIP LOCKED behavior, lease ownership, attempts, idempotency constraint use, retry classification, cleanup, and signal handling. Then push, create the PR, wait for all seven GitHub CI jobs, merge with a merge commit, fetch main, rerun queue and full synthetic extraction checks on post-merge main, and record the merge commit before beginning feat/document-analysis-api.

Completed in PR #11 at merge commit `cc651436cab884109dc6fdc7f793c8b32e9c86d4`; PR CI and post-merge `main` CI each passed 7/7, and the post-merge local checks passed 23 queue tests and 59 extraction tests. Task 5 is the next pending branch.

---

### Task 5: Expose the local synthetic asynchronous document-analysis API

**Branch:** feat/document-analysis-api

**Files:**

- Create: apps/api/src/familycare_api/documents/__init__.py
- Create: apps/api/src/familycare_api/documents/router.py
- Create: apps/api/src/familycare_api/documents/service.py
- Create: apps/api/src/familycare_api/errors.py
- Create: apps/api/tests/test_document_analysis_api.py
- Create: apps/api/tests/test_document_analysis_e2e.py
- Modify: apps/api/src/familycare_api/main.py
- Modify: packages/contracts/openapi/familycare.v1.json
- Modify: scripts/check_contracts.py
- Modify: .env.example

**Interfaces:**

- Consumes: the merged Worker queue and v1 generated contracts.
- Produces: POST /api/v1/documents/analysis returning 202 with job_id, state, and status_url; GET /api/v1/analysis-jobs/{job_id} returning state, attempts, error_code, and sanitized extraction summary.
- API factory signature: create_app(*, readiness_probe: ReadinessProbe | None = None, enable_synthetic_ingestion: bool | None = None) -> FastAPI. When the override is None, routes are enabled only when FAMILYCARE_ENV=development and FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true. The module-level runtime app passes no override and defaults to disabled unless both variables opt in. Contract generation passes enable_synthetic_ingestion=True explicitly so the canonical OpenAPI contains the routes without changing runtime defaults.
- POST validates relative source_key and request shape only, creates or reuses a row in documents by source_key, enqueues analysis_jobs, and always returns 202 for a valid source key. It cannot create document_versions or know content_sha256. Worker intake opens the descriptor, computes content hash, creates or reuses document_versions, then creates or reuses extractions.
- POST request has source_key, document_kind, and extractor settings only. Pydantic extra fields are forbidden, so password, absolute_path, raw bytes, and external URL fields are rejected. A custom FastAPI RequestValidationError handler returns 422 with stable error_code INVALID_REQUEST.
- Missing, corrupt, or encrypted PDFs are asynchronous job outcomes: encrypted input is PASSWORD_REQUIRED. Unknown status GET UUIDs return 404 with ANALYSIS_JOB_NOT_FOUND, not DOCUMENT_NOT_FOUND.
- The API performs no authentication or authorization and includes a clear local synthetic-only route description. It must not claim production safety.

### Approved local API contract

Task 5 exposes a deliberately narrow local development boundary. The runtime router is registered only when both `FAMILYCARE_ENV=development` and `FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true`; the default is disabled, and a disabled app does not register either document route, so both paths return 404. Tests may pass `enable_synthetic_ingestion=True` explicitly. OpenAPI generation may use that explicit opt-in to describe the routes, but it must not change the module-level runtime default or the `/health/live` and `/health/ready` routes.

| Method | Path | Enabled response | Contract boundary |
|---|---|---|---|
| `POST` | `/api/v1/documents/analysis` | `202 Accepted` | Validate the request and enqueue an `AnalysisJob`; return `job_id`, `state`, and relative `status_url`. |
| `GET` | `/api/v1/analysis-jobs/{job_id}` | `200 OK` | Project the job state, attempts, sanitized `error_code`, and extraction summary counts. |

The request contains only `schema_version: "1"`, a relative `source_key`, the contract `document_kind`, and canonical `extractor_config`. Extra fields are forbidden. A malformed body, an absolute or parent-traversal source key, or fields such as `password`, `absolute_path`, `raw_pdf`, or `url` returns HTTP `422` with the stable `error_code: "INVALID_REQUEST"` envelope. Validation details are sanitized and never echo raw values, passwords, absolute paths, or document content.

For an enabled route, every valid source key is accepted asynchronously with `202`; the API does not open the source, compute `content_sha256`, create a `DocumentVersion`, or inspect whether the PDF exists, is corrupt, or is encrypted. The Worker later performs intake and extraction. Consequently, missing, corrupt, and encrypted inputs are job outcomes rather than synchronous POST errors; encrypted input becomes `PASSWORD_REQUIRED`. An unknown status UUID returns `404` with `ANALYSIS_JOB_NOT_FOUND`, not `DOCUMENT_NOT_FOUND`. `PASSWORD_INVALID` remains a direct one-shot adapter diagnostic and is not transported through a queued password field.

The intended sequence is `POST → documents/analysis_jobs enqueue → Worker intake and isolated extraction → GET status`. The API has no authentication or authorization and is not production-safe; Authentication provider integration remains Phase 7. No Policy Ledger, OCR execution, external URL, external AI, or insurance decision logic is part of this contract.

- [x] **Step 1: Write failing API contract and behavior tests**

Add FastAPI unit tests that assert the default-disabled app returns 404, an explicitly enabled app returns 202 for a valid source_key, the response contains a UUID job_id and status_url, the status GET returns queued/running/succeeded projections from seeded rows, unknown UUIDs return ANALYSIS_JOB_NOT_FOUND, invalid absolute or parent-traversal source keys return the stable error envelope, and request fields named password, absolute_path, raw_pdf, or url are rejected without creating a job. Do not claim this unit file reaches the Worker or classifies encrypted PDFs.

~~~python
def test_submit_analysis_is_async_and_password_free(client) -> None:
    response = client.post(
        "/api/v1/documents/analysis",
        json={
            "source_key": "synthetic/policy-001.pdf",
            "document_kind": "policy",
            "extractor_config": {"profile": "quality-v1"},
        },
    )
    assert response.status_code == 202
    body = response.json()
    assert body["state"] == "queued"
    assert body["status_url"].startswith("/api/v1/analysis-jobs/")
    assert "password" not in body


def test_disabled_app_does_not_register_synthetic_route(disabled_client) -> None:
    response = disabled_client.post(
        "/api/v1/documents/analysis",
        json={"source_key": "synthetic/policy-001.pdf", "document_kind": "policy"},
    )
    assert response.status_code == 404


def test_submit_rejects_password_field(client) -> None:
    response = client.post(
        "/api/v1/documents/analysis",
        json={"source_key": "synthetic/policy-001.pdf", "password": "synthetic-only-test"},
    )
    assert response.status_code == 422
    assert response.json()["error_code"] == "INVALID_REQUEST"
~~~

Run:

~~~bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_api.py -q
~~~

Expected: FAIL because the document router, service, and OpenAPI paths do not yet exist.

- [x] **Step 2: Implement the explicit local synthetic-ingestion gate**

Add `FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=false` to `.env.example` and make the module-level runtime app derive its default from both `FAMILYCARE_ENV=development` and `FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true`. Implement `create_app(*, readiness_probe: ReadinessProbe | None = None, enable_synthetic_ingestion: bool | None = None) -> FastAPI`: `None` derives the two-variable gate, while tests may pass `True` explicitly. The committed OpenAPI generator calls `create_app(enable_synthetic_ingestion=True)` so the canonical contract contains the routes without changing runtime defaults. When disabled, do not register the router and let both document-analysis paths return 404; leave `/health/live` and `/health/ready` unchanged. Tests cover disabled and enabled apps. The endpoint remains unauthenticated and local synthetic-only.

Add the custom FastAPI `RequestValidationError` handler to `apps/api/src/familycare_api/errors.py`. It returns HTTP 422 with the stable `INVALID_REQUEST` error code and sanitized field locations/messages without echoing raw request values, passwords, absolute paths, or document content.

- [x] **Step 3: Implement strict request and response schemas**

Use the generated contract types and FastAPI/Pydantic validation. Accept only relative source_key, document_kind from the contract enum, and canonical extractor settings. Configure extra="forbid". Keep password, absolute paths, raw bytes, URL fields, and arbitrary metadata outside the model. The response exposes only job UUID, state, sanitized error code, attempts, and extraction summary counts; it never exposes source path, PDF body, password, or private external identifiers.

- [x] **Step 4: Implement enqueue and status use cases**

`service.py` validates the relative `source_key` only, creates or reuses a `documents` row by that key, and enqueues an `analysis_jobs` row with queued state and canonical settings/config hash in one transaction. The POST response is 202 for every valid source key when the gate is enabled; it cannot open the file, compute `content_sha256`, create `document_versions`, or create `extractions`. Worker intake later opens the descriptor, computes the content hash, creates or reuses `document_versions` by `(document_id, content_sha256)`, and creates or reuses `extractions` by the succeeded partial uniqueness key. Missing, corrupt, and encrypted files therefore become asynchronous job errors; encrypted input is `PASSWORD_REQUIRED`. The GET status use case projects `PASSWORD_INVALID` only for direct adapter diagnostics that are never queued, returns `ANALYSIS_JOB_NOT_FOUND` for an unknown job, and never exposes retry details containing a source path or document body.

Register the router without changing /health/live or /health/ready. Add a clear local synthetic-only note to the API route documentation. Do not add authentication, session cookies, HouseholdSpace authorization, Policy Ledger, OCR, or insurance decision logic.

- [x] **Step 5: Regenerate and check the OpenAPI contract**

Regenerate `packages/contracts/openapi/familycare.v1.json` by calling `create_app(enable_synthetic_ingestion=True).openapi()` and extend `scripts/check_contracts.py` to compare the new paths byte-for-byte. Include request schemas, 202 response, 422 `INVALID_REQUEST`, 404 `ANALYSIS_JOB_NOT_FOUND`, and status response examples with only synthetic source keys. The generated contract opt-in must not alter the module-level runtime gate or the health contract.

- [x] **Step 6: Add the PostgreSQL/temp-root end-to-end integration test**

Create `apps/api/tests/test_document_analysis_e2e.py` as an integration test against a fresh PostgreSQL database and a `TemporaryDirectory`/`tmp_path` root outside the checkout. Opt into both gate variables, generate wholly synthetic unencrypted and encrypted fixtures with reportlab, set `FAMILYCARE_DOCUMENT_ROOT` to the temporary root, and exercise POST → Worker run → GET `succeeded` for the unencrypted fixture. Submit the encrypted fixture through the same API path, run the Worker, and assert GET reports `PASSWORD_REQUIRED`. Assert that passwords and source paths do not enter the job payload or captured logs. This is the test that proves encrypted input reaches the Worker; the API unit tests must retain their narrower contract scope.

Run the focused integration test before implementation:

~~~bash
FAMILYCARE_ENV=development FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_e2e.py -m integration -q
~~~

Expected: FAIL because the route, Worker wiring, migration, and integration fixture setup do not yet exist.

- [x] **Step 7: Run API, Worker, contract, and safety checks serially**

~~~bash
TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_api.py -q
FAMILYCARE_ENV=development FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_e2e.py -m integration -q
TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests -q
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run python scripts/check_repository_safety.py
TMPDIR=/tmp uv run ruff format --check apps/api workers/analyzer scripts
TMPDIR=/tmp uv run ruff check apps/api workers/analyzer scripts
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
git diff --check
~~~

Expected: every command exits 0; the asynchronous synthetic path is covered; no password, absolute path, real document, or private root is touched.

Local pre-PR evidence: 19 focused API tests, 178 complete non-integration tests, and 27 PostgreSQL integration tests passed. The integration set includes three API cases: synthetic POST → Worker → succeeded GET, encrypted → `PASSWORD_REQUIRED`, and active Document reuse with distinct queued jobs. Ruff format/lint, mypy, OpenAPI/JSON contracts, container/workflow policy, documentation, repository safety, Web/PWA checks, and `git diff --check` passed. Web, API, and Worker images built one at a time; the API image ran as UID 10001 and exposed only health routes by default, then exposed the two analysis routes only with the exact opt-in variables. One E2E retry was interrupted before collection by transient WSL memory exhaustion while unrelated Rust builds consumed swap; the unchanged test passed on the immediate serial retry. No unrelated process or container was stopped.

- [x] **Step 8: Commit the independently reviewable branch**

~~~bash
git add apps/api/src/familycare_api apps/api/tests/test_document_analysis_api.py apps/api/tests/test_document_analysis_e2e.py packages/contracts/openapi/familycare.v1.json scripts/check_contracts.py .env.example
git commit -m "feat(api): expose document analysis jobs"
~~~

- [x] **Step 9: Complete the PR checkpoint**

The root agent reviews the full feat/document-analysis-api diff once, checking the two-variable route gate and disabled 404 behavior, strict request parsing, custom `INVALID_REQUEST` handling, async state/error semantics, generated OpenAPI opt-in, no-password transport, local-only copy, unchanged Foundation health routes, the PostgreSQL/temp-root POST → Worker → GET E2E test, and absence of Policy Ledger or decision logic. Then push, create the PR, wait for all seven GitHub CI jobs, merge with a merge commit, fetch main, run the complete synthetic API-to-Worker path on post-merge main, and record the merge commit.

Completed in PR #12 at merge commit `1c77f019c9d2b150053e431c31171b97ff3d90c3`; PR run `32703651544` and post-merge `main` run `32703792722` each passed all seven required jobs. The verified post-merge main worktree passed the complete synthetic API-to-Worker path and the full Phase 1 verification recorded below.

---

## Full verification after all five merges

Run serially from the verified main worktree. Before Docker commands, inspect memory and repository-owned containers; do not stop unrelated processes or containers.

~~~bash
pwd
git status --short --branch
python3 scripts/check_documentation.py
python3 scripts/check_repository_safety.py
corepack pnpm@11.22.0 web:check
TMPDIR=/tmp uv run ruff format --check apps/api workers/analyzer scripts
TMPDIR=/tmp uv run ruff check apps/api workers/analyzer scripts
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run python scripts/check_containers.py
TMPDIR=/tmp uv run python scripts/check_workflows.py
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_pdf_intake.py workers/analyzer/tests/test_pdf_isolation.py workers/analyzer/tests/test_pdf_extraction.py workers/analyzer/tests/test_pdf_passwords.py -q
TMPDIR=/tmp uv run pytest workers/analyzer/tests/test_analysis_job_queue.py workers/analyzer/tests/test_analysis_job_runner.py -m integration -q
TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_api.py -q
FAMILYCARE_ENV=development FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true TMPDIR=/tmp uv run pytest apps/api/tests/test_document_analysis_e2e.py -m integration -q
git diff --check
~~~

For PostgreSQL integration, apply migration 0002 against a fresh PostgreSQL 18 database, run the queue and end-to-end synthetic tests, downgrade only when the migration test requires it, and rerun upgrade head. Build Web, API, and Worker images one at a time. Do not tag or push an image during this plan.

### Final verification record

Verified on `main` merge commit `1c77f019c9d2b150053e431c31171b97ff3d90c3` after PR #12 and post-merge CI completed:

- Documentation contract passed for 21 files, and repository safety passed for 134 paths.
- Web formatting, lint, TypeScript compilation, one Vitest regression test, production build, and PWA service-worker generation passed serially.
- Ruff format/lint and mypy passed for the API, Worker, and scripts.
- The complete non-integration Python suite passed 178 tests with 27 integration tests deselected.
- The PostgreSQL integration suite passed all 27 tests; the focused queue/runner files passed 22 tests, and the focused API-to-Worker E2E file passed all three tests.
- The focused PDF intake/isolation/extraction/password boundary passed 59 tests, and the focused API contract file passed 19 tests.
- OpenAPI/JSON contracts, container definitions, workflow policy, and `git diff --check` passed.
- Web, API, and Worker images built one at a time from verified main as local `phase-one-final` images. No image was tagged for release or pushed.
- The PostgreSQL 18 integration database and all PDF fixtures were repository-owned synthetic test resources; fixtures lived below checkout-external temporary roots. No unrelated process or container was stopped.

## Acceptance checklist

- [x] All five approved branches have one conventional commit purpose, one root-agent whole-diff review before push, one PR, seven successful GitHub CI jobs, a merge commit, and a post-merge main verification record.
- [x] THIRD_PARTY_NOTICES.md exists only after pdfplumber, pypdf, and reportlab are present in uv.lock and records their exact versions and distribution boundary.
- [x] No implementation or CI command opens a real PDF or private external root. Synthetic fixtures are generated or copied into checkout-external temporary roots.
- [x] Relative source_key, root containment, regular-file, symlink, PDF magic, 25 MiB size, 500-page, 1 MiB hash, and 0700/0600 rules have regression tests.
- [x] Dedicated child process applies 120-second parent wall, 90-second child CPU, 1536 MiB address-space, 64 MiB `RLIMIT_FSIZE` plus supervisor result cap, and 64 descriptor limits; child receives only the inherited or duplicated read-only source descriptor and canonical JSON settings.
- [x] pdfplumber is the primary extractor, pypdf validates structure/page/encryption, and reportlab creates deterministic fixtures. PyMuPDF remains explicitly rejected in ADR 0006.
- [x] Coordinates, TextBlock words, table/cell bboxes, per-page cache closure, quality-v1 classification, and OCR exclusion have regression tests.
- [x] PASSWORD_REQUIRED is returned for encrypted asynchronous input; direct one-shot PASSWORD_INVALID is tested; no password appears in DB, job payload, or logs.
- [x] Eight minimum physical tables and their parent-scoped uniqueness constraints exist; DocumentVersion represents content identity, and one succeeded extraction is enforced per document-version content plus extractor config hash.
- [x] AnalysisJob uses exactly six states with lease, heartbeat, attempts, recovery, retry classification, and cancellation tests.
- [x] The API is explicitly local synthetic-only and unauthenticated; Authentication provider remains Phase 7 and no endpoint is described as production-safe.
- [x] Synthetic routes are registered only when `FAMILYCARE_ENV=development` and `FAMILYCARE_ENABLE_SYNTHETIC_INGESTION=true`; the default-disabled runtime returns 404, while contract generation explicitly opts in without changing health routes.
- [x] API validation returns stable `INVALID_REQUEST` for 422 responses, unknown jobs return `ANALYSIS_JOB_NOT_FOUND`, and the PostgreSQL/temp-root E2E test covers POST → Worker → GET `succeeded` plus encrypted → `PASSWORD_REQUIRED`.
- [x] Policy Ledger, OCR execution, external URLs, external AI, Drive, real-data acceptance, Cloud Run, and production deployment remain outside this plan.

## Explicitly unverified boundaries

The following are not claimed by this plan even when local tests and GitHub CI pass: Windows process-limit behavior, browser/device PWA acceptance, real insurance or medical documents, private external roots, production OS egress enforcement, authentication provider integration, Google Drive, external AI, GHCR publishing, Cloud Run, and production deployment. Their completion requires the later phase's approval and evidence.
