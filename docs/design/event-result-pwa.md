# Event and result PWA design

- 상태: coverage decision v2 결과 UI와 assistance polling 구현 완료; 실제 브라우저 acceptance 대기
- 적용 단계: Phase 5 + private knowledge decision publication
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

사용자가 `결과 확인`을 선택했을 때 구조화 fact가 하나도 없고 provider가 설정되어 있으면,
Web은 사건을 저장한 뒤 structuring job을 최대 한 번만 실행하고 최신 event version으로
analysis를 실행한다. 자동 job은 provider 재시도 없이 끝나며 생성 후 55초의 절대 완료 기한을
가진다. 늦게 claim되거나 기한 뒤 완료된 provider 결과는 사건 version에 반영하지 않으므로 60초
Web polling fallback 결과를 나중에 덮어쓸 수 없다. API가 사건별 자동 시도 여부를 반환하므로
provider를 호출한 뒤 version 충돌로 취소된 job도 시도 완료로 보존하고, 빈 결과나 실패 뒤에도 같은 사건에서
자동 호출을 반복하지 않는다. 이미 구조화 fact가 있거나 이전 자동 시도가 있으면 provider를
다시 호출하지 않는다. Provider가 없거나 structuring이 실패해도 같은 action은 deterministic
structured search를 계속 실행한다. 사용자가 누르는 수동 구조화 action은 다시 실행할 수 있다.

- confirmed-looking value: analysis에 즉시 사용, 수정 가능
- ambiguous value: warning과 optional question
- missing required fact: null, dependent rule `UNKNOWN`
- conflicting user/AI value: user-entered structured value 우선, conflict audit 유지

치료 후에는 visit/admission/pharmacy receipt line table을 추가한다. amount input은 decimal currency control을 사용하고 category/coverage status를 명시한다. receipt image upload는 제공하지 않는다.

## Action-first result layout

화면 순서:

1. FamilyMember, event summary와 분석 범위 completeness
2. `지금 할 일` primary action과 추가 확인 질문
3. 통화별 `조건부 정액 합계`와 unresolved count
4. 정액과 합산하지 않는 별도 실손 상태
5. `MATCH`, `UNKNOWN`, `NO_MATCH`의 모든 담보 card
6. 계산 trace와 증권·약관 page/Clause Evidence
7. 별도 `관련 약관 추천`과 `DB 검색`/`LLM 보조` mode

각 card는 Rider, result group, conditional estimate 또는 hold reason, missing facts, required-document checklist preview를 보여준다. Evidence drawer는 policy/terms document label, physical page, Clause와 bounded excerpt를 보여준다. 증권 가입금액 기반 예상액은 검토된 가입금액 전용 위치가 있으면 “증권 가입금액 직접 근거”로 표시한다. 담보 페이지밖에 없거나 금액 검토가 끝나지 않았으면 예상액은 유지하되 “가입금액 위치 확인 필요”로 분리하고 정액 합계에는 넣지 않는다. 자동 판정 규칙을 실제 실행한 private coverage는 completeness panel에 담보명과 계약명을 나열한다. 같은 계약에 속했다는 이유만으로 검색된 catalog-only coverage는 event card로 표시하지 않는다.

`MATCH`를 지급 가능 또는 지급 확정으로 번역하지 않는다. 사용자 문구는 `청구 검토`, `추가 확인 필요`, `조건 불일치`를 사용한다.

private knowledge candidate는 같은 이름이어도 coverage ID별로 모두 표시하며 claim-start action을
제공하지 않는다. operational candidate만 기존 claim workflow 준비 조건을 만족할 때 action을
유지한다. 가입 catalog가 있으나 publication이 미완료된 경우 빈 결과로 숨기지 않고
`가입 담보는 확인됐지만 실행 규칙 검토가 완료되지 않음`을 표시한다.

## Data and cache behavior

- server state는 in-memory query cache에만 두고 page reload 뒤 API에서 다시 읽는다.
- localStorage, sessionStorage, IndexedDB에 event, result, Evidence를 저장하지 않는다.
- API와 Evidence response는 `Cache-Control: no-store`다.
- service worker는 hashed static app shell만 cache한다.
- logout/session expiry는 query cache와 visible state를 지운다.
- `LLM_PENDING` polling은 같은 immutable result GET만 bounded 횟수로 읽고 새 analyze 또는 provider
  job을 만들지 않는다. 응답에서는 assistance만 교체하며 verified candidate와 합계는 유지한다.

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
- complete/partial/unavailable catalog 표시와 모든 private coverage card 보존
- DB 추천 즉시 표시, LLM 보조 전환과 timeout fallback 시 verified result 불변
