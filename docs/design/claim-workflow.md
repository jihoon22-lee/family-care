# Claim workflow design

- 상태: v0.1 구현 경계 반영, PR·통합 릴리스 검증 대기
- 적용 단계: Phase 6
- 선행 조건: versioned ClaimCandidate and Evidence result

## Scope

사용자가 보험사별 청구 준비, 외부 channel 접수, 보완 요청과 실제 지급 결과를 기록하는 기능을 정의한다. 결과 카드에서 선택한 `rider_id`만으로 ClaimCase 생성을 시작하며, 서버가 HouseholdScope 안에서 해당 Rider의 정책과 보험사 식별자를 파생한다. FamilyCare는 청구를 전송하지 않고 의료 document file을 저장하지 않는다.

## Claim aggregate

하나의 ClaimCase는 다음을 가진다.

- HouseholdScope, MedicalEvent, FamilyMember
- 서버가 `rider_id`로 확인한 insurer, PolicyContract and selected Rider
- creation-time ClaimCandidate/result snapshot
- rule, policy, Evidence and all matching BenefitCalculation snapshot versions
- required-document checklist items
- expected claim categories and conditional estimate
- status and optimistic concurrency version
- user-entered insurer receipt number and dates
- claimed, paid amount/currency and reduction/denial reason
- created/updated timestamps and status audit events

ClaimCase는 analysis result의 live pointer만 저장하지 않는다. 생성 당시 Candidate·Rule·Policy·Evidence와 선택한 후보에 연결된 모든 계산의 정규화된 allowlist snapshot 및 SHA-256을 보존하고, later reanalysis와 차이를 별도 표시한다. 저장된 snapshot은 이후 규칙 재분석이나 원본 행 변경으로 덮어쓰지 않는다. API는 이 snapshot의 bounded ID·version·상태 projection만 반환한다.

stale result는 ClaimCase를 만들 수 없다. 계산은 선택한 exact decision run만 사용하며 생성 도중 PolicyContract, selected Rider, PolicyParty 또는 관련 status snapshot이 바뀌면 전체 생성을 중단한다. 하나의 사건·Rider에는 active ClaimCase 하나만 있고 반복 POST는 기존 active case를 반환한다.

## State machine

```text
preparing
  -> submitted

submitted
  -> supplementation_requested
  -> paid
  -> partially_paid
  -> denied

supplementation_requested
  -> submitted
  -> paid
  -> partially_paid
  -> denied

paid | partially_paid | denied
  -> closed
```

`submitted`는 사용자가 보험사 app, Web, fax 또는 다른 channel로 제출했다고 기록한 상태다. FamilyCare가 외부 전송을 수행했다는 뜻이 아니다. v0.1은 closed ClaimCase를 reopen하지 않고 correction audit 또는 새 ClaimCase를 사용한다.

## Checklist

checklist item은 문서 종류, requirement code, 필수/조건부 여부, 준비 상태, bounded `note_code`와 source rule/Evidence ID를 가진다. file path, binary, image, OCR text, medical text와 external document ID를 갖지 않는다. 현재 workflow는 파일 자체가 아니라 준비 여부와 제한된 메타데이터만 기록한다.

AI explanation은 verified rules에서 checklist wording을 만들 수 있지만 item requirement와 Evidence를 변경하지 않는다.

## Claim history feedback

paid, partially_paid, denied result는 non-null `rider_id`가 있는 ClaimHistory projection을 만든다. decision engine은 최초 1회, 지급 횟수, 과거 지급 필요 rule에서 현재 평가 Rider와 같은 이력만 읽는다.

- paid/partially_paid는 payment date와 `counted_occurrence=true`를 같은 transaction에서 명시한다.
- denied는 `counted_occurrence=false`인 감사 이력으로 남으며 자동으로 future `NO_MATCH` 근거가 되지 않는다.
- missing or conflicting history는 `UNKNOWN`이다.
- 실제 지급액은 future AI provider training/input으로 자동 전송하지 않는다.

