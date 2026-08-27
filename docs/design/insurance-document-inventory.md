# Insurance document inventory design

- 상태: 실제 가족 자료 검토 결과를 반영한 구현 대기
- 적용 단계: Private policy structuring 후속
- 선행 조건: FamilyMember, private document batch, PolicyContract, Evidence

## Scope

가족 구성원별로 증권 근거가 있는 실제 보험과 그 보험에 연결된 약관·상품설명서·청약서의 보유 현황을 보여 준다. 사용자는 어떤 보험에 어떤 문서가 있고 무엇이 부족한지 확인한 뒤 누락 자료를 추가할 수 있어야 한다.

이 화면은 가입 사실과 문서 보유 사실을 구분한다. 증권으로 확인된 `PolicyContract`만 등록 보험으로 표시하고, 약관이나 상품설명서만 있는 자료는 가입 확인 전 문서로 별도 표시한다.

원본 PDF 한 개가 곧 문서 역할 하나라는 가정은 하지 않는다. 한 파일에 서로 다른 보험의 증권이 함께 있거나, 증권과 약관이 이어 붙어 있거나, 파일명은 증권이지만 실제 내용은 상품설명서·청약서일 수 있다. 따라서 물리 source file, immutable `DocumentVersion`, 검수된 page-range component, 보험별 document set을 분리한다.

## User-visible model

가족 구성원을 선택하면 화면을 두 영역으로 나눈다.

### 등록된 보험

증권 Evidence를 가진 `PolicyContract`만 포함한다. 각 보험 카드는 다음 정보를 표시한다.

- 보험사와 상품 표시명
- 현재 계약 상태. 최신 상태 근거가 없으면 `현재 상태 확인 필요`
- 실제 가입 담보 수
- 증권, 약관, 상품설명서, 청약서, 기타 보조자료의 보유 여부와 component 건수
- 한 source file에 여러 역할이 있으면 `묶음 문서` 표시와 역할별 page range
- 문서 연결 상태와 보완할 자료

등록 보험의 문서 완전성은 다음 두 값만 사용한다.

- `CERTIFICATE_AND_TERMS`: 증권과 사용자 확인된 적용 약관이 모두 있다.
- `CERTIFICATE_ONLY`: 증권은 있지만 사용자 확인된 적용 약관이 없다.

상품설명서와 청약서는 필수 약관을 대체하지 않는다. `has_product_explanation`, `has_application`과 각각의 component/source 건수로 별도 표시하므로 `증권+약관+상품설명서`, `약관+상품설명서이나 증권 없음` 같은 조합을 정확히 표현할 수 있다.

### 미연결·보완 필요 문서

등록 보험에 사용자 확인 상태로 연결되지 않은 문서를 표시한다. 문서의 주 분류는 문서 역할에 따라 정하고, 처리·연결·중복 상태를 별도 축으로 함께 표시한다.

- `TERMS_ONLY`: 약관은 있으나 증권 계약과의 연결이 확인되지 않았다.
- `PRODUCT_EXPLANATION_ONLY`: 상품설명서는 있으나 증권·약관과의 보험별 묶음이 확인되지 않았다.
- `APPLICATION_ONLY`: 청약서는 있으나 발행된 증권 계약이 확인되지 않았다.
- `POLICY_UNREVIEWED`: 증권으로 분류됐지만 아직 계약이 게시되지 않았다.
- `SUPPORTING_ONLY`: 다른 보조자료이며 가입 근거로 사용할 수 없다.

각 document set과 component는 다음 보조 상태를 독립적으로 가진다.

- processing state: 처리 완료, OCR 필요, password 필요, 판독 실패, 처리 실패
- pairing state: 미연결, 연결 제안, 사용자 확인, 상충, 거부
- duplicate state: 고유 문서, 같은 가족 내 중복, 가족 간 공유 사본 가능성

따라서 판독할 수 없는 약관은 `TERMS_ONLY + 판독 실패`로, 두 계약 후보가 상충하는 상품설명서는 `PRODUCT_EXPLANATION_ONLY + 상충`으로 표현한다. 약관과 상품설명서가 같은 미등록 document set에 있으면 주 분류는 `TERMS_ONLY`이고 상품설명서 존재를 별도 표시한다. 한 상태가 다른 문서 역할을 숨기지 않는다.

`TERMS_ONLY`, `PRODUCT_EXPLANATION_ONLY`, `APPLICATION_ONLY`는 보험 가입 내역이나 Rider를 만들지 않는다. 화면 문구도 `가입 확인 안 됨`을 명시한다.

## Source, component, and set classification

