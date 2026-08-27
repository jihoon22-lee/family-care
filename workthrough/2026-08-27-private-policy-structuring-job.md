# Retryable private policy structuring job

## Overview

Private `policy` batch import을 extraction/archive 성공과 분리된 leased policy-candidate pipeline에 연결했다. 성공한 policy import만 별도 PostgreSQL job을 만들고, Worker는 제한된 Evidence와 runtime-only FamilyMember terms를 최소화한 뒤 structurer/verifier를 호출한다. 후보와 job 성공은 한 transaction에 저장되지만, 초기 Evidence가 `NEEDS_REVIEW`인 동안 policy ledger projection은 만들지 않는다.

이 문서는 현재 브랜치의 커밋되지 않은 Task 3 구현을 기록한다. 실제 보험자료, 실제 provider, Drive, 인증정보, 운영 runtime은 이 작업에 포함하지 않았다.

## Context

- Task 1에서 batch source마다 `policy`, `terms`, `supporting`을 명시하게 되었고, Task 2에서 성공 extraction마다 page-addressable Evidence를 `NEEDS_REVIEW`로 남기게 되었다.
- 기존 import 성공 경계만으로는 policy Evidence를 비동기 후보 구조화 작업으로 넘길 수 없었다.
- provider timeout이나 rate limit이 발생해도 이미 완료된 document batch와 archive를 rollback하지 않고, policy structuring job만 retryable 또는 permanent failure로 전이해야 했다.
- 약관에 rider 조항이 있다는 사실은 가입 증거가 아니므로, policy document만 job을 만들고 후보는 사람 검수 전 원장에 게시하지 않는다.

## Changes made

### 1. Leased queue and migration

Files:

- `apps/api/migrations/versions/0016_policy_structuring_jobs.py`
- `apps/api/tests/test_policy_structuring_job_migration.py`
- `workers/analyzer/src/familycare_worker/policy_jobs.py`
- `workers/analyzer/tests/test_policy_structuring_jobs.py`

Migration `0016`은 `0015_private_batch_document_kind` 뒤에 `policy_structuring_jobs`를 추가한다. 각 job은 HouseholdSpace, batch item, 선택된 FamilyMember, DocumentVersion, successful Extraction, 예약된 `policy_aggregate_id`를 연결하며, pipeline version, availability, lease/heartbeat, bounded attempts, sanitized error code와 completion metadata만 가진다. source key/path, 문서 text, provider payload는 job row나 Worker record에 없다.

상태는 `queued`, `running`, `succeeded`, `retryable_failed`, `permanently_failed`, `cancelled`로 닫혀 있다. pipeline version non-empty, `0 <= attempts <= max_attempts <= 5`, 허용된 `POLICY_STRUCTURING_*` error code, running lease 일관성, terminal completion 일관성을 database check로 고정했다. batch item, extraction, aggregate 각각 unique이며 queue와 household/document 조회 index를 둔다. `analysis_candidate_versions`에는 nullable `structuring_job_id`와 `source_candidate_id`를 추가하고 둘 다 null이거나 둘 다 non-null이어야 한다. 두 값의 pair unique constraint와 job index도 추가했으며, 기존 manual/API row의 두 null 값은 허용한다.

```sql
WITH candidate AS (
  SELECT job.id
  FROM policy_structuring_jobs AS job
  WHERE job.state IN ('queued', 'retryable_failed')
    AND job.available_at <= clock_timestamp()
    AND job.attempts < job.max_attempts
  ORDER BY job.available_at, job.created_at, job.id
  FOR UPDATE OF job SKIP LOCKED
  LIMIT 1
)
```

Worker queue는 기본 lease 180초, 최대 lease 3600초를 사용한다. 만료 lease를 owner-safe하게 회수한 뒤 due job 하나만 `SKIP LOCKED`로 claim하고 attempts를 증가시킨다. heartbeat와 completion은 live lease의 현재 owner만 수행할 수 있다. authentication, invalid response, no evidence는 즉시 permanent failure이고, provider timeout, rate limit, unavailable은 최대 300초 bounded exponential backoff로 재시도하며 attempts 소진 시 permanent failure가 된다. psycopg 오류는 입력값이나 연결 상세를 포함하지 않는 고정 `POLICY_STRUCTURING_QUEUE_UNAVAILABLE` 오류로 변환한다.

