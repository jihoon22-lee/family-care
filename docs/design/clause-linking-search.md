# Clause linking and search design

- 상태: PR #17~#18 Clause search와 Rider-Clause/CoverageRule review boundary 구현·합성 검증
  완료; private knowledge publication은 별도 append-only 경계 사용
- 적용 단계: Phase 4–5
- 선행 조건: Phase 2 candidate review (main PR #16 merged), verified PolicyContract and Rider ledger

## Scope

약관 판본을 조항 단위로 구조화하고, 계약 시점에 맞는 Clause와 실행 가능한 CoverageRule을 실제 가입 Rider에 연결한다. 검색은 조사 도구이며 검색 hit만으로 가입이나 지급 조건을 확정하지 않는다. Phase 2 candidate review는 main PR #16에 merge되었고, Phase 4 search와 Phase 5 link/rule review slice는 그 다음 경계를 설명한다. 이 문서는 CoverageRule을 평가하거나 보험금 지급을 확정하는 설계가 아니다.

## Terms structure

`TermsEdition`은 product/contract applicability, effective period, DocumentVersion과 content hash를 가진다. `Clause`는 parent-child hierarchy, type, label, normalized title/text, 1-based page range와 optional bbox Evidence를 가진다.

v0.5의 프로그램 판본은 검증된 terms component마다 등록하며 동일 파일의 여러 판본을
구분한다. `source_component_id`, 원래 물리 페이지 범위, 판본일과 metadata 원문 검증
snapshot은 불변이다. 판본일을 적용 시작일로 바꾸지 않는다. 상품명 없이 명시된 상품코드만
있으면 그 코드를 표시값으로 보존한다. 기존 전체 문서 판본에는 component를 추정하여
채우지 않으며 같은 가정/bytes의 수동 판본·삭제 이력은 자동 등록보다 우선한다.
수동/프로그램 등록은 같은 content transaction lock으로 직렬화한다.

Clause 전체 범위와 Evidence는 해당 판본 범위 안에 있어야 한다. 현재 catalog·검색·링크·
규칙 사용은 component 역할/범위 교정·제외·삭제와 문서/구성원 삭제를 확인하며 검색 제한
이전에 무효 범위를 제외한다. 이미 저장한 규칙 버전의 이력 조회는 원래 snapshot을 유지한다.
복원 실패는 transaction 안에서 끝내므로 삭제 상태/버전이 바뀌지 않는다.

신규 판본은 원문 적용 시작일과 선택적 종료일을 검증한 경우에만 기존 날짜 검증 경로를
사용한다. 누락·잘못된 날짜·상충 기간을 무제한 적용으로 해석하지 않는다. 전체 문서의
legacy NULL 날짜 의미는 호환용으로 유지한다. 판본 등록은 계약에 대한 적용 연결과 별개다.
검증된 component의 적용 연결은 아래 별도 근거 경로를 사용한다. 변경·갱신 문서의
시간별 적용은 0045~0048의 범위별 관계와 사건일 선택으로 연결하며 추가 본문 형식은 보완 중이다.

`document-metadata-v5`는 전체 페이지에 명시 목차 제목과 페이지 번호가 있는 탐색 항목만
존재하는 경우를 원문으로 확인한다. 유효한 native 줄이 대표한 단어는 전체 본문·주소·좌표를
검증한 뒤 중복에서 제외하며 숨겨진 문장·잘못된 주소·미지원 표/흐름은 목차로 확정하지 않는다.
목차의 설명서/예시 항목은 새 문서 경계가 아니며 앞에서 시작한 설명·예시 문맥도 끝내지 않는다.
목차 자체는 component/판본으로 등록하지 않는다. API는 전체 원문을 독립 검증하고 문맥 캐시는
같은 generation lineage와 판독 revision에서만 재사용한다. v1~v4 이력과 검증 의미는 유지한다.

`document-metadata-v6`는 상품설명서를 참고·참조·확인하도록 요청하는 완결된 한 줄의
안내 문장을 새 설명 문서의 시작으로 처리하지 않는다. 실제 설명서 제목·예시/인용 문맥과
이미 시작한 제한은 보존한다. native 줄은 단어 전체·주소·좌표·정수 범위의 원문 연결을
확인한 경우에만 그 단어의 중복 boundary를 제외하며 불완전한 연결은 기존 검사를 유지한다.
이 예외는 v6의 지속 문맥 검사에만 적용하고 조항 본문 판독 및 v1~v5 의미는 바꾸지 않는다.
0050은 v6 validator 이력을 허용하며 해당 제안/게시가 존재하면 downgrade를 거부한다.

### Component applicability

`0042_terms_applicability`는 계약/피보험자와 증권 Evidence의 정확한 version·페이지를 먼저
확인한다. 같은 원문 페이지가 여러 계약에 걸치면 자동 연결하지 않는다. 검증된 증권/약관의
보험사와 명시적 적용 약관/판본 참조가 모두 맞거나, 보험사·상품코드·실제 계약일과 인쇄 적용기간이
맞으면 `MATCH`다. 일반 참조·상품 표시명 유사도·판본일만으로 적용을 확정하지 않는다.
결정적인 코드/기간 모순은 `NO_MATCH`, 부족하거나 상충하는 근거는 `UNKNOWN`으로 보존한다.

원장 날짜가 과거 자동 publication의 보장 시작일에서 유래했는지 원래 publication으로 구분한다.
그 날짜를 실제 계약일로 차용하지 않으며 기존 날짜 호환 경로로도 우회하지 않는다.
기존 원장 값을 재작성하지 않는다. 원장 교정과
원문 metadata가 충돌하면 자동 연결을 보류한다. 확인된 적용은 계약의 현재 유효 상태나
해당 약관의 모든 특약 가입을 뜻하지 않는다.

API projector는 검증 revision·출처 publication·판본·사용자 선택과 현재 입력 digest를 가진
불변 평가를 저장한다. 동일 입력 재시도는 중복을 만들지 않는다. 사용자 확인된 동일 component
선택은 `USER_SELECTED`로 표시하고 다른 사용자 결정·삭제 이력은 자동 선택보다 우선한다.
서로 다른 페이지/판본이 동시에 맞으면 임의 선택하지 않는다. 현재 입력이 바뀐 평가는 현재
조회에서 제외하지만 원래 이력은 보존한다. 관련 없는 문서 추가는 기존 연결을 무효화하지 않는다.
현재 근거는 조회 범위의 계약/구성원마다 한 번 계산하고 digest로 평가 이력을 찾는다.
한 계약의 처리 실패는 평가 transaction을 rollback한 뒤 30초 재시도로 격리한다.

조항 확인·규칙 게시·현재 판정/계산은 같은 DB 적용 함수를 사용한다. 원문 대조로 적용이 확인된
component에는 표시명 완전 일치와 별도 날짜 중복 gate를 적용하지 않는다. 현재 적용 모순·
사용자 결정·오래된 평가가 있으면 이전 문자열/날짜 경로로 우회하지 않는다. 평가가 없거나
단순 metadata 부족인 기존 경로의 호환성과 과거 규칙 버전 ID 조회는 유지한다.

지원 type:

- chapter, section, article, paragraph, item
- special terms
- definition
- appendix and table

목차의 표시 page와 PDF physical page를 분리하고 Evidence는 항상 PDF 1-based physical page를 사용한다.

### Original Clause source

`0047_clause_sources`는 기존 Clause를 수정하지 않고 원문 구간 평가를 불변 이력으로
보존한다. 현재 지원하는 원문 주소는 한 페이지의 명시된 article 제목부터 다음 제목 또는
검증된 component 끝까지다. 전체 페이지 manifest·읽기 순서·표 문맥·본문과 Evidence를
대조하며, 번호만 같거나 다른 조항 본문을 가져온 경우는 `UNKNOWN`이다. 검색용 정규화는
후보 선택에만 사용하고 실제 주소는 원래 node·문자 범위·좌표·본문·경계와 해시로 남긴다.
여러 페이지에 걸친 조항과 미지원 경계는 완결된 원문으로 확정하지 않는다.

DB guard는 출처 관계·원문 주소·입력 digest와 불변성을 검사한다. current view의 `MATCH`는
감사 후보이며 조항 적용 권한이 아니다. API의 `read_verified_clause_source`가 현재 원문
전체를 다시 구성하고 저장 구간과 정확히 대조한 결과만 후속 연결에서 사용할 수 있다.
변경 가능한 Clause·component·Evidence와 그 입력 context는 한 SQL snapshot에서 읽고,
이후 조회는 불변 generation을 지정한다. 일시 변경 후 원복으로 전후 context만 같아지는
경우도 다른 시점의 근거를 섞지 않는다. JSONB context는 DB 표현 그대로 전달하여 좌표의
소수점 표기 차이가 digest를 바꾸지 않게 한다.

Clause·Evidence·component 입력 변경은 현재 평가를 제외하고 이력을 보존한다. 같은 입력
재시도는 중복을 만들지 않으며, 기록이 있는 downgrade는 거부한다. 잘못된 저장 후보는
전체 원문 재생에서 거부하며 같은 revision의 이력을 덮어쓰지 않는다. 조항별 변경 전후
대상 연결과 사건일 적용은 별도 소비 경계이며 원문 평가만으로 완료되지 않는다.

`0048_clause_change_pairs`의 `terms-change-v2`는 변경서의 이전/새 조항 라벨과 판본을
원래 Clause 및 전체 원문 평가 ID에 연결한다. 정확한 가입 담보의 승인된 후보·근거 관계를
검증한 뒤, 게시와 사건일 조회에서 변경서/대상을 다시 대조한다. 새 라벨이 명시됐지만
모호하면 이전 라벨로 대체하지 않는다. 거부된 링크는 자동 재활성화하지 않는다.
기존 v1 행의 원문 JSON은 유지하고 양쪽 대상 컬럼은 NULL로 추가한다. v2 이력이 있으면
downgrade는 거부한다. 적용 관계의 양쪽 ID와 후속 연결은 snapshot에 보존하고 인접 조항은
포함하지 않는다. 상위 조항의 변경만 확인된 하위 항은 대응이 해결될 때까지 UNKNOWN이다.
현재 원문 재검증을 통과한 관계만 조항 간 연결 경로로 사용한다. 내부 snapshot의
`scope_relation_ids`는 시행 전 관계를 포함한 조항 연결 근거이고, 사건에 적용한
`applied_relation_ids`와 구분한다. DB는 두 목록의 정확한 가정·구성원·계약·담보와
조항 연결을 검사한다. 과거 JSON에 없는 연결 근거는 빈 목록으로 읽으며 재구성하지 않는다.

## Search

v0.1은 PostgreSQL만 사용한다.

- Unicode NFC, whitespace와 punctuation normalization
- `simple` text-search configuration의 versioned normalized tokens
- normalized title에 `pg_trgm` similarity 보조; FTS가 놓친 공백 차이는 similarity `0.4` 이상일 때만 후보로 포함
- curated synonym table with version and audit
- server-derived household/date/edition/insurer/product scope filter
- simple FTS match and Evidence page projection

검색어와 result text는 일반 log에 남기지 않는다. 검색 response는 bounded excerpt와 Evidence만 반환하며, Evidence page는 항상 1-based PDF physical page다. 검색 hit는 가입 여부나 지급 금액을 확정하지 않는다. vector embedding과 별도 search service는 없다.

이 Phase 4 구현과 검증은 wholly synthetic corpus만 사용한다. 실제 보험 자료, 외부 AI, Google Drive, 운영 배포는 이 변경에서 사용하거나 검증하지 않았다.

## Rider-Clause linking

AI structurer는 Rider name, Clause title/text, terms scope를 이용해 link candidate를 만든다. verifier는 후보가 주어진 Evidence와 applicability에 맞는지만 확인한다. deterministic validator는 다음을 검사한다.

- Rider가 policy Evidence로 verified인지
- TermsEdition이 contract date에 적용되는지
- Clause가 해당 DocumentVersion에 존재하는지
- common/special terms scope가 충돌하지 않는지
- page/bbox Evidence가 extraction 범위에 있는지

link 상태는 `AI_VERIFIED`, `NEEDS_REVIEW`, `USER_CONFIRMED`를 사용한다. `confirm`과 `reject`는 예상 link version을 함께 받아 stale write를 막으며, 성공 전에 서버가 Rider·TermsEdition·Clause·모든 Evidence의 scope와 lineage를 다시 검증한다. 실패 후보는 `NEEDS_REVIEW`와 고정 reason code를 보존한다. Terms-only Rider는 link를 만들거나 가입 담보로 승격할 수 없다.

## Executable rule publication

CoverageRule candidate는 `docs/design/ai-document-analysis.md`의 allowlist DSL을 사용한다. Rule은 정확한 Clause/Policy Evidence, schema/generator/verifier version과 required/optional 성격을 가진다. DSL validator는 bounded JSON 구조, 허용된 field path/operator/unit와 Evidence index를 검증하지만 규칙을 실행하지 않는다. 임의 Python·SQL·JavaScript, import·reflection·shell, dynamic path와 raw executable string은 허용하지 않는다.

검증된 후보를 게시할 때 publisher는 저장된 후보와 연결을 한 transaction에서 lock하고, 정확한 Evidence와 현재 후보 상태를 다시 확인한 뒤 immutable `coverage_rule_versions`를 만든다. 실행 가능한 상태는 `AI_VERIFIED` 또는 `USER_CONFIRMED`뿐이다. 지원하지 않는 cross-reference, 손실된 table, 상충 definition, `NEEDS_REVIEW` 후보는 informational candidate로 남기며, 이후 engine은 이를 실행하지 않고 dependent result를 `UNKNOWN`으로 만든다. 후보의 typed field 수정은 원본을 덮어쓰지 않고 child candidate version을 생성한다.

## API boundary

- `GET /api/v1/terms-editions`
- `GET /api/v1/terms-editions/{id}/clauses`
- `POST /api/v1/clauses/search` — 검색어를 URL·access log·browser history에 남기지 않는 no-store JSON request
- `GET /api/v1/review-items?domain=rider_clause|coverage_rule&status=NEEDS_REVIEW`
- `PATCH /api/v1/review-items/{id}/fields/{field_id}` — generated typed correction; server-selected child version
- `GET /api/v1/riders/{id}/clause-links`
- `POST /api/v1/rider-clause-links/{id}/confirm|reject`
- `GET /api/v1/coverage-rules/{id}/versions`
- `POST /api/v1/coverage-rules/{id}/publish`

Search response는 Clause label, bounded excerpt, TermsEdition, Evidence를 반환한다. raw query와 전체 document text를 response error나 log에 echo하지 않는다.

`GET /api/v1/coverage-rules/{id}/versions`는 현재 aggregate `expected_version`과 저장된 immutable versions를 반환한다. `publish`는 새 DSL body를 받지 않고 `expected_version`과 이미 저장된 `version_id`만 받아 optimistic concurrency를 적용한다. Web `/app/clauses/review`는 이 계약을 사용해 link/rule 예외를 별도 대기열로 보여주고, Evidence를 확인한 뒤에만 확인·제외·게시를 활성화한다. raw DSL editor는 제공하지 않는다.

모든 TermsEdition/Clause search route는 server-derived `HouseholdScope`를 사용한다. 기본 resolver는 인증 연결 전까지 `401 AUTHENTICATION_REQUIRED`로 fail-closed하며, 현재 실제 인증된 route 사용은 제공하지 않는다. 합성 테스트에서만 resolver를 주입해 scope를 검증한다.

## Failure behavior

- wrong terms edition candidate는 publish하지 않고 reason code를 남긴다.
- table/appendix Evidence가 끊겼으면 해당 rule만 `NEEDS_REVIEW`다.
- optimistic version이 오래되면 `VERSION_CONFLICT`로 거부하고 사용자가 편집 중인 typed draft는 화면에 유지한다.
- unsupported DSL은 저장 가능한 검토 후보로 남을 수 있지만 게시·실행 버튼을 활성화하지 않는다.
- v0.1에는 별도 live search-index rebuild endpoint가 없다. 초기 normalization version은 DB constraint로 고정한다.
- 향후 normalization version bump는 PostgreSQL transaction migration에서 old committed state를 commit 전까지 유지하고, commit 시 새 version으로 원자적으로 전환한다.
- 앱/DB mismatch나 stale hit는 `SEARCH_INDEX_VERSION_MISMATCH`로 명시적으로 실패하며 silent fallback하지 않는다.
- synonym conflict도 silent fallback하지 않는다.
- 한 Clause parse failure가 다른 searchable Clause를 제거하지 않는다.

## Tests

- hierarchy, appendix/table and physical-page Evidence
- contract date and TermsEdition boundaries
- same Rider name with different definitions
- Unicode/whitespace normalization and Korean synthetic queries
- full-text/trigram ranking baseline with wholly synthetic corpus
- search result outside Rider/date scope exclusion
- AI verifier invented Evidence rejection
- unsupported DSL and cross-reference remain non-executable
- link/rule version publish and stale result handling
- query/document text absence from logs
- separate Web review queues, bounded Evidence disclosure, typed child correction, dialog focus, and no raw DSL/provider/path output
- synthetic 320px Playwright publication flow with no local/session storage writes
