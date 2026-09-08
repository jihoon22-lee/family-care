# Insurance ledger reconciliation design

- 상태: API·Web 구현·합성 검증과 보호된 runtime count-only 수용 완료; runtime migration·재배포 대기
- 선행 권위: current private knowledge snapshot, operational `PolicyContract`, insurance document inventory
- 권위 경계: 계약 identity 대사와 Evidence 준비 상태를 분리

## Goal

가족별 `전체 가입 보험 분석`과 앱 내부 원장·업로드 문서 현황을 하나의 일관된 읽기
projection으로 설명한다. private knowledge snapshot은 전체 분석 계약의 권위로 유지하고,
운영 원장과 문서 inventory는 청구 Evidence 준비 상태를 소유한다. 어느 쪽도 다른 쪽을
덮어쓰거나 문자열 유사도로 자동 연결하지 않는다.

현재 두 화면의 건수 차이는 오류로 취급하지 않는다. 대신 분석 계약마다 운영 연결과 문서
준비 상태를 명시하고, current snapshot에 대응하지 않는 기존 앱 계약도 별도 대사 대상으로
보존한다. 과거 판독 실패는 삭제하지 않고 현재 해결 상태와 감사 이력을 구분한다.

## Authority model

다음 권위를 독립적으로 유지한다.

1. `private_knowledge_contracts.certificate_decision`은 분석 snapshot의 증권 가입 판정이다.
2. operational link는 knowledge contract와 기존 `PolicyContract`가 같은 계약인지에 대한 대사다.
3. document readiness는 연결된 `PolicyContract`의 Evidence와 사용자 확인 document set에서 계산한다.
4. unreadable resolution은 한 batch 실패가 현재 작업 대상인지에 대한 이력이다.

operational link의 `NO_MATCH`는 두 내부 계약 identity가 결정적으로 다르다는 뜻일 뿐 실제 보험
미가입 판정이 아니다. link가 없거나 정보가 부족하면 `UNKNOWN`을 유지한다. 사용자 확인 link는
계약 identity만 확인하며 약관 판본, 담보 자격, 보험금 지급 또는 계산 권위를 만들지 않는다.

## Append-only operational link history

`private_knowledge_operational_links`는 current knowledge contract의 사후 대사를 snapshot 밖에
보존한다. 핵심 필드는 다음과 같다.

- knowledge import run, HouseholdSpace, FamilyMember, knowledge contract
- optional operational `PolicyContract`
- `MATCH`, `NO_MATCH`, `UNKNOWN` decision과 독립적인 conflict flag
- `USER_CONFIRMED_OPERATIONAL_IDENTITY` authority와 bounded reason code
- 확인 AppUser와 UTC 확인 시각
- current/superseded 상태와 canonical link digest

`MATCH`만 `policy_contract_id`를 가질 수 있다. 같은 current knowledge contract에는 current link가
최대 하나이며, 같은 operational policy도 current `MATCH` link 하나에만 연결된다. 새 확인은 기존
current 행을 비현재로 전환하고 새 행을 추가한다. current snapshot 자체의 import-time exact binding은
`SNAPSHOT_EXACT_EVIDENCE`로 읽되 snapshot row를 사후 수정하지 않는다. 사용자 확인 history가 있으면
그 current 행이 import-time binding보다 우선한다.

mutation은 current knowledge run과 family binding을 잠그고, 선택한 policy가 같은 HouseholdSpace와
FamilyMember의 active policy party인지 확인한다. insurer/product 표시 문자열은 후보 설명에도 쓰지
않으며 자동 `MATCH`를 만들지 않는다. 요청은 nullable expected current link ID를 반드시 포함해
동시 변경을 감지한다.

## Append-only unreadable resolution history

`document_batch_item_resolutions`는 실패 batch item을 물리 삭제하거나 원시 상태를 바꾸지 않고
현재 작업 목록에서 해결 여부를 설명한다.

