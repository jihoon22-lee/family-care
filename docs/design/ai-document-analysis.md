# AI document analysis design

- 상태: 구현·합성 provider/PostgreSQL 및 provider-zero protected result fallback 검증 완료;
  실제 document structuring provider·전체 보험 형식 acceptance 미검증
- 적용 단계: Policy Ledger, Clause Linking, Coverage Rule, Event Structuring
- 권위 경계: AI는 후보를 구조화·검증하지만 보험 자격과 금액을 직접 판정하지 않음

## Scope

이 문서는 PDF 추출 결과를 보험 원장, 약관 조항, 실행 가능한 규칙 후보로 바꾸고 자연어 의료사건을 구조화하는 AI 경계를 정의한다. v0.1은 기존 WSL의 `OPENAI_API_KEY`를 Worker에만 주입하며 Gemini를 사용하지 않는다.

## Components

### Structurer

Evidence가 있는 추출 block·table batch를 strict JSON 후보로 변환한다. 요청된 필드만 반환하며 없는 값을 추측하지 않는다.

Private policy schema v2는 한 번의 bounded request에서 정확히 하나의 `policy_contract`와 최대 31개 `rider` 후보를 반환한다. Candidate ID는 batch 안에서 유일해야 하고 `policy_party`는 AI가 만들지 않는다. Rider는 증권 Evidence에서 가입 또는 현재 상태가 명시된 경우에만 후보가 될 수 있으며 약관 조항의 존재는 허용 근거가 아니다. 각 후보는 별도 verifier request와 deterministic validation을 거친다.

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

Provider-bound minimizer는 선택된 구성원 한 명만이 아니라 같은 HouseholdSpace의 모든 active FamilyMember 표시값과 내부 별칭을 최대 16개까지 수집한다. 한도를 넘거나 구성원·가정 범위를 증명하지 못하면 provider 호출 전에 fail closed한다. 수집한 값, label이 붙은 policy/contract identifier, email, phone뿐 아니라 한글·영문 계약자·피보험자·수익자·성명·주소·생년월일·식별번호 field의 값도 `[REDACTED]`로 대체한다. 날짜와 가입금액처럼 구조화에 필요한 비식별 숫자는 blanket digit masking으로 제거하지 않는다. 최소화 뒤에도 각 slice는 240자를 넘지 않으며 `EvidenceSlice`의 `repr`은 text를 포함하지 않는다.

### Retained policy stages and request accounting

정책 구조화 Worker는 `policy_candidate_batch_structurer_v2` 1회와
`policy_candidate_batch_verifier_v2` 1회로 후보 묶음을 처리한다. 검증 응답은 입력과 정확히
같은 candidate ID 집합이어야 하며 추가 필드·중복·잘못된 Evidence를 거부한다. 개별 후보의
문제는 다른 후보의 결과를 지우지 않는다. provider 실패가 발생한 부분 결과는 완료로
publication하지 않고 재시도한다.

`0028_policy_request_budget`의 `policy_provider_requests`는 요청 전에 commit한 예약으로
실제 시도 수를 센다. SDK 내부 자동 재시도는 끈다. 동일 job의 모델·schema 이름·지침·최소화
입력 hash가 같은 성공 응답은 비공개 DB에서 재사용한다. 원문 입력 자체는 이 표에 저장하지
않는다. verifier timeout 후에는 structurer를 재호출하지 않는다. 응답 캐시는 분석 검증이나
원장 반영 권위를 부여하지 않으며 기존 validator·lease·publisher 검사를 다시 거친다.

기본 문서 전체 이력 한도는 4회, Worker 정책 구조화 전체의 UTC 일일 한도는 8회다. 새 문서
버전이나 job도 같은 document의 소비량을 초기화하지 않는다. 타임아웃·실패·프로세스 중단
예약도 계산하며 예약을 환불하거나 이력을 삭제하지 않는다. DB advisory lock으로 마지막
예산의 동시 소비를 막고, 동일 입력의 진행 중 예약은 중복 호출하지 않는다. 예산 대기는
처리 재시도 횟수를 소모하지 않고 다음 UTC 날짜까지 보류한다. 문서 누적 한도는 날짜가
바뀌어도 복구되지 않으므로 정책 변경 전에는 계속 대기한다. 출력은 구조화 8192 token,
묶음 검증 4096 token으로 제한하며 초과/불완전 출력은 schema 실패로 다룬다.

