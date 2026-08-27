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

1. Add RED Worker repository tests proving successful private extraction writes Evidence in the same transaction as extraction/archive metadata.
2. Derive `household_space_id` through the locked batch row and reject inconsistent document/version/page/bbox relationships.
3. Persist one page-addressable Evidence row per extracted page with initial `NEEDS_REVIEW`. Keep the native extraction ID and content hash authoritative; OCR remains a separate provenance layer for the same page.
4. Build at most 64 deterministic, 240-character internal Evidence slices from page text, preferring successful OCR text on OCR-required pages. Keep text out of logs and batch responses.

## Task 3: Retryable policy-candidate job

1. Add a leased `policy_structuring_jobs` table keyed to one policy `document_version_id` and extraction.
2. Enqueue it atomically only for successful `policy` imports.
3. Extend the structurer schema to a bounded candidate batch; verify candidates independently and retain only request IDs, validated fields, issues, and bounded Evidence.
4. Add a Worker-side publisher/job repository that persists the same candidate tables consumed by the API review use cases.
5. Wire the job runner and policy schemas into the existing Worker process. Provider configuration/retry errors change only the structuring job, never the completed document batch.

## Task 4: Human confirmation and family ledger projection

1. Reserve and reuse one aggregate ID for contract/rider candidates from the same policy document.
2. On `USER_CONFIRMED`, promote referenced Evidence in the same household and publish contract/rider projections atomically.
3. For a private batch policy, create the selected FamilyMember as `primary_insured` using confirmed Evidence.
4. Prove policy, party, riders, review queue, and Evidence API behavior with synthetic PostgreSQL integration tests.

## Task 5: UI and grouped verification

1. Add document-kind controls and status copy to the import page.
2. Keep password handling, no-store behavior, and current login/session behavior unchanged.
3. Run Web checks, Python format/lint/type/tests, contract/container/workflow checks, PostgreSQL integration tests, migration upgrade, repository safety, and `git diff --check` serially.
4. Rebuild API/Worker images, apply migrations, restart only FamilyCare-owned services, and revalidate the existing HTTPS login endpoint without changing credentials.
5. After synthetic acceptance passes, materialize the approved Drive files outside Git and begin root-owned family-by-family analysis.

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
