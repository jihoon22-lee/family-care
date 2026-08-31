# Coverage decision engine design

- 상태: operational 및 private knowledge deterministic 실행, v2 결과와 선택적 assistance 구현 완료; 실제 보호 자료 acceptance 대기
- 적용 단계: Phase 6 — Coverage Decision Engine
- 상위 기준: `docs/design/v0.1-product.md`

## Scope

MedicalEvent와 실제 가입 Rider, 계약 상태, 약관 CoverageRule을 결합해 근거가 있는 청구 후보를 생성하는 결정론적 엔진을 정의합니다. AI 설명과 보험사 최종 심사는 이 엔진의 권위 범위 밖입니다.

Phase 5의 선행 boundary는 이 문서가 소비할 입력을 준비합니다. 즉, 검증된 Rider-Clause 연결과 immutable CoverageRule version을 만들고, data-only DSL이 지원되는지 확인합니다. Phase 5 validator는 규칙을 실행하지 않으며 이 문서의 `MATCH`·`NO_MATCH`·`UNKNOWN` evaluation은 별도 구현 범위입니다.

## PR6/PR7 implementation status

PR6는 이 설계의 결정론적 실행 경계를 구현했습니다. `0007_coverage_decision_engine`은 구조화된 MedicalEvent, immutable decision run, RuleEvaluation, Evidence 연결, Rider 후보를 저장하며, 각 평가에는 당시 Evidence의 ID·문서/추출 ID·페이지·좌표·검토 상태·content hash를 담은 `evidence_snapshot_json`도 함께 저장합니다. 결과를 다시 읽을 때 이 snapshot을 우선 사용하므로 이후 Evidence 원본 행이 바뀌어도 이미 성공한 결과의 근거가 조용히 바뀌지 않습니다.

현재 구현에 포함된 범위는 다음과 같습니다.

- 허용된 구조화 fact와 확인 수준을 저장하는 `pre_visit`/`post_treatment` MedicalEvent lifecycle
- household-scoped create/read/update, optimistic version conflict, soft delete, trash listing, restore
- 실제 가입 Rider와 published executable CoverageRule만 읽는 순수 deterministic engine
- 필수 결과의 `NO_MATCH` → `UNKNOWN` → `MATCH` 우선순위 집계, 추가 질문과 근거 Evidence 반환
- immutable run/evaluation/candidate의 transactional PostgreSQL persistence
- strict `coverage-decision.v1` JSON Schema와 no-store HTTP response

PR7은 PR6의 `ClaimCandidate`와 published executable CoverageRule을 입력으로 받아 계산 경계를 추가했습니다. `0008_benefit_calculations`는 `0007_coverage_decision_engine` 뒤에 수동 `ReceiptLine`, 계산 header, immutable step row를 추가하고, direct `psycopg` repository가 server-derived `HouseholdScope`로 조회·저장합니다. 계산은 `Decimal`/통화/반올림 규칙을 사용하며, rule version과 Evidence ID를 결과에 보존합니다. 하나의 유효한 rule/evidence chain을 선택할 수 있는 후보만 계산 projection으로 저장하고, 같은 입력·규칙·engine cutoff의 trace는 재사용하며, 계산에 영향을 주는 변경은 새 version row와 step 집합으로 남깁니다.

ClaimHistory projection은 아직 연결되지 않았으며 현재 repository port는 빈 history를 반환합니다. 따라서 history가 필요한 결정 규칙은 0회로 추정하지 않고 `UNKNOWN`으로 남습니다. 기본 `HouseholdScope` resolver는 Phase 7 인증 전까지 fail-closed이므로, 실제 로그인 없이 운영 route를 사용한다고 해석하면 안 됩니다. PR7의 계산 결과도 지급 확정이나 보험사 지급 보장이 아니라 조건부 청구 검토 자료입니다.

## Private knowledge v2 execution status

`0020_private_publications`부터 `0022_analysis_assistance`까지의 additive 경계는 current private
knowledge snapshot을 기존 운영 원장과 별도 stream으로 평가한다. 증권 가입, current confirmation,
사건일 status interval, 약관 identity/edition/mapping, coverage disposition, rule와 calculation
publication이 모두 닫힌 담보만 실행한다. 약관에 조항이 있다는 사실이나 검색 유사도는 가입 또는
`MATCH` 근거가 아니다.

