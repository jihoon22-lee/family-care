# Private insurance knowledge catalog design

- 상태: PR #39 merge·필수 CI, 보호된 dry-run/backup/restore/atomic apply와 authenticated API
  acceptance 완료; PR #42 publication/advisory layer의 immutable 입력으로 사용
- 적용 단계: private policy structuring과 document inventory 이후
- 권위 경계: 증권 가입 사실, 약관 적용성, 의미 사실, 실행 규칙을 서로 다른 상태로 보존

## Goal

저장소 밖에서 정밀 검토한 실제 보험 분석 패키지를 손실 없이 PostgreSQL에 보존하고,
기존 `PolicyContract`·`Rider` 원장을 덮어쓰지 않은 채 검토·조회·후속 규칙 구조화에
사용할 수 있게 한다. 같은 패키지를 다시 적용해도 중복 행을 만들지 않으며, 실제 적용
전에 전체 대사 결과를 읽기 전용으로 산출한다.

## Chosen architecture

기존 원장을 새 모델로 즉시 교체하지 않는다. 대신 한 번의 분석 결과를 immutable
knowledge snapshot으로 저장하고, 현재 snapshot만 조회 projection에 사용한다. 기존
`PolicyContract`, `Rider`, `TermsEdition`, `Clause`, `CoverageRule`은 검증·사용자 확인을
통과한 항목의 호환 projection 또는 실행 경계로 계속 유지한다.

검토한 대안은 다음과 같다.

1. 모든 구성요소를 기존 `Rider`에 넣는 방식은 `fixed`·`indemnity`로 분류되지 않는
   구성요소와 가입 미확정 행을 왜곡하므로 사용하지 않는다.
2. 기존 원장을 즉시 교체하는 방식은 현재 사용자 데이터와 후보 이력을 되돌리기 어렵게
   하므로 사용하지 않는다.
3. additive snapshot과 명시적 publish를 분리하는 방식을 채택한다. 원본 분석을 보존하고,
   기존 기능은 검증된 projection만 읽으며, 이후 모델을 확장해도 과거 snapshot을 재현할
   수 있다.

## Domain boundaries

### Knowledge import run

`PrivateKnowledgeImportRun`은 하나의 검증된 패키지 snapshot을 나타낸다.

- HouseholdSpace
- package schema version과 전체 content digest
- 분석 권위와 importer version
- `VALIDATED`, `APPLIED`, `SUPERSEDED`, `REJECTED` 상태
- 검증한 전체 manifest, manifest와 reconciliation의 bounded count projection
- 적용 당시 entity count와 독립 판정 행렬의 검증 projection
- 모든 정규화 열과 snapshot 내부 관계를 묶은 projection SHA-256
- 적용 actor와 시각
- 현재 snapshot 여부

패키지 경로, source path, Google Drive ID, password, archive key는 저장하지 않는다.
같은 HouseholdSpace와 package digest 조합은 unique다. 새 snapshot을 적용할 때 이전
current snapshot을 같은 transaction에서 `SUPERSEDED`로 바꾸되 과거 행은 삭제하지 않는다.

### Knowledge subject

`PrivateKnowledgeSubject`는 package의 비식별 family alias를 snapshot 안에서 한 번만 보존하고
모든 계약이 이를 참조하게 한다.

- package 내부 canonical subject key와 private family alias
- 선택적 FamilyMember binding
- binding 판정 `MATCH`, `NO_MATCH`, `UNKNOWN`과 독립적인 conflict flag
- bounded reason code와 명시적 확인 provenance

가족 별칭과 기존 FamilyMember의 문자열이 비슷하다는 이유로 자동 연결하지 않는다. 정확한
외부 binding manifest 또는 인증된 사용자의 확인이 없으면 `UNKNOWN`으로 남긴다. 이 분리는
같은 사람의 여러 계약을 한 번에 검토하게 하고, 잘못 추정한 가족 연결이 계약별로 반복되는
것을 막는다.

### Knowledge contract

`KnowledgeContract`는 증권 검토 결과 한 계약을 snapshot 안에서 보존한다.

