# Coverage decision engine design

- 상태: v0.1 대화 설계 승인 완료, Phase 5 rule publication boundary 반영; deterministic execution 문서
- 적용 단계: Phase 6 — Coverage Decision Engine
- 상위 기준: `docs/design/v0.1-product.md`

## Scope

MedicalEvent와 실제 가입 Rider, 계약 상태, 약관 CoverageRule을 결합해 근거가 있는 청구 후보를 생성하는 결정론적 엔진을 정의합니다. AI 설명과 보험사 최종 심사는 이 엔진의 권위 범위 밖입니다.

Phase 5의 선행 boundary는 이 문서가 소비할 입력을 준비합니다. 즉, 검증된 Rider-Clause 연결과 immutable CoverageRule version을 만들고, data-only DSL이 지원되는지 확인합니다. Phase 5 validator는 규칙을 실행하지 않으며 이 문서의 `MATCH`·`NO_MATCH`·`UNKNOWN` evaluation은 별도 구현 범위입니다.

## PR6 implementation status

PR6는 이 설계의 결정론적 실행 경계를 구현했습니다. `0007_coverage_decision_engine`은 구조화된 MedicalEvent, immutable decision run, RuleEvaluation, Evidence 연결, Rider 후보를 저장하며, 각 평가에는 당시 Evidence의 ID·문서/추출 ID·페이지·좌표·검토 상태·content hash를 담은 `evidence_snapshot_json`도 함께 저장합니다. 결과를 다시 읽을 때 이 snapshot을 우선 사용하므로 이후 Evidence 원본 행이 바뀌어도 이미 성공한 결과의 근거가 조용히 바뀌지 않습니다.

현재 구현에 포함된 범위는 다음과 같습니다.

- 허용된 구조화 fact와 확인 수준을 저장하는 `pre_visit`/`post_treatment` MedicalEvent lifecycle
- household-scoped create/read/update, optimistic version conflict, soft delete, trash listing, restore
- 실제 가입 Rider와 published executable CoverageRule만 읽는 순수 deterministic engine
- 필수 결과의 `NO_MATCH` → `UNKNOWN` → `MATCH` 우선순위 집계, 추가 질문과 근거 Evidence 반환
- immutable run/evaluation/candidate의 transactional PostgreSQL persistence
- strict `coverage-decision.v1` JSON Schema와 no-store HTTP response

ClaimHistory projection은 아직 연결되지 않았으며 현재 repository port는 빈 history를 반환합니다. 따라서 history가 필요한 규칙은 0회로 추정하지 않고 `UNKNOWN`으로 남습니다. PR6 결과에는 금액·지급 보장 문구가 없고 정액형/실손형 계산은 다음 benefit-calculation 단계의 handoff로만 남아 있습니다. 또한 기본 `HouseholdScope` resolver는 Phase 7 인증 전까지 fail-closed이므로, 실제 로그인 없이 운영 route를 사용한다고 해석하면 안 됩니다.

## Inputs

- FamilyMember와 사건일
- 사전 또는 사후 MedicalEvent 필드
- 사고일 기준 PolicyContract·Rider 상태
- 검수된 Rider-Clause 연결
- 버전이 있는 CoverageRule
- 과거 ClaimHistory
- 정액형 가입금액 또는 실손 계산에 필요한 비용 자료
- `AI_VERIFIED` 또는 `USER_CONFIRMED` 상태의 실행 가능한 CoverageRule

CoverageRule은 저장된 `coverage_rule_versions`의 published executable version만 사용합니다. review queue에서 아직 검토 중인 candidate, unsupported DSL, stale/missing Evidence는 입력으로 사용하지 않고 관련 결과를 `UNKNOWN`으로 남깁니다.

입력 필드에는 값과 확인 수준, 선택적 Evidence를 함께 둡니다. 자연어 사건을 AI가 구조화할 수 있지만 사용자가 수정할 수 있는 사실 레코드로 저장한 뒤에만 이 엔진의 입력이 됩니다.

## Outputs

Rider별 ClaimCandidate:

- 후보 상태와 담보 유형
- 개별 RuleEvaluation 목록
- 현재 충족 사실
- 부족하거나 충돌한 정보
- 선택형 추가 질문
- 정액형 계산 내역 또는 실손형 계산 보류 이유
- 증권과 약관 Evidence
- 판정 엔진·규칙 버전
- 보험금 지급을 보장하지 않는 설명

## Tri-state semantics

### MATCH

평가에 필요한 사실과 계약 근거가 있고 규칙 조건을 충족합니다. 다른 필수 규칙이 `UNKNOWN`이면 Rider 전체 지급 가능성을 확정하지 않습니다.

### NO_MATCH

확인된 사실이 규칙과 결정적으로 불일치합니다. 단순 정보 부족, 최신 계약 상태 부재, 검색 실패에는 사용하지 않습니다.

### UNKNOWN