private batch와 공통 `Document` 종류에 `product_explanation`과 `application`을 추가한다. 이 값은 intake 시점의 source-level 분류이며, 검수된 component 역할을 대체하지 않는다.

- `policy`: 보험증권과 계약 상태를 직접 확인할 수 있는 계약 문서
- `terms`: 보통약관, 특별약관, 별표를 포함하는 약관 판본
- `product_explanation`: 청약 전·계약 안내용 상품설명서. 확정 계약 근거가 아니다.
- `application`: 청약 의사와 선택안을 담은 청약서. 발행된 증권이나 현재 계약 상태의 근거가 아니다.
- `supporting`: 앞선 보험 문서 역할에 해당하지 않는 보조자료

`insurance_document_components`는 하나의 immutable `DocumentVersion` 안에서 검수된 역할과 1-based inclusive page range를 보존한다.

- component role: `policy`, `terms`, `product_explanation`, `application`, `supporting`
- page start/end와 component Evidence
- `SUGGESTED`, `USER_CONFIRMED`, `CONFLICT`, `REJECTED` 검수 상태
- optimistic version과 soft-delete 시각

한 DocumentVersion은 서로 다른 role 또는 보험을 위한 component를 여러 개 가질 수 있다. 같은 role의 active page range가 겹치면 자동 병합하지 않고 conflict로 보낸다. source-level kind와 component role이 다르면 원시 batch 값을 덮어쓰지 않고 검수된 component를 새 version으로 남긴다.

`insurance_document_sets`는 같은 FamilyMember에 속하며 같은 보험 상품·계약으로 검토되는 component 묶음이다. 아직 증권이 없는 set도 존재할 수 있고, 등록 보험 set만 nullable `policy_contract_id`를 가진다. set의 상품 표시는 사용자가 검수한 안전한 보험사·상품 표시값이며 source filename이나 외부 ID를 그대로 사용하지 않는다.

상품설명서나 청약서에 예상 보험료, 예시 가입금액 또는 선택안이 있어도 `PolicyContract`나 Rider를 게시하지 않는다. 등록 set은 기존 `PolicyContract`와 그 증권 Evidence를 가리킬 뿐, document set이 가입 authority를 새로 만들지 않는다.

## Association boundary

`insurance_document_set_items`는 document set과 component를 연결한다. 등록 보험과 미등록 자료가 같은 모델을 사용하므로 약관+상품설명서는 함께 보이되 증권이 없다는 상태를 유지할 수 있다.

핵심 필드:

- HouseholdSpace, FamilyMember, InsuranceDocumentSet, optional PolicyContract
- 원래 import 문맥을 가리키는 `document_batch_item_id`
- 연결 당시의 immutable `document_version_id`
- 정확한 page range와 역할을 가진 `insurance_document_component_id`
- `policy`, `terms`, `product_explanation`, `application`, `supporting` 중 하나인 document role
- `SUGGESTED`, `USER_CONFIRMED`, `CONFLICT`, `REJECTED` 중 하나인 match state
- 선택적 Evidence, 확인 사용자·시각, optimistic version, soft-delete 시각

등록 set의 authoritative policy component는 `PolicyContract.source_document_version_id`와 정책 Evidence page에 포함되어야 한다. `PolicyContract`가 여전히 가입 authority이며 component나 set만으로 계약을 생성하지 않는다. 하나의 공통 약관 component가 같은 가족 구성원의 여러 계약에 적용될 수 있으므로 component와 set은 다대다 연결을 허용한다.

`USER_CONFIRMED` active set item만 문서 완전성 계산에 포함한다. AI나 문자열 유사도가 만든 `SUGGESTED`는 검토 대기 자료로만 표시한다. set, 계약, component와 batch item은 같은 HouseholdSpace와 FamilyMember여야 하며, component의 DocumentVersion은 그 batch item이 처리한 document의 version이어야 한다.

## Duplicate and shared-copy handling

content SHA-256이 같은 active DocumentVersion과 동일 page-range component identity를 조회해 다음 경고를 계산한다.

- 같은 가족 구성원 안의 중복 사본
- 다른 가족 구성원 배치에도 존재하는 공유 사본 가능성

물리 source 수와 역할 component 수를 별도로 보여 준다. 동일 source 안의 여러 component는 source 한 건으로, 동일 component 사본은 역할별 보완 필요 수에서 한 번만 집계한다. 다만 다른 가족 구성원에게 자동 이동하거나 소유자를 바꾸지 않는다. 공유 사본은 사용자가 각 구성원의 적용 여부를 확인해야 한다.