결과 v2는 source-discriminated candidate/evaluation, exact clause/page citation, catalog completeness,
통화별 조건부 정액 subtotal과 별도 실손 summary를 반환한다. 정액 subtotal은 검토된 eligibility
rule이 일치해 계산된 정액 조건부 예상액을 더한다. 가입금액 전용 위치 검토가 남아 있는 예상액도
hold와 함께 포함하지만 catalog-only 담보와 실손 `UNKNOWN`은 섞지 않는다. 저장 직후 응답과
immutable result 재조회는 금액 문자열과 fact-path ordering을 canonicalize해 동일한 verified
projection을 제공한다.

분석 뒤 member-scoped structured search는 관련 약관 검토 후보를 즉시 만든다. Worker key가 있으면
이미 선택된 bounded token만 한 번 재정렬·설명할 수 있고, key가 없거나 호출이 실패하면 DB 검색을
그대로 유지한다. 이 assistance projection은 candidate, evaluation, calculation, subtotal을 수정할
권위가 없다.

## Inputs

- FamilyMember와 사건일
- 사전 또는 사후 MedicalEvent 필드
- 사고일 기준 PolicyContract·Rider 상태
- 검수된 Rider-Clause 연결
- 버전이 있는 CoverageRule
- 과거 ClaimHistory
- 정액형 가입금액 또는 실손 계산에 필요한 비용 자료
- 사용자가 입력한 통원·입원·약제비 `ReceiptLine`과 확인 수준
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
- 정액형 `BenefitCalculationResult`와 순서가 있는 `CalculationStep` trace
- 실손형의 `computed`/`partial`/`unknown` 상태, confirmed/additional/excluded 금액
- deductible, applied rate/limit, rounding rule과 hold/exclusion reason code
- 복수 실손 Rider의 독립 후보와 비례분담 `UNKNOWN`
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

`calculate_fixed_benefit`는 ClaimCandidate가 `MATCH`이고, 규칙이 published·executable이며 `AI_VERIFIED` 또는 `USER_CONFIRMED`이고 모든 Evidence가 승인된 경우에만 실행합니다. 검증된 data-only DSL의 `add`/`subtract`/`multiply`/`min`/`max`/`round` 연산을 `Decimal`로 평가하고 다음 항목을 모두 기록합니다.

- 계약 통화
- Rider 가입금액
- 규칙의 고정 금액 또는 비율 계산 입력
- 계산에 사용한 확인된 Rider 통화·가입금액 fact
- 중간 계산값
- 반올림 규칙
- 최종 추정값

계산 결과의 각 DSL 연산은 `CalculationStep(step_number, operation, input_amount, output_amount, rounding_rule, reason_code)`로 보존합니다. 결과가 소수 단위인데 명시적 `round` 경계가 없거나, fact/Evidence/rule이 없거나 stale하거나, overflow·지원하지 않는 식이면 금액을 0으로 대체하지 않고 `status="unknown"`과 안정적인 hold reason code를 반환합니다.

정액형 coverage의 가입과 eligibility rule은 검토됐지만 별도 calculation publication만 없는
경우에는 증권의 `insured_amount`를 지급 확정값이 아닌 조건부 예상액으로 사용할 수 있습니다.
이 경로는 `calculation_publication_id=null`, `confirmed_amount=null`,
`operation="certificate_insured_amount"`,
`reason_code="CERTIFICATE_INSURED_AMOUNT_ESTIMATE"`를 기록합니다. 규칙이 없거나 필수 규칙이
일치하지 않은 catalog-only coverage에는 이 fallback을 적용하지 않습니다. 실손형에도 적용하지
않습니다. `certificate_review.amount_decision=MATCH`이고 가입금액 전용
`amount_evidence_locations`가 있을 때만 `certificate_amount_evidence_state=DIRECT`로 표시하고,
해당 문서 별칭과 물리 페이지를 계산 응답에 보존합니다. 담보 존재만 확인하는 일반 증권
페이지밖에 없거나 금액 검토가 `UNKNOWN`이면 예상액 자체는 참고값으로 표시하고
`CERTIFICATE_AMOUNT_EVIDENCE_REVIEW_REQUIRED` hold와 “가입금액 위치 확인 필요” 표기를 유지합니다.
사용자가 요청한 예상 합계에는 이 조건부 정액 예상액도 포함하되 `confirmed_amount`는 만들지
않습니다. 이 근거는 계산이 참조한 immutable knowledge snapshot에서 다시 읽어 약관 eligibility
citation과 함께 한 카드에서 확인합니다.
검토된 calculation publication이 `Rider.insured_amount`를 입력으로 사용하더라도 같은 경계를
적용합니다. 금액 review가 `MATCH + DIRECT`가 아니면 계산식은 참고 예상액을 만들 수 있지만
`confirmed_amount`를 만들 수는 없고 review hold를 유지한 채 조건부 정액 합계에만 포함됩니다.

