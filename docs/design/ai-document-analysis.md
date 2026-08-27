# AI document analysis design

- 상태: v0.1 대화 설계 승인 완료, 문서 검토 대기
- 적용 단계: Policy Ledger, Clause Linking, Coverage Rule, Event Structuring
- 권위 경계: AI는 후보를 구조화·검증하지만 보험 자격과 금액을 직접 판정하지 않음

## Scope

이 문서는 PDF 추출 결과를 보험 원장, 약관 조항, 실행 가능한 규칙 후보로 바꾸고 자연어 의료사건을 구조화하는 AI 경계를 정의한다. v0.1은 기존 WSL의 `OPENAI_API_KEY`를 Worker에만 주입하며 Gemini를 사용하지 않는다.

## Components

### Structurer

Evidence가 있는 추출 block·table batch를 strict JSON 후보로 변환한다. 요청된 필드만 반환하며 없는 값을 추측하지 않는다.

### Verifier

Structurer와 분리된 요청으로 후보가 제공된 Evidence에 의해 지지되는지 검사한다. 승인, 거부, Evidence 추가 요청만 할 수 있으며 후보에 없던 사실을 만들 수 없다.

### Deterministic validator

다음을 프로그램으로 검사한다.

- JSON Schema와 enum
- 모든 필수 Evidence ID의 존재와 document version 일치
- 페이지·좌표 범위
- 날짜, 기간, 통화, 금액, 비율, 횟수 단위
- 허용 DSL 연산자와 field path
- 같은 후보 안의 모순
- 계약일·약관 판본·Rider scope
- Evidence가 실제 전송 batch에 있었는지 여부

### Publisher

검증 결과를 versioned candidate로 저장하고 실행 가능 여부를 전환한다. 기존 현재 버전은 새 버전이 완전히 검증되기 전까지 유지한다.

## Candidate lifecycle

```text
generated
  -> AI_VERIFIED
  -> NEEDS_REVIEW
  -> rejected

AI_VERIFIED | NEEDS_REVIEW
  -> USER_CONFIRMED
  -> user_corrected new version
```

- `AI_VERIFIED`: Structurer, Verifier, deterministic validator를 모두 통과했다.
- `NEEDS_REVIEW`: 근거 부족, 상충, 낮은 구조 품질, 지원하지 않는 규칙이 있다.
- `USER_CONFIRMED`: 사용자가 Evidence를 보고 승인하거나 수정했다.
- 실행 엔진은 `AI_VERIFIED`와 `USER_CONFIRMED`만 읽는다.

## Prompt and response governance

- prompt template, response schema, provider adapter, model configuration을 각각 versioning한다.
- raw provider response는 권위 데이터가 아니며 validated projection과 provider request ID만 업무 이력에 사용한다.
- 로그에는 prompt, response, 문서 text, 자연어 사건 입력을 기록하지 않는다.
- 재현 테스트는 실제 provider 응답이 아니라 처음부터 만든 합성 응답 fixture를 사용한다.
- 모델명이 바뀌어도 schema와 domain 계약을 바꾸지 않도록 model은 runtime configuration으로 둔다.

## OpenAI request boundary

허용 입력:

- 필요한 페이지 block의 text와 bounding box
- 합성되지 않은 로컬 DB 내부 Evidence token
- 문서 종류와 페이지 번호
- 이미 검증된 계약·Rider의 최소 context
- 의료사건 구조화에 필요한 사용자의 해당 요청 text

금지 입력:

- PDF binary 또는 page image
- PDF password와 archive key
- 실제 로컬 경로와 Google Drive ID
- 인증 token, cookie, 계정 password
- 구조화에 불필요한 이름, 연락처, 주소, 증권번호
- 전체 데이터베이스 또는 관련 없는 계약·사건

요청 batch는 문서 전체를 관성적으로 보내지 않고 목표 필드 또는 Clause에 필요한 page 범위로 제한한다.