필수 입력, 계약 상태, 조항 연결, 과거 지급 이력, 계산 자료가 없거나 서로 충돌합니다. `UNKNOWN`은 정상적인 결과이며 추가 확인 대상을 구체적으로 제공합니다.

## Evaluation order

1. FamilyMember가 계약의 피보험자인지 확인합니다.
2. 사건일이 보험기간 안인지 확인합니다.
3. Rider 실제 가입과 사건일 상태를 확인합니다.
4. 질병·상해 등 상위 분류를 확인합니다.
5. 보장개시, 대기·감액기간을 평가합니다.
6. 지급사유 정의와 별표 분류를 평가합니다.
7. 면책과 횟수·최초 1회 제한을 평가합니다.
8. 정액형 계산 또는 실손형 필요자료를 평가합니다.
9. RuleEvaluation을 손실 없이 ClaimCandidate로 집계합니다.

가입하지 않은 Rider는 조항 검색에 나타나도 1차 필터에서 제외합니다.

## Rule boundary consumed by this engine

Rule publication 단계는 다음 불변식을 보장해야 합니다.

- 실제 가입이 검증된 Rider와 계약일에 적용되는 TermsEdition의 Clause만 연결됩니다.
- DSL은 허용된 JSON operator, field path, unit, calculation과 Evidence reference만 포함합니다.
- version read의 `expected_version`과 publish 대상 `version_id`가 일치할 때만 게시됩니다.
- `AI_VERIFIED` 또는 `USER_CONFIRMED`이고 exact Clause/Policy Evidence가 있는 immutable version만 실행 가능합니다.
- typed correction은 child version을 만들며 이전 후보와 원문 Evidence를 덮어쓰지 않습니다.

이 불변식이 깨졌거나 이후 Evidence가 stale이면 engine은 규칙을 평가하지 않고 `UNKNOWN` 경로를 선택합니다. Rule validator가 반환한 typed structure를 engine이 직접 신뢰해 실행하는 것이 아니라, 저장·게시 상태와 Evidence lineage를 다시 확인해야 합니다.

## Aggregation

- 결정적인 필수 `NO_MATCH`가 있으면 제외 가능성이 높은 후보로 표시합니다.
- 필수 규칙이 모두 `MATCH`이면 청구 검토 우선 후보로 표시합니다.
- 필수 규칙 중 하나라도 `UNKNOWN`이고 결정적인 `NO_MATCH`가 없으면 추가 확인 후보입니다.
- 선택 규칙과 설명 규칙은 필수 규칙의 결과를 덮어쓰지 않습니다.
- 단일 확률 점수로 규칙 결과를 대체하지 않습니다.

## Pre-visit and post-treatment modes

사전 모드는 증상·예정 진료·예상 치료처럼 불완전한 입력을 허용하고 넓은 Rider 후보와 준비 질문을 반환합니다. 사후 모드는 진단코드, 수술명·분류, 입퇴원일, 영수증 등 상세 입력으로 같은 사건 레코드를 보강합니다.

사용자는 추가 질문에 답하지 않고 현재 결과를 볼 수 있습니다. 질문은 결과 정확도를 높이는 선택 단계이며 어떤 RuleEvaluation이 바뀔 수 있는지 설명합니다.

## Fixed-benefit calculation

정액형 계산은 다음 항목을 모두 기록합니다.

- 계약 통화
- Rider 가입금액
- 규칙 지급 비율 또는 고정 금액
- 감액 비율
- 횟수·일수
- 중간 계산값
- 반올림 규칙
- 최종 추정값

입력 단위가 불명확하거나 규칙 수식이 검수되지 않았으면 금액을 계산하지 않고 `UNKNOWN`을 반환합니다.

## Indemnity handling

실손형은 사용자가 수동 입력한 통원·입원·약제비 영수증 항목, 급여 본인부담금, 비급여, 실제 지출액, 한도, 자기부담, 중복 계약 자료로 계산합니다. 일부 자료만 있어도 확인된 청구 검토 금액, 추가 확인 금액, 제외 금액과 이유를 분리해 반환합니다. 자료가 없으면 관련 Rider 후보와 필요한 서류를 반환하되 금액을 확정하지 않습니다.

복수 실손 Rider가 발견되면 계약별 독립 예상액을 더하지 않습니다. 공통 청구 검토 항목과 각 계약의 조건을 보여주고 최종 비례분담은 `UNKNOWN`입니다.

## HTTP lifecycle implemented in PR6

The service exposes the following versioned operations under
`/api/v1/medical-events`:

```text
POST   /api/v1/medical-events
GET    /api/v1/medical-events/{id}
PATCH  /api/v1/medical-events/{id}
DELETE /api/v1/medical-events/{id}
GET    /api/v1/medical-events/trash
POST   /api/v1/medical-events/{id}/restore
POST   /api/v1/medical-events/{id}/analyze
GET    /api/v1/medical-events/{id}/results/{version}
```