- package 내부 canonical key
- PrivateKnowledgeSubject 참조
- 보험사·상품 표시값
- 계약 시작·종료일
- 현재 상태와 후보 상태
- 증권 검토 상태와 field conflict/reconciliation metadata
- 선택적 기존 PolicyContract binding

현재 상태 근거가 없으면 `UNKNOWN`을 보존한다. 기존 PolicyContract binding은 content와
Evidence lineage가 정확히 일치할 때만 `MATCH`이며, 문자열 유사도만으로 연결하지 않는다.

### Enrolled coverage

`EnrolledCoverage`는 증권 표에서 발견한 주계약·특약·행정성 계약 구성요소를 모두 보존한다.

- KnowledgeContract
- package 내부 canonical key
- `MAIN_CONTRACT` 또는 `RIDER` 역할
- `BENEFIT_COVERAGE`, `NON_BENEFIT_CONTRACT_COMPONENT`, `UNKNOWN` 분류
- 가입 판정 `MATCH`, `NO_MATCH`, `UNKNOWN`
- 보장 유형 `FIXED`, `INDEMNITY`, `UNKNOWN`, `NOT_APPLICABLE`
- 가입금액·통화·기간·갱신 여부
- 현재 상태
- 증권 Evidence reference와 검토 issue
- 선택적 기존 Rider binding

가입 `UNKNOWN`은 오류가 아니며 current Rider projection을 만들지 않는다. 행정성 구성요소를
가짜 보장이나 fixed benefit으로 변환하지 않는다. 가입 `NO_MATCH`는 결정적 불일치 근거가
있는 패키지만 허용한다. 구성요소 성격 자체가 미확인인 `UNKNOWN`도 급부로 추정하지 않고
보장 유형을 `UNKNOWN`으로 보존한다.

### Terms assignment

`KnowledgeTermsAssignment`는 계약과 약관 문서의 관계를 다음 독립 축으로 저장한다.

- document identity decision
- edition applicability decision
- overall review decision
- 선택된 terms alias와 reason code

assignment 본체는 계약별 판정을 한 번 저장하고, 선택된 terms alias는
`PrivateKnowledgeTermsAssignmentSource`에 0개 이상 순서대로 저장한다. 따라서 약관 미선택,
단일 선택, 복수 선택을 같은 모델에서 손실 없이 표현한다. 선택 alias에 구조화된 section이
없어도 assignment는 유효하며 section mapping만 `UNKNOWN`으로 남는다.

문서 동일성이 `MATCH`여도 판본 적용성이 `UNKNOWN`이면 약관 검색 후보로만 사용할 수 있고,
Rider-Clause 또는 CoverageRule의 실행 근거가 될 수 없다. assignment는 약관 존재를 가입
사실로 바꾸지 않는다.

### Terms section, source clause, and semantic fact

`KnowledgeTermsSection`은 분석 단위, `KnowledgeSourceClause`는 원문 조항 index와
page/hash lineage, `BenefitProvisionFact`는 검토한 의미 사실을 보존한다.

`KnowledgeSemanticReview`는 section별 분석 상태·요약·분류 건수·경고·이전 결과 감사와
요약 citation을 한 행으로 보존한다. 개별 fact가 없는 section이나 fact로 환원되지 않는
검토 metadata도 유실되지 않으며, fact는 해당 review와 section을 함께 참조한다.

의미 사실은 지급사유, 정의, 면책, 대기, 감액, 횟수, 금액 기준, 갱신, 청구서류,
소멸, 교차참조 범주를 가질 수 있다. 사실의 설명·조건·숫자 용어와 citation을 저장하지만
`executable=false`인 사실을 CoverageRule로 승격하지 않는다.

`BenefitProvisionFactCitation`은 같은 section의 정확한 source clause index, 물리 page
범위, source-text SHA-256을 가리킨다. 패키지가 원문 전체를 담지 않으므로
`KnowledgeSourceClause`를 기존 `Clause.normalized_text`로 가장하지 않는다. 실제 Clause
catalog publish는 DB Extraction의 원문을 다시 읽고 citation hash를 검증하는 별도 단계다.

