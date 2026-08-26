# Changelog

FamilyCare의 주요 변경사항은 이 파일에 기록합니다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르고, 버전은 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 사용합니다.

## [Unreleased]

### Added

- Approved v0.1 product design for Phases 2 through 8, targeting a usable private WSL/Tailscale release.
- AI-assisted policy, clause, and rule structuring with an independent verifier and deterministic evidence/schema validation.
- Hybrid pre-visit and post-treatment input, fixed-benefit and partial indemnity calculation, and action-first mobile results.
- Local two-admin authentication, encrypted family-scoped PDF batches, managed encrypted archive, selective Korean/English OCR, and claim metadata/checklists in the v0.1 design.
- Selective OCR implementation: `OCR_REQUIRED`-only selection, separate `ocr_layers`/`ocr_pages`/`ocr_blocks` provenance, bounded descriptor-derived PDFium rendering, direct no-shell `/usr/bin/tesseract` stdout TSV with fixed `kor+eng` at 300 DPI, per-page and outer-workspace cleanup, bounded batch progress, and synthetic Worker image language checks.
- Local authentication implementation with exactly two equal administrators, safe `familycare-admin` TTY/stdin provisioning, hash-only server sessions, and the authenticated Web login/session boundary.
- Private encrypted-import reliability implementation: decrypted plaintext is bounded to 25 MiB and encrypted PDFs over 500 pages are rejected before cloning; cancellation, stop, and lease-loss paths dispose batch passwords and deactivate the secret-server identity; archive writes are bracketed by owned heartbeats with definite-orphan cleanup; ambiguous success commits retain ciphertext and emit only `batch_archive_commit_uncertain`; source labels are normalized to printable, path-free 1–160 character values with aligned API/OpenAPI/JSON Schema constraints.
- Private local Compose implementation with one loopback Web gateway, internal API/PostgreSQL/Worker, read-only API/Worker import binds, Worker-only archive/work/key/AI scope, a fixed-group secret socket, fail-closed Worker readiness, and a bounded read-only Tailscale inspector. Synthetic Compose permission smoke passed; PR/CI/merge and actual private-data, Windows/mobile, Tailscale, provider, and private OCR acceptance remain pending.
- One-time `familycare-admin init` provisioning that atomically creates the unseeded sole HouseholdSpace and first administrator while keeping the password on TTY/stdin and rejecting repeat or concurrent initialization.