## API boundary

- `POST /api/v1/medical-events/{event_id}/claims`
- `GET /api/v1/claims?event_id={event_id}&status={status}&cursor={cursor}&limit={limit}`
- `GET /api/v1/claims/trash`
- `GET /api/v1/claims/{id}`
- `PATCH /api/v1/claims/{id}` with expected version
- `POST /api/v1/claims/{id}/transitions`
- `PATCH /api/v1/claims/{id}/checklist/{item_id}`
- `DELETE /api/v1/claims/{id}`
- `POST /api/v1/claims/{id}/restore`

결과 카드의 생성 요청 body는 `{"rider_id":"..."}` 하나만 받는다. 정책·보험사·가구 범위는 클라이언트가 보내지 않고 서버가 Rider와 HouseholdScope에서 확인한다. 생성은 `preparing` 상태와 immutable snapshot을 함께 만든다. Transition request는 target status, expected version, occurred-at과 `amount`, `currency`, `payment_date`, `reason_code` 중 허용된 metadata만 받는다. 임의 source/target 상태 변경을 직접 update하지 않으며 ClaimCase 응답과 모든 route는 `Cache-Control: no-store` 경계를 사용한다.

`submitted`는 사용자가 보험사 앱·웹·팩스 등 외부 channel에서 접수했다고 기록하는 수동 상태다. FamilyCare가 insurer API, 이메일, 팩스 또는 다른 channel로 제출하지 않는다. checklist도 파일 업로드나 문서 보관을 제공하지 않는다.

## Failure behavior

- invalid transition은 `409 INVALID_CLAIM_TRANSITION`이다.
- stale result 또는 생성 중 정책 lineage 변경은 ClaimCase를 만들지 않는다.
- stale expected version은 `409 VERSION_CONFLICT`다.
- paid amount가 음수거나 currency/date 형식이 잘못되면 validation error다.
- related Evidence가 stale이어도 historical ClaimCase snapshot을 삭제하지 않고 warning을 표시한다.
- soft-deleted ClaimCase는 기본 query에서 제외하고 restore conflict를 검증한다.
- 한 ClaimCase 변경이 같은 MedicalEvent의 다른 insurer ClaimCase를 변경하지 않는다.
- 삭제는 soft delete이며 일반 목록·조회에서 제외하고 trash 조회와 expected-version restore에서만 복구한다.

## Privacy boundary

- insurer receipt number는 제한된 ASCII identifier token이며 자유 메모를 허용하지 않고 일반 log에 남기지 않는다.
- diagnosis, receipt, prescription file과 scan은 저장하지 않는다.
- bounded `note_code`와 reason code는 API body이므로 일반 log에 남기지 않는다.
- claim amount와 reason은 browser persistent cache에 저장하지 않는다.
- insurer 제출 endpoint, 파일 저장소, raw document/provider payload는 이 workflow 경계에 없다.
- status audit에는 from/to status, timestamp, bounded transition reason code를 보존하고 full old/new free text를 복제하지 않는다.

## Tests

- one MedicalEvent with independent insurer/policy ClaimCases
- result/rule/Evidence snapshot immutability, database-level UPDATE/DELETE rejection
- all allowed and denied state transitions
- supplementation_requested resubmission and terminal close
- checklist metadata with no file/path fields
- optimistic concurrency, soft delete, restore
- paid/partially_paid history and same-Rider frequency/first-payment projection
- denied result does not force future NO_MATCH
- response/log/cache absence of medical document and user note content
- synthetic Web flow from result card to checklist, submitted and paid/denied record

현재 검증 경계는 합성 데이터와 코드 수준 테스트다. 실제 보험 문서·개인정보·모바일 PWA 설치·Tailscale 접속·보험사 제출은 이 설계의 완료 증거가 아니다.
