# Policy candidate batch schema and Evidence minimization

## Overview

단일 rider 후보만 만들던 policy AI 경계를 한 policy contract와 여러 enrolled rider 후보를 처리할 수 있는 versioned batch schema로 확장했다. 동시에 provider-bound Evidence에서 runtime FamilyMember 표시값과 형식으로 식별 가능한 불필요한 policy/contact identifier를 제거하고, Evidence text가 객체 repr에 나타나지 않도록 했다.

## Context

- 기존 structurer schema는 한 번에 `StructurerCandidate` 한 건만 반환하여 실제 증권의 여러 rider를 한 import job에서 구조화할 수 없었다.
- `policy_party`는 document text에서 추론할 대상이 아니라 batch에서 사용자가 고른 FamilyMember로 만들어야 한다.
- 약관에 rider 조항이 있다는 사실은 가입 근거가 아니므로 batch instruction과 deterministic validation 모두 증권 Evidence를 요구해야 한다.
- Existing `EvidenceSlice`는 provider에 허용된 240-character text를 담지만 default dataclass repr에도 text가 표시되었다.

## Changes made

### Versioned candidate batch

- `workers/analyzer/src/familycare_worker/ai/schemas.py`
  - `StructurerCandidateBatch` schema version 2를 추가했다.
  - 정확히 하나의 `policy_contract`, 최대 31개 `rider`, 전체 candidate ID uniqueness를 검증한다.
  - candidate field 최대치를 PostgreSQL candidate table과 같은 15개로 정렬했다.
  - OpenAI schema registry에 `policy_candidate_batch_structurer_v2`를 등록했다.
- `workers/analyzer/src/familycare_worker/ai/structurer.py`
  - batch 전용 strict schema call과 instruction을 추가했다.
  - terms presence, policy number/PII, eligibility/payment inference를 명시적으로 금지한다.
- `workers/analyzer/src/familycare_worker/ai/policy_pipeline.py`
  - 한 structurer 결과의 policy/rider를 각각 독립 verifier request로 검증한다.
  - candidate별 status/issues/request IDs를 보존하고 전체 classification은 가장 강한 error/review 상태로 결합한다.
  - 기존 v1 single-candidate pipeline은 공통 verifier helper를 사용하되 계약을 유지한다.

### Provider-bound minimization

- `workers/analyzer/src/familycare_worker/ai/minimizer.py`
  - 최대 64 Evidence와 최대 16 runtime sensitive terms를 받는다.
  - 선택된 FamilyMember 표시값, email, phone, label이 있는 증권/계약 identifier를 fixed marker로 대체한다.
  - blanket number masking을 피하여 ISO date와 가입금액 같은 구조화 입력은 유지한다.
  - identity/page/bbox/document kind를 그대로 보존하고 redaction 뒤 text를 다시 240자로 제한한다.
- `workers/analyzer/src/familycare_worker/ai/provider.py`
  - `EvidenceSlice.text`를 dataclass repr에서 제외했다.

### Tests

- `workers/analyzer/tests/test_policy_candidate_batch.py`: policy+rider independent verification, mixed NEEDS_REVIEW, duplicate candidate rejection, strict bounded schema를 검증한다.
- `workers/analyzer/tests/test_policy_ai_minimization.py`: synthetic member/contact/policy identifier removal, date/amount preservation, 64-slice and sensitive-term bounds, repr privacy를 검증한다.

## Key code examples

```python
class StructurerCandidateBatch(BaseModel):
    schema_version: Literal["2"]
    policy: StructurerCandidate
    riders: tuple[StructurerCandidate, ...] = Field(max_length=31)
```

```text
one structurer request
  -> policy contract -> verifier request
  -> rider A        -> verifier request
  -> rider B        -> verifier request
  -> deterministic status per candidate
```

## Verification results

### Test-first evidence

```text
Minimizer RED: module import failed because ai.minimizer did not exist
Minimizer GREEN: 4 passed in 11.94s
Candidate batch RED: run_policy_batch_pipeline import failed
Candidate batch GREEN: 4 passed in 10.93s
Focused AI regression: 39 passed in 13.12s
```

One attempted focused command named a nonexistent `test_worker_policy_pipeline.py` and exited 4 with no tests; the corrected command used `test_policy_ai_pipeline.py` and passed.

### Static checks

```text
Ruff format: 7 focused files passed after one formatting correction
Ruff lint: 7 focused files passed
mypy: 5 source files passed
Documentation contract: passed (45 files)
Repository safety: passed (520 paths)
git diff --check: passed
```

## Privacy and authority boundary

- 테스트는 처음부터 만든 `Family Member A`, `synthetic-policy-001`, reserved email/phone markers만 사용했다.
- 실제 PDF, 실제 이름, 증권번호, 연락처, Drive ID, password, token, provider call은 사용하지 않았다.
- Evidence text와 matched values를 log/exception/result에 넣지 않는다.
- Minimizer와 batch pipeline은 아직 runtime job에 연결하지 않았으며 실제 provider acceptance를 주장하지 않는다.
- Candidate는 보험 가입·자격·금액 판정이 아니고, AI는 `policy_party`를 생성하지 않는다.

## Next steps

- Successful policy import에 별도 leased `policy_structuring_jobs` row를 enqueue한다.
- Job runner에서 Evidence loader -> runtime-sensitive minimizer -> v2 pipeline을 연결한다.
- Candidate persistence와 job completion을 하나의 transaction으로 처리하고, reserved policy aggregate ID를 모든 rider candidate에 공유한다.
