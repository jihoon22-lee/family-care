# Human confirmation and private policy ledger projection

## Overview

Task 4 of the private policy structuring pipeline is complete at the synthetic PostgreSQL boundary. A reviewed candidate can now move through `USER_CONFIRMED` as one transaction: the referenced Evidence is promoted within the household scope, the policy or rider projection is published, and a private batch policy creates its batch-selected FamilyMember as `primary_insured`. Contract and rider candidates from one policy document retain the same reserved policy aggregate ID.

The implementation and insurance-domain review were owned by the root agent. This workthrough records only the synthetic implementation and verification; no actual insurance document, provider, current FamilyCare runtime, authentication, or credential was accessed or changed. The isolated root-owned synthetic PostgreSQL test container was used for integration verification.

## Context

- Private policy candidates are initially held for human review because initial page Evidence is `NEEDS_REVIEW`.
- Publishing a candidate without promoting the Evidence would leave the review state inconsistent with a confirmed ledger projection.
- The selected FamilyMember comes from the authenticated private import batch, not from document text or AI output.
- Contract and rider candidates must remain linked to one policy aggregate while they wait in the review queue.

## Changes Made

### 1. Atomic confirmation and Evidence promotion

File: `apps/api/src/familycare_api/policies/candidate_repository.py`

- `USER_CONFIRMED` promotes only Evidence referenced by the confirmed candidate.
- Household scope and candidate lineage are checked before promotion; unrelated or out-of-scope Evidence cannot be used for the projection.
- Evidence promotion and candidate projection occur in the same database transaction, so a failed projection does not leave a partial confirmation.
- Existing raw extraction and candidate version history remain preserved.

### 2. Family-linked contract and rider projection

File: `apps/api/src/familycare_api/policies/candidate_repository.py`

- The policy aggregate ID reserved by the private structuring job is reused for the contract and its riders.
- A private batch policy creates a `primary_insured` party for the batch-selected FamilyMember and carries the confirmed Evidence and policy period.
- Rider confirmation uses the already reserved aggregate and does not infer enrollment from terms-only material.

### 3. Synthetic PostgreSQL integration coverage

File: `apps/api/tests/test_private_policy_confirmation_integration.py`

- Seeds only synthetic household, member, document, Evidence, batch, job, and candidate records.
- Proves the review queue contains candidates before confirmation and the ledger remains unpublished at that point.
- Confirms the policy and rider, then checks Evidence state, aggregate reuse, family party role/period, and projection linkage.

## Confirmation contract

```text
candidate in NEEDS_REVIEW
  -> USER_CONFIRMED transaction
  -> household-scoped Evidence = USER_CONFIRMED
  -> policy contract / rider projection
  -> private policy primary_insured party
```

The transaction boundary is intentional: Evidence state and ledger state either advance together or remain unchanged. The batch-selected member is authoritative for the party projection; no member identity is extracted from or inferred from source content.

## Test-first evidence

The focused RED test failed once because the referenced Evidence remained `NEEDS_REVIEW` when confirmation was expected to promote it. That failure identified the missing confirmation behavior before implementation.

After the root-owned implementation:

- Focused GREEN confirmation coverage: 2 passed.
- Regression bundle (`apps/api/tests/test_policy_candidate_integration.py` plus the new confirmation integration test): 13 passed.

All test data was synthetic and contained no actual policy text, identifiers, source paths, Drive IDs, or credentials.

## Verification Results

```text
Focused confirmation integration: 2 passed
Policy candidate regression bundle: 13 passed
Ruff format (relevant scope): passed
Ruff check (relevant scope): passed
mypy policy scope: passed (12 files)
```

The full repository verification gate has not been run in this work unit and must not be inferred from the focused results above. Web checks, the complete Python test suite, contract/container/workflow checks, runtime acceptance, provider acceptance, and actual private-document acceptance remain separately pending.

## Privacy and authority boundary

- No actual insurance policy, terms, medical document, extraction text, OCR output, screenshot, or embedding was opened, copied, or stored.
- No Drive file, provider, credential, password, authentication/session state, or current FamilyCare runtime container was accessed or changed.
- The isolated synthetic PostgreSQL test container was queried for this integration proof; it contained only generated fixtures.
- No actual source path, document identifier, policy number, or private value was added to code, fixtures, logs, or this document.
- AI remains a candidate-structuring aid; this confirmation path does not delegate insurance-domain interpretation or claim/payment decisions to AI.

## Next Steps

- Complete the grouped repository verification and runtime acceptance planned in Task 5.
- Keep actual family-document classification, Evidence selection, policy interpretation, and final ledger review root-owned and outside Git.
