# FamilyCare 프로젝트 로드맵

- 상태: Phase 0~8 구현·합성 CI 완료, `v0.1.0`·`v0.2.0` 컨테이너 릴리스 완료, 실제 자료 acceptance 일부 미검증
- 기준 설계: `docs/design/v0.1-product.md`
- 목표 릴리스: 다음 버전 미지정
- 실행 위치: 개인 WSL Docker Compose와 Tailscale private access

## Plan rules

1. 각 기능은 독립적으로 검토 가능한 branch, Conventional Commit, PR, CI, merge commit을 가진다.
2. 하나의 거대한 Phase 2~8 PR을 만들지 않는다.
3. 각 PR 직전 root agent가 전체 diff와 최신 검증을 한 번 집중 검토한다.
4. 기능·동작 변경은 실패하는 test를 먼저 작성한다.
5. 공개 CI는 실제 자료, 외부 AI, Google Drive, 실제 secret 없이 실행한다.
6. 실제 자료 acceptance, device 확인, release tag는 코드 검증과 분리해 증거를 기록한다.
7. 이전 단계 결과를 깨뜨리는 변경은 다음 단계에 묵시적으로 포함하지 않는다.

## Phase status

| Phase | Status | Evidence or target |
|---|---|---|
| Phase 0 — Project Foundation | Complete | PR #1, merge `0f632989df891ae944c012bfcce6c838009867a9`, required CI passed. |
| Phase 1 — Synthetic PDF Ingestion | Complete | PR #8~#12 implementation and PR #13 completion record; synthetic ingestion regression remains in CI. |
| Phase 2 — Policy Ledger | Implemented; private acceptance pending | Family, contract, party, Rider, candidate review, Evidence and insurance-document inventory are implemented; external family-by-family comparison remains. |
| Phase 3 — Clause Linking and Search | Complete in synthetic boundary | TermsEdition, Clause, full-text search, Rider links and validated rule candidates are implemented. |
| Phase 4 — Coverage Decision Engine | Complete in synthetic boundary | MedicalEvent, tri-state engine and fixed/indemnity calculation are implemented and regression-tested. |
| Phase 5 — Event and Result PWA | Complete in synthetic boundary | Hybrid input, action-first results and bounded Evidence disclosure are implemented. |
| Phase 6 — Claim Workflow | Complete in synthetic boundary | Checklist, manual submission state and outcome history are implemented. |
| Phase 7 — Local Authentication | Complete in local boundary | Two equal local admins, server-side sessions, CSRF and private HTTPS login flow are implemented. |
| Phase 8 — Private Local Acceptance | Implemented; device/data/recovery checks remain | Encrypted batch import, managed archive, selective OCR, WSL/Tailscale runtime, offline backup-set packaging and count-only archive audit are implemented; actual restore drill, actual documents, Windows/mobile and other-device checks remain unverified. |
| v0.1.0 — Container release | Complete | Release workflow run `32951939190`; Web/API/Worker images and GitHub Release published on 2026-08-26. |
| v0.2.0 — Container release | Complete | Release workflow run `33090324105`; Web/API/Worker images and GitHub Release published on 2026-08-27. |

## Dependency flow

```text
Phase 0 Foundation [complete]
  -> Phase 1 Synthetic PDF Ingestion [complete]
  -> Phase 2 Policy Ledger, candidate review and inventory [implemented]
  -> Phase 3 Clause search, linking and rule validation [implemented]
  -> Phase 4 MedicalEvent, tri-state decision and calculations [implemented]
  -> Phase 5 Hybrid input and action-first PWA [implemented]
  -> Phase 6 Claim checklist and outcome history [implemented]
  -> Phase 7 Two-admin authentication and session boundary [implemented]
  -> Phase 8 Encrypted import, local OCR and WSL/Tailscale runtime [implemented]
  -> v0.1.0 and v0.2.0 GHCR releases [complete]
  -> Root-owned private family comparison and remaining device checks [pending]
```

AI adapters, encrypted archive, and OCR can be developed earlier with wholly synthetic fixtures, but actual private PDF use waits until the authenticated Phase 8 runtime boundary is present.

## Completed foundation

### Phase 0 — Project Foundation

Completed scope:

- repository documents, privacy boundary, branch and commit rules
- React PWA, FastAPI, Analyzer Worker, PostgreSQL skeleton
- OpenAPI and JSON Schema contracts
- Docker Compose and non-root Web/API/Worker images
- PR/main CI, safety checks, Dependabot, GHCR tag workflow

Not claimed by Phase 0: release tag, GHCR publish, Cloud Run, actual device, real/private document verification.

### Phase 1 — Synthetic PDF Ingestion

Completed scope:

- descriptor-safe local PDF intake and content identity
- pdfplumber text/table/coordinate extraction and pypdf validation
- versioned page quality and `OCR_REQUIRED` classification
- eight-table document/extraction/job model
- PostgreSQL lease, heartbeat, retry, cancellation, idempotent reuse
- default-disabled synthetic document analysis API
- synthetic API-to-Worker PostgreSQL E2E

Phase 1 intentionally leaves encrypted asynchronous batch handling, OCR execution, external AI, policy ledger, authentication, and private-data acceptance to the approved v0.1 work.

## Phase 2 — Policy Ledger

### Goal

Turn evidence-bearing policy extraction into a family-scoped ledger of actual contracts and Riders that can be used without mandatory review of every field.

### Scope

- HouseholdSpace and FamilyMember lifecycle
- PolicyContract, PolicyParty, Rider, PolicyStatusSnapshot
- policy document and field-level Evidence
- AI structurer, independent verifier, deterministic schema/Evidence validation
- `AI_VERIFIED`, `NEEDS_REVIEW`, `USER_CONFIRMED`
- user correction as versioned audit, not raw-result overwrite
- soft delete, trash, restore
- ledger Web UI

### Acceptance

- a Terms-only Rider cannot become an actual subscribed Rider
- AI-verified candidates are usable immediately and exceptions are visible
- renewal status without current Evidence remains unconfirmed
- AppUser and FamilyMember lifecycles are independent
- all business records are server-scoped to one HouseholdSpace

## Phase 3 — Clause Linking and Search

### Goal

Structure terms editions and connect only the relevant clauses and executable rules to confirmed Riders.

### Scope

- TermsEdition, Clause hierarchy and appendix/table Evidence
- PostgreSQL Korean full-text search and version/date filters
- RiderClauseLink candidate, verifier, user correction history
- CoverageRule DSL candidate and deterministic validation
- executable publication only for `AI_VERIFIED` or `USER_CONFIRMED`
- unsupported prose remains informational and causes dependent `UNKNOWN`

### Acceptance

- linked Clause points to exact DocumentVersion, page and optional coordinates
- contract date excludes the wrong terms edition by default
- AI verifier cannot invent missing Evidence or facts
- unknown DSL operator, field, unit or conflicting rule cannot publish
- vector search is not a product dependency in v0.1

## Phase 4 — Coverage Decision Engine

### Goal

Evaluate verified contracts and rules against incomplete or detailed medical events with reproducible tri-state results and conditional estimates.

### Scope

- shared pre-visit/post-treatment MedicalEvent model
- AI natural-language fact structuring with user-editable fields
- incident-date Policy/Rider validity filter
- versioned rule evaluation and reason codes
- `MATCH`, `NO_MATCH`, `UNKNOWN`
- fixed-benefit calculation with intermediate values
- manual receipt lines and indemnity category calculation
- partial calculation and missing-information questions
- multiple-indemnity detection without summing independent estimates

### Acceptance

- missing facts create `UNKNOWN`, not an exception or `NO_MATCH`
- a decisive mismatch is required for `NO_MATCH`
- AI does not directly produce tri-state or money
- every amount has inputs, units, rounding, rule version and Evidence
- multiple indemnity policies show shared claimable categories and unknown allocation

## Phase 5 — Event and Result PWA

### Goal

Provide a mobile-first flow from natural-language situation input to actionable, evidence-backed results.

### Scope

- family selection and natural-language first input
- editable structured facts and optional questions
- receipt line editor for post-treatment analysis
- action-first result layout
- groups for claim review, needs more information and decisive mismatch
- conditional estimate, withheld amount and required-document display
- expandable policy/terms page Evidence
- small-screen, keyboard and PWA installability checks
- app-shell-only service worker cache

### Acceptance

- current candidates appear without requiring all optional questions
- next action is visible before detailed legal Evidence
- all displayed results trace to actual Rider and Clause Evidence
- API/PDF/medical data do not remain in persistent browser cache
- partial API or Rider failure does not hide successful results

## Phase 6 — Claim Workflow

### Goal

Track what to prepare, what the user submitted through insurer channels, and what was actually paid without becoming an insurer submission or medical document system.

### Scope

- ClaimCase per MedicalEvent and insurer/policy
- candidate/rule/estimate/Evidence snapshot
- required-document checklist only
- preparing, submitted, supplementation_requested, paid, partially_paid, denied, closed
- manually entered receipt number, dates, claimed amount, paid amount and reason
- state transition audit, soft delete, restore
- ClaimHistory input to first-payment/frequency rules

### Acceptance

- candidate and actual claim outcome remain separate
- no insurer API submission exists
- no diagnosis, receipt or prescription file is stored
- later rule changes do not rewrite a submitted ClaimCase snapshot
- paid/denied history affects later frequency evaluation without becoming a guess

