# Changelog

FamilyCare의 주요 변경사항은 이 파일에 기록합니다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르고, 버전은 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 사용합니다.

## [Unreleased]

### Fixed

- GitHub Release notes now derive their change list from the matching CHANGELOG section, append
  verified workflow and immutable image-digest evidence, and use real Markdown files so escaped
  newline text cannot replace actual line breaks.

## [0.3.2] - 2026-08-31

### Changed

- Structured clause search reuses only reviewed exact normalizer tokens and keeps the list of
  coverages whose automatic rules actually ran, while hiding all-unknown rows from result cards and
  related-clause recommendations.
- Conditional fixed-benefit subtotals include certificate-derived estimates that still carry an
  explicit amount-evidence review hold; those values remain unconfirmed and indemnity candidates
  stay separate.

### Fixed

- Event results no longer flood the additional-review section with every catalog coverage or retain
  stale recommendations whose rules were all unrelated to the event.
- Reviewed fixed-benefit rules now surface every relevant component in the conditional subtotal,
  including the protected two-scenario acceptance baseline used for release verification.

### Security

- Authoritative exact-token normalizers never widen to arbitrary Korean compound prefixes; a
  compound alias must be an explicit reviewed, digest-covered private publication record.

## [0.3.1] - 2026-08-31

### Changed

- Event result requests now make at most one bounded structuring attempt and fall back to deterministic local analysis without repeatedly spending external API balance.
- Result pages list only event-relevant coverages, name the coverages that were evaluated, and keep indemnity guidance separate from fixed-benefit totals.

### Fixed

- Relevant fixed-benefit candidates now show each conditional expected amount and a conditional subtotal from a reviewed formula or certificate insured amount, together with the available certificate evidence state, without presenting eligibility as confirmed.
- AI-structured facts remain non-authoritative, and a late provider response cannot replace the event version already used by local analysis.

## [0.3.0] - 2026-08-31

### Added

- Complete private insurance catalog publication with explicit `PUBLISHED`, `ADVISORY`, and `NOT_APPLICABLE` dispositions, publication-scoped enrollment authority, and cited conditional fixed-benefit calculations.
- Result completeness snapshots expose the full catalog disposition counts while keeping indemnity benefits separate from fixed-benefit amounts.
- Offline private-runtime backup-set tooling packages an already-created PostgreSQL custom dump and quiesced encrypted archive snapshot into an authenticated, path-free manifest, verifies the set, and materializes fresh restore inputs without invoking `pg_dump` or `pg_restore`.
- The Worker now provides `familycare-archive-audit`, a read-only reconciliation command that compares managed-archive database references with ciphertext metadata and emits aggregate finding counts only.

### Changed

- Results now distinguish confirmed eligibility from conditional calculations, and optional external assistance falls back to structured catalog search without a provider call when no provider is configured or safe input minimization cannot be proven.
- PostgreSQL integration tests now require a dedicated `FAMILYCARE_TEST_DATABASE_URL`, an exact destructive-test opt-in, and a connected database name containing a standalone `test` or `ci` marker before collection can proceed.
- Suggested ready document sources without a component ID now require an explicit role and bounded page-range confirmation before a `USER_CONFIRMED` component is created.
- Dependabot keeps `@types/node` on the Node 24 runtime major, uses a short `dev` group name that satisfies commit-subject policy, and applies the compatible `@types/react-dom` patch update.
- Container builders now use Node `24.20.0-alpine` and uv `0.12.7`; Web development uses ESLint `10.9.1` and typescript-eslint `8.68.0` with the regenerated lockfile.
- Container policy checks now validate ordered stages and fully specified tags within the approved Node 24, Python 3.14, and uv 0.12 lines instead of duplicating each patch value, while the nginx runtime remains fixed to its exact approved tag.

### Fixed