- 프로젝트 기반 설계와 전체 단계별 로드맵
- 공개 저장소 개인정보 경계와 개발 지침
- 브랜치 및 Conventional Commits 규칙과 자동 검증
- React PWA, FastAPI API, PostgreSQL 준비 상태, Analyzer Worker의 최소 실행 기반
- 버전이 지정된 OpenAPI·Analyzer 작업 계약과 완전 합성 fixture
- Alembic `0001_foundation` 마이그레이션 기준선
- 비루트 Web/API/Worker 이미지와 PostgreSQL 기반 로컬 Docker Compose 환경
- PR과 `main`에서 문서·보안·Web·Python·DB·컨테이너를 검증하는 CI
- Semantic-version Git tags publish independently versioned Web, API, and Worker images to GHCR after full Foundation validation.
- Published images receive version and 12-character commit SHA tags; pre-1.0 releases do not create `latest`.
- Phase 1 synthetic PDF ingestion design, implementation plan, and minimum Document/Extraction/AnalysisJob model boundary.
- ADR 0006 selecting pdfplumber 0.11.10, pypdf 6.16.2, and reportlab 5.0.1 for the permissive synthetic-only parser stack.
- Versioned document-ingestion, pre-intake analysis-job, and evidence-preserving extraction-result contracts with deterministic API/Worker TypedDict generation.
- Alembic `0002_document_ingestion` with the eight-table Phase 1 document, extraction, evidence-coordinate, and analysis-job model.
- Descriptor-based local PDF intake with no-follow traversal, structural validation, bounded hashing, private workspaces, and resource-limited parser-child supervision.
- Deterministic synthetic PDF fixtures and descriptor-only pdfplumber extraction for words, tables, cells, evidence coordinates, and versioned page-quality metrics.
- Third-party parser inventory with pdfplumber and pypdf as Worker runtime dependencies and reportlab restricted to development/test fixtures.
- PostgreSQL AnalysisJob claims with `SKIP LOCKED`, owner leases and heartbeats, bounded retry classification, cancellation, and max-attempt recovery.
- A single-job Analyzer runner with descriptor-only parser execution, shutdown cancellation, sanitized cleanup failure handling, strict child-result validation, and transactional extraction persistence.
- Idempotent DocumentVersion and succeeded Extraction reuse for matching content and extractor configuration identities.
- A default-disabled local synthetic document-analysis API with asynchronous enqueue/status routes, strict v1 request validation, and sanitized extraction summaries.
- Phase 2 household-scoped family, policy, policy-party, subscribed-Rider, Evidence, and status-snapshot ledger with optimistic versions and soft-delete trash/restore.
- Evidence-backed policy APIs and a deterministic `policy-ledger.v1` JSON Schema/OpenAPI consumer with fixed value-free business error codes.
- Evidence-bounded OpenAI policy structuring and independent verification with deterministic publication guards.
- Immutable policy-candidate review versions, exact review-item corrections, optimistic confirmation/rejection, and terms-only Rider exclusion.
- A memory-only, no-store family policy ledger PWA with candidate review, typed corrections, bounded Evidence display, and synthetic Chromium coverage.
- Phase 4 TermsEdition and Clause hierarchy with PostgreSQL `simple` FTS + `pg_trgm`, household/date/edition/insurer/product scope, bounded Evidence-backed results, and private no-store Web search.
- Phase 5 scoped Rider-Clause link confirmation/rejection and CoverageRule publication boundary with exact Policy/Clause Evidence checks.
- A bounded data-only CoverageRule DSL validator that records typed rule candidates without executing decision logic.
- Generic `rider_clause` and `coverage_rule` review domains with typed child-version corrections and optimistic concurrency metadata.
- `/app/clauses/review` with separate link/rule queues, Evidence disclosure, generated typed controls, and no raw DSL editor.
- Synthetic Web review coverage including a 320px Playwright flow for Evidence disclosure, stored-version publication, and browser-storage privacy.
- Phase 6 deterministic Coverage Decision Engine with structured pre-visit/post-treatment MedicalEvent lifecycle, household-scoped soft-delete/trash/restore, and optimistic version checks.
- Exact `MATCH`/`NO_MATCH`/`UNKNOWN` aggregation for actually subscribed Riders and published executable CoverageRules, with bounded follow-up questions and Evidence lineage.
- Transactional decision runs, RuleEvaluations, and Rider candidates with immutable Evidence metadata/content-hash snapshots for reproducible result reads.
- Strict `coverage-decision.v1` contract and no-store MedicalEvent create/update/analyze/result HTTP boundary, including synthetic PostgreSQL integration and privacy tests.
- PR7 benefit-calculation boundary: PostgreSQL `0008_benefit_calculations`, Decimal fixed/indemnity traces, manual household-scoped receipt lines, partial results, multiple-indemnity allocation `UNKNOWN`, strict `benefit-calculation.v1`, and no-store calculation HTTP routes.
- Phase 5 Event/Result PWA with optional natural-language fact structuring, editable user overrides, manual post-treatment receipt lines, action-first result groups, stale/partial handling, and bounded Evidence disclosure.
- A separate PostgreSQL event-structuring queue and strict provider-neutral contract that cannot return tri-state decisions or monetary results.
- Immutable Rider label snapshots on ClaimCandidate results so historical result cards retain the analyzed coverage label.
- Phase 6 ClaimCase workflow with immutable Candidate/Rule/Policy/Evidence/all-matching-calculation snapshots, exact manual status transitions, metadata-only checklist, and soft-delete/trash/restore.
- Result-card direct ClaimCase creation that accepts only `rider_id`; the server derives the scoped policy and insurer from the selected Rider.
- Paid/partially-paid ClaimHistory projection with counted occurrences and denied audit-only outcomes that do not become future `NO_MATCH` evidence.
- Strict `claim-workflow.v1` schema/example/checker coverage and no-store claim routes without insurer submission or claim-file storage.