### Coverage-to-terms mapping

`KnowledgeCoverageTermsMapping`은 EnrolledCoverage와 terms section 후보의 연결을 저장한다.
가입, 문서 동일성, 판본 적용성, section mapping을 각각 보존한다. 모든 축이 `MATCH`이고
내부 Evidence binding까지 유효할 때만 기존 `RiderClauseLink` 후보를 만들 수 있다.

`NOT_APPLICABLE`은 판정 3값에 섞지 않는다. non-benefit 구성요소처럼 section mapping 대상이
아닌 경우 `mapping_applicability=NOT_APPLICABLE`과 `section_mapping_decision=UNKNOWN`을 함께
저장한다. 적용 대상인 보장은 `APPLICABLE`, 적용성 자체가 미확인인 경우 `UNKNOWN`이다.

## Evidence references and bindings

패키지 내부 Evidence reference는 source alias, 1-based physical page, optional source-text
digest를 가진다. importer는 source alias 원문을 일반 로그에 쓰지 않고 snapshot DB 안에만
보존한다.

`KnowledgeDocumentBinding`은 package source alias와 내부 DocumentVersion을 연결한다.

- alias digest와 private alias
- optional DocumentVersion
- `MATCH`, `NO_MATCH`, `UNKNOWN` 판정과 독립적인 conflict flag
- bounded reason code
- binding에 사용한 content SHA-256·page-count 검증 결과

자동 binding은 exact content digest, page count, document kind가 모두 일치할 때만 허용한다.
패키지에 그 값이 없으면 external binding manifest 또는 사용자 확인이 필요하다. 이름,
보험사, 상품, page 범위의 유사성만으로 `MATCH`를 만들지 않는다. 내부 Evidence UUID 연결도
같은 DocumentVersion, physical page, content hash를 모두 확인한다.

선택적 FamilyMember·AppUser·PolicyContract·Rider·TermsEdition·Evidence binding 행에는 import
run과 같은 `household_space_id`를 중복 보존하고 복합 외래키로 강제한다. Evidence binding은
같은 household와 DocumentVersion을 가리키는 exact Evidence가 함께 있어야 `MATCH`가 될 수
있다. 따라서 다른 가구의 유효한 UUID를 알고 있어도 knowledge snapshot에 연결할 수 없다.

## Package contract

지원하는 첫 package schema는 `private-analysis-package.sol-v2`다. importer가 다음 파일을
manifest SHA-256과 byte size로 검증한다.

- `contracts.jsonl`
- `coverage-components.jsonl`
- `policy-terms-pairings.jsonl`
- `coverage-terms-mappings.jsonl`
- `terms-sections.jsonl`
- `clause-evidence-index.jsonl`
- `terms-semantic-review.jsonl`
- `reconciliation.json`

manifest에 선언된 모든 파일도 hash와 size를 확인한다. importer는 absolute external
directory, mode `0700`, regular mode-`0600` file만 허용하고 symlink, repository 내부,
중복 manifest entry, unexpected mutable replacement를 거부한다. 최대 파일 수·개별 크기·총
크기·JSONL 행 수·중첩 배열 길이를 제한한다.

manifest가 선언한 분석 보고서·감사·dry-run 보조 파일은 알려진 supplementary 이름만
허용하고 hash·size·mode는 동일하게 검증하되 DB source row로 import하지 않는다. 위 8개
data role은 항상 필수이며, 선언되지 않은 파일과 알 수 없는 supplementary 이름은 거부한다.

## Deterministic validation

DB 연결 전에 다음을 검사한다.

1. manifest shape, schema version, hash, size, exact required file set
2. canonical policy와 coverage key uniqueness
3. 모든 coverage·pairing·mapping의 referential integrity
4. section·clause·fact·citation hierarchy와 page/hash 범위
5. contract row reconciliation과 package reconciliation count
6. 허용 enum, 날짜, 통화, 비음수 금액, bounded string/array
7. 모든 imported semantic fact와 mapping의 `executable=false`
8. 가입 UNKNOWN과 non-benefit component가 publishable Rider로 분류되지 않는지

