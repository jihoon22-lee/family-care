# Selective OCR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Render and OCR only Phase 1 pages classified as OCR_REQUIRED with local Korean and English language packs, preserve native extraction provenance, and remove every rendered image on every exit path.

**Architecture:** The Worker receives a validated descriptor and native ExtractionResult, selects pages using the existing quality-v1 classification, renders selected pages through a descriptor-only PDFium adapter, and sends each image to a local Tesseract adapter with the fixed language set kor+eng. OCR output is stored as a separate versioned layer with engine, language, page, coordinate, and quality provenance; native blocks and the Phase 1 extraction-result.v1 contract are never overwritten.

**Tech Stack:** Python 3.14, pdfplumber 0.11.10, pypdf 6.16.2, pypdfium2==5.13.0, Pillow==12.3.0, the fixed `/usr/bin/tesseract` command with Debian English and Korean language packages, PostgreSQL 18, Alembic 1.19.1, and the existing Worker workspace/cleanup boundary. The Worker does not depend on `pytesseract` and does not create a Tesseract artifact file: it requests TSV on stdout through a no-shell subprocess boundary.

**Branch status:** The contract, migration, local adapters, selective processor, provenance persistence, bounded batch progress, and synthetic Worker language smoke checks are implemented on this branch. The complete PR gate, CI/merge evidence, private Compose runtime, and private-document acceptance remain pending.

**Spec:** docs/design/pdf-ingestion.md, docs/design/private-data-runtime.md, docs/design/security-privacy.md, docs/design/test-strategy.md, docs/design/v0.1-product.md, docs/plan/003-v0.1-implementation-index.md

## Global Constraints

- Migration `0013_selective_ocr.py` has `down_revision = "0012_encrypted_document_import"` and adds a separate OCR layer without rewriting native extraction or archive rows.
- Actual insurance, medical, identity, PDF, OCR, image, password, archive key, provider, and local-path values never enter Git, fixtures, logs, responses, or CI artifacts.
- Public tests use only from-scratch synthetic PDFs and synthetic English/Korean labels authored for the test.
- Public CI never calls OpenAI, Google Drive, Tailscale, or an external OCR provider.
- AI remains non-authoritative; only deterministic domain code may produce MATCH, NO_MATCH, UNKNOWN, or money.
- Missing facts, Evidence, contract state, renewal state, or rule support remain UNKNOWN, never NO_MATCH, zero, or an exception.
- Existing Phase 1 v1 schemas, native extraction rows, quality-v1 thresholds, descriptor-only parser input, job states, page numbering, coordinates, and cleanup contracts remain compatible.
- Only pages whose native quality classification is OCR_REQUIRED reach the renderer and OCR engine; TEXT_SUFFICIENT pages receive no OCR call.
- Native and OCR layers remain separately queryable and visibly labelled; OCR never replaces a native block.
- OCR page images use mode 0600 inside a mode-0700 job workspace and are deleted per page; Tesseract TSV is bounded on stdout and is never written as an artifact. The outer workspace is deleted after success, failure, cancellation, timeout, or shutdown.
- The Worker has no network OCR client; the Tesseract process is invoked without a shell, with bounded output and sanitized stdout/stderr.
- The Root PR gate in docs/plan/003-v0.1-implementation-index.md is run once on the complete branch immediately before push, followed by focused post-merge verification.

---

## File Responsibility Map

~~~text
apps/api/migrations/versions/0013_selective_ocr.py
  OCR layer, page, and block provenance tables. Phase 1 native tables remain unchanged.

packages/contracts/schemas/ocr-result.v1.schema.json
packages/contracts/examples/ocr-result.v1.json
scripts/check_ocr_contracts.py
  Versioned OCR result shape and safety checks.

workers/analyzer/src/familycare_worker/ocr/__init__.py
workers/analyzer/src/familycare_worker/ocr/models.py
  OCR layer, page, block, engine, language, and provenance types.