Private policy ingestion의 후보 입력 loader는 household, DocumentVersion, successful Extraction, content hash가 모두 일치하는 bbox-free page Evidence만 사용한다. 물리 페이지 순서가 모호하거나 중복되면 요청을 만들지 않는다. 각 페이지 text는 공백을 정규화한 뒤 240자로 제한하고 전체 입력은 64개 slice를 넘지 않는다. Native 품질이 `OCR_REQUIRED`인 페이지에만 성공한 OCR layer를 우선하며, OCR text가 없으면 native layer로 제한적으로 fallback한다. 이 loader는 DB·메모리 내부 경계일 뿐이며 provider 연결 전에는 선택된 가족 표시값과 형식으로 식별 가능한 불필요한 증권번호·연락처를 추가로 제거해야 한다.

## Coverage rule DSL

DSL은 data-only JSON이며 임의 code를 실행하지 않는다.

허용 rule kind:

- `eligibility`
- `classification`
- `temporal`
- `exclusion`
- `frequency`
- `fixed_amount`
- `rate_amount`
- `indemnity_eligibility`
- `deductible`
- `limit`
- `required_document`

허용 expression은 `all`, `any`, `not`, `present`, `equals`, `in`, `range`, `date_between`, `days_since`, `count_before`와 versioned lookup만 사용한다. field path는 registry에 등록된 MedicalEvent, Policy, Rider, ClaimHistory field로 제한한다. 산술은 decimal 기반의 add, subtract, multiply, min, max와 명시적 rounding만 허용한다.

모든 executable rule은 다음을 가진다.

- rule kind와 schema version
- required/optional 여부
- 입력 field paths
- expression 또는 calculation
- 결과 reason code
- `TermsEdition`, `Clause`, page, optional bbox Evidence
- generator와 verifier version
- review state와 published version

복잡한 상호참조·표·정의가 이 DSL로 손실 없이 표현되지 않으면 executable rule을 만들지 않는다.

## Medical event structuring

자연어 입력은 구조화 facts와 확인 수준으로 변환한다. AI가 명확히 추출한 값은 즉시 분석에 사용할 수 있지만 화면에서 수정 가능하다. 불확실한 값은 null과 질문 후보로 남긴다. AI는 진단코드나 수술분류를 사용자 입력 또는 제공 Evidence 없이 추정 확정하지 않는다.

## Failure behavior

- timeout, rate limit, 일시적 provider 오류는 제한된 횟수만 재시도한다.
- authentication/config 오류는 반복하지 않고 운영자 조치가 필요한 상태로 전이한다.
- invalid JSON, unknown enum, missing Evidence, invented Evidence는 `NEEDS_REVIEW`다.
- Structurer 성공 후 Verifier 실패는 부분 성공이며 후보를 실행하지 않는다.
- 새 분석 실패가 기존 published version을 stale하지 않는다.
- provider 장애는 수동 원장·규칙 편집과 기존 deterministic decision을 차단하지 않는다.

## Tests

- 필드 누락·추측 거부와 strict JSON schema
- verifier가 새 사실을 추가하는 응답 거부
- 존재하지 않거나 다른 document version의 Evidence 거부
- 날짜·통화·비율·단위 경계
- unknown DSL operator와 field path 거부
- prompt/model/schema version 기록
- raw request·response가 log에 없는지 확인
- provider timeout·rate limit·auth 오류 분류
- 두 단계 성공만 `AI_VERIFIED`가 되는 상태 전이
- `NEEDS_REVIEW` rule이 decision engine에 들어가지 않는 통합 테스트
- 외부 API 없는 합성 CI fixture

## Invariants

1. AI 응답만으로 Rider를 가입 상태로 만들 수 없다.
2. AI 응답만으로 `MATCH`, `NO_MATCH`, `UNKNOWN` 또는 금액을 만들 수 없다.
3. executable rule은 Clause Evidence와 deterministic validation 없이 publish되지 않는다.
4. verifier는 입력 후보와 Evidence 밖의 사실을 발명할 수 없다.
5. OpenAI key, PDF password, archive key는 request·DB·log에 없다.
6. provider 장애가 기존 판정과 Evidence 조회를 중단시키지 않는다.