검증 오류는 실제 값 없이 stable reason code, 파일 역할, row number만 반환한다. 한 오류가
다른 private value를 error message에 echo하지 않는다.

## Dry-run and apply lifecycle

```text
external package
  -> validate files and references
  -> calculate package digest
  -> open repeatable-read, read-only DB transaction
  -> reconcile current snapshot and optional operational bindings
  -> write count-only dry-run report outside repository
  -> operator approves exact report digest
  -> open repeatable-read apply transaction
  -> lock operational baseline tables and household import scope
  -> insert immutable snapshot rows
  -> compare persisted counts and decision matrices with approved report
  -> atomically make snapshot current
  -> verify row digests, parent-child closure, non-executable flags, and bindings
```

apply는 dry-run report digest와 package digest를 함께 요구한다. dry-run 이후 패키지나 DB
baseline이 바뀌면 `STALE_DRY_RUN`으로 거부한다. 현재 snapshot과 같은 package digest의
재실행은 기존 run을 반환하고 새 row를 만들지 않는다. 과거에 supersede된 digest를 다시
지정하면 이를 거짓 no-op으로 처리하거나 과거 감사 행을 변경하지 않고 `BLOCKED`로 중단한다.
재활성화가 필요하면 별도 activation-history 설계와 명시적 승인을 거친다.

snapshot write는 한 transaction이다. 실패 시 새 snapshot 행은 전부 rollback되고 이전
current snapshot은 유지된다. 적용 시 승인 보고서의 entity count와 가입·보장유형·약관 동일성·
판본 적용성·mapping 적용성·현재 상태 행렬을 저장한다. `verify`는 현재 indexed column에서 이
행렬을 다시 계산하고, 모든 source record digest와 정규화 projection digest, fact-review 및
fact-citation의 동일 section 폐쇄성, mapping과 선택 문서 alias 관계, 실행 가능 행 0건,
미확인 operational binding 0건을 함께 확인한다. apply는 기준선에 포함되는 운영 테이블을
`SHARE` 잠금으로 고정해 dry-run 재대사와 삽입 사이의 혼합 snapshot을 막는다. commit 결과가 불명확하면
자동 재적용하지 않고 package digest로 DB를 조회해 결과를 판별한다.

## Current-enrollment confirmation boundary

증권에서 계약과 가입 담보를 확인하는 것과, 해당 계약이 특정 기준일에 현재 가입 상태라는
확인을 분리한다. 증권 근거의 `certificate_decision`과 담보별
`enrollment_decision`은 분석 snapshot에 보존하고, 사용자의 현재 가입 확인은
append-only confirmation으로 보존한다.

confirmation은 current knowledge contract, HouseholdSpace, 확인한 AppUser, 확인 시각,
`status_as_of`, 상태, 3값 판정, bounded reason code와 authority를 가진다. 새 확인은 이전
이력을 수정하지 않고 기존 current confirmation을 비현재로 전환한 뒤 새 행을 추가한다.
`active` 표시는 current confirmation의 decision이 `MATCH`일 때만 사용한다. confirmation이
없으면 증권 계약 자체는 가입 계약으로 표시할 수 있지만 현재 상태는 `unknown`으로 남긴다.

가족 연결도 같은 권위 경계를 따른다. package family alias와 실제 FamilyMember는 정확한
binding manifest 또는 인증된 사용자의 명시적 확인으로만 연결한다. 한 snapshot의 모든
subject binding과 current-status confirmation은 snapshot digest와 DB baseline을 포함한
read-only dry-run을 먼저 통과하고 한 transaction으로 적용한다.

이 confirmation은 약관 판본 적용성, 개별 보험금 자격, 지급액을 확인하지 않는다. 따라서
현재 가입 확인이 있어도 edition applicability나 coverage-to-section mapping의
`UNKNOWN`을 `MATCH`로 바꾸지 않는다.

## Compatibility and query boundary