범위 경로는 `policy_ranges.py`와 `policy_range_repository.py`에서 별도로 제공한다. 기존
240자 Evidence 경로는 유지하며 새 계획은 primary 범위 최대 4096자, 문맥 최대 4096자,
묶음당 primary 32개/Evidence 64개/최소화 본문 합계 16384자로 제한한다. 표 행은 쪼개지
않으며 넘친 행·문맥·문서 예산은 누락 범위로 저장한다. 페이지 전체에서 식별자 span을
한 번 계산한 뒤 raw 문자 좌표로 window를 잘라 경계에 걸친 식별자도 가린다. 이 검사는
모든 비정형 개인정보의 완전한 익명화를 보장하지 않는다. 문서·페이지 이미지는 보내지 않는다.

`policy_range_structurer_v3`는 계약 header 없는 특약 범위와 후보 없는 범위를 허용하지만
모든 primary 범위에 후보 연결/가입 사실 없음/미해결 중 하나를 명시해야 한다. supplied
범위·Evidence 밖의 인용과 연결되지 않은 후보를 거부한다. 문서 역할은 content 분류이며
약관·unknown·ambiguous 범위에 verifier가 동의해도 가입 권위를 부여하지 않는다.

`0029_policy_ranges`는 문서 generation과 최소화된 입력, 개별 검증 결과를 별도로 보존한다.
읽기/저장은 가정·구성원·문서/추출·현재 lease owner와 시도 번호를 검사한다. 완료 범위는
재처리하지 않으며 한 범위의 잘못된 응답도 나머지 범위 처리를 막지 않는다. 일부 미해결이나
누락이 있으면 전체 완료로 표시하지 않는다. 구성원 최소화 목록이 바뀌면 이전 입력을
재전송하지 않는다. 기존 요청 예약/캐시로 verifier 재시도는 structurer 결과를 재사용한다.
기본 Worker는 이 범위 경로를 사용한다. 기존 publisher는 과거 합성/호환 경로에 남는다.
범위 저장 자체는 원장/교정을 덮지 않으며 아래 API projector가 별도로 검증한다.

`0030_range_candidates`는 각 완료 범위의 후보를 기존 검토 저장소에 같은 transaction으로
반영한다. provider 후보 ID는 구간 안에서만 고유하므로 job·envelope로 namespace하고 원래
ID를 provenance에 보존한다. 인용은 실제 Evidence FK와 generation/node/문자 위치로 연결하고
240자 excerpt는 미리보기로만 사용한다. 일부 범위가 실패한 문서의 후보도 해당 가정·구성원의
검토 목록에 남으며, 다른 실패한 legacy job의 권한을 확대하지 않는다.

원장 반영에 앞서 `range_grounding.py`는 명시된 필드명·단위·날짜와 담보명을 인용과 대조한다.
지원하지 않는 형식은 검토 대상으로 보존한다. 증권에 적힌 과거 active 상태를 최신 상태로
승격하지 않는다. `policy_source_association.py`는 로컬 원문의 피보험자 label/value를 가정의
구성원과 정확히 대조하고, 같은 문서 버전의 명시적 계약번호로 페이지를 연결한다. 계약자·
수익자·이름의 부분 일치·등록 시 선택한 구성원은 피보험자 근거가 아니다. 여러 계약/대상자가
모호하면 자동 연결하지 않는다. 원문 식별값은 외부 DTO에 넣지 않고 opaque scope와 원문
위치만 연결 정보에 보존하며, publication 때 전체 구성원 identity/version을 다시 검사한다.
`0031_range_enrollment`의 publication 이력은 원본 후보/연결과 실제 원장 ID·반영 당시
원장 version·사용자/프로그램 권위를 append-only로 묶는다. API는 보관 후보를 durable inbox로
소비하며 별도 broker나 GET 부작용을 만들지 않는다. Compose API는
`FAMILYCARE_ENABLE_RANGE_ENROLLMENT=true`로 활성화하고 2초 간격/최대 25개씩 처리한다.
직접 API 실행은 이 설정과 DB URL이 있을 때 활성화한다. 실패는 고정 코드만 기록하며
종료 signal과 DB 연결/statement 제한을 따른다. 개별 보류/DB 값 오류는 다른 후보를 막지
않도록 시도 시각을 기록한다.