### Changed

- The roadmap now records Phase 1 as complete and defines the independent PR sequence and acceptance gate for `v0.1.0`.
- OpenAI document structuring moves into v0.1 while Google Drive automation, Gemini, insurer submission, Cloud Run, and host disk/swap changes remain deferred.
- Business routes now derive `HouseholdScope` from the authenticated local session; client-supplied household identifiers remain non-authoritative, and Tailscale private access does not replace app login.
- Local sessions use a host-only `Secure`, `HttpOnly`, `SameSite=Strict` cookie and expire after 7 days of inactivity or 30 days absolute, whichever comes first; session listing, revocation, and recent reauthentication are available.

- 로컬 검증 명령은 WSL 임시파일 경로와 고정 pnpm 버전을 재현 가능하게 사용합니다.
- Foundation completion is recorded at PR #1 merge commit `0f632989df891ae944c012bfcce6c838009867a9`; PR and post-merge CI had seven successful jobs. Tag/GHCR, Cloud Run, and real/private-data verification remain outside that evidence.
- Phase 1 documentation uses pnpm 11.22.0 and explicitly keeps implementation and CI synthetic-only.
- Web Vitest runs with `--maxWorkers=1` to avoid the measured WSL worker-start timeout under memory pressure; this serializes workers without reducing test coverage.
- Dependabot policy ignores semver-major updates for npm `typescript` and Docker `node`/`postgres`, ignores semver-minor and semver-major updates for Docker `python`, and therefore leaves Python 3.14 patch updates eligible; `check_workflows.py` validates the official update-type syntax.
- The unprivileged Web runtime is pinned to `nginxinc/nginx-unprivileged:1.31.2-alpine3.23`.
- Phase 1 document contracts and the eight-table ingestion model were merged in PR #8 at `9802781c98c0a6aee3fcd7018dfc020da087fee9`; all seven PR and post-merge `main` checks passed.
- Phase 1 PDF intake and parser-child isolation were merged in PR #9 at `523bd68be3d951e37a9f4ba19b858d9ac9bdcfcc`; all seven PR and post-merge `main` checks passed.
- Phase 1 synthetic PDF extraction was merged in PR #10 at `eac98171fd72604c7ff0c641f7c80f02c99d145a`; all seven PR and post-merge `main` checks passed, along with the local post-merge extraction checks.
- Phase 1 AnalysisJob queue and Worker runner were merged in PR #11 at `cc651436cab884109dc6fdc7f793c8b32e9c86d4`; PR and post-merge `main` CI each passed 7/7, with 23 local queue tests and 59 local extraction tests passing after merge.
- Phase 1 local synthetic document-analysis API was merged in PR #12 at `1c77f019c9d2b150053e431c31171b97ff3d90c3`; PR and post-merge `main` CI each passed 7/7.
- Phase 2 candidate review was merged into `main` in PR #16; Phase 4 Clause search remains synthetic-only and its default household scope resolver stays fail-closed without an authenticated local session.
- CoverageRule version reads expose `expected_version`; publication accepts only the expected version and a stored version ID, while deterministic rule evaluation remains deferred to the next phase.
- Claim workflow records user-entered receipt/payment metadata and expected-version manual transitions; it does not send submissions to insurers or store medical/claim files. Historical snapshots remain immutable when later results are reanalyzed.
- Phase 1 final verification passed Web/PWA checks, 178 non-integration tests, 27 PostgreSQL integration tests, 59 focused PDF-boundary tests, 19 focused API tests, three focused API-to-Worker E2E tests, all contract/policy checks, and serial local Web/API/Worker image builds. No release tag, image push, Cloud Run, production deployment, or real/private-data verification was performed.
- Selective OCR merged in PR #25 and private-import reliability merged in PR #26 after their serial local gates, required PR checks, post-merge `main` CI, and focused post-merge verification passed. Private runtime and actual private-data acceptance remain separate.

