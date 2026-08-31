# Policy ledger design

- 상태: 원장·candidate review 구현·합성 검증 완료; 보호된 전체 catalog는 별도 immutable
  private-knowledge layer로 수용, 남은 암호/legacy source와 최신 계약 상태는 `UNKNOWN` 유지
- 적용 단계: Phase 2
- 선행 조건: Phase 1 DocumentVersion과 Evidence contract

## Scope

보험 대상 가족과 실제 계약·Rider를 증권 Evidence로 관리하는 원장을 정의한다. AI가 검증한 후보는 즉시 사용할 수 있고 예외만 사용자에게 노출한다. 약관에 존재한다는 사실만으로 가입 Rider를 만들지 않는다.

가족별 증권·약관·상품설명서 보유 현황과 누락 문서 표시는 `docs/design/insurance-document-inventory.md`가 소유한다. 이 읽기 모델은 PolicyContract 원장을 확장하지만 증권 근거가 없는 문서를 원장 계약으로 승격하지 않는다.

## Components

### Family registry

`FamilyMember`는 표시 이름, 내부 별칭, HouseholdSpace, soft-delete version을 가진다. AppUser와 직접 상속·대체하지 않는다.

### Policy aggregate

하나의 PolicyContract aggregate는 다음을 포함한다.

- insurer와 product display/normalized value
- contract, coverage start/end dates
- current status와 status Evidence
- source policy DocumentVersion
- 선택적 reviewed policy component. 묶음 PDF라면 PolicyContract Evidence page가 그 component page range 안에 있어야 함
- PolicyParty role과 effective period
- 실제 가입 Rider
- optimistic concurrency version

### Rider

Rider는 원문 명칭과 정규화 key, 정액·실손 유형, 가입금액·통화, 납입·보장기간, 갱신 여부, current status, Evidence를 가진다. Evidence는 policy DocumentVersion, 1-based page와 optional bbox를 필수 lineage로 사용한다.

### Candidate review

AI candidate와 사용자 수정은 `AnalysisCandidateVersion`으로 보존한다. `AI_VERIFIED`는 즉시 current projection에 publish할 수 있고 `NEEDS_REVIEW`는 exception queue에만 나타난다. `USER_CONFIRMED`는 사용자가 Evidence를 확인한 새 published version이다.

원장 projection은 candidate의 임의 첫 Evidence를 재사용하지 않는다. Policy source는 `product_name` 뒤 `insurer`, Rider source는 `rider_name` 뒤 `rider_key` 순서의 field-specific policy Evidence를 결정론적으로 고르며, 같은 field에 여러 근거가 있으면 가장 작은 Evidence UUID를 사용한다. Policy/Rider status는 각각 정확한 `policy_status`/`rider_status` Evidence가 있을 때만 확정하고, 값만 있으나 그 근거가 없으면 `unknown`과 null status Evidence로 저장한다.

## API boundary

대표 resource contract:

- `GET/POST /api/v1/family-members`
- `GET/PATCH/DELETE /api/v1/family-members/{id}`
- `GET/POST /api/v1/policies`
- `GET/PATCH/DELETE /api/v1/policies/{id}`
- `GET /api/v1/policies/{id}/riders`
- `PATCH /api/v1/policies/{id}/candidate-fields/{field_id}`
- `PATCH /api/v1/review-items/{review_item_id}/candidate-fields/{field_id}`: 여러 후보가 같은 계약에 연결되어도 Web 검수에서 정확한 후보 하나를 수정
- `GET /api/v1/review-items?domain=policy`
- `POST /api/v1/review-items/{id}/confirm|reject`

서버는 session에서 HouseholdSpace와 mutation actor를 결정한다. 인증 context의 HouseholdSpace가 active scope와 일치하지 않거나 actor UUID를 얻을 수 없으면 user correction·confirm·reject를 실행하지 않는다. create/update는 expected version을 받고 stale write를 `409 VERSION_CONFLICT`로 거부한다. response는 실제 source path, archive object key, 증권번호를 포함하지 않는다.

## Data flow

```text
policy extraction
  -> AI structurer candidate
  -> independent verifier
  -> deterministic Evidence/schema validation
  -> AI_VERIFIED or NEEDS_REVIEW
  -> publish current ledger projection
  -> optional user correction as new version
```

새 분석 version은 기존 current projection을 즉시 교체하지 않는다. 전체 aggregate invariant를 만족한 뒤 transaction으로 publish한다.

## Invariants

1. verified Rider는 policy DocumentVersion의 Evidence를 가진다.
2. Terms DocumentVersion만 참조하는 candidate는 Rider가 될 수 없다.
3. renewal Rider는 사건 시점의 최신 상태 Evidence가 없으면 active로 확정되지 않는다.
4. Party role은 FamilyMember와 기간을 가지며 AppUser identity를 재사용하지 않는다.
5. user correction은 raw extraction과 AI candidate를 overwrite하지 않는다.
6. deleted aggregate는 기본 query에서 제외되고 trash endpoint에서만 보인다.
7. 모든 record access는 server-derived HouseholdSpace scope를 사용한다.
8. user mutation audit actor는 인증 context에서만 오며 nullable 또는 client-provided actor를 허용하지 않는다.
9. non-unknown Policy/Rider status에는 같은 candidate field의 status Evidence가 필요하다.

## Failure behavior

- Evidence가 없거나 다른 document version을 가리키면 candidate를 publish하지 않는다.
- 같은 policy의 date/status가 충돌하면 임의 최신값을 고르지 않고 review item을 만든다.
- 일부 Rider 실패는 확인된 다른 Rider의 원장 publish를 막지 않되 policy를 partial 상태로 표시한다.
- duplicate content hash는 기존 DocumentVersion을 사용하고 새 candidate analysis version만 만들 수 있다.
- stale edit, duplicate restore, already-deleted request는 안정적인 conflict code를 반환한다.

## Tests

- AppUser/FamilyMember lifecycle separation
- policyholder, primary/additional insured, beneficiary periods
- Terms-only Rider rejection
- Evidence required for AI/user verified Rider
- renewal status missing/expired/conflicting cases
- AI_VERIFIED immediate publish and NEEDS_REVIEW exception queue
- user correction version/audit and raw candidate preservation
- field-specific source/status Evidence selection and missing-status fallback to `unknown`
- authenticated mutation actor persistence and HouseholdSpace mismatch rejection
- HouseholdSpace object-scope success/denial
- optimistic concurrency, soft delete, trash and restore
- response/log absence of source path, policy number and document text