첫 단계의 API는 `GET /api/v1/private-knowledge/current`, `GET
/api/v1/private-knowledge/current/contracts`, `GET
/api/v1/private-knowledge/current/contracts/{contract_id}`로 current snapshot의 count·alias·계약·
구성요소·약관 assignment·semantic fact projection을 HouseholdScope로 조회한다. source path,
document alias, statement 전체를 목록 응답에 무제한 노출하지 않는다. 상세 fact 응답은 인증된
household 안에서 bounded citation과 함께 제공하며 `Cache-Control: no-store`를 사용한다.
상세 조회는 section UUID cursor로 최대 50개 section만 반환하고, 계약당 coverage·mapping은
각 256개, page당 fact 1,000개, citation 4,000개, 직렬화 응답 2 MiB로 제한한다. citation은
운영 DocumentVersion ID가 아니라 해당 snapshot 안의 불투명한 `source_document_ref`를 제공한다.

기존 policy/rider API는 즉시 바꾸지 않는다. 호환 projection은 다음 조건을 모두 만족하는
항목만 기존 원장에 publish한다.

- 증권 가입 `MATCH`
- 내부 policy DocumentVersion과 Evidence binding `MATCH`
- 지원되는 benefit type
- authenticated user confirmation 또는 기존 verified candidate
- current status는 정확한 상태 Evidence가 없으면 `unknown`

Knowledge snapshot 자체는 분석·대사·검토의 권위 기록이며 보험금 지급 결정이 아니다.

Web의 가족별 전체 가입 보험 목록은 current knowledge snapshot과 명시적으로 binding된
FamilyMember를 기준으로 조회한다. 기존 policy/rider 원장과 document inventory는 내부
Evidence 연결과 청구 실행 준비가 끝난 일부 계약을 다루는 운영 subset이다. 두 projection의
건수가 같다는 가정을 하지 않으며, 운영 subset을 전체 가입 보험 수로 표시하지 않는다.
가족별 필터에는 앱의 다른 가족 API와 동일한 opaque `family_member_id`만 노출하며,
실제 이름·생년월일·외부 Drive ID·package alias는 binding 식별자로 사용하거나 응답하지 않는다.
사후 operational identity 확인과 통합 readiness projection은 immutable snapshot을 수정하지 않는
`docs/design/insurance-ledger-reconciliation.md`의 append-only history가 소유한다.

## Verified publication and event decision boundary

catalog import의 모든 semantic fact는 계속 `executable=false`다. 실행 권위는 exact current snapshot에
묶인 별도 `private-knowledge-rule-publication.sol-v1` package에만 있다. package는 모든 coverage의
`PUBLISHED | BLOCKED | NOT_APPLICABLE` disposition, 사건일 status interval, allowlisted rule와
calculation document, exact section/clause/fact/page citation을 포함한다. dry-run은 snapshot digest,
confirmation, disposition closure, citation lineage와 count를 검증하며 apply는 append-only current
publication을 만든다. 같은 package의 재적용은 `NO_OP`이고 과거 package 재활성화는 차단한다.

event analysis는 operational 원장과 publication-backed private catalog를 독립 평가해 v2 응답에서만
합친다. private candidate/evaluation/calculation은 별도 immutable table에 저장되며 다른 household의
행이나 운영 Rider ID로 바뀌지 않는다. catalog coverage count는 전체 가입 지식 범위,
published/blocked count는 실행 준비 범위를 뜻한다. 고정형 conditional subtotal과 실손 unresolved
summary는 분리하고 모든 evaluation은 exact private citation을 가져야 한다.

합성 PostgreSQL acceptance는 package apply -> exact subject/current confirmation -> publication apply ->
event create/update -> combined analyze -> immutable reload를 통과한다. 두 fixed 담보 및 네 fixed 담보와
별도 indemnity `UNKNOWN`, idempotent apply, cross-household 0행을 검증했다. 이는 실제 보호 package나
실제 보험금 결과 acceptance를 대신하지 않는다.

## CLI boundary

운영 CLI는 private path를 argv로 받지 않고 환경변수로만 읽는다. 저장소 보호 경계는 호출자가
지정하지 못하며 설치된 Python runtime root에서 계산한다. 명령은 다음 네 가지다.