원장은 문서 버전·로컬 계약 scope별로 만들고 담보는 원래 이름의 정확한 원문 node/문자
위치에 고정한다. 한 블록의 여러 담보와 다른 계약의 같은 상품을 구분하며, 다른 행의 금액을
차용하지 않는다. 이름/인용을 교정해도 원래 담보 ID를 유지한다. 동일 source 재시도는
중복 생성하지 않고, 이미 반영한 값을 자동 결과로 바꾸거나 직접 원장 수정을 덮지 않는다.
같은 값의 반복 header는 근거만 추가하며 원래 교정 권위를 빼앗지 않는다. 피보험자 anchor의
정확한 Evidence와 사용한 범위 Evidence만 같은 transaction에서 확인한다. 원본 페이지의
미검수 Evidence는 승격하지 않는다. 최신 계약 상태는 unknown을 유지하고 가입금액은 지급
예상액으로 바꾸지 않는다. 다른 source row/document version의 담보 동등성·판본 연결은
후속 canonical mapping에서 검증해야 하며 이름/금액만으로 합치지 않는다.

연속표는 검증된 이전 가입 표와 연결된 unknown 페이지의 해당 행에만 역할/계약 근거를
이어 준다. 페이지 전체나 일반 블록을 승격하지 않고, 명시적인 약관/변경/모호한 페이지와
다른 계약번호·피보험자 충돌은 자동 연결하지 않는다. 요청 envelope revision은 v3다.
`table_grounding.py`는 불변 generation의 실제 data row와 인용된 header/unit 문맥을 읽어
정확한 담보명 열·가입금액 열·단위를 대조한다. 보험료 열, 병합/충돌 header, 빠진 문맥,
header 자체는 가입 담보 근거가 아니다. 필요한 header/unit Evidence를 필드 인용에 더하고
원래 provider 응답은 그대로 보존한다. 결과에는 `range-grounding-v2` 검증 revision을 남긴다.
표 이름 열 또는 명시적인 가입 담보명/금액 행은 유형 근거가 없어도 `unknown`으로 보존한다.
명시적 미가입/예시 행과 연결된 표 각주는 `NOT_ENROLLED`로 원장 반영을 막는다.
첫 반영 전 교정한 필드는 교정값을 사용하고, 반영된 담보 교정은 기존 publication ID를
유지한다. 원문 이름의 native 단어 좌표를 유일하게 확인하면 같은 DocumentVersion/계약의
새 extraction과 TEXT_LINE/TABLE_ROW를 같은 담보에 연결한다. raw node ID·추출 이력은
변경하지 않으며 기존 publication과 정확한 content hash·물리 페이지·이름 단어 bbox를
비교한다. 금액·교정된 표시명·배열 순서·표 전체 bbox는 동등성 근거가 아니다. 좌표가 없는
같은 이름의 재추출은 추가 가입으로 만들지 않고 보류한다. 기존 원장 교정/soft delete와
primary insured 구성원이 달라진 경우도 자동 반영하지 않는다.

일반 재가져오기에서 새 DocumentVersion을 만들더라도 `contract_source_locator.py`가
불변 원문의 정확한 계약번호 anchor와 기존 local scope를 재검증한다. 같은 가정·bytes·
정규화된 계약번호 hash·피보험자가 일치하는 기존 publication의 계약 ID가 하나이면 이를
재사용한다. 서로 다른 bytes를 합치지 않고, 복수 기존 계약이나 입증할 수 없는 다른 문서의
동등성은 보류한다. 같은 문서의 서로 다른 local contract scope는 독립 계약으로 유지한다.
기존 원장/교정/청구와 source 이력은 수정·삭제하지 않으며, 새 publication이 별도 출처를
기존 계약/담보에 연결한다. 두 번째 문서에서 계약을 교정해도 PolicyContract/Party의 원래
DocumentVersion과 Evidence를 유지하고 교정 필드 근거는 새 publication에 남긴다.