- Decision evaluation suppresses explicit enrollment, authority, or contract-state mismatches; aggregates multiple coverage mappings conservatively; and never substitutes a policy insured amount for an uncited calculation formula.
- Policy and Rider publication now choose deterministic field-specific source and status Evidence; an asserted status without exact status Evidence is stored as `unknown` instead of inheriting unrelated Evidence.
- User candidate mutations now record the authenticated request actor and fail closed when its HouseholdSpace does not match the active scope.
- Document-import polling now recovers from transient network and server failures while stopping on client and authentication errors.

### Security

- Immutable database guards now protect publication and calculation history, and every provider-bound recommendation field is minimized before the optional external call boundary.
- Provider-bound policy Evidence redacts all active household member display names and aliases plus labelled identity fields; oversized identity sets fail closed before transmission.
- Backup commands read private paths from environment variables instead of argv, never copy the archive master key, reject repository paths, symlinks, overlapping inputs, and existing destinations, and authenticate artifact hashes with a key-derived HMAC.
- Archive reconciliation uses a read-only repeatable-read database transaction, does not open ciphertext contents, never deletes or quarantines entries, and excludes paths and object keys from output.

## [0.2.0] - 2026-08-27

### Added

- Evidence-bounded policy structuring, candidate confirmation, and family document inventory views for registered, incomplete, unpaired, and review-needed insurance documents.
- Explicit document-kind intake and larger bounded private PDF imports for local encrypted analysis.

### Changed

- Candidate review and document inventory results are scoped to the selected family member, including successful documents that still need registration or classification.

### Fixed

- Recoverable PDF cross-reference deviations and invalid geometry no longer prevent otherwise usable documents from continuing through bounded analysis.

### Security

- Private document analysis remains outside the repository, with minimized Evidence inputs and no document content or personal identifiers in public fixtures and release metadata.

## [0.1.0] - 2026-08-26

### Added

- A private, household-scoped insurance ledger for family members, policies, subscribed riders,
  source Evidence, contract-status snapshots, and soft-delete recovery.
- Evidence-backed policy candidate review, terms and clause search, rider-clause linking, and
  deterministic coverage-rule publication without treating a terms entry as proof of enrollment.
- Structured medical events, tri-state coverage decisions, fixed-benefit and indemnity calculations,
  action-first result cards, and claim-case history with reproducible rule and Evidence snapshots.
- Password-protected PDF intake with bounded isolated parsing, selective Korean and English OCR,
  encrypted managed archives, resumable analysis jobs, and sanitized progress and failure states.
- Two equal local administrators, server-derived household sessions, a private Tailscale-ready
  Compose runtime, and versioned Web, API, and Worker images published independently to GHCR.

### Changed

- Policy, event, result, calculation, and claim APIs derive household scope from the authenticated
  server session instead of trusting a client-selected household identifier.
- Fixed-benefit and indemnity results remain separate; incomplete enrollment, contract status,
  Evidence, receipt, or calculation facts produce an explicit UNKNOWN result instead of an
  unsupported payment promise.
- Optional AI assists bounded structuring and explanation only; schema, Evidence, eligibility, and
  monetary calculations remain subject to deterministic validation and user review.

### Fixed

- Optional structuring failures no longer discard manually entered event facts, and receipt retries
  reuse the existing medical event instead of creating inconsistent versions.
- Evidence disclosure fails safely on content-hash, extraction-state, or page-coordinate mismatch
  rather than showing stale source text.
- Worker readiness, generated PWA formatting, archive cleanup, lease ownership, and transactional
  extraction completion now fail deterministically without exposing private source details.

### Security

- Real insurance and medical documents, extracted text, OCR output, identifiers, private paths, and
  credentials are prohibited from the repository, fixtures, logs, and public release metadata.
- Document passwords and administrator secrets enter through bounded TTY, stdin, or private runtime
  channels and are never stored in job payloads, database rows, command arguments, or logs.
- Parser subprocesses use bounded descriptor-based, no-shell IPC; malformed output, traversal,
  symlinks, oversized input, and credential-bearing requests fail closed.
- Authenticated mutations require same-origin and CSRF validation, and sensitive API and PWA
  responses use no-store boundaries without persistent browser caches.
- The v0.1.0 tag publishes immutable GHCR images; it does not constitute a production deployment or
  verification with real private insurance documents.