AI가 구조화한 fact는 규칙 평가가 `MATCH`, `NO_MATCH`, exclusion match 중 어느 결과를
만들더라도 단독으로 확정 판정을 만들지 않습니다. 계약 기간·가입 상태처럼 별도 신뢰 근거가
결정적으로 불일치한 경우에만 coverage `NO_MATCH`가 우선합니다. AI가 제안한 사건 날짜와 방문
날짜는 candidate fact로만 보존하고 authoritative event date column에는 투영하지 않습니다.
사건 날짜가 없더라도 검토된 정액 eligibility rule이 일치하면 `EVENT_DATE_REQUIRED` hold를 붙인
증권 기준 조건부 예상액을 표시합니다.

## Indemnity handling

실손형은 사용자가 수동 입력한 통원·입원·약제비 `ReceiptLine`만 입력으로 받습니다. `covered`이면서 `user` 또는 `ai_structured`로 확인된 항목만 confirmed에 합산하고, `possible_excluded`·`unknown`·미확인 항목은 additional로 보존하며, `excluded` 항목은 excluded와 bounded reason code로 보존합니다. 모든 항목은 하나의 uppercase ISO 통화를 사용해야 하며 통화가 다르면 계산하지 않고 `UNKNOWN`을 반환합니다.

승인된 indemnity 식은 `max(0, confirmed - deductible) × applied_rate`, `applied_limit` 상한, 명시적 반올림 순서로 계산하고 각 중간값을 trace에 남깁니다. additional 금액이 남으면 계산된 confirmed 금액을 숨기지 않고 `status="partial"`과 `ADDITIONAL_RECEIPT_REVIEW_REQUIRED` hold reason을 함께 반환합니다. 영수증·규칙·통화가 없거나 formula shape가 지원되지 않으면 0원으로 추정하지 않고 `UNKNOWN`입니다.

복수 실손 Rider가 발견되면 `detect_multiple_indemnity_contracts`가 후보 ID만 보존하고 `allocation="UNKNOWN"`을 반환합니다. repository도 독립 계약의 금액을 합산하지 않고 각 계산을 `MULTIPLE_INDEMNITY_ALLOCATION_UNKNOWN` hold 상태로 남깁니다. 최종 비례분담은 별도 근거가 확인될 때까지 `UNKNOWN`입니다.

## Receipt and calculation HTTP boundary

PR7은 다음 다섯 개의 household-scoped operation을 `MedicalEvent` router에 추가했습니다.

```text
POST   /api/v1/medical-events/{event_id}/receipt-lines
GET    /api/v1/medical-events/{event_id}/receipt-lines
PATCH  /api/v1/medical-events/{event_id}/receipt-lines/{line_id}
DELETE /api/v1/medical-events/{event_id}/receipt-lines/{line_id}
GET    /api/v1/medical-events/{event_id}/calculations
```

create/update는 category, coverage category, Decimal 문자열 amount, uppercase currency, confirmation level과 bounded `note_code`만 받습니다. list는 재접속한 이벤트 editor가 수정·삭제를 이어가도록 active line의 ID와 version을 반환합니다. update/delete는 `expected_version`을 요구하고 stale write는 value-free `409 VERSION_CONFLICT`로 반환합니다. receipt line 삭제는 soft delete이며 기본 목록·계산 조회에서 제외됩니다. 계산 결과는 `BenefitCalculationsResponse` envelope와 bounded steps/hold/exclusion reason 및 `evidence_ids`를 반환하고, 모든 다섯 route는 `Cache-Control: no-store`를 사용합니다. client는 confirmed amount, applied rate, rule version, household scope, 파일/path/raw note를 authoritative input으로 제출할 수 없습니다.

