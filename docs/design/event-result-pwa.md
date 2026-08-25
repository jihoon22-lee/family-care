# Event and result PWA design

- 상태: Phase 5 구현 완료, PR 전체 검증 대기
- 적용 단계: Phase 5
- 선행 조건: MedicalEvent and decision API contracts

## Scope

병원 방문 전의 짧은 자연어 입력과 치료 후의 상세 비용 입력을 하나의 모바일 흐름으로 제공한다. 결과는 승인된 행동 우선 카드 구조를 사용하며 추가 질문을 완료 조건으로 만들지 않는다.

## Navigation

주요 화면:

1. login
2. household dashboard
3. family and policy ledger
4. document batch/status
5. new or existing medical event
6. current analysis result
7. Evidence viewer
8. claim cases
9. device sessions

상단에는 현재 FamilyMember context를 표시하지만 route/query의 member ID만으로 authorization하지 않는다.

## Hybrid event input

첫 화면은 FamilyMember, 대략적 event date와 natural-language situation만으로 제출할 수 있다. AI structuring 결과는 editable chips/fields로 보여준다.

- confirmed-looking value: analysis에 즉시 사용, 수정 가능
- ambiguous value: warning과 optional question
- missing required fact: null, dependent rule `UNKNOWN`
- conflicting user/AI value: user-entered structured value 우선, conflict audit 유지

치료 후에는 visit/admission/pharmacy receipt line table을 추가한다. amount input은 decimal currency control을 사용하고 category/coverage status를 명시한다. receipt image upload는 제공하지 않는다.

## Action-first result layout

화면 순서:

1. FamilyMember와 event summary
2. `지금 할 일` primary action
3. claim-review count와 needs-review count
4. claim-review cards
5. needs-more-information cards
6. decisive mismatch cards
7. calculation detail and Evidence drawer

각 card는 Rider, result group, conditional estimate 또는 hold reason, missing facts, required-document checklist preview를 보여준다. Evidence drawer는 policy/terms document label, physical page, Clause와 bounded excerpt를 보여준다.

`MATCH`를 지급 가능 또는 지급 확정으로 번역하지 않는다. 사용자 문구는 `청구 검토`, `추가 확인 필요`, `조건 불일치`를 사용한다.

## Data and cache behavior

- server state는 in-memory query cache에만 두고 page reload 뒤 API에서 다시 읽는다.
- localStorage, sessionStorage, IndexedDB에 event, result, Evidence를 저장하지 않는다.
- API와 Evidence response는 `Cache-Control: no-store`다.
- service worker는 hashed static app shell만 cache한다.
- logout/session expiry는 query cache와 visible state를 지운다.

## Stale and partial results

result는 MedicalEvent version, policy snapshot, rule set, engine version과 generated time을 표시한다. 새 document/rule/event version이 생기면 기존 result를 stale로 표시하고 사용자가 reanalyze할 수 있게 한다.

한 Rider evaluation failure는 다른 card를 숨기지 않는다. partial result banner는 실패 수와 재시도 action을 제공하지만 source text나 internal exception을 노출하지 않는다.

## Accessibility and responsive behavior

- 320 CSS px부터 핵심 action과 card가 horizontal scroll 없이 보인다.
- semantic heading, button, list, dialog와 live region을 사용한다.
- color alone으로 result state를 구분하지 않는다.
- Evidence drawer와 optional question은 keyboard와 screen reader로 닫고 이동할 수 있다.
- focus는 route/dialog 전환 뒤 예측 가능한 heading 또는 control로 이동한다.

## API boundary

- `POST /api/v1/medical-events`
- `PATCH /api/v1/medical-events/{id}` with expected version
- `POST /api/v1/medical-events/{id}/structure`
- `POST /api/v1/medical-events/{id}/analyze`
- `GET /api/v1/medical-events/{id}/results/{version}`
- `GET /api/v1/evidence/{id}`

Natural-language structuring과 decision analysis는 별도 job/result다. AI structuring 실패 후에도 structured fields를 수동 입력해 deterministic analysis를 실행할 수 있다.

## Failure behavior

- offline/API failure는 unsaved sensitive draft를 persistent storage에 자동 저장하지 않는다.
- session expiry는 login으로 이동하고 재로그인 뒤 server-saved event에서 재개한다.
- Evidence unavailable/hash mismatch는 card를 stale로 표시하고 지급 조건을 확정하지 않는다.
- empty candidate와 analysis failure를 서로 다른 empty/error state로 표시한다.
- invalid amount, negative value, currency mismatch는 field-level validation으로 막는다.

## Tests

- minimal natural-language pre-visit flow without optional answers
- editable AI facts, ambiguous/missing/conflicting facts
- post-treatment receipt line add/edit/remove and decimal validation
- action-first card order and user-facing terminology
- partial Rider failure and stale result
- Evidence drawer exact page/Clause and unavailable Evidence
- 320 px layout, keyboard, focus, role/name and live-region behavior
- login/session expiry and query cache clearing
- no medical/result data in service-worker and Web Storage
- browser E2E from event creation to ClaimCase start