workers/analyzer/src/familycare_worker/ocr/renderer.py
  Descriptor-only PDFium rendering of selected pages.
workers/analyzer/src/familycare_worker/ocr/engine.py
  Tesseract command adapter with no shell and bounded output.
workers/analyzer/src/familycare_worker/ocr/processor.py
  OCR_REQUIRED selection, layer creation, and cleanup orchestration.
workers/analyzer/src/familycare_worker/ocr/provenance.py
  Engine/language/version and Evidence mapping.

workers/analyzer/tests/test_ocr_renderer.py
workers/analyzer/tests/test_ocr_engine.py
workers/analyzer/tests/test_selective_ocr.py
workers/analyzer/tests/test_ocr_cleanup.py
workers/analyzer/tests/test_ocr_contracts.py
  Unit and contract tests using fake renderer/engine plus synthetic image bytes.

infra/containers/worker.Dockerfile
workers/analyzer/pyproject.toml
uv.lock
THIRD_PARTY_NOTICES.md
  Runtime package and Tesseract language-pack installation.

.gitignore
scripts/check_repository_safety.py
scripts/tests/test_repository_safety.py
  Minimal source-package exception for workers/analyzer/src/familycare_worker/ocr.
~~~

## OCR Interfaces

~~~python
@dataclass(frozen=True)
class RawOcrBlock:
    text: str
    pixel_bbox: tuple[int, int, int, int]
    reading_order: int
    confidence: float


@dataclass(frozen=True)
class EnginePageResult:
    engine_name: Literal["tesseract"]
    engine_version: str
    image_width_pixels: int
    image_height_pixels: int
    blocks: tuple[RawOcrBlock, ...]
    warning_codes: tuple[OcrWarningCode, ...]


@dataclass(frozen=True)
class OcrBlock:
    text: str
    bbox: tuple[float, float, float, float]
    reading_order: int
    confidence: float
    source_layer: Literal["ocr"] = "ocr"
    review_state: Literal["candidate"] = "candidate"


class PageRenderer(Protocol):
    def render(
        self,
        source_fd: int,
        page_number: int,
        output: BinaryIO,
        *,
        dpi: int,
    ) -> RenderedPage: ...


class OcrEngine(Protocol):
    engine_version: str

    def recognize(
        self,
        image_path: Path,
        *,
        languages: tuple[str, ...],
    ) -> EnginePageResult: ...


class SelectiveOcrProcessor:
    def process(
        self,
        extraction: ExtractionResult,
        source_fd: int,
        workspace: Workspace,
        *,
        document_version_id: UUID,
        content_sha256: str,
    ) -> SelectiveOcrResult | None: ...
~~~

`SelectiveOcrResult` is mapped by `ocr/provenance.py` to the strict schema-version-1 `ocr-result.v1` contract. It carries the same DocumentVersion/content hash, fixed `language_codes=[kor, eng]`, quality rule, engine/version, selected page metadata, OCR blocks, warning codes, and OCR Evidence. A document with no `OCR_REQUIRED` page returns no OCR result. The implementation does not add an `ocr` field to the existing extraction-result.v1 JSON Schema.

## Task 1: Define OCR layer contracts and physical provenance tables

**Files:**

- Create: apps/api/migrations/versions/0013_selective_ocr.py
- Create: packages/contracts/schemas/ocr-result.v1.schema.json
- Create: packages/contracts/examples/ocr-result.v1.json
- Create: scripts/check_ocr_contracts.py
- Create: apps/api/tests/test_ocr_contracts.py
- Create: workers/analyzer/tests/test_ocr_contracts.py
- Create: workers/analyzer/tests/test_ocr_database.py
- Modify: scripts/check_contracts.py
- Modify: packages/contracts/README.md

**Interfaces:**