- failed batch item, HouseholdSpace, FamilyMember
- optional successful replacement batch item
- `REPLACED`, `DISMISSED`, `REOPENED` resolution
- `USER_CONFIRMED_DOCUMENT_RESOLUTION` authority와 bounded reason code
- 확인 AppUser와 UTC 확인 시각
- current/superseded 상태와 canonical resolution digest

`REPLACED`는 같은 household/member에 속하고 `DocumentVersion`이 고정된 성공 item만 가리킨다.
`DISMISSED`는 replacement를 가지지 않으며 사용자의 명시적 검토를 요구한다. `REOPENED`는 이전
해결을 되돌려 현재 작업 목록에 다시 표시한다. 같은 opaque source의 더 늦은 성공은 별도 사용자
확인 없이 기존 exact-current 규칙으로 해소할 수 있지만, source ID가 다른 교체본은 이 history가
없으면 자동으로 숨기지 않는다.

## Reconciliation read projection

대표 읽기 API는 다음과 같다.

```text
GET /api/v1/family-members/{member_id}/insurance-reconciliation
```

서버는 한 `REPEATABLE READ READ ONLY` transaction에서 current knowledge run, subject binding,
current operational link, active member policy, document set/item, unresolved batch item을 읽는다.
응답은 `Cache-Control: no-store`이며 다음 bounded 구조를 가진다.

```text
MemberInsuranceReconciliation
  schema_version
  member_id
  knowledge_run_id
  generated_at
  summary
    total_contracts
    evidence_ready_contracts
    documents_pending_contracts
    link_review_required_contracts
    conflict_contracts
    orphan_operational_contracts
    unresolved_unreadable_sources
  contracts[]
    knowledge contract display summary
    reconciliation_state
    operational_link
    document_readiness
  orphan_operational_contracts[]
  unresolved_sources[]
    current_resolution_id
```

계약의 `reconciliation_state`는 상호 배타적으로 계산한다.

- `EVIDENCE_READY`: current link가 `MATCH`이고 증권과 사용자 확인 약관 Evidence가 준비됨
- `DOCUMENTS_PENDING`: current link가 `MATCH`이지만 필수 문서 연결이 부족함
- `LINK_REVIEW_REQUIRED`: link가 없거나 `UNKNOWN`/`NO_MATCH`
- `CONFLICT`: 명시적 link conflict이거나 linked policy가 current member scope에서 유효하지 않음

따라서 `total_contracts`는 네 상태 count의 합과 정확히 같아야 한다. unreadable source는 계약과
자동 연결하지 않는 독립 문서 작업 차원이다. current knowledge contract에 연결되지 않은 active
operational policy는 `orphan_operational_contracts`에 남겨 사용자가 검토할 수 있게 한다.

## Mutation API

인증된 사용자 mutation은 다음 두 경계만 제공한다.

```text
POST /api/v1/private-knowledge/current/contracts/{contract_id}/operational-link
POST /api/v1/document-batch-items/{item_id}/resolution
```

두 요청 모두 CSRF·origin·session 검증을 기존 인증 경계에 맡기고 expected current history ID로
optimistic concurrency를 강제한다. 응답은 내부 UUID, stable enum/reason code, 시각만 포함한다.
source key, 파일명, 경로, 문서 본문, policy number, private alias, digest, credential은 포함하지 않는다.

## Cache and UI boundary

Web은 reconciliation endpoint를 전체 계약과 앱 준비 상태의 단일 요약 source로 사용한다.
inventory endpoint는 상세 document set/component 편집에만 유지한다. link 또는 resolution mutation,
batch terminal transition, private snapshot 변경 뒤 reconciliation과 inventory cache를 함께 무효화한다.
창 focus와 사용자의 명시적 새로고침에서도 재검증한다. 서비스 워커와 persistent browser storage에는
어느 응답도 저장하지 않는다.

`전체 가입 보험 분석` 패널은 닫힌 네 상태 count와 계약별 준비 상태를 함께 표시한다. 정확한
operational ID를 고른 사용자의 명시적 확인만 `MATCH`를 만들 수 있고, 자동 문자열 유사도 후보는
제공하지 않는다. 연결되지 않은 앱 계약은 `앱 원장 단독 계약`, 현재 해결되지 않은 판독 실패는
`판독·해결 작업`으로 같은 projection 안에 둔다. 해결 mutation에 필요한 nullable
`current_resolution_id`도 projection이 제공해 stale UI가 최신 이력을 덮어쓰지 못하게 한다.