### 2. Atomic policy-only enqueue

Files:

- `workers/analyzer/src/familycare_worker/repository.py`
- `workers/analyzer/tests/test_batch_database.py`

Worker의 성공 persistence transaction은 DocumentVersion, successful Extraction, provenance, archive metadata, page Evidence와 terminal batch state를 함께 기록한다. 그 transaction 안에서 `document_kind == 'policy'`인 item에만 `policy_structuring_jobs`를 enqueue하고, 예약된 pipeline version과 선택된 FamilyMember를 연결한다. `terms`와 `supporting` item은 job을 만들지 않는다. 따라서 import/archive가 성공한 뒤 provider 장애가 나도 batch 성공 상태는 유지되고 structuring job만 회수·재시도된다.

```python
if item["document_kind"] == "policy":
    connection.execute(
        "INSERT INTO policy_structuring_jobs (...) VALUES (...)",
        policy_job_values,
    )
```

### 3. Runtime schema and provider minimization

Files:

- `workers/analyzer/src/familycare_worker/ai/policy_pipeline.py`
- `workers/analyzer/src/familycare_worker/ai/evidence_loader.py`
- `workers/analyzer/src/familycare_worker/ai/provider.py`

앞선 Task 3 단위에서 구현한 structurer batch schema v2와 minimizer를 leased runtime에서 사용하도록 연결했다. schema v2는 정확히 하나의 policy contract와 최대 31개의 rider candidate를 반환한다. 전체 Evidence는 최대 64개 bounded slice이고, candidate field, issue, provider request ID도 각 database/API bound와 정렬한다. 각 candidate는 독립 verifier 호출과 deterministic validation을 거치며 raw provider response는 저장하지 않는다. verifier의 일시 장애나 configuration 오류는 batch 검증을 즉시 중단하고 job retry/permanent-failure 경계로 돌려 incomplete 후보를 성공 처리하지 않는다.

Evidence loader는 household, DocumentVersion, successful Extraction, content hash, policy document와 `NEEDS_REVIEW` page Evidence를 모두 일치시킨다. 선택된 FamilyMember의 display name과 internal alias는 DB에서 필요한 동안에만 읽는다. provider 경계 직전 minimizer가 그 terms와 label이 붙은 policy/contract identifier, email, phone 형식을 `[REDACTED]`로 바꾸고, 구조화에 필요한 날짜·금액은 blanket digit masking으로 제거하지 않는다. Evidence text는 provider payload와 candidate Evidence에 필요한 bounded 값으로만 존재하며 로그와 job projection에는 없다.

Provider adapter는 structured output을 `store=False`로 호출하고 output token과 120초 timeout을 제한한다. timeout, rate limit, connection/server unavailable도 서로 다른 고정 error code로 보존한다. `EvidenceSlice`의 repr도 text를 포함하지 않는다.

### 4. Per-call lease heartbeat and runtime wiring

Files:

- `workers/analyzer/src/familycare_worker/runner.py`
- `workers/analyzer/src/familycare_worker/__main__.py`
- `workers/analyzer/tests/test_policy_structuring_runner.py`

`PolicyStructuringJobRunner`는 job을 claim한 뒤 Evidence와 member terms를 읽고 최소화된 Evidence로 batch pipeline을 실행한다. `_LeasedPolicyProvider`는 structurer 또는 verifier provider call 직전에 heartbeat를 수행하므로 lease가 사라지면 다음 provider call을 시작하지 않는다. pipeline 뒤에도 한 번 더 heartbeat한 뒤 publisher에 넘긴다. provider/configuration/validation 오류는 고정된 policy structuring error code로만 job에 기록된다.

Private runtime이 구성되면 `__main__`이 기존 event/document runner와 함께 policy queue, Evidence loader, candidate publisher를 `FairJobRunner`에 연결한다. batch/archive 성공과 policy provider 결과는 별도 lifecycle로 유지된다.

