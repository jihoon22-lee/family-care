# Private Knowledge Decision Publication Design

- 상태: 합성 publication/decision/assistance, v2 UI와 보호된 package/database/runtime acceptance
  완료(PR #42); 관련 결과·조건부 subtotal 회귀는 PR #44/#47과 `v0.3.2`에서 보완
- 범위: current private knowledge snapshot을 deterministic event decision과 benefit calculation에 연결
- 선행 설계: `docs/design/private-knowledge-catalog.md`,
  `docs/design/coverage-decision-engine.md`, `docs/design/event-result-pwa.md`
- 개인정보 경계: 실제 문서, 실제 사건, 실제 진단, 실제 보험금과 외부 식별자는 이 문서와
  저장소에 기록하지 않는다.

## 1. Problem statement

FamilyCare에는 서로 다른 두 데이터 계층이 있다.

1. 기존 운영 원장은 `PolicyContract`, `Rider`, `RiderClauseLink`, `CoverageRule`을 사용해
   claim-ready subset을 실행한다.
2. private knowledge catalog는 증권·약관 쌍을 더 손실 없이 보존하지만, imported fact와
   mapping을 의도적으로 `executable=false`로 저장한다.

현재 event decision engine은 첫 번째 계층만 읽는다. 따라서 private knowledge catalog에
가입 계약과 담보가 있어도 운영 Rider와 published CoverageRule이 없으면 분석 결과가 빈
배열이 된다. 이 빈 결과는 다음 세 상태를 구분하지 못한다.

- 실제로 검토할 가입 담보가 없음
- 가입 담보는 있지만 실행 규칙이 아직 게시되지 않음
- 규칙은 있지만 사건 정보나 약관 근거가 부족해 판정할 수 없음

또한 event 저장 요청이 빈 `structured_facts` 배열을 보내면 API가 update를 거부해 분석
endpoint에 도달하지 못하는 별도 Web 결함이 있다. 이 결함은 publication layer와 분리해
수정하되, 최종 acceptance는 event 입력부터 결과 화면까지 하나의 흐름으로 검증한다.

## 2. Goals

이 설계의 목표는 다음과 같다.

1. current knowledge snapshot의 증권 가입 담보를 deterministic decision engine이 안전하게
   평가할 수 있게 한다.
2. 증권 가입, 현재 계약 상태, 약관 문서 동일성, 판본 적용성, 담보-조항 mapping, 실행 규칙
   publication을 서로 독립된 권위로 유지한다.
3. 한 사건에 여러 계약과 여러 담보가 동시에 해당할 수 있게 하며, 정액형 결과를 담보별로
   계산하고 합계 trace를 보존한다.
4. 실손형은 정액형과 분리해 영수증, 급여 구분, 자기부담 조건과 다계약 조정 정보가 있을
   때만 계산한다.
5. 실행 규칙이 없는 가입 담보와 정보가 부족한 담보를 빈 결과로 숨기지 않고 `UNKNOWN`과
   coverage completeness로 보여 준다.
6. 모든 판정과 계산에 exact knowledge coverage, 약관 section, source clause, 물리 page와
   source digest lineage를 남긴다.
7. 실제 규칙 package, 사건 acceptance data, backup과 report는 저장소 밖의 보호된 경로에서만
   처리하고 외부 model API에 보내지 않는다.
8. runtime에 외부 LLM provider가 구성되어 있으면 사건과 locally selected clause 후보를 최소화해
   LLM 보조 추천을 만들고, provider가 없거나 실패하면 구조화 DB 검색 추천을 즉시 제공한다.
9. verified decision과 assistance recommendation을 분리해 LLM 또는 검색 결과가 가입·자격·금액의
   실행 권위로 승격되지 않게 한다.

## 3. Non-goals

- 약관에 존재하는 담보를 증권 가입 담보로 추정하지 않는다.
- private knowledge catalog 전체를 기존 `PolicyContract`와 `Rider` 테이블에 복제하지 않는다.
- 문구·상품명·담보명 유사도나 free-text 검색 결과를 자격 판정으로 사용하지 않는다.
- LLM 또는 검색 추천을 `MATCH`, `NO_MATCH`, 지급 확정이나 정액 보험금으로 번역하지 않는다.
- AI가 제안한 사건 fact나 규칙 초안을 사람의 승인 없이 실행 권위로 승격하지 않는다.
- `MATCH`를 지급 확정, 청구 승인 또는 최종 보험금으로 표현하지 않는다.
- 정액형 조건부 합계와 실손형 추정값을 하나의 총액으로 합치지 않는다.
- 이 단계에서 보험사 청구 API나 외부 보험 조회 연동을 추가하지 않는다.
- v1에서 browser나 DB에 외부 API key를 저장하는 설정 UI를 추가하지 않는다. provider 등록은
  Worker 전용 runtime environment로 유지하고 UI에는 사용 가능 여부와 실제 사용 mode만 노출한다.

## 4. Chosen architecture

current private knowledge snapshot 옆에 append-only verified rule publication layer를 추가한다.
publication package는 exact snapshot과 coverage를 참조하고, allowlisted condition DSL과
calculation DSL, clause citations, coverage별 publication disposition을 포함한다.

새 decision run은 두 source를 독립적으로 평가한다.

```text
MedicalEvent version
  -> confirmed normalized facts
  -> legacy operational Rider rules
  -> current private knowledge catalog
       -> current contract confirmations
       -> coverage publication dispositions
       -> published knowledge rules
  -> two independent evaluation streams
  -> one combined v2 result projection
       -> fixed conditional totals by currency
       -> separate indemnity status
       -> catalog execution completeness
  -> bounded recommendation search over the same member's enrolled catalog
       -> provider available: one strict-schema LLM rerank/explanation call
       -> provider absent/failure: deterministic structured-search ranking
  -> separate assistance projection; never an eligibility authority
```

기존 운영 테이블의 non-null Rider 외래키를 nullable polymorphic reference로 바꾸지 않는다.
private knowledge evaluation과 candidate는 별도 additive table에 저장하고 API에서만 공통
discriminated union으로 합친다. 이 방식은 기존 claim-ready 이력과 private knowledge evidence를
왜곡하지 않으며, 과거 decision run을 exact rule set으로 재현할 수 있다.

### Alternatives considered

#### A. 기존 운영 원장으로 전부 publish

private coverage를 모두 `Rider`와 `CoverageRule`로 복제하면 기존 engine을 거의 그대로 쓸 수
있다. 그러나 행정성 구성요소와 분류 미확정 담보가 운영 Rider로 왜곡되고, knowledge citation과
기존 `Evidence` lineage가 정확히 일치하지 않는 경우 가짜 내부 Evidence가 필요해진다. 따라서
선택하지 않는다.

#### B. event마다 semantic fact를 검색해 즉석 판정

coverage label과 semantic fact를 검색하면 구현은 빠르지만 동일 단어가 정의, 면책, 예시,
지급사유에서 서로 다른 의미를 갖는다. 검색 결과만으로 가입 여부, 적용 판본, required rule,
계산 기준을 증명할 수 없으므로 선택하지 않는다.

검색 자체는 버리지 않는다. 동일한 member에 실제 가입된 coverage와 연결된 section/fact만 대상으로
후보를 좁히고, page citation과 rank reason을 가진 `STRUCTURED_SEARCH` 추천으로 사용한다. 이 추천은
판정 stream과 분리되므로 publication이 미완료여도 사용자가 검토할 약관 후보를 볼 수 있다.

#### C. append-only verified publication layer

원본 snapshot과 실행 권위를 분리하고 exact reference, deterministic validation, dry-run/apply,
versioned result를 제공한다. 추가 schema가 필요하지만 데이터 권위와 감사 가능성을 유지하므로
이 방식을 채택한다.

## 5. Authority and execution gates

한 private knowledge coverage가 새 사건에서 실행되려면 다음 조건을 모두 만족해야 한다.

1. referenced knowledge import run이 해당 HouseholdSpace의 current snapshot이다.
2. coverage의 `enrollment_decision`이 증권 근거로 `MATCH`다.
3. coverage classification이 `BENEFIT_COVERAGE`이고 benefit type이 지원되는 값이다.
4. contract의 current confirmation이 `MATCH`이며 publication 기준일 상태가 실행 가능한
   상태다.
5. terms document identity, edition applicability와 overall review가 모두 `MATCH`다.
6. coverage-to-section mapping이 `APPLICABLE + MATCH`다.
7. publication이 exact coverage와 exact source clauses를 참조한다.
8. 모든 citation page/hash와 parent-child closure가 유효하다.
9. rule document와 calculation document가 allowlisted schema를 통과한다.
10. publication package가 인증된 사용자의 명시적 승인과 dry-run digest 승인을 받았다.

어느 하나라도 충족하지 않으면 importer가 실행 규칙을 게시하지 않는다. 대신 coverage별
disposition에 stable reason code를 보존한다. runtime은 이 상태를 `NO_MATCH`로 바꾸지 않는다.
계약 상태나 정보 부족은 `UNKNOWN`이다.

publication 가능 여부와 개별 사건의 계약 상태는 다시 분리한다. current 상태 확인은 과거
전체 기간의 무중단 유지 사실을 자동 증명하지 않는다. runtime에서 사건일이 status evidence가
직접 포괄하는 기간 밖이면 사용자가 그 사건일의 계약 상태를 확인하거나 별도 상태 이력이
있어야 한다. 계약 시작·종료일 안이라는 사실만으로 중도 실효·부활이 없었다고 추정하지 않는다.

## 6. Persistence model

모든 새 테이블은 UUID primary key, `household_space_id`, UTC timestamp와 same-household composite
foreign key를 가진다. source JSON은 strict schema를 통과한 bounded JSON만 저장하며 SQL,
Python, template 또는 provider expression으로 평가하지 않는다.

기존 point-in-time `private_knowledge_contract_confirmations`는 current catalog 표시에 계속
사용한다. 사건일 상태를 별도로 증명해야 할 때는 append-only
`private_knowledge_contract_status_intervals`를 추가한다.

- exact knowledge contract와 HouseholdSpace
- `effective_from`, `effective_through` inclusive date interval
- bounded contract status와 tri-state decision
- `USER_CONFIRMED` 또는 exact reviewed-document authority
- confirming AppUser, confirmed time와 stable reason code
- optional superseded confirmation reference

point confirmation은 시작일과 종료일이 같은 interval로 표현할 수 있다. 서로 상충하는 active
interval은 자동 병합하지 않고 해당 날짜를 `UNKNOWN`으로 만든다. current confirmation이나
계약 start/end만으로 넓은 interval을 만들어 내지 않는다.

### 6.1 `private_knowledge_rule_import_runs`

한 external rule publication package의 immutable 적용 단위다.

- `id`, `household_space_id`, `knowledge_import_run_id`
- `schema_version`, `package_digest_sha256`
- knowledge snapshot digest와 DB baseline digest
- approved dry-run report digest
- `VALIDATED`, `APPLIED`, `SUPERSEDED`, `REJECTED` 상태
- rule/disposition/citation/calculation count projection과 digest
- reviewer AppUser, approved/apply 시각, importer/reviewer version
- current 여부

같은 household와 package digest는 unique다. household마다 current publication run은 하나만
허용하며, 그 run은 current knowledge snapshot 하나에만 종속된다. knowledge snapshot이 바뀌면
기존 publication은 과거 run 재현에만 사용하고 새 사건에는 실행하지 않는다.

### 6.2 `private_knowledge_coverage_execution_dispositions`

current snapshot의 모든 coverage component에 대해 정확히 한 행을 요구한다.

- knowledge coverage와 contract reference
- `PUBLISHED`, `BLOCKED`, `NOT_APPLICABLE` disposition
- stable reason code와 bounded review note
- expected rule count와 calculation kind
- review actor/time

`PUBLISHED`는 `BENEFIT_COVERAGE`에 실행 규칙이 한 개 이상 있고 gate가 전부 통과한 경우만
허용한다. `BLOCKED`는 benefit coverage의 근거·판본·mapping·현재 상태·계산 기준 중 하나가
부족한 경우다. `NOT_APPLICABLE`은 급부가 아닌 것으로 검토 완료된 구성요소처럼 실행 대상이
아닌 경우만 사용한다. importer는 모든 component가 disposition matrix에 한 번 나타나고 모든
benefit coverage가 `PUBLISHED` 또는 `BLOCKED`인지 대사한다.

### 6.3 `private_knowledge_rule_publications`

한 coverage에 대한 한 immutable deterministic rule version이다.

- publication run, knowledge import run, contract와 coverage reference
- bounded `rule_key`, version과 rule kind
- required 여부와 result reason code
- allowlisted condition document와 declared input field paths
- `USER_CONFIRMED` review state, executable flag와 published time
- generator, verifier, reviewer provenance
- canonical normalized projection digest

같은 publication run에서 `(coverage_id, rule_key)`는 unique다. 실행 가능한 version은 수정하지
않는다. 변경은 새 external package와 새 import run으로만 추가한다.

### 6.4 `private_knowledge_rule_citations`

각 rule이 의존한 약관 근거를 목적별로 연결한다.

- rule publication
- exact knowledge terms section, source clause와 optional semantic fact
- `ELIGIBILITY`, `DEFINITION`, `EXCLUSION`, `WAITING`, `REDUCTION`, `FREQUENCY`,
  `AMOUNT`, `RENEWAL`, `INDEMNITY` evidence purpose
- source-document reference, physical page range와 source-text digest

citation은 같은 knowledge import run과 selected terms assignment에 속해야 한다. semantic fact를
참조하더라도 원문 fact의 `executable` 값은 계속 false다. publication이 별도 실행 권위다.

### 6.5 `private_knowledge_calculation_publications`

coverage의 계산 규칙을 eligibility rule과 분리한다.

- publication run과 knowledge coverage
- `FIXED`, `INDEMNITY` kind
- allowlisted calculation document
- required input field paths
- currency/rounding/frequency coordination policy
- exact amount/reduction/limit citation links
- review provenance와 normalized digest

`FIXED`는 enrolled amount, exact constant, percentage, bounded tier 중 citation으로 증명되는
형식만 허용한다. `INDEMNITY`는 receipt category, covered amount, deductible, rate, per-event limit과
coordination requirement를 명시한다. 임의 수식 문자열은 허용하지 않는다.

### 6.6 Decision-run extensions

기존 `decision_runs`에 다음 nullable snapshot identity를 추가한다.

- `knowledge_import_run_id`
- `knowledge_rule_import_run_id`
- `knowledge_status_projection_digest`
- `event_fact_schema_version`

run 생성 시점의 IDs와 사건일에 사용한 contract status interval projection digest를 고정한다.
result stale 계산은 event version, legacy policy/rule set, knowledge snapshot, knowledge publication
set 또는 applicable status confirmation 중 하나라도 바뀌면 true가 된다.

다음 additive table을 추가한다.

- `private_knowledge_rule_evaluations`: run, publication, coverage, tri-state result, fact paths,
  missing/conflicting fields, reason code와 citation snapshot
- `private_knowledge_claim_candidates`: run, contract, coverage, aggregate result, required counts,
  questions, hold reasons와 safe label snapshot
- `private_knowledge_benefit_calculations`: candidate, calculation publication, status, Decimal inputs,
  adjustments, output와 evidence trace

이 테이블은 기존 `rule_evaluations`, `claim_candidates`, `benefit_calculations`의 Rider foreign key를
변경하지 않는다. 하나의 run에 두 source 결과가 공존할 수 있으며 한 source 실패가 다른 source
transaction을 무효화하지 않는다. run status는 `SUCCEEDED`, `PARTIAL`, `FAILED`를 구분한다.

## 7. External publication package

첫 schema는 `private-knowledge-rule-publication.sol-v1`이다. manifest는 다음 data role을
hash와 byte size로 고정한다.

- `coverage-dispositions.jsonl`
- `contract-status-intervals.jsonl`
- `fact-normalizers.jsonl`
- `rule-publications.jsonl`
- `rule-citations.jsonl`
- `calculation-publications.jsonl`
- `calculation-citations.jsonl`
- `reconciliation.json`

`contract-status-intervals.jsonl`은 point-in-time current confirmation과 별도로 user-confirmed
또는 exact reviewed-document authority가 직접 포괄하는 기간만 담는다. 빈 배열은 허용하지만
current confirmation이나 계약 시작·종료일에서 더 넓은 interval을 자동 생성하지 않는다.

`fact-normalizers.jsonl`은 자연어 전체를 규칙 operand로 사용하지 않기 위한 검토 경계다.
각 행은 exact normalized token sequence, 허용된 event field path, normalized code/boolean,
priority와 `USER_CONFIRMED` review provenance를 가진다. regex, fuzzy score, substring heuristic,
임의 code 또는 외부 model 호출은 허용하지 않는다. 같은 field에 서로 다른 값이 동시에
일치하면 값을 고르지 않고 `CONFLICTING`으로 만든다. 실제 phrase와 normalized value는 private
package와 database 안에만 보존하고 저장소 fixture와 일반 로그에 기록하지 않는다.

reader는 absolute external mode-`0700` directory와 mode-`0600` regular file만 허용한다.
symlink, repository 내부 path, 예상하지 않은 파일, hash/size mismatch, duplicate key, unknown
knowledge snapshot, stale baseline과 post-validation replacement를 거부한다. private path와 DSN은
argv가 아니라 environment/file descriptor 경계로만 전달한다.

Lifecycle은 다음과 같다.

```text
validate package without DB write
  -> open read-only repeatable-read transaction
  -> reconcile exact current knowledge snapshot and every coverage disposition
  -> write count-only mode-0600 dry-run report outside Git
  -> authenticated user approves exact report digest
  -> restore current backup into disposable PostgreSQL
  -> apply and verify there
  -> apply once to real PostgreSQL in one transaction
  -> verify digests, counts, closure and runtime projection
```

stdout/stderr에는 private value, path, statement, event fact, SQL, DSN 또는 credential을 쓰지
않는다. 오류는 stable reason code와 file role/row number만 반환한다.

## 8. Event fact contract

free-text situation은 사용자 입력 보조 수단이지 실행 rule의 직접 operand가 아니다. v2 event
fact schema는 allowlisted structured fields를 제공한다.

- 사건/방문일과 치료 전·후 mode
- diagnosis classification과 optional standardized code
- procedure kind와 optional standardized code
- anatomical site와 pathology category
- treatment setting과 treatment context
- admission/outpatient/pharmacy flags
- separately billed treatment 여부
- receipt category와 covered/non-covered classification

필드 값은 bounded enum, date, boolean, Decimal 또는 normalized code다. free-text label은 표시와
검토용으로만 보존하며 equals/in rule operand로 사용하지 않는다.

Fact provenance는 다음처럼 구분한다.

- `USER_CONFIRMED`: 사용자가 직접 입력하거나 제안을 확인함
- `DOCUMENT_REVIEWED`: 검수된 내부 문서에서 deterministic하게 제공됨
- `DERIVED_CONFIRMED`: 앞선 confirmed fact만으로 allowlisted transform을 수행함
- `AI_SUGGESTED`: AI structuring 제안이며 실행에는 미확인
- `UNCONFIRMED`, `CONFLICTING`

required condition은 첫 세 provenance만 사용할 수 있다. `AI_SUGGESTED`, 누락, 상충,
stale evidence는 `UNKNOWN`을 만든다. UI는 필요한 field를 사람이 확인할 수 있는 질문으로
표시한다. 외부 structuring provider가 없어도 수동 fact 입력으로 분석을 완료할 수 있어야 한다.

## 9. Condition and calculation DSL

기존 data-only expression compiler를 재사용하되 field registry와 trust semantics를 v2로
확장한다. 허용 operator는 exact comparison, set membership, numeric/date range, boolean
composition, elapsed days와 reviewed claim-history count 같은 bounded operation뿐이다.

다음은 금지한다.

- regex와 arbitrary substring matching으로 진단·시술을 결정
- dynamic attribute access, import, network, filesystem, SQL, Python, JavaScript 또는 template eval
- external model call을 runtime eligibility step으로 사용
- unbounded recursion, collection, string 또는 calculation precision
- event free text와 coverage label의 fuzzy match

required rule aggregation은 기존 tri-state 의미를 유지한다.

- required `NO_MATCH` 하나 이상: candidate `NO_MATCH`
- required `NO_MATCH`가 없고 `UNKNOWN` 하나 이상: candidate `UNKNOWN`
- 모든 required rule `MATCH`: candidate `MATCH`

optional rule은 설명과 calculation adjustment에는 참여할 수 있지만 required eligibility를
뒤집지 않는다. exclusion은 DSL의 명시적인 required rule로 모델링해 부호 해석을 감사할 수
있게 한다.

## 10. Benefit calculations

### 10.1 Fixed benefit

각 private knowledge coverage를 독립 candidate로 계산한다. 같은 사건이 여러 계약이나 여러
coverage에 해당하면 policy label 유사도 때문에 하나로 합치지 않는다. 횟수 제한이나 계약 간
조정 조항이 exact rule로 게시된 경우에만 중복을 제한한다.

calculation status는 `CALCULATED`, `UNKNOWN`, `NOT_APPLICABLE`, `FAILED`를 사용한다. 결과에는
입력, 조정, rounding과 output trace를 남긴다. API summary는 다음만 합산한다.

- candidate aggregate result가 `MATCH`
- calculation status가 `CALCULATED`
- currency가 같은 값

합계는 currency별 `conditional_fixed_subtotals` 배열로 반환한다. UNKNOWN candidate와 계산 근거
없는 MATCH candidate 수를 별도 표시한다. 문구는 `조건부 정액 합계`이며 최종 지급액으로
표현하지 않는다.

### 10.2 Indemnity benefit

실손형은 별도 candidate와 summary를 사용한다. receipt line별로 다음 정보가 있어야 계산한다.

- 실제 결제된 환자 부담액
- 급여/비급여 또는 published category
- 보장 대상 여부
- deductible, reimbursement rate와 limit
- 다른 실손 계약 및 proportional allocation에 필요한 정보

하나라도 없으면 해당 단계는 `UNKNOWN`이다. 별도로 청구된 치료비가 있다는 사실만으로 전액을
추정하지 않는다. 여러 실손 계약이 있으면 exact coordination rule과 다른 계약 정보가 없을 때
각 계약 금액이나 합계를 확정하지 않는다. 실손 결과는 fixed subtotal에 더하지 않는다.

## 11. Analysis service flow

한 analyze request는 다음 순서로 동작한다.

1. HouseholdScope와 event optimistic version을 확인한다.
2. event와 confirmed fact projection을 immutable snapshot으로 만든다.
3. 같은 FamilyMember의 legacy operational policy snapshot을 읽는다.
4. exact subject binding을 통해 current knowledge contracts와 coverages를 읽는다.
5. current confirmation과 event date 기준 계약 상태를 평가한다.
6. current knowledge publication run과 complete disposition matrix를 읽는다.
7. legacy와 knowledge rules를 독립적으로 deterministic evaluation한다.
8. 각 source의 evaluation/candidate/calculation을 한 decision-run transaction에 저장한다.
9. coverage execution completeness와 partial failure를 계산한다.
10. combined v2 result를 반환하고 모든 response에 `Cache-Control: no-store`를 적용한다.

knowledge catalog가 있지만 publication run이 없거나 일부 coverage가 `BLOCKED`면 endpoint는
성공한 빈 배열만 반환하지 않는다. `analysis_completeness=UNAVAILABLE` 또는 `PARTIAL`과 reason
codes, blocked coverage count를 반환한다. 개별 private value나 내부 exception은 reason text에
넣지 않는다.

### 11.1 Analysis assistance modes

deterministic evaluation 뒤에는 별도 recommendation stream을 만든다. 이 stream은 다음 mode만
사용한다.

- `LLM_ASSISTED`: local search가 선택한 bounded 후보를 구성된 provider가 strict schema로
  재정렬하고 설명했다.
- `STRUCTURED_SEARCH`: provider가 구성되지 않았거나 호출이 실패해 local PostgreSQL ranking을
  그대로 사용했다.
- `NONE`: 검색 가능한 current catalog 또는 usable query token이 없다.

local search는 current snapshot, exact FamilyMember subject binding, 증권 enrollment `MATCH`,
coverage-to-section mapping, section/fact/clause citation 범위를 모두 강제한다. coverage label,
section heading, reviewed semantic fact와 clause text에서 normalized token overlap과 PostgreSQL
rank를 계산한다. 다른 member, 다른 household, 미가입 coverage, old snapshot은 후보가 될 수 없다.

추천은 `recommendation_id`, safe contract/coverage label, bounded excerpt, physical page, opaque
clause/section citation, local rank, stable reason code를 가진다. `eligibility_result`, payable amount,
claim-ready flag는 갖지 않는다. 화면과 API는 verified `candidates[]`와 `recommendations[]`를 다른
section과 type으로 유지한다.

### 11.2 Provider job and cost boundary

API는 analyze transaction에서 structured-search 결과를 즉시 저장하고 assistance job을 event
version과 decision run digest로 deduplicate한다. Worker만 `OPENAI_API_KEY`를 읽는다. key가 없으면
외부 호출 없이 job을 `STRUCTURED_SEARCH`로 완료한다. key가 있으면 다음 입력만 한 번 전송한다.

- bounded event situation과 user-confirmed/AI-suggested fact projection
- local search가 먼저 고른 최대 12개 candidate의 request-local opaque token
- 각 candidate의 safe label, 최대 240자 excerpt, page와 citation kind

DB UUID, 가족 이름, policy number, source alias/path, document binary/image, 전체 section, unrelated
contract는 전송하지 않는다. 응답은 supplied opaque token의 순서, bounded explanation code와
missing-fact question만 허용한다. unknown token, invented citation, decision/amount field와 schema
밖 출력은 거부한다. raw prompt/response는 저장하거나 logging하지 않고 provider request ID,
model/config version과 sanitized outcome만 보존한다.

한 event version과 candidate digest에는 external call을 최대 1회만 허용하고 자동 retry하지 않는다.
assistant schema의 `max_output_tokens` 기본값은 1,200, hard maximum은 4,000이다. timeout, rate limit,
auth, invalid response가 발생해도 이미 저장한 `STRUCTURED_SEARCH` 추천을 유지하며 provider 오류
본문은 사용자나 log에 노출하지 않는다. 실제 provider smoke test는 합성 입력으로만 수행하고
사용자가 승인한 작업별 호출 상한을 넘지 않는다.

## 12. API contract v2

기존 `coverage-decision.v1`은 과거 호환 schema로 보존하고, event analyze/result endpoint를
Web과 함께 `coverage-decision.v2`로 원자적으로 전환한다.

v2 envelope에는 다음을 추가한다.

- legacy와 knowledge snapshot/version identity
- `analysis_completeness`: `COMPLETE`, `PARTIAL`, `UNAVAILABLE`
- current catalog contract/benefit coverage/published/blocked count projection
- discriminated `candidates[]`와 `evaluations[]`
- `conditional_fixed_subtotals[]`
- separate `indemnity_summary`
- bounded source failure reason codes
- `assistance`: mode, state, model label, fallback reason code와 bounded `recommendations[]`

candidate source는 다음 union이다.

```text
OperationalCandidateSource
  kind = OPERATIONAL_RIDER
  rider_id

PrivateKnowledgeCandidateSource
  kind = PRIVATE_KNOWLEDGE_COVERAGE
  knowledge_contract_id
  knowledge_coverage_id
```

공통 candidate는 safe contract/coverage label, benefit kind, aggregate tri-state, required result
counts, questions, hold reasons와 calculation reference를 가진다. evaluation은 source-specific
rule version ID와 exact citation projection을 가진다. private citation은 snapshot-local opaque
document reference, physical page와 clause reference만 노출하고 raw statement 전체를 목록
응답에 넣지 않는다.

claim workflow는 기존 operational candidate만 즉시 시작할 수 있다. private knowledge candidate는
별도 claim snapshot schema가 구현될 때까지 `claim_start_ready=false`로 표시하되, 이를 지급
자격 부정으로 번역하지 않는다.

assistance state는 `READY`, `REFINING`, `FAILED`가 아니라 `SEARCH_READY`, `LLM_PENDING`,
`LLM_READY`의 closed vocabulary를 사용한다. provider failure는 `SEARCH_READY`와 sanitized
fallback reason으로 표현해 usable 검색 결과를 실패 화면으로 숨기지 않는다. result reload는 같은
event version의 latest assistance projection만 읽고 새 외부 호출을 만들지 않는다.

## 13. Web behavior

Event composer는 저장과 분석 상태를 명확히 분리한다.

- 빈 structured fact list는 update field 자체를 보내지 않는다.
- 새 submit 전에 이전 success/error live-region을 지운다.
- 저장 실패, structuring 실패와 analysis 실패를 서로 다른 메시지로 표시한다.
- 서버에 저장된 event version을 받은 뒤에만 analyze를 호출한다.

결과 화면은 다음 순서를 사용한다.

1. 분석 범위와 completeness
2. 지금 할 일과 추가 확인 질문
3. 조건부 정액 합계와 unresolved count
4. 실손형 별도 상태
5. `MATCH`, `UNKNOWN`, `NO_MATCH` candidate group
6. contract/coverage별 계산 trace와 clause/page Evidence
7. 앱 내부 legacy document-linking audit
8. 별도 `관련 약관 추천` section과 `LLM 보조`/`DB 검색` mode label

가입 catalog가 존재하지만 published rule이 0개이면 `해당 보험 없음`을 표시하지 않는다.
`가입 담보는 확인됐지만 실행 규칙 검토가 완료되지 않음`과 blocked count를 표시한다. legacy
inventory의 password/OCR/page-limit 이력은 `이전 업로드 처리 기록`으로 표시하고 current
knowledge catalog의 문서 완전성이나 가입 여부와 섞지 않는다.

추천 card에는 `검토 후보이며 지급 판정이 아님`을 항상 표시하고 page citation을 제공한다.
`LLM_PENDING` 동안 이미 계산된 verified 결과와 DB 검색 추천을 즉시 렌더링하며 polling으로
`LLM_READY`가 되면 recommendation section만 교체한다. provider key, provider request ID, raw
failure detail과 prompt는 browser에 보내지 않는다.

## 14. Failure and consistency behavior

- current snapshot/publication mismatch: 새 분석 차단, 기존 run stale
- disposition coverage 누락 또는 중복: package 전체 거부
- current confirmation 없음: contract/coverage result `UNKNOWN`
- citation page/hash mismatch: affected publication 비실행, apply rollback
- event fact 누락·AI 제안·상충: dependent rule `UNKNOWN`
- calculation input 부족: eligibility result를 유지하고 calculation만 `UNKNOWN`
- one source runtime failure: 다른 source 결과를 보존하고 run `PARTIAL`
- provider 미구성: 외부 call 0회, `STRUCTURED_SEARCH`
- provider timeout/rate/auth/schema failure: retry 0회, 기존 검색 추천과 sanitized fallback code 유지
- repeated result reload 또는 동일 event analyze: 동일 digest external call을 중복 생성하지 않음
- mixed currency: currency별 subtotal, cross-currency total 없음
- concurrent event edit: optimistic version conflict, 분석하지 않음
- commit outcome uncertainty: digest로 조회해 결과 확인, mutation 자동 retry 금지

## 15. Security and privacy

1. 실제 증권·약관·추출문·페이지 이미지·OCR·event acceptance fixture를 Git에 넣지 않는다.
2. 실제 rule package, report, backup과 confirmation은 저장소 밖에 mode `0700`/`0600`으로 둔다.
3. browser persistent storage와 service-worker cache에 event/result/Evidence를 저장하지 않는다.
4. API, worker와 CLI 로그에는 situation text, fact value, diagnosis, source alias, amount, path,
   statement, token, DSN 또는 SQL을 쓰지 않는다.
5. 모든 query와 FK는 server-derived HouseholdSpace를 강제한다.
6. publication과 calculation document를 code로 평가하지 않는다.
7. external model API는 actual document ingestion, rule publication과 protected acceptance manifest에
   사용하지 않는다. runtime event assistance만 bounded user situation과 locally selected excerpt를
   Worker에서 전송할 수 있다.
8. API key는 Worker environment에만 있고 DB, API response, browser, prompt, log와 Git에 없다.
9. actual apply는 backup과 승인된 dry-run digest 없이는 실행하지 않는다.

## 16. Test strategy

공개 test는 처음부터 만든 합성 문서·담보·사건만 사용한다.

### Unit and schema tests

- complete publication gate와 각 독립 authority 축의 실패
- every benefit coverage disposition closure
- duplicate rule, broken citation, stale snapshot, cross-household reference 거부
- rule/calculation DSL allowlist와 arbitrary expression 거부
- AI-suggested/missing/conflicting fact가 UNKNOWN을 만드는지
- tri-state required aggregation, exclusion, wait, reduction와 frequency
- Decimal precision, rounding, mixed currency와 trace
- fixed/indemnity summary 분리
- same-member/current-snapshot/enrolled-coverage search scope와 deterministic ranking
- LLM response가 supplied token만 재정렬할 수 있고 decision/amount/invented citation은 거부되는지
- provider key 없음, timeout, rate limit, auth와 malformed response의 zero-retry fallback
- same event/candidate digest 중복 분석이 external call을 한 번만 만드는지
- assistant output token limit, bounded payload와 prompt/response/log 비보존

### Synthetic acceptance scenarios

- 한 사건에 서로 다른 두 fixed coverage가 MATCH하고 두 금액이 독립적으로 합산됨
- 한 사건에 서로 다른 계약의 네 fixed coverage가 MATCH하고 네 계산 trace가 보존됨
- 같은 사건에 indemnity coverage가 있으나 receipt 일부 정보가 없어서 fixed subtotal과 별도로
  UNKNOWN으로 남음
- 필수 pathology/diagnosis/procedure fact가 없거나 미확인일 때 관련 coverage만 UNKNOWN
- 결정적 exclusion이 있을 때만 NO_MATCH
- current confirmation 또는 applicable terms edition이 없을 때 empty result가 아닌 PARTIAL
- 한 knowledge publication 평가 실패에도 legacy result가 보존됨
- provider 없음에도 동일 사건의 관련 약관 후보와 citation이 `STRUCTURED_SEARCH`로 반환됨
- synthetic provider가 local 후보를 재정렬하면 verified result는 그대로이고 assistance mode만
  `LLM_ASSISTED`로 바뀜

위 합성 수용은 disposable PostgreSQL 18에서 하나의 test로 실행한다. 첫 RED는 저장 직후와 immutable
reload 사이 decimal scale 및 operational fact-path ordering 차이를 검출했고, wire projection을
canonicalize한 뒤 GREEN이 되었다. missing-key 경로는 provider client 생성 0회, fake success와 fake
timeout은 각각 provider 호출 1회이며 실제 네트워크 호출은 0회다.

실제 사용자 acceptance 사례는 저장소 밖 protected manifest에서만 실행한다. 검증 보고에는
식별자, 진단, 약관명과 금액 대신 다음 aggregate만 기록한다.

- 예상 candidate count와 actual candidate count 일치 여부
- coverage별 tri-state와 calculation status 일치 여부
- currency별 conditional subtotal 일치 여부
- indemnity가 fixed와 분리됐는지와 missing reason code
- 모든 candidate에 exact clause/page citation이 있는지

### End-to-end and migration tests

- migration upgrade/downgrade/upgrade on disposable PostgreSQL
- package validate -> dry-run -> restored DB apply/verify -> idempotent apply
- event create/update/analyze/result and browser result rendering
- authenticated household isolation, no-store와 response bounds
- stale event/knowledge/rule version behavior
- assistance job claim/dedupe, provider-free fallback와 fake-provider upgrade
- real runtime API and browser acceptance when an authenticated session is available

## 17. Implementation sequence

1. 현재 Web event update 결함을 기존 RED/GREEN test로 완료한다.
2. migration과 strict publication package model을 TDD로 구현한다.
3. validation, reconciliation, dry-run/apply/verify와 privacy tests를 구현한다.
4. private knowledge rule runtime과 persistence를 구현한다.
5. member-scoped structured recommendation search와 assistance job persistence를 구현한다.
6. fake provider로 one-call LLM rerank, strict validation과 zero-retry fallback을 구현한다.
7. v2 API schema를 추가하고 generated contracts를 갱신한다.
8. Web result UI와 explicit partial/empty/recommendation states를 구현한다.
9. wholly synthetic multi-coverage와 dual-mode assistance acceptance를 통과시킨다.
10. 외부 actual package를 coverage disposition까지 전수 대사하고 규칙 초안을 만든다.
11. 사용자에게 count-only dry-run과 review summary를 제시해 exact digest 승인을 받는다.
12. backup -> disposable restore -> actual apply -> runtime/browser verification을 수행한다.

## 18. Completion boundary

다음이 모두 충족될 때만 이 작업을 완료로 보고한다.

- Event composer가 빈 structured facts에서도 analyze endpoint에 도달한다.
- current catalog의 모든 benefit coverage에 publication disposition이 있다.
- published private rules는 exact certificate/terms/mapping/citation와 user approval을 가진다.
- result endpoint가 legacy와 private candidates, partial completeness와 stale identity를 반환한다.
- fixed candidate를 독립 계산하고 currency별 conditional subtotal을 제공한다.
- indemnity가 receipt 조건에 따라 별도 계산되며 fixed subtotal에 포함되지 않는다.
- provider가 없으면 외부 호출 없이 citation-backed DB 검색 추천을 반환한다.
- provider가 있으면 event/candidate digest당 최대 한 번만 strict LLM 보조를 수행하고 실패 시 DB
  추천을 보존한다.
- verified candidate와 recommendation이 API, persistence와 UI에서 명확히 분리된다.
- 합성 two-coverage와 four-coverage scenarios가 정확한 candidate/count/trace를 재현한다.
- actual protected acceptance가 count, per-coverage decision, conditional subtotal과 citation 기준을
  만족한다.
- backup, restored-database apply/verify, actual apply, authenticated runtime verification이 통과한다.
- 전체 repository verification과 privacy scan이 통과한다.
- 실제 private material은 Git history, test output와 일반 로그에 존재하지 않는다.