## Phase 7 — Local Authentication

### Goal

Replace the synthetic-only unauthenticated route boundary with exactly two equal local administrators and a shared HouseholdSpace.

### Scope

- TTY/stdin-only admin provisioning command
- Argon2id password hashes
- PostgreSQL opaque sessions
- Secure, HttpOnly, SameSite Strict cookies
- CSRF and same-origin checks
- inactivity 7 days, absolute 30 days
- reauthentication for sensitive account/session actions
- device session list and revoke
- no signup, email reset, invite or role management

### Acceptance

- raw passwords and session tokens are absent from DB, logs and shell arguments
- a client cannot choose another HouseholdSpace
- account deactivation revokes sessions without deleting family data
- Tailscale access does not replace app login
- service-worker and Web Storage do not hold long-lived credentials

## Phase 8 — Private Local Acceptance

### Goal

Import actual user-selected PDFs safely enough for personal use and verify the full local WSL/Tailscale flow without expanding into host-security redesign.

### Scope

- one-FamilyMember document batch
- encrypted PDF password once per batch in process memory
- retry only failed password files
- application-encrypted managed archive with per-document data keys
- local Korean/English OCR only for `OCR_REQUIRED` pages
- OpenAI structurer and verifier using Worker-only `OPENAI_API_KEY`
- WSL Docker Compose with one exposed Web gateway
- API/Worker/PostgreSQL on internal Compose network
- Tailscale private device access and local app login
- actual-data acceptance using only user-specified external paths
- log, temp, browser storage and Git leakage inspection
- authenticated packaging and verification of pre-created DB/archive backup snapshots
- read-only aggregate reconciliation of managed archive references and ciphertext metadata

### Explicit exclusions

- Google Drive API or automatic sync
- Cloud Run or public ingress
- LUKS, BitLocker and WSL swap changes
- preallocated 32/40/128 GB encrypted volume
- insurer submission and medical document storage

### Acceptance

- successful encrypted import does not ask for the original password on ordinary reanalysis
- password, archive key and plaintext intermediates are not persisted in DB/job/log/Git
- archive survives project Compose restart and can be read with the runtime key
- only OCR-required pages are rendered and OCR images are cleaned on all paths
- actual document findings are recorded only as sanitized error categories and new synthetic regressions
- existing projects, containers, ports and WSL configuration are not modified
- unavailable mobile/document formats remain explicitly unverified
- synthetic backup capture/verify/materialize/decrypt round trip passes without copying the master key
- archive audit remains count-only and deletion-free; actual backup acquisition, restore and cleanup require separate approval

## Independently reviewable PR sequence

The detailed file/task plan is written only after the v0.1 design document is reviewed. The approved review units are:

1. `docs/v0-1-product-design`
2. `feat/policy-ledger`
3. `feat/policy-candidate-review`
4. `feat/clause-search`
5. `feat/rider-clause-rules`
6. `feat/coverage-decision-engine`
7. `feat/benefit-calculations`
8. `feat/event-result-pwa`
9. `feat/claim-workflow`
10. `feat/local-authentication`
11. `feat/encrypted-document-import`
12. `feat/selective-ocr`
13. `build/private-local-runtime`
14. `release/v0-1-0`

Branch names contain no `codex/` prefix. Each PR uses one logical Conventional Commit purpose unless a small follow-up fix is needed during review.

## v0.1.0 release record

The gate below completed before tag commit `4fff47b41e22eb95fed42887038640fb75e0388a` was published. Release workflow run `32951939190` passed and published the three version/SHA image pairs and GitHub Release metadata recorded in `docs/release/v0.1.0-verification.md`. This does not close the separately listed actual-data and device boundaries.

1. All Phase 2~8 feature PRs are merged with required CI success.
2. Documentation, repository safety, Web, Python, PostgreSQL, contract, workflow and container checks pass on current main.
3. Web/API/Worker images build one at a time.
4. Docker Compose starts, migrates, restarts and preserves DB/archive state.
5. The synthetic login-to-claim browser E2E passes.
6. Private-data acceptance and unverified formats/devices are recorded honestly.
7. `CHANGELOG.md` has a `0.1.0` release section.
8. `v0.1.0` was then created and pushed.
9. The GHCR workflow published version and commit-SHA tags for all three images.
10. GHCR success is recorded as a container release, not Cloud Run deployment.

## Deferred after v0.1

- Google Drive read-only automatic synchronization
- multi-provider AI and Gemini failover
- direct insurer claim integration
- medical document management
- broader proportional indemnity allocation automation
- multi-household identity and invitations
- public/Cloud Run deployment, live snapshot automation and disaster-recovery drills
- host disk and swap encryption changes
