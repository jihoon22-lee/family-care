# Policy Ledger Member-Scoped Review Queue

## Overview

The policy ledger previously loaded one household-wide review queue regardless of
the selected family member. This change adds a server-side member filter for
policy candidates, requests it from the ledger, and makes each queue row show
the selected member and every available review issue.

## Changes Made

- `apps/api/src/familycare_api/policies/candidate_router.py` accepts the optional
  `family_member_id` query parameter while preserving calls for generic rule
  review domains.
- `apps/api/src/familycare_api/policies/candidate_service.py` forwards the
  optional member identifier to the repository.
- `apps/api/src/familycare_api/policies/candidate_repository.py` scopes policy
  candidates through their exact Evidence extraction and succeeded policy
  structuring job, with household and active-member predicates.
- `apps/web/src/api/ledger.ts` and `apps/web/src/features/ledger/useLedger.ts`
  request review items for the selected member.
- `apps/web/src/features/ledger/CandidateReviewQueue.tsx` displays the member
  context, the insurer/product identity when available, all issue codes and
  explanations, and a low-confidence fallback when an item has no issue entries.
- `apps/web/src/features/ledger/LedgerPage.tsx` passes the selected member name
  into the queue. Synthetic Web API fixtures and the OpenAPI contract now cover
  the query parameter.
- API tests include a route forwarding regression and a synthetic PostgreSQL
  integration test covering cross-member isolation. Web tests cover selection,
  request parameters, issue rendering, and confirmed-policy visibility.

## Key implementation

```sql
AND EXISTS (
    SELECT 1
    FROM analysis_candidate_evidence AS candidate_evidence
    JOIN evidence AS linked_evidence
      ON linked_evidence.id = candidate_evidence.evidence_id
    JOIN policy_structuring_jobs AS job
      ON job.document_version_id = candidate_evidence.document_version_id
     AND job.extraction_id = linked_evidence.extraction_id
    WHERE candidate_evidence.candidate_version_id = candidate.id
      AND job.family_member_id = %(family_member_id)s
      AND job.state = 'succeeded'
)
```

The outer query remains household-scoped by the trusted `HouseholdScope`.

## Verification Results

- `TMPDIR=/tmp uv run pytest apps/api/tests/test_policy_candidate_api.py apps/api/tests/test_rider_clause_rules_api.py -q` — 29 passed.
- Focused Web ledger and candidate-review Vitest suite — 18 passed.
- Targeted Ruff format/check, ESLint, and Prettier checks — passed.
- `TMPDIR=/tmp uv run python scripts/generate_web_contract_types.py --check` — passed.
- `git diff --check` — passed.
- Synthetic PostgreSQL integration collection — 3 skipped because
  `FAMILYCARE_DATABASE_URL` is not configured in this environment.

Root review added the exact Evidence-extraction join and the insurance identity
label. The focused ledger regression was observed failing before the label was
implemented and then passed all 9 ledger tests.

All committed tests and documentation use synthetic data only. Private runtime
review data stayed outside the repository and no document content, identifier,
path, or credential was copied into this workthrough.
