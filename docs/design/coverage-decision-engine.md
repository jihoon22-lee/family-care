# Coverage decision engine design

- 상태: 후속 구현 기준
- 적용 단계: Coverage Decision Engine

## Scope

MedicalEvent와 실제 가입 Rider, 계약 상태, 약관 CoverageRule을 결합해 근거가 있는 청구 후보를 생성하는 결정론적 엔진을 정의합니다. AI 설명과 보험사 최종 심사는 이 엔진의 권위 범위 밖입니다.

## Inputs

- FamilyMember와 사건일
- 사전 또는 사후 MedicalEvent 필드
- 사고일 기준 PolicyContract·Rider 상태
- 검수된 Rider-Clause 연결
- 버전이 있는 CoverageRule
- 과거 ClaimHistory
- 정액형 가입금액 또는 실손 계산에 필요한 비용 자료

입력 필드에는 값과 확인 수준, 선택적 Evidence를 함께 둡니다.

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

실손형은 영수증, 급여·비급여, 본인부담, 한도, 자기부담, 중복 계약 자료가 있어야 계산합니다. 자료가 없으면 관련 Rider 후보와 필요한 서류를 반환하되 금액을 확정하지 않습니다. 비례보상은 계약별 결과를 단순 합산하지 않습니다.

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

## Failure behavior

- 규칙 버전을 찾지 못하면 Rider 후보는 `UNKNOWN`입니다.
- 증권과 최신 상태가 충돌하면 둘 중 하나를 임의 선택하지 않습니다.
- 조항 연결이 검수되지 않았으면 조건 판정을 확정하지 않습니다.
- 계산 overflow, 단위 불일치, 음수 비용은 안정적인 validation 오류입니다.
- 하나의 Rider 평가 실패가 다른 Rider 결과를 제거하지 않습니다.

## Security considerations

- 엔진 로그에는 MedicalEvent 원문을 남기지 않습니다.
- 판정 재현에는 내부 ID와 규칙 버전을 사용합니다.
- 사용자에게 필요 없는 개인 필드는 엔진 입력에서 제거합니다.
- AI 설명 제공자에게 전체 계약이나 PDF를 전달하지 않습니다.

## Tests

- 각 규칙 연산자의 MATCH/NO_MATCH/UNKNOWN 표 테스트
- 사건일 경계와 대기·감액기간
- 갱신 상태 미확인
- 가입하지 않은 특약 제외
- 최초 1회 지급 이력 미확인과 확인
- 정액형 수식·반올림·통화
- 실손 자료 부족과 중복 계약
- 충돌 근거와 stale Evidence
- 추가 질문 없이 현재 후보 반환
- AI 계층 없이 동일한 판정 재현

## Deferred decisions

초기 지원 CoverageRule DSL, KCD·수술분류 버전 관리, 실손 계산 범위는 대표 합성 사례 설계에서 확정합니다. 확률 점수는 사용자 연구와 보정 데이터 없이 도입하지 않습니다.