재가져온 문서에서 추가된 담보를 읽거나 약관 링크를 검증할 때 `enrollment_alias.py`는 원래
계약 publication과 해당 담보의 정확한 source Evidence publication을 함께 조회한다. 현재
문서 hash·generation·extraction·가정·삭제되지 않은 primary insured·원문 계약 locator가 모두 맞아야
문서 ID 차이를 허용한다. 클라이언트는 이 검증 값을 설정할 수 없다. 약관 판본·계약일·문서
종류·Evidence 검토 상태·무결성·정확한 인용 검사는 유지한다. 운영/private knowledge의
공통 담보 identity와 서로 다른 bytes의 계약/약관 판본 동등성은 후속 연결 대상이다.

`document-structure-v2`는 단어별 원본 BLOCK을 보존하고, 같은 layer/줄에서 가까이 이어지는
단어를 `TEXT_LINE`으로 묶는다. 삽입한 공백 이외의 문자와 원본 block/line 문자 범위는
`source_spans`로 역추적한다. 표 cell 안의 단어를 다시 줄로 예약하지 않고, 다른 열·겹침·
먼 상태 표시는 미해결 범위로 남긴다. 줄은 4096자 범위 안에서 원자적으로 처리하며 초과분을
잘라 이름/상태를 잃지 않는다. 준비 revision도 4096자 primary/context 기준으로 갱신했다.
등록되지 않은 이름도 bare `Insured` label로 최소화하고, 줄 끝의 미가입/예시 상태는 유지해
가입 반영을 막는다. 최소화 revision이 바뀌면 보관된 이전 전송 입력을 재사용하지 않는다.

과거 묶음 호환 경로는 성공한 private `policy` import만 별도 `policy_structuring_jobs` leased queue를 같은 transaction에서 생성한다. Worker는 각 provider 호출 직전에 lease를 갱신하고 호출을 120초로 제한한다. 검증된 candidate batch와 job 성공은 하나의 transaction으로 저장하며, 커밋 결과가 불명확하면 실패 상태를 덮어쓰지 않고 lease 복구에 맡긴다. 후보는 예약된 policy aggregate ID를 공유하지만 초기 page Evidence가 `NEEDS_REVIEW`이므로 자동 원장 projection을 만들지 않는다. 이 runtime wiring은 합성 provider와 PostgreSQL 18 경계까지 검증되었으며 실제 provider와 실제 보험자료 acceptance는 아직 수행하지 않았다.

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

## Optional event-clause assistance

문서 structuring과 사건 결과의 관련 약관 추천은 서로 다른 job이다. deterministic decision이 먼저
완료된 뒤, server-scoped current snapshot에서 해당 member의 증권 가입 `MATCH` 담보와 연결된
section/fact만 구조화 검색한다. 추천에는 bounded label/excerpt와 exact page citation만 포함하며
판정·금액·새 근거를 만들 수 없다.

`OPENAI_API_KEY`가 Worker에 있으면 동일 event version과 candidate digest당 최대 한 번 strict-schema
호출로 supplied `candidate-XX` token을 재정렬하고 bounded explanation code를 붙인다. 원문 PDF,
path, source alias, household ID, API key와 verified calculation은 보내지 않는다. key가 없으면 provider
client를 만들지 않고 `STRUCTURED_SEARCH`; timeout, rate limit, auth 또는 schema 오류는 retry 없이
같은 DB 추천을 `SEARCH_READY`로 보존한다. raw prompt/response는 저장하지 않으며 result GET은 새
job이나 외부 호출을 만들지 않는다.

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
- missing-key 0-call, supplied-token-only fake success와 one-attempt failure fallback
- assistance 전후 candidate/evaluation/calculation/subtotal immutable result 동일성

## Invariants

1. AI 응답만으로 Rider를 가입 상태로 만들 수 없다.
2. AI 응답만으로 `MATCH`, `NO_MATCH`, `UNKNOWN` 또는 금액을 만들 수 없다.
3. executable rule은 Clause Evidence와 deterministic validation 없이 publish되지 않는다.
4. verifier는 입력 후보와 Evidence 밖의 사실을 발명할 수 없다.
5. OpenAI key, PDF password, archive key는 request·DB·log에 없다.
6. provider 장애가 기존 판정과 Evidence 조회를 중단시키지 않는다.
7. assistance는 verified decision과 calculation을 생성하거나 수정하지 않는다.