document inventory는 `문서 근거 정리` 편집기로 표시한다. 계약·준비 상태 summary를 반복하지 않고,
연결된 document set 세부는 기본으로 접어 둔다. 미연결 set/component 편집은 유지하며, 과거 실패
목록은 reconciliation 작업 큐에서만 현재 상태를 설명한다. 기존 operational Evidence 원장은
`청구 근거 세부 원장`으로 명시해 전체 계약 목록과 구분한다. reconciliation 요청 실패는 inventory,
Evidence 원장, 후보 검토를 막지 않으며 각 경계가 독립적으로 재시도할 수 있다.

## Invariants

1. private knowledge snapshot과 기존 `PolicyContract`·`Rider`를 대사 과정에서 수정하지 않는다.
2. 문자열·파일명·보험사·상품명 유사성으로 operational `MATCH`를 만들지 않는다.
3. 미연결과 문서 부족은 실제 가입의 `NO_MATCH`가 아니다.
4. 사용자 identity 확인만으로 Evidence readiness를 완료하지 않는다.
5. link와 resolution history는 household/member 범위를 벗어나지 않는다.
6. current history는 대상별 최대 한 행이며 superseded 행은 삭제하지 않는다.
7. orphan operational policy와 unresolved source를 숨기거나 자동 삭제하지 않는다.
8. 응답·로그·fixture에는 실제 보험 자료, 개인 식별자, source path와 문서 본문을 넣지 않는다.
9. 모든 summary count는 같은 transaction의 bounded row 집합에서 계산한다.
10. actual runtime 변경은 backup, count-only dry run, 복원 DB 연습, 명시적 적용과 사후 검증을 거친다.

## Verification

- current snapshot exact binding과 user-confirmed history precedence
- `MATCH`/`NO_MATCH`/`UNKNOWN`, conflict, stale expected ID와 duplicate policy link
- cross-household/member knowledge contract, policy, batch item, actor 거부
- linked ready, linked document-pending, unlinked, conflict의 closed summary partition
- orphan operational policy 보존
- exact-source automatic resolution과 changed-source manual resolution, dismissal, reopen
- `REPEATABLE READ READ ONLY`, no-store, bounded arrays와 private-field 부재
- migration upgrade/downgrade, current unique index, digest/idempotency와 supersede history
- 단일 Web summary, 정확한 ID 기반 link/resolution mutation, 부분 실패, focus/수동 refresh와
  reconciliation·inventory 동시 cache invalidation

## v0.5 source-verified canonical coverage identity

`0034_canonical_links`는 현재 비공개 지식의 담보와 가입 원장의 Rider를 별도 불변 이력으로
연결한다. exact content manifest가 증권의 bytes·쪽수·종류를 연결하고, 해당 페이지의 원래
담보명과 native 물리 좌표가 유일한 가입 publication에 대응해야 한다. 패키지의 `line`은
원래 출처 위치로 보존하며 추출 행 번호로 해석하지 않는다. 저장된 모든 동일 bytes IR의
미처리 줄·표·raw words까지 검사하고 다른 위치의 동일 이름, OCR·불명확한 이름 근거는
자동 연결하지 않는다. 관계없는 미해결 텍스트는 자동 연결을 막지 않는다.

현재 가정·구성원·primary insured 관계, 원본 문서/추출/근거, 원장 version, 사용자 연결
결정과 계약/담보 1:1 관계를 저장과 조회 때 재검사한다. 사용자 `NO_MATCH`·재검토·충돌
결정은 자동 연결보다 우선한다. 기존 exact snapshot 연결과 원본 값은 수정하지 않는다.
원장 금액/이름 교정 후 identity를 재검사할 수 있지만 snapshot과의 값 차이는 별도로 남기며
이 연결이 과거 금액을 최신 확정 금액으로 승격하지 않는다. 계산 소비는 B04의 독립 경계다.