- `validate`: package-only 검증과 digest
- `dry-run`: read-only DB reconciliation report 생성
- `apply`: 승인된 dry-run digest와 actor를 검증한 뒤 snapshot 적용
- `verify`: current snapshot count와 digest 재검증

stdout에는 상태, digest prefix가 아닌 opaque run ID, count, stable reason code만 출력한다.
실제 값, source alias, path, SQL, DSN은 출력하지 않는다.

## Operational apply gate

실제 DB apply 전 다음을 모두 만족해야 한다.

1. FamilyCare 외 다른 container와 process를 건드리지 않는다.
2. 현재 DB를 custom-format dump로 백업하고 mode·hash를 확인한다.
3. 합성 package 단위·PostgreSQL integration·전체 repository 검증이 통과한다.
4. dry-run의 package counts, create/no-op/conflict/block counts, 예상 전후 counts를 전수 대조한다.
5. conflict 또는 대사 불일치가 하나라도 있으면 apply하지 않는다.
6. 작은 pilot snapshot을 별도 disposable database에서 apply/verify한 뒤 실제 DB에 적용한다.
7. apply 뒤 package digest, table counts, FK, current snapshot uniqueness, API projection을 다시
   확인한다.

이번 import는 encrypted archive를 수정하지 않는다. rollback은 기존 snapshot 삭제가 아니라
이전 current snapshot 재선택 또는 새 snapshot 비활성화로 수행하며 별도 승인 없이 실제 데이터를
물리 삭제하지 않는다.

## Security and privacy

- 실제 package와 report는 저장소 밖에만 둔다.
- 실제 row, statement, source alias, Evidence excerpt를 fixture·Git·CI·로그에 넣지 않는다.
- 공개 테스트는 처음부터 만든 합성 package만 사용한다.
- package reader는 symlink와 post-validation replacement를 거부한다.
- DB query와 mutation은 HouseholdSpace를 필수 scope로 가진다.
- actual apply actor는 인증된 AppUser UUID 또는 명시적으로 검증한 local operator UUID다.
- package JSON을 임의 SQL, Python, template, expression으로 평가하지 않는다.
- `executable=false`는 import 중 true로 바뀔 수 없다.

## Failure behavior

- package validation 실패: DB 연결 전 종료
- DB binding 누락: snapshot에는 `UNKNOWN`으로 보존하되 operational publish 차단
- 중복 canonical key 또는 끊어진 reference: 전체 package 거부
- stale dry-run, current snapshot race, count mismatch: apply transaction rollback
- unsupported future schema: fail closed
- 현재와 같은 knowledge snapshot: idempotent no-op
- 과거 non-current digest 재지정: `BLOCKED`; 과거 run을 암묵적으로 재활성화하지 않음
- 실제 데이터가 Git에 들어갈 가능성 발견: 변경·commit·push·apply 중단

## Tests

- valid synthetic package validate/dry-run/apply/verify round trip
- manifest tamper, size mismatch, symlink, repository path, permission failure
- duplicate contract/coverage, missing policy reference, missing section/clause citation
- row and package reconciliation mismatch
- UNKNOWN enrollment, unknown benefit type, non-benefit component preservation
- executable true rejection
- idempotent second apply and stale dry-run rejection
- every entity-stage rollback preserving the prior current snapshot and explicit supersede
- indexed decision-matrix drift and source-record digest drift detection
- HouseholdSpace isolation and no-store bounded API projection
- log/stdout absence of private values, paths, aliases, DSN, statements
- migration upgrade/downgrade shape and PostgreSQL constraints

## Completion boundary

구현 완료는 합성 package와 PostgreSQL에서 immutable snapshot round trip, 전체 공개 검증,
actual package zero-write dry-run을 증명하는 것을 뜻한다. 실제 DB 적용 완료는 별도로 backup,
approved dry-run digest, apply, post-apply DB/API 검증까지 성공해야 한다. CoverageRule 0건인
snapshot은 계약·약관 지식 조회에는 사용할 수 있지만 자동 보험금 판정 완료를 의미하지 않는다.