## Read projection

대표 API는 다음과 같다.

```text
GET /api/v1/family-members/{member_id}/insurance-document-inventory
POST /api/v1/family-members/{member_id}/insurance-document-sets
POST /api/v1/insurance-document-sets/{set_id}/items
DELETE /api/v1/insurance-document-set-items/{item_id}
```

읽기 응답은 다음 구조를 가진다.

```text
MemberInsuranceDocumentInventory
  member_id
  summary
    certificate_backed_policies
    certificate_and_terms
    certificate_only
    terms_only_documents
    product_explanation_documents
    application_documents
    unreadable_documents
    pairing_conflicts
  registered_policies[]
    policy summary
    completeness
    documents by role
    has_product_explanation
    has_application
    missing_document_roles[]
  unregistered_document_sets[]
    safe insurer/product label
    primary classification
    has_product_explanation
    has_application
    components[]
  unpaired_components[]
    internal item id
    source kind and reviewed component role
    1-based page range
    processing state
    primary inventory classification
    pairing state
    duplicate state
    safe display label
```

응답은 source key, 절대경로, archive object key, 문서 본문, 정책번호, password, provider payload를 포함하지 않는다. 모든 응답은 `Cache-Control: no-store`이며 Web query cache는 메모리에서만 유지한다.

## UI behavior

기존 가족별 원장 route를 유지하고 원장 상단에 `보험·문서 현황`을 추가한다.

- 요약 카드는 `증권 근거 보험`, `증권+약관`, `증권만`, `미연결 약관`, `상품설명서`, `판독 필요`를 보여 준다.
- 등록 보험 카드는 문서 role별 chip과 component/source 건수를 표시한다. 여러 역할이 한 파일에 있으면 `묶음 문서`를 표시한다.
- 약관이 없으면 `약관 보완 필요`, 상품설명서가 없으면 중립적인 `상품설명서 없음`을 표시한다. 상품설명서는 필수가 아니므로 결함으로 단정하지 않는다.
- 미등록 document set과 미연결 component는 등록 보험 카드와 시각적으로 분리하고 `가입 확인 안 됨`을 표시한다.
- 사용자는 기존 import 화면으로 이동해 같은 가족 구성원에게 누락 문서를 추가하거나, 검토한 문서를 계약에 연결·해제할 수 있다.
- unreadable 문서는 원래의 문서 역할을 유지한 채 재업로드/OCR 보완 표시를 추가하고 상품명이나 계약을 추정하지 않는다.

## Invariants

1. 증권 근거가 없는 자료는 등록 보험 수에 포함하지 않는다.
2. 약관이나 상품설명서만으로 PolicyContract 또는 Rider를 만들지 않는다.
3. 상품설명서는 약관을 대체하지 않는다.
4. 청약서는 증권이나 현재 계약 상태 근거를 대체하지 않는다.
5. `USER_CONFIRMED` set item만 `CERTIFICATE_AND_TERMS`를 만든다.
6. source filename이나 source-level kind만으로 component role이나 보험별 묶음을 확정하지 않는다.
7. 하나의 DocumentVersion이 여러 보험·역할 component를 가질 수 있으며 물리 source 수와 component 수를 혼동하지 않는다.
8. 누락·판독 불가·상충 상태는 오류로 숨기지 않고 보완 필요 자료로 보여 준다.
9. 중복 사본 탐지는 소유권이나 FamilyMember 연결을 자동 변경하지 않는다.
10. 모든 조회·수정은 server-derived HouseholdSpace 범위를 사용한다.
11. 실제 문서와 파생 본문은 저장소, fixture, test, log에 들어가지 않는다.

## Verification

- 증권+약관, 증권만, 증권+상품설명서, 약관+상품설명서이나 증권 없음, 상품설명서만, 청약서만 합성 조합
- 한 source에 서로 다른 두 증권, 증권+약관, 상품설명서+청약서가 있는 합성 page-range component
- filename/source kind와 검수된 component role이 다른 경우
- 상품설명서와 약관의 역할 혼동 거부
- terms-only, explanation-only, application-only 자료의 PolicyContract/Rider 비게시
- 같은 content hash 중복 집계와 가족 간 자동 이동 금지
- 다른 HouseholdSpace와 다른 FamilyMember link 거부
- suggested/conflict link가 완전성에 포함되지 않음
- no-store, 메모리 전용 Web cache, app-shell-only service worker
- source path, archive key, 문서 본문, 정책번호, password의 API/log 부재
- 키보드와 작은 화면에서 요약·등록 보험·미연결 자료 구분