기존 API background consumer가 30초 간격으로 연결 이력을 갱신한다. 현재 유효한 이력만
통합 계약 조회의 `PROGRAM_VERIFIED_SOURCE_IDENTITY`로 표시하고 Web에서 자동 연결을
다시 검토할 수 있다. 새 사용자 확인은 기존 mutation 계약의 `expected_current_link_id`를
사용한다. 가입/유효성·약관 판본·청구 가능성은 별도 상태로 유지한다. source inventory는 현재
증권 검토의 alias와 물리 페이지만 요청한다. 각 페이지의 전체 native/OCR 노드와 명시된
문맥 노드를 모든 동일 bytes 재추출본에서 SQL로 투영하고, 그 합계가 64 MiB를 넘으면 해당
페이지 연결만 보류한다. 다른 약관·페이지·중복된 source snapshot 본문은 Python으로 읽지
않으며 마지막 페이지 하나만 캐시한다. 이 제한은 직렬화 JSON 제한이며 전체 프로세스나 DB의
RAM 상한은 아니다. 과거 이력과 원본 IR은 보존한다. 약관 판본/component 연결은 후속 작업이다.


상세 담보와 로컬 안내의 optional `canonical_identity`는 공통 ref와 두 출처의 원래 ID,
원장 version·검증 digest·필드별 차이를 같은 계약으로 전달한다. 원문 snapshot의 표시명과
금액은 보존한다. 결과 입력에서는 충돌한 가입금액/통화만 사용할 수 없는 값으로 처리하고
해당 입력에 의존하는 금액은 계산식으로 남긴다. 이름 차이는 독립적인 계산을 막지 않는다.
로컬 안내의 ref는 확인된 운영 Rider ID를 사용하며 두 출처의 참조도 결과 snapshot에
보존한다. 기존 v2 운영/지식 평가 기록은 감사와 이전 API 호환성을 위해 유지한다.
운영 원장만 있는 담보까지 로컬 안내로 통합하는 작업은 B04에서 수행한다.

확인된 공통 Rider로 기존 청구 이력을 조회하되 이력이 없다는 이유로 0회를 만들지 않는다.
identity 조회 실패는 savepoint 안에서 격리하여 기존 private catalog·통합 계약·로컬 안내를
숨기지 않는다. 실패한 identity는 현재 공통 연결로 반환하지 않는다. 과거 결과의 원본 JSON은
새 연결이나 원장 교정 때문에 다시 쓰지 않으며 새 분석은 최신 source 검증을 사용한다.


`0035_user_identity_proof`는 최초 publication이 사용자 확인인 경우에도 native 원문에서
identity를 독립적으로 검증한다. proof의 `publication_authority`와
`name_source_candidate_version_id`는 원래 확인 주체의 종류와 이름을 가져온 정확한 후보
버전을 남긴다. 기존 사용자 publication을 프로그램 publication으로 다시 쓰지 않는다.
원래 이름의 물리 위치를 우선 사용하고 원래 이름에 위치 근거가 없을 때만 교정된 이름을
같은 source 범위의 native 원문과 대조한다. 원장 표시명 변경은 원래 담보 identity를 바꾸지
않는다. 현재 편집 중인 후보가 있어도 마지막 publication과 원장 값이 유효하면 연결을
유지한다. 계약 연결의 명시적 거부·충돌과 삭제된 문서/관계는 계속 별도로 반영한다.

canonical 표시명과 증권 검토의 원래 이름이 달라도 양쪽의 canonical 계약/담보 ID가
명시적으로 일치하면 증권 검토 이름으로 원문을 검증한다. 전체 원문 중복 검사는 이 원래
이름을 사용한다. 알려진 source alias를 활용하는 경로이며 이름 유사도만으로 연결하지
않는다. 원래 publication authority를 속이거나 다른 후보를 이름 근거로 쓰는 DB 입력은
거부한다. 사용자 publication을 참조하는 새 이력이 있으면 지원하지 않는 이전 guard로의
schema downgrade도 거부하여 원본을 보존한다.