- Consumes: native Extraction, ExtractionPage quality classification, DocumentVersion Evidence, and the latest Alembic head.
- Produces: tables ocr_layers, ocr_pages, and ocr_blocks.
- ocr_layers stores extraction ID, source layer, engine name/version, language configuration hash, quality rule version, status, and timestamps.
- ocr_pages stores layer ID, 1-based page number, rendered DPI, image width/height, selected classification, warning codes, and status.
- ocr_blocks stores page ID, text, top-left PDF-point bbox rounded to three decimals, reading order, confidence, and Evidence coordinates.
- A unique key on layer/page/language configuration prevents duplicate successful OCR output. No native table is altered and no PDF bytes or image path is stored.

- [x] **Step 1: Write failing schema and migration tests**

~~~python
def test_ocr_result_preserves_layer_and_page_provenance() -> None:
    value = load_example("ocr-result.v1.json")

    assert value["schema_version"] == "1"
    assert value["source_layer"] == "ocr"
    assert value["language_codes"] == ["kor", "eng"]
    assert value["pages"][0]["page_number"] == 1
    assert value["pages"][0]["evidence"]["page_number"] == 1
    assert "image_path" not in json.dumps(value)
    assert "pdf_bytes" not in json.dumps(value)


def test_ocr_migration_has_no_native_overwrite_columns(migration_text: str) -> None:
    assert "ocr_layers" in migration_text
    assert "ocr_pages" in migration_text
    assert "ocr_blocks" in migration_text
    assert "UPDATE extraction_blocks" not in migration_text
~~~

- [x] **Step 2: Run the RED contract tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_database.py -q
~~~

Expected: FAIL because the OCR schema, checker, generated example, and migration 0013_selective_ocr do not exist.

- [x] **Step 3: Add the versioned OCR schema and migration**

Use the exact output boundary:

~~~json
{
  "schema_version": "1",
  "source_layer": "ocr",
  "language_codes": ["kor", "eng"],
  "engine_name": "tesseract",
  "engine_version": "synthetic-engine-1",
  "pages": [
    {
      "page_number": 1,
      "blocks": [],
      "evidence": {
        "document_version_id": "00000000-0000-4000-8000-000000000002",
        "page_number": 1,
        "content_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      }
    }
  ]
}
~~~

Reject unknown properties, absolute paths, image paths, PDF bytes, passwords, and external URLs. Add foreign keys to existing extraction/document-version records, positive page/DPI constraints, bbox array bounds, and the partial unique success index. Keep page numbers 1-based and reading order 0-based.

- [x] **Step 4: Run the GREEN contract and migration checks**

Run:

~~~bash
TMPDIR=/tmp uv run python scripts/check_ocr_contracts.py
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run pytest \
  apps/api/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_database.py -q
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
~~~

Expected: OCR contract and migration tests pass while the Phase 1 document contract checker remains unchanged except for additive route/schema validation.

- [x] **Step 5: Commit the OCR contract slice**

~~~bash
git add apps/api/migrations/versions/0013_selective_ocr.py \
  packages/contracts/schemas/ocr-result.v1.schema.json \
  packages/contracts/examples/ocr-result.v1.json \
  scripts/check_ocr_contracts.py \
  apps/api/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_database.py \
  scripts/check_contracts.py packages/contracts/README.md
git commit -m "feat(ocr): define provenance contracts"
~~~

## Task 2: Add descriptor-only rendering and local Tesseract execution

**Files:**

- Create: workers/analyzer/src/familycare_worker/ocr/renderer.py
- Create: workers/analyzer/src/familycare_worker/ocr/engine.py
- Create: workers/analyzer/src/familycare_worker/ocr/models.py
- Create: workers/analyzer/tests/test_ocr_renderer.py
- Create: workers/analyzer/tests/test_ocr_engine.py
- Modify: workers/analyzer/pyproject.toml
- Modify: uv.lock
- Modify: infra/containers/worker.Dockerfile
- Modify: THIRD_PARTY_NOTICES.md

**Interfaces:**

- Consumes: a read-only source descriptor, a 1-based page number, and a pre-created mode-0600 output handle.
- Produces: PdfiumPageRenderer.render and TesseractOcrEngine.recognize.
- Renderer reads from a duplicate descriptor or bounded bytes and never accepts a filesystem path to the source PDF.
- Tesseract receives an argument list, not a shell command; its stdout/stderr are discarded or mapped to a sanitized stable error.
- The fixed language tuple is (kor, eng); no runtime language input can add an arbitrary executable or external language file.
- Render DPI is fixed at 300, image output is PNG, and subprocess wall timeout is 60 seconds per selected page.

- [x] **Step 1: Write renderer and engine RED tests**

~~~python
def test_renderer_uses_descriptor_and_requested_page(fake_pdfium, tmp_path: Path) -> None:
    renderer = PdfiumPageRenderer(pdfium=fake_pdfium)
    output = tmp_path / "synthetic-page.png"

    renderer.render(SYNTHETIC_SOURCE_FD, 2, output, dpi=300)

    assert fake_pdfium.opened_from_descriptor is True
    assert fake_pdfium.rendered_pages == [2]
    assert output.stat().st_mode & 0o777 == 0o600


def test_tesseract_engine_rejects_unapproved_language(fake_runner, tmp_path: Path) -> None:
    engine = TesseractOcrEngine(runner=fake_runner)
    with pytest.raises(OcrConfigurationError):
        engine.recognize(tmp_path / "synthetic.png", languages=("eng", "deu"))
~~~

- [x] **Step 2: Run the RED renderer/engine tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  workers/analyzer/tests/test_ocr_renderer.py \
  workers/analyzer/tests/test_ocr_engine.py -q
~~~

Expected: FAIL because the renderer, engine, and package dependencies are absent.

- [x] **Step 3: Implement the bounded descriptor renderer and no-shell engine**

The production boundary is a pre-created mode-0600 workspace output handle plus a validated read-only source descriptor. PDFium receives bounded bytes read from that descriptor; it never receives a source path:

~~~python
def render(self, source_fd: int, page_number: int, output: BinaryIO, *, dpi: int) -> RenderedPage:
    if page_number < 1 or dpi != 300:
        raise OcrConfigurationError
    _validate_output(output)  # regular writable file, mode 0600
    source = _read_source(source_fd)  # fstat/read-only/regular, <= 25 MiB
    document = pdfium.PdfDocument(source)
    page = document.get_page(page_number - 1)
    try:
        image = page.render(scale=300 / 72).to_pil()
        image.save(output, format="PNG", optimize=False)
        output.flush()
        os.fsync(output.fileno())
        return RenderedPage(page_number, 300, *image.size)
    finally:
        image.close()
        page.close()
        document.close()


def recognize(self, image_path: Path, *, languages: tuple[str, ...]) -> EnginePageResult:
    if languages != ("kor", "eng"):
        raise OcrConfigurationError
    output = run_bounded_command(
        ["/usr/bin/tesseract", str(image_path), "stdout", "--psm", "6", "-l", "kor+eng", "tsv"],
        timeout_seconds=60.0,
        max_output_bytes=8 * 1024 * 1024,
    )
    if output.returncode != 0:
        raise OcrExecutionError("OCR_FAILED")
    return parse_tsv(output.stdout)
~~~

The renderer fixes DPI at 300, bounds rendered dimensions/pixels, and validates the source descriptor as regular/read-only with the existing 25 MiB limit. The engine fixes the executable to `/usr/bin/tesseract`, passes an argv list with `shell=False`, discards stderr, captures bounded UTF-8 TSV from stdout, rejects malformed rows, and bounds block count. It validates the mode-0600 PNG before invoking Tesseract; no `.tsv` artifact is created.

- [x] **Step 4: Run the GREEN unit, lock, and image dependency checks**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  workers/analyzer/tests/test_ocr_renderer.py \
  workers/analyzer/tests/test_ocr_engine.py -q
TMPDIR=/tmp uv run ruff format --check workers/analyzer
TMPDIR=/tmp uv run ruff check workers/analyzer
TMPDIR=/tmp uv run mypy workers/analyzer/src
docker build --file infra/containers/worker.Dockerfile --tag familycare-worker:ocr-validation .
~~~

Expected: unit tests pass, the Worker lock contains pypdfium2 5.13.0 and Pillow 12.3.0 but no pytesseract dependency, and the image installs only the local Tesseract binary plus eng/kor language packages without embedding fixtures or secrets. The container-definition test checks those packages and the CI Worker image job loads the image and runs `tesseract --list-langs`, requiring `eng` and `kor`.

- [x] **Step 5: Commit renderer and engine implementation**

~~~bash
git add workers/analyzer/src/familycare_worker/ocr \
  workers/analyzer/tests/test_ocr_renderer.py \
  workers/analyzer/tests/test_ocr_engine.py \
  workers/analyzer/pyproject.toml uv.lock \
  infra/containers/worker.Dockerfile THIRD_PARTY_NOTICES.md
git commit -m "feat(ocr): add local Korean English engine"
~~~

## Task 3: Implement OCR_REQUIRED selection, provenance persistence, and cleanup

**Files:**

- Create: workers/analyzer/src/familycare_worker/ocr/processor.py
- Create: workers/analyzer/src/familycare_worker/ocr/provenance.py
- Create: workers/analyzer/tests/test_selective_ocr.py
- Create: workers/analyzer/tests/test_ocr_cleanup.py
- Modify: workers/analyzer/src/familycare_worker/runner.py
- Modify: workers/analyzer/src/familycare_worker/repository.py
- Modify: workers/analyzer/src/familycare_worker/health.py
- Modify: apps/api/src/familycare_api/documents/batch_service.py
- Modify: apps/web/src/features/documents/BatchProgress.tsx
- Modify: apps/web/src/features/documents/document-import.test.tsx
- Test: workers/analyzer/tests/test_analysis_job_runner.py

**Interfaces:**

- Consumes: native ExtractionResult pages, PdfiumPageRenderer, TesseractOcrEngine, Workspace, and the OCR repository tables.
- Produces: SelectiveOcrProcessor.process, OcrRepository.persist, a combined document-analysis result that points to both native and OCR layers, and a safe batch-item projection with `ocr_state`, `ocr_pages_processed`, and stable warning codes.
- Pages with TEXT_SUFFICIENT never call renderer or engine. Pages with OCR_REQUIRED call each exactly once unless a retryable local resource failure occurs.
- OCR Evidence always includes the original DocumentVersion UUID, 1-based page number, content hash, source layer ocr, engine/version, and language codes.
- Cleanup runs in a finally block and a cleanup failure is a stable TEMP_CLEANUP_FAILED result, not a successful OCR state.

- [x] **Step 1: Write selective-page and all-exit-path RED tests**

~~~python
def test_only_ocr_required_pages_are_processed(fake_renderer, fake_engine, workspace) -> None:
    extraction = synthetic_extraction(
        classifications=["TEXT_SUFFICIENT", "OCR_REQUIRED", "TEXT_SUFFICIENT", "OCR_REQUIRED"]
    )

    result = SelectiveOcrProcessor(fake_renderer, fake_engine).process(
        extraction, SYNTHETIC_SOURCE_FD, workspace
    )

    assert fake_renderer.pages == [2, 4]
    assert fake_engine.pages == [2, 4]
    assert [page.page_number for page in result.pages] == [2, 4]
    assert all(page.source_layer == "ocr" for page in result.pages)


@pytest.mark.parametrize("exit_kind", ["success", "engine_error", "cancelled", "shutdown"])
def test_ocr_images_are_removed_on_every_exit(
    fake_renderer, fake_engine, exit_kind, workspace
) -> None:
    run_processor(exit_kind, fake_renderer, fake_engine, workspace)
    assert workspace.remaining_files(suffix=".png") == []
    assert workspace.remaining_files(suffix=".tsv") == []
~~~

- [x] **Step 2: Run the RED processor tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  workers/analyzer/tests/test_selective_ocr.py \
  workers/analyzer/tests/test_ocr_cleanup.py \
  workers/analyzer/tests/test_analysis_job_runner.py -q
~~~

Expected: FAIL because the selector, provenance mapper, repository persistence, and cleanup integration are absent.

- [x] **Step 3: Implement selection and repository persistence**

~~~python
def process(
    self,
    extraction: ExtractionResult,
    source_fd: int,
    workspace: Workspace,
    *,
    document_version_id: UUID,
    content_sha256: str,
) -> SelectiveOcrResult | None:
    selected = [
        page for page in extraction["pages"] if page["quality"]["classification"] == "OCR_REQUIRED"
    ]
    if not selected:
        return None
    pages: list[OcrPageResult] = []
    for page in selected:
        image_path = workspace.path / f"ocr-page-{page['page_number']}.png"
        created = False
        try:
            with workspace.create_file(image_path.name) as image:
                created = True
                rendered = self.renderer.render(source_fd, page["page_number"], image, dpi=300)
            pages.append(self._recognize_and_map(page, image_path, rendered))
        finally:
            if created:
                image_path.unlink()  # immediate per-page cleanup; failure is TEMP_CLEANUP_FAILED
    return self._with_provenance(extraction, pages, document_version_id, content_sha256)
~~~

Persist OCR rows only after all selected pages pass shape/Evidence validation; partial OCR rows are rolled back. Keep native extraction rows and native Evidence unchanged. A page with no OCR text becomes an OCR warning/result state and does not become a native block. The processor removes each PNG immediately after that page's recognition; the enclosing AnalysisJob/BatchRunner then removes the entire mode-0700 workspace on success, failure, cancellation, timeout, shutdown, or cleanup error.

Project only bounded OCR progress through the existing authenticated batch status route and `BatchProgress`: `ocr_state`, `ocr_pages_processed` (0..500), and a unique allowlist of at most eight warning codes. Show native-only, OCR-running, OCR-complete, and OCR-warning as text states; never return OCR text, image paths, Tesseract stderr, filenames, or coordinates in the progress projection. The Web adds no raw-OCR editor and keeps the progress response memory-only.

- [x] **Step 4: Run the GREEN selective OCR and PostgreSQL checks**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  workers/analyzer/tests/test_selective_ocr.py \
  workers/analyzer/tests/test_ocr_cleanup.py \
  workers/analyzer/tests/test_analysis_job_runner.py -q
FAMILYCARE_DATABASE_URL=postgresql+psycopg://synthetic_ci:synthetic_only@127.0.0.1:5432/synthetic_ci \
TMPDIR=/tmp uv run pytest -m integration \
  workers/analyzer/tests/test_ocr_database.py workers/analyzer/tests/test_analysis_job_runner.py -q
~~~

Expected: only OCR_REQUIRED pages are rendered, native/OCR provenance remains separate, the bounded progress projection contains no OCR payload, and success/failure/cancel/shutdown leaves no temporary image or TSV. This synthetic evidence does not validate actual private PDFs or the private Compose runtime.

- [x] **Step 5: Commit the processor slice**

~~~bash
git add workers/analyzer/src/familycare_worker/ocr/processor.py \
  workers/analyzer/src/familycare_worker/ocr/provenance.py \
  workers/analyzer/tests/test_selective_ocr.py \
  workers/analyzer/tests/test_ocr_cleanup.py \
  workers/analyzer/tests/test_analysis_job_runner.py \
  workers/analyzer/src/familycare_worker/runner.py \
  workers/analyzer/src/familycare_worker/repository.py \
  workers/analyzer/src/familycare_worker/health.py \
  apps/api/src/familycare_api/documents/batch_service.py \
  apps/web/src/features/documents/BatchProgress.tsx \
  apps/web/src/features/documents/document-import.test.tsx
git commit -m "feat(ocr): process classified pages selectively"
~~~

## Task 4: Add the narrow repository safety exception and acceptance evidence

**Files:**

- Modify: .gitignore
- Modify: scripts/check_repository_safety.py
- Modify: scripts/tests/test_repository_safety.py
- Modify: docs/guide.md
- Modify: docs/design/private-data-runtime.md
- Test: workers/analyzer/tests/test_ocr_contracts.py
- Test: workers/analyzer/tests/test_ocr_cleanup.py

**Interfaces:**

- Consumes: source-only OCR package, Worker image language packs, cleanup processor, and OCR contract checker.
- Produces: a scanner policy that permits only tracked Python modules under workers/analyzer/src/familycare_worker/ocr and continues to reject OCR output directories, images, PDFs, and logs everywhere else.
- Produces operator documentation that distinguishes synthetic OCR regression from user-approved private acceptance and records Windows, mobile, real-format, and Tailscale checks separately.

- [x] **Step 1: Write the safety exception tests**

~~~python
def test_ocr_source_module_is_allowed_but_ocr_output_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "workers/analyzer/src/familycare_worker/ocr/engine.py"
    output = tmp_path / "ocr/synthetic-page.png"
    source.parent.mkdir(parents=True)
    source.write_text("synthetic = True", encoding="utf-8")
    output.parent.mkdir(parents=True)
    output.write_bytes(b"synthetic-image")

    assert inspect_path(tmp_path, source) == []
    assert inspect_path(tmp_path, output)
~~~

- [x] **Step 2: Run the RED safety tests**

Run:

~~~bash
TMPDIR=/tmp uv run pytest \
  scripts/tests/test_repository_safety.py \
  workers/analyzer/tests/test_ocr_contracts.py -q
~~~

Expected: FAIL because the current scanner and .gitignore reject the source package path or do not test the narrow exception.

- [x] **Step 3: Implement the minimum source-only exception**

Add a precise predicate for the exact Worker package path and Python suffixes. Add matching .gitignore negations for the source package only. Do not allow files under any runtime OCR directory, generated output path, image suffix, PDF suffix, or log suffix. Keep the existing public PDF/image allow roots unchanged.

- [ ] **Step 4: Run the complete OCR-focused gate**

Run serially:

~~~bash
TMPDIR=/tmp uv run python scripts/check_ocr_contracts.py
TMPDIR=/tmp uv run python scripts/check_repository_safety.py
TMPDIR=/tmp uv run pytest \
  workers/analyzer/tests/test_ocr_renderer.py \
  workers/analyzer/tests/test_ocr_engine.py \
  workers/analyzer/tests/test_selective_ocr.py \
  workers/analyzer/tests/test_ocr_cleanup.py \
  workers/analyzer/tests/test_ocr_contracts.py \
  scripts/tests/test_repository_safety.py -q
TMPDIR=/tmp uv run ruff format --check .
TMPDIR=/tmp uv run ruff check .
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
git diff --check
~~~

Expected: every required check passes; no OCR image, extracted text, actual PDF, or language-pack output is tracked. The branch still needs this complete gate after the documentation and bounded batch-projection changes are assembled.

- [ ] **Step 5: Commit safety/documentation and invoke the Root PR gate**

~~~bash
git add .gitignore scripts/check_repository_safety.py \
  scripts/tests/test_repository_safety.py docs/guide.md \
  docs/design/private-data-runtime.md \
  workers/analyzer/tests/test_ocr_contracts.py \
  workers/analyzer/tests/test_ocr_cleanup.py
git commit -m "test(ocr): enforce source-only repository boundary"
~~~

Before push, execute the complete Root PR gate from docs/plan/003-v0.1-implementation-index.md, review the full diff once, and only then open a PR. No PR, CI result, merge commit, tag, or post-merge main verification is claimed by this branch documentation. Actual private-document OCR, private Compose, Windows, mobile/browser, Tailscale, and provider checks remain separately reported and unverified.