### 5. Atomic candidate and job persistence

File:

- `workers/analyzer/src/familycare_worker/policy_candidates.py`

`PolicyCandidatePublisher`는 live owner job과 batch/member/document/extraction lineage를 잠근다. policy document와 successful Extraction에 속한 bounded Evidence만 확인한 후 candidate version, field, candidate Evidence를 삽입하고 job을 `succeeded`로 전이한다. 이 모든 작업은 하나의 transaction이다. contract와 rider candidates는 job이 예약한 같은 `policy_aggregate_id`와 `(structuring_job_id, source_candidate_id)` pair를 공유한다.

초기 page Evidence가 `NEEDS_REVIEW`이면 provider가 `AI_VERIFIED`라고 반환해도 candidate status를 `NEEDS_REVIEW`로 낮춘다. publisher는 policy contract, party, rider ledger row를 만들지 않으며, 사용자 확인 단계가 해당 Evidence를 확인한 뒤에만 다음 projection이 가능하다. commit 결과가 불명확한 경우 실패로 덮어쓰지 않고 lease recovery가 재처리하도록 둔다.

```text
policy import success
  -> same transaction: Evidence + archive + policy_structuring_job
  -> claim live lease
  -> scoped Evidence + member terms
  -> minimize, heartbeat, structurer
  -> heartbeat, per-candidate verifier
  -> atomic candidate rows + job succeeded
  -> NEEDS_REVIEW until human confirmation
```

## Test-first evidence

Task 3의 최소 RED는 다음과 같이 확인했다.

- migration contract는 `0016_policy_structuring_jobs.py`가 없을 때 5개가 missing migration으로 실패했다.
- Worker queue contract는 `familycare_worker.policy_jobs`가 없을 때 collection 단계에서 `ModuleNotFoundError`로 실패했다.
- 성공 batch integration은 policy structuring job이 0개인 상태에서 1개를 기대해 실패했다.
- candidate publisher와 job runner는 module/class 부재로 collection에 실패한 뒤 GREEN으로 전환했다.
- private runtime wiring은 policy runner가 0개여서 실패했고, provider timeout bound는 request에 `timeout`이 없어 실패했다.
- verifier retry test는 incomplete candidates 2개가 남아 실패한 뒤, 첫 transient failure에서 batch를 중단하도록 수정해 GREEN으로 전환했다.

모든 fixture는 처음부터 만든 synthetic UUID, member, policy label과 Evidence만 사용했으며 실제 문서 본문이나 식별자를 복사하지 않았다.

## Verification results

### Focused and integration verification

```text
Unit: 82 passed, 3 deselected
PostgreSQL integration: 4 passed, 19 deselected
PostgreSQL 18 migration head: upgrade and downgrade/upgrade round-trip passed
```

### Static and repository checks

```text
Ruff format: passed
Ruff lint: passed
mypy: passed
Documentation contract: passed
Repository safety: passed
git diff --check: passed
```

The full repository gate remains pending. The migration/runtime acceptance above is synthetic and does not represent actual provider, Drive, private PDF, or production runtime acceptance.

## Privacy and authority boundary

- 실제 보험증권·약관·의료문서, 추출 text/OCR, Drive ID, password, token, provider response는 저장소·fixture·로그·이 문서에 넣지 않았다.
- 실제 provider/Drive를 호출하지 않았고, auth/session code와 credential을 변경하지 않았다.
- 실행 중인 운영 container, archive, import root, database runtime 상태는 변경하지 않았다.
- AI 결과는 후보 구조화와 검수 보조일 뿐이며 가입 여부, `MATCH`/`NO_MATCH`, 지급액 또는 claim eligibility를 확정하지 않는다.

## Next steps

- Full serial repository gate와 root-owned final review를 수행한다.
- Human confirmation 이후 Evidence promotion과 family-scoped policy/rider ledger projection을 별도 Task 4에서 검증한다.
- 실제 provider, Drive, private PDF acceptance는 명시적 승인과 저장소 밖 runtime 경계에서만 수행한다.