## HTTP lifecycle implemented in PR6/PR7

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

After a successful decision run, the calculation read route uses the latest
household-scoped run and manual receipt lines. It returns only normalized
Decimal-string amounts and immutable trace metadata; it does not open or
accept receipt documents.

## Evidence contract

각 RuleEvaluation은 다음을 추적합니다.

```text
MedicalEvent fact
  -> normalized fact
  -> CoverageRule version
  -> Clause Evidence
  -> Policy/Rider Evidence
  -> tri-state result and reason code
  -> BenefitCalculation header and immutable CalculationStep rows
```

근거 문서를 열 수 없거나 해시가 바뀌면 이전 결과를 stale로 표시하고 다시 확인합니다.

## Invariants

1. ClaimCandidate는 증권에서 확인된 Rider만 참조합니다.
2. 모든 RuleEvaluation은 정확히 하나의 tri-state와 규칙 버전을 가집니다.
3. `NO_MATCH`에는 확인된 결정적 불일치가 필요합니다.
4. Evidence가 없거나 stale이면 지급 조건을 확정하지 않습니다.
5. AI 설명은 구조화 판정과 근거를 변경하지 않습니다.
6. 정액형과 실손형 금액 경로를 혼합하지 않습니다.
7. ReceiptLine은 수동 구조화 metadata와 bounded reason code만 가지며 문서 binary/text/path를 가지지 않습니다.
8. 계산 trace는 같은 행을 수정하지 않고 새 calculation/version과 step 집합으로 재분석을 보존합니다.
9. 복수 indemnity의 독립 예상액은 합산하지 않고 allocation을 `UNKNOWN`으로 남깁니다.

## Explanation boundary

설명 계층은 구조화 판정만 읽습니다. `MATCH`를 지급 확정으로 바꾸거나 `UNKNOWN` 정보를 추측할 수 없습니다. AI를 사용하면 출력 스키마를 검증하고 허용된 사실·근거 ID만 참조하게 합니다.

문서 구조화 AI와 별도 verifier는 `docs/design/ai-document-analysis.md`에 따라 CoverageRule 후보를 만들 수 있습니다. 두 AI 단계와 deterministic validator를 통과한 `AI_VERIFIED`, 또는 사용자가 Evidence를 확인한 `USER_CONFIRMED` 규칙만 엔진이 읽습니다. AI는 이 엔진의 tri-state나 금액을 직접 반환하지 않습니다.

## Failure behavior

- 규칙 버전을 찾지 못하면 Rider 후보는 `UNKNOWN`입니다.
- 증권과 최신 상태가 충돌하면 둘 중 하나를 임의 선택하지 않습니다.
- 조항 연결이 검수되지 않았으면 조건 판정을 확정하지 않습니다.
- 계산 overflow, 단위 불일치, 음수 비용은 안정적인 validation 오류입니다.
- Decimal wire amount가 아니거나 scale/precision/currency/category/confirmation 범위를 벗어나면 요청을 거부합니다.
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
- `0008_benefit_calculations`의 exact table/FK/check/precision과 reverse downgrade
- Decimal money/receipt validation, fixed trace, partial indemnity, deductible/rate/limit/rounding
- household-scoped receipt CRUD, expected-version conflict, soft delete, calculation response contract
- strict `benefit-calculation.v1` schema/example와 file/path/diagnosis/raw-note privacy boundary
- synthetic PostgreSQL calculation persistence와 immutable step/result reanalysis
- complete synthetic package -> confirmation -> publication -> event -> combined result round trip
- 두 정액 및 네 정액+실손 미확정 시나리오의 담보별 계산, subtotal과 exact citation
- provider 미설정·합성 성공·합성 timeout에서 동일한 verified projection과 DB fallback 유지

## Deferred decisions

CoverageRule DSL validation은 `docs/design/ai-document-analysis.md`의 data-only allowlist를 사용합니다. KCD·수술분류 사전의 확대, ClaimHistory 연결, receipt document intake, 복수 실손 비례분담 자동 계산은 아직 이 경계에 포함하지 않습니다. 확률 점수는 사용자 연구와 보정 데이터 없이 도입하지 않습니다.