## v0.5 stored-source preparation

Worker는 파일·provider 접근 없이 성공한 batch item의 저장 추출을 발견하고, 최신 성공
extraction/OCR와 pipeline revision별로 전문 IR·범위 계획을 준비한다. `0027`의
`document_structure_preparations`는 로컬 준비 상태를 provider chunk 성공이나 가입/약관
지식 활성화와 분리한다. 한 transaction에서 item row를 `SKIP LOCKED`로 잡고 savepoint 안에서
준비한다. 종료/취소로 connection이 닫히면 미커밋 작업은 롤백되며 완료 identity는 재사용한다.
로컬 DB 처리 실패만 60초 뒤 최대 3회 시도하고 읽을 수 없는 저장 추출은 고정 오류로 남긴다.
새 추출/OCR는 새 준비 identity를 만들고 기존 정상 generation·가입·청구 이력을 삭제하지 않는다.

인증된 batch status에는 optional `structure_state`, `structure_error_code`,
`structure_planned_chunks`, `structure_unprocessed_ranges`를 추가한다. `PREPARED`는 문서 내용의
범위 준비 완료다. 구조화·가입 반영·약관 지식 완료를 뜻하지 않는다. 최신 원문 버전의 추출이
없으면 이전 버전의 준비 상태를 최신 완료로 반환하지 않는다. Web은 가져오기 성공 후에도
준비 대기를 최대 300번 조회하고, 처리 실패를 재업로드 요구로 바꾸지 않는다. 본문·파일 경로·
추출 식별자는 이 상태 응답에 포함하지 않는다. 기존 provider 경로의 범위 소비 전환은 B02 후속이다.

## v0.5 bounded terms proposals

`FAMILYCARE_ENABLE_TERMS_STRUCTURING=true`는 API의 unresolved 구역 작업 준비와 Worker의
선택 구조화를 함께 켠다. 기본값은 false이며 API background consumer에는 기존
`FAMILYCARE_ENABLE_RANGE_ENROLLMENT=true`도 필요하다. 원문 로컬 compilation과 저장된
Worker 후보의 API 재검증·반영은 terms 설정이나 key 없이 계속된다. HTTP 조회는 작업을
예약하거나 provider를 호출하지 않는다.

API는 완전한 Article과 필요한 정의/별표/각주 참조를 같은 source revision의 envelope에
넣는다. 16구역·16,384자·전송 JSON 128KiB를 넘거나 참조를 유일하게 찾지 못하면 원문을
자르지 않고 미지원 범위를 기록한다. Worker는 전체 가정의 활성 이름/별칭을 적용해 최소화하고,
내부 식별자를 일시 별칭으로 바꾼다. provider 출력은 8,192 token과 JSON 128KiB로 제한한다.
원문 주소는 복원 후에만 후보로 저장하며 API가 원문 의미·인용·관계를 다시 검증한다.

작업은 180초 lease와 별도 lease token을 사용하며 최대 3회의 실행 실패를 허용한다.
key 미설정·기능 비활성, 공유 일일/문서 예산 초과 및 동일 요청 진행 대기는 실패 재시도를
소모하지 않는다. 일일 예산은 다음 UTC 날짜에, 진행 대기는 짧은 지연 후 재개한다.
문서 누적 예산 초과는 자동으로 다음 날 재시작하지 않는다. 증권 구조화와 같은 예약 장부와
global 잠금을 쓰므로 기본 문서당 4회·하루 8회 한도를 두 경로가 공유하며 실패 예약도 센다.
캐시는 실제 model·instruction·schema·최소화 payload에 묶인 검증된 최소화 응답만 보존하고
새 source envelope마다 인용을 다시 복원한다. DB transaction을 네트워크 호출 중 유지하지 않는다.

완료 후보와 작업 성공은 한 transaction에 저장한다. API는 source/privacy revision을 다시
확인해 PUBLISHED/STALE/REJECTED receipt를 남긴다. 의미 검증/compiler revision 변경 시
기존 후보를 재검증하므로 provider를 다시 호출할 필요가 없다. 이러한 성공은 해당 구역의
지식화이며 전체 상품·실제 문서 판독 품질·Rider 가입 사실을 자동으로 확정하지 않는다.