Create and patch accept only a member ID, mode, dates, and a bounded map of
structured `FactInput` values with `user`, `ai_structured`, `unconfirmed`, or
`conflicting` confirmation. The client cannot submit a tri-state, candidate,
amount, household scope, or Evidence ID. Updates, deletes, and restores require
the current `expected_version`; a stale version is a value-free conflict.

Analyze runs the deterministic engine in a repeatable-read transaction and
persists a new run, evaluations, Evidence joins/snapshots, and candidates
atomically. A missing fact or unavailable current Evidence is a normal result
with `UNKNOWN`, not an HTTP failure. A result is selected by MedicalEvent
version, and all decision responses carry `Cache-Control: no-store`.

## Evidence contract

각 RuleEvaluation은 다음을 추적합니다.

```text
MedicalEvent fact
  -> normalized fact
  -> CoverageRule version
  -> Clause Evidence
  -> Policy/Rider Evidence
  -> tri-state result and reason code
```

근거 문서를 열 수 없거나 해시가 바뀌면 이전 결과를 stale로 표시하고 다시 확인합니다.

## Invariants

1. ClaimCandidate는 증권에서 확인된 Rider만 참조합니다.
2. 모든 RuleEvaluation은 정확히 하나의 tri-state와 규칙 버전을 가집니다.
3. `NO_MATCH`에는 확인된 결정적 불일치가 필요합니다.
4. Evidence가 없거나 stale이면 지급 조건을 확정하지 않습니다.
5. AI 설명은 구조화 판정과 근거를 변경하지 않습니다.
6. 정액형과 실손형 금액 경로를 혼합하지 않습니다.

## Explanation boundary

설명 계층은 구조화 판정만 읽습니다. `MATCH`를 지급 확정으로 바꾸거나 `UNKNOWN` 정보를 추측할 수 없습니다. AI를 사용하면 출력 스키마를 검증하고 허용된 사실·근거 ID만 참조하게 합니다.

문서 구조화 AI와 별도 verifier는 `docs/design/ai-document-analysis.md`에 따라 CoverageRule 후보를 만들 수 있습니다. 두 AI 단계와 deterministic validator를 통과한 `AI_VERIFIED`, 또는 사용자가 Evidence를 확인한 `USER_CONFIRMED` 규칙만 엔진이 읽습니다. AI는 이 엔진의 tri-state나 금액을 직접 반환하지 않습니다.

## Failure behavior

- 규칙 버전을 찾지 못하면 Rider 후보는 `UNKNOWN`입니다.
- 증권과 최신 상태가 충돌하면 둘 중 하나를 임의 선택하지 않습니다.
- 조항 연결이 검수되지 않았으면 조건 판정을 확정하지 않습니다.
- 계산 overflow, 단위 불일치, 음수 비용은 안정적인 validation 오류입니다.
- 하나의 Rider 평가 실패가 다른 Rider 결과를 제거하지 않습니다.
- AI verifier 실패, 지원하지 않는 DSL, Evidence 상충은 종속 규칙을 `UNKNOWN`으로 남깁니다.
- 계산 예외를 0원 또는 `NO_MATCH`로 변환하지 않습니다.

## Security considerations

- 엔진 로그에는 MedicalEvent 원문을 남기지 않습니다.
- 판정 재현에는 내부 ID와 규칙 버전을 사용합니다.
- 사용자에게 필요 없는 개인 필드는 엔진 입력에서 제거합니다.
- AI 제공자에게 PDF binary, page image, password, archive key, 실제 path를 전달하지 않습니다.
- 문서 구조화는 필요한 page text batch와 Evidence token만 Worker에서 전송합니다.
- `OPENAI_API_KEY`는 Worker runtime에만 주입하고 DB·job·log에 저장하지 않습니다.

## Tests

- 각 규칙 연산자의 MATCH/NO_MATCH/UNKNOWN 표 테스트
- 사건일 경계와 대기·감액기간
- 갱신 상태 미확인
- 가입하지 않은 특약 제외
- 최초 1회 지급 이력 미확인과 확인
- 정액형 수식·반올림·통화
- 실손 자료 부족과 중복 계약
- 실손 영수증 일부 항목만 확인된 partial calculation
- 복수 실손 계약의 독립 예상액 비합산과 비례분담 `UNKNOWN`
- 충돌 근거와 stale Evidence
- 추가 질문 없이 현재 후보 반환
- AI 계층 없이 동일한 판정 재현
- `NEEDS_REVIEW` 규칙의 실행 거부와 verifier 실패의 `UNKNOWN`
- Phase 5 boundary의 stored-version selection, expected-version conflict, unsupported DSL non-execution

## Deferred decisions

CoverageRule DSL validation은 `docs/design/ai-document-analysis.md`의 data-only allowlist를 사용하며 Phase 5에서 실행 없이 경계만 구현합니다. KCD·수술분류 사전의 확대와 복수 실손 비례분담 자동 계산은 v0.1 이후로 미룹니다. 확률 점수는 사용자 연구와 보정 데이터 없이 도입하지 않습니다.
