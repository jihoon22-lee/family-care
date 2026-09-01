# Insurance ledger reconciliation design

- 상태: 구현 진행 중
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