### Deprecated

### Removed

### Fixed

- Worker 콘솔 진입점이 `--health` 인자를 읽고 종료 코드로 준비 상태를 보고합니다.
- 생성된 PWA 산출물이 소스 포맷 검사에 다시 포함되지 않습니다.
- 로컬 필수 Ruff 검사의 범위를 CI와 동일하게 맞춰 문서의 Python 코드블록 포맷 차이도 PR 전에 발견합니다.
- A failed optional structuring job no longer remounts the event editor or discards manually entered analysis facts.
- Evidence disclosure rejects content-hash mismatches, failed extractions, and out-of-page coordinates instead of presenting stale source text.
- Receipt-save retries reuse the already created MedicalEvent, and historical event results never mix in calculations from a newer event version.

### Security

- 실제 보험 문서와 파생 데이터의 저장소 유입을 금지하는 정책 수립
- Phase 1 parser boundary records that passwords never enter database rows, job payloads, or logs and that production acceptance waits for an approved runtime boundary.
- Document contracts reject absolute, traversal, Windows-style, multiline, password-bearing, and client-hashed intake requests; PostgreSQL enforces job, extraction, evidence-review, and successful-extraction identity states.
- Parser-child IPC accepts only bounded canonical JSON and never unpickles child-controlled values in the parent process.
- The extraction child receives an exact password-free post-intake settings object, preserves the validated descriptor identity, and sanitizes corrupt or invalid-password failures to stable codes.
- Queue payloads are revalidated against their server-computed config hash before processing, and lease ownership is required for heartbeat, failure, and success transitions.
- Malformed parser output is rejected before persistence; extraction rows and job success commit atomically, while temporary-cleanup failure is permanently failed and logged only with a job UUID.
- Synthetic analysis routes require the exact development/feature-flag opt-in, reject extra credential/path/body fields, and return value-free validation errors without opening documents in the API process.
- Local administrator passwords are prompted through TTY/stdin only; they are not accepted in argv or environment variables, and identity rows persist only Argon2id password hashes plus hashed session/CSRF proofs.
- Policy routes reject client-selected household scope, terms-only or unreviewed Evidence, stale writes, and cross-document Evidence lineage; the scope resolver fails closed without an authenticated local session.
- Authenticated state-changing requests require same-origin and CSRF validation, while auth and business responses use no-store boundaries. Signup, email reset, and invite flows are intentionally absent.
- Windows browser, mobile PWA, real Tailscale device/network, and private-document acceptance remain unverified; synthetic tests do not substitute for those acceptance checks.
- Candidate review keeps provider prose and private paths out of API/UI errors, traps modal focus, never persists server state in Web Storage, and downgrades unpublishable AI candidates to review instead of silently treating them as enrolled coverage.
- Clause search uses a no-store JSON POST, server-derived household/date/edition/insurer/product scope, bounded 1-based physical-page Evidence, and no raw query/full-text logging; v0.1 has no live rebuild endpoint, and app/DB or stale-index mismatches fail explicitly as `SEARCH_INDEX_VERSION_MISMATCH` without silent fallback.
- Rider-Clause and CoverageRule review routes remain server-scoped and no-store. The Web boundary shows bounded Evidence and safe reason-code copy, never raw DSL, provider prose, private paths, or document text; unsupported rule candidates remain informational and cannot be published as executable.
- Event/result API responses are no-store, the PWA service worker caches only hashed app-shell assets, and browser tests verify that medical events, receipt lines, calculations, Evidence, and results never enter Web Storage, IndexedDB, or sensitive runtime caches.
- Claim routes derive policy/insurer and HouseholdScope from server-side Rider resolution, reject extra private/file fields, use no-store responses, and keep checklist/outcome data to bounded metadata. Insurer payloads, document paths, raw text, and persistent browser claim cache are outside the workflow.

릴리스되지 않은 비어 있는 섹션은 다음 변경을 안정적으로 분류하기 위해 유지합니다.
