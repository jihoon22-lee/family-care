# Private policy structuring implementation plan

**Status:** In progress

**Goal:** Connect an authenticated private PDF batch to explicit document classification, durable Evidence, retryable AI candidate structuring, human review, and a family-scoped policy/rider ledger without exposing source paths or document content through HTTP, logs, fixtures, or Git.

## Scope and decisions

- The client explicitly labels every selected source as `policy`, `terms`, or `supporting`; filename heuristics never decide enrollment authority.
- A private batch item stores its document kind and the Worker creates the `documents` row with that value instead of hard-coding `supporting`.
- Successful extraction persists bounded, page-addressable Evidence as `NEEDS_REVIEW`. Native and OCR text remain in their existing provenance tables.
- Only `policy` documents enqueue policy-candidate structuring. Terms-only material cannot establish an enrolled rider.
- Policy candidate work uses its own leased, retryable database job. Import/archive success is not rolled back by a provider outage.
- The structurer may return one policy contract and multiple rider candidates from at most 64 bounded Evidence slices. Each candidate is independently verified and deterministically validated.
- Candidate persistence reserves one policy aggregate ID for the batch and links rider candidates to it before publication. This preserves the link while the contract and riders wait for human review.
- User confirmation atomically promotes only the candidate's referenced Evidence to `USER_CONFIRMED` before publishing the projection.
- A policy imported for one batch `family_member_id` creates a `primary_insured` party projection when the policy is published. The selected member is never inferred from document text.
- Actual family insurance documents are handled only outside the repository. Their analysis, policy/terms classification, evidence selection review, and final ledger verification are performed by the root agent, not delegated.

## Non-goals

- Do not infer that a terms rider is enrolled.
- Do not automatically confirm candidates or make claim eligibility/payment decisions.
- Do not expose extraction text, OCR text, source keys, paths, passwords, policy numbers, or provider payloads through the batch API.
- Do not implement terms-edition clause/rule publication in this task; it follows after policy/rider ledger publication is proven.
- Do not add Redis, Kafka, a search service, or a second Worker service.

## Task 1: Explicit document kind at the batch boundary

- [x] Add RED API/contract/repository/migration tests for a strict per-source request containing `source_id` and `document_kind`.
- [x] Reject duplicate source IDs, unsupported kinds, client paths, and mixed unscoped payloads.
- [x] Add `document_kind` to `document_batch_items` with a forward migration and a database check.
- [x] Carry the kind through API responses and Web selection controls without exposing `source_key`.
- [x] Make the Worker create `documents` with the stored kind.

## Task 2: Atomic Evidence persistence

- [x] Add RED Worker repository tests proving successful private extraction writes Evidence in the same transaction as extraction/archive metadata.
- [x] Derive `household_space_id` through the locked batch row and reject inconsistent document/version/page/bbox relationships.
- [x] Persist one page-addressable Evidence row per extracted page with initial `NEEDS_REVIEW`. Keep the native extraction ID and content hash authoritative; OCR remains a separate provenance layer for the same page.
- [x] Build at most 64 deterministic, 240-character internal Evidence slices from page text, preferring successful OCR text on OCR-required pages. Keep text out of logs and batch responses.

## Task 3: Retryable policy-candidate job

- [x] Add a leased `policy_structuring_jobs` table keyed to one policy `document_version_id` and extraction.
- [x] Enqueue it atomically only for successful `policy` imports.
- [x] Extend the structurer schema to a bounded candidate batch; verify candidates independently and retain only request IDs, validated fields, issues, and bounded Evidence.
- [x] Before provider submission, remove selected-member display values and format-detected policy numbers/contact details that are unnecessary for structuring; never log the source or redacted text.
- [x] Add a Worker-side publisher/job repository that persists the same candidate tables consumed by the API review use cases.
- [x] Wire the job runner and policy schemas into the existing Worker process. Provider configuration/retry errors change only the structuring job, never the completed document batch.

## Task 4: Human confirmation and family ledger projection

- [x] Reserve and reuse one aggregate ID for contract/rider candidates from the same policy document.
- [x] On `USER_CONFIRMED`, promote referenced Evidence in the same household and publish contract/rider projections atomically.
- [x] For a private batch policy, create the selected FamilyMember as `primary_insured` using confirmed Evidence.
- [x] Prove policy, party, riders, review queue, and Evidence API behavior with synthetic PostgreSQL integration tests.

## Task 5: UI and grouped verification

1. [x] Add document-kind controls and status copy to the import page.
2. [x] Keep password handling, no-store behavior, and current login/session behavior unchanged.
3. [x] Run Web checks, Python format/lint/type/tests, contract/container/workflow checks, PostgreSQL integration tests, migration upgrade, repository safety, and `git diff --check` serially.
4. [x] Rebuild API/Worker images, apply migrations, restart only FamilyCare-owned services, and revalidate the existing HTTPS login endpoint without changing credentials.
5. [ ] Continue root-owned actual family-by-family analysis outside Git after synthetic acceptance:
   - [x] Family E review complete from 6 readable PDFs: 3 policies and 94 coverages. All statuses remain `UNKNOWN` pending current/renewal verification.
   - [x] Family F review complete from 8 readable PDFs: 4 certificate-backed policies and 160 coverages. One nonfinal product explanation was excluded from the policy aggregate. All statuses remain `UNKNOWN` pending current/renewal verification.
   - [x] Family C source review complete from 17 PDFs and 1 image: 12 readable and 6 password-required sources, yielding 2 readable certificate-backed policies and 19 coverages. Six encrypted policy candidates remain unpublished. All statuses remain `UNKNOWN` pending current/renewal verification.
   - [x] Family B source review complete from 18 PDFs: 15 readable and 3 password-required sources, yielding 6 readable certificate-backed policies and 92 coverages. Three encrypted policy candidates remain unpublished, and one legacy-font certificate retains an exact-label boundary. All statuses remain `UNKNOWN` pending current/renewal verification.
   - [x] Family D accessible-source review complete from 21 PDFs: 18 readable and 3 password-required sources, yielding 4 certificate-backed policies and 95 coverages. Two additional password-required policy candidates remain unpublished. All statuses remain `UNKNOWN` pending current/renewal verification.
   - [x] Family A accessible-source review complete from 37 PDFs: 34 readable and 3 password-required sources, yielding 13 readable certificate-backed policies and 132 coverages. Three password-required policy candidates remain unpublished. Mixed-role source files and same-member duplicates were reviewed without inflating the aggregate. All statuses remain `UNKNOWN` pending current/renewal verification.
   - [ ] Complete remaining password/font/raw visual boundaries across the family set and verify current/renewal status before changing any `UNKNOWN` result.
   - Keep actual source files and all raw or derived private data outside Git; only sanitized aggregate progress may be recorded here.

## Follow-up after actual review

After the family-by-family review above is complete, implement `docs/plan/018-insurance-document-inventory.md`. It adds the per-member certificate+terms, certificate-only, unpaired-terms, product-explanation, unreadable, conflict, and duplicate views without treating non-policy documents as enrolled insurance.

## Acceptance boundary

This plan is complete only when a synthetic private policy import can move through:

```text
explicit policy kind
  -> batch extraction/archive success
  -> Evidence rows
  -> retryable structurer/verifier job
  -> NEEDS_REVIEW or AI_VERIFIED candidates
  -> user/root confirmation
  -> family-linked policy and rider ledger
```

Actual PDF acceptance remains a separate, explicitly reported runtime result and never becomes repository test data.
