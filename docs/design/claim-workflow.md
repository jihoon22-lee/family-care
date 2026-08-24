# Claim workflow design

- 상태: v0.1 대화 설계 승인 완료, 문서 검토 대기
- 적용 단계: Phase 6
- 선행 조건: versioned ClaimCandidate and Evidence result

## Scope

사용자가 보험사별 청구 준비, 외부 channel 접수, 보완 요청과 실제 지급 결과를 기록하는 기능을 정의한다. FamilyCare는 청구를 전송하지 않고 의료 document file을 저장하지 않는다.

## Claim aggregate

하나의 ClaimCase는 다음을 가진다.

- HouseholdSpace, MedicalEvent, FamilyMember
- insurer, PolicyContract and selected Riders
- creation-time ClaimCandidate/result snapshot
- rule, engine, policy snapshot and Evidence versions
- required-document checklist items
- expected claim categories and conditional estimate
- status and optimistic concurrency version
- user-entered insurer receipt number and dates
- claimed, paid amount/currency and reduction/denial reason
- created/updated actor and audit events

ClaimCase는 analysis result의 live pointer만 저장하지 않는다. 접수 당시 snapshot을 보존하고 later reanalysis와 차이를 별도 표시한다.

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

checklist item은 문서 종류, 필수/조건부 여부, 준비 상태, 사용자 note와 source rule/Evidence를 가진다. file path, binary, image, OCR text와 external document ID를 갖지 않는다.

AI explanation은 verified rules에서 checklist wording을 만들 수 있지만 item requirement와 Evidence를 변경하지 않는다.

## Claim history feedback

paid, partially_paid, denied result는 ClaimHistory projection을 만든다. decision engine은 최초 1회, 지급 횟수, 과거 지급 필요 rule에서 이 projection을 읽는다.

- paid/partially_paid는 payment date와 counted occurrence를 명시한다.
- denied는 자동으로 future `NO_MATCH` 근거가 되지 않는다.
- missing or conflicting history는 `UNKNOWN`이다.
- 실제 지급액은 future AI provider training/input으로 자동 전송하지 않는다.

## API boundary

- `POST /api/v1/medical-events/{event_id}/claims`
- `GET /api/v1/claims/{id}`
- `PATCH /api/v1/claims/{id}` with expected version
- `POST /api/v1/claims/{id}/transitions`
- `PATCH /api/v1/claims/{id}/checklist/{item_id}`
- `DELETE /api/v1/claims/{id}`
- `POST /api/v1/claims/{id}/restore`

Transition request는 target status, expected version, occurred-at과 허용된 metadata만 받는다. 임의 source/target 상태 변경을 직접 update하지 않는다.

## Failure behavior

- invalid transition은 `409 INVALID_CLAIM_TRANSITION`이다.
- stale expected version은 `409 VERSION_CONFLICT`다.
- paid amount가 음수거나 currency가 policy/result와 충돌하면 validation error다.
- related Evidence가 stale이어도 historical ClaimCase snapshot을 삭제하지 않고 warning을 표시한다.
- soft-deleted ClaimCase는 기본 query에서 제외하고 restore conflict를 검증한다.
- 한 ClaimCase 변경이 같은 MedicalEvent의 다른 insurer ClaimCase를 변경하지 않는다.

## Privacy boundary

- insurer receipt number는 user-visible business metadata이며 일반 log에 남기지 않는다.
- diagnosis, receipt, prescription file과 scan은 저장하지 않는다.
- user note는 API body이므로 log에 남기지 않는다.
- claim amount와 reason은 browser persistent cache에 저장하지 않는다.
- audit에는 changed field names, actor, timestamp, transition reason code를 보존하고 full old/new free text를 복제하지 않는다.

## Tests

- one MedicalEvent with independent insurer/policy ClaimCases
- result/rule/Evidence snapshot immutability
- all allowed and denied state transitions
- supplementation_requested resubmission and terminal close
- checklist metadata with no file/path fields
- optimistic concurrency, soft delete, restore
- paid/partially_paid history and frequency/first-payment projection
- denied result does not force future NO_MATCH
- response/log/cache absence of medical document and user note content
- browser flow from result card to checklist, submitted and paid record
