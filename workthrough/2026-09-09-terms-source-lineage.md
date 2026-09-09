# 약관 원문 줄 근거의 일관성

원문 단어에서 줄을 만들 때는 첫 단어의 높이/수직 위치를 기준으로 삼았지만 약관 근거
검증기는 직전 단어를 기준으로 삼았다. 그 결과 생성기가 만든 정상 줄과 실제 지급 조항을
버리고, 반대로 생성기가 허용하지 않는 누적 위치 이동을 받아들일 수 있었다.

Worker 검증을 생성기의 기준과 일치시키되 직전 단어와의 순서·수평 간격, 전체 문자 span,
source layer와 합친 bbox 검사는 유지한다. 새 metadata v9/0067은 동일 원문 세대에 새
증거를 추가하고 v1–v8 기록을 보존한다. API의 독립 검증과 후속 약관 source reader에도
revision별 기준을 전달한다. 과거 검증 결과를 새 기준으로 다시 쓰거나
사용자 편집/약관 연결의 supersession 보호를 우회하지 않는다.

- Worker `b6f9cfb`(통합 `5da1ffd`): 실패 6건/통과 5건을 먼저 확인한 뒤 11건 통과.
  관련 약관/줄 검사 81건 통과(1.02초), scoped mypy·Ruff·안전·diff 검사 통과.
- v9가 없는 동일 세대의 v8 재처리에서 새 작업을 만들지 못하는 PostgreSQL 실패 1건
  (2.36초)을 먼저 확인했다. 새 proposal/publication append, 동일 원문 세대와 과거 이력
  보존, 반복 실행과 v9 이력 downgrade 거부를 통합 완료 후 검증한다.
- 공유 계약의 revision enum과 range evidence 요구를 갱신하고 공식 generator로
  API/Worker 소비자를 재생성했다. API/Worker runtime fence는 0067을 요구한다.
- 저장된 JSON 좌표 배열이 줄의 tuple bbox와 다르다고 판정되는 추가 문제도 확인했다.
  실제 metadata `_pages` 복원 경로를 쓰는 합성 JSON 왕복으로 지원 조항이 AMBIGUOUS가
  되는 실패를 재현했고, 좌표 값으로 비교하도록 정규화했다. 초기 fixture import 오류는
  이 기능 실패 확인 전에 수정했다. 원문/IR 값과 identity는 변경하지 않는다.

PR84 개인정보 변경 `7372592`·`caf2f2f`를 PR85 기준 `f880ab5` 위에 통합했다.
최소화 v3·retained 처리 v2와 `0066_provider_privacy_revision`을 보존하고 약관 migration을
`0067_metadata_lineage`로 옮겨 `0065 → 0066 privacy → 0067 metadata` 순서로 연결했다.
metadata/검증기 v9는 유지한다. readiness fixture는 과거 0066 privacy와 미래 합성 0068을
거부하며, metadata 이력 downgrade 테스트는 바로 앞선 0066 privacy를 대상으로 한다.

증권 범위의 실제 제한 요청은 HTTP 200이었지만 한 범위/후보 쌍의 primary 인용이
빠져 전체 응답이 REVIEW로 보존됐다. 이 결과를 후보 게시 성공으로 집계하지 않는다.
`764a195`(통합 `3efa54c`)는 각 쌍의 exact primary를 실제 근거가 있는 field에 인용하도록
prompt를 명확히 한다. 다중 범위 배정과 반환 전 self-check도 명시했다. validator/schema는
유지하며, 새 지시는 요청 fingerprint에 포함되어 과거 prompt의 응답 캐시와 구분된다.
추가 4개 다중-primary 경계 사례는 기존 validator에서도 통과하므로 RED로 기록하지 않는다.
변경 전 15건/변경 후 관련 23건과 Ruff를 통과했다. 실제 prompt 효과는 별도 수용 대상이다.

## 통합 검증

2026-09-09, 전용 합성 PostgreSQL·Python 3.14 환경에서 수행했다.

- `413a3ed`의 문서 50개·안전 1095개 경로와 `corepack pnpm web:check`:
  Web 238건/31개 파일(50.11초), format/lint/type/build 통과. 이후 Web 입력 변경은 없다.
- 최초 전체 Python은 과거 revision을 암묵적으로 가정하던 테스트 2건 실패/3709건 통과였다.
  navigation context는 v8/v9 각각의 명시 revision으로 확인하고, body의 현재 producer는
  v9로 검증한다. 관련 44건 통과(4.43초) 후 전체 검사를 다시 실행했다.
- 0066→0067 합성 DB upgrade 통과. 최초 PG 명령의 잘못된 파일명은 수집 전 거부되어
  결과가 없다. 수정된 실행은 과거 v8 producer를 고정하지 않은 navigation 3건 실패/
  18건 통과였다. 해당 역사적 테스트에 v8 producer를 명시한 뒤 아래 6개 모듈의
  PostgreSQL 21건을 함께 통과했다(42.47초): `test_metadata_lineage_revision`,
  `test_terms_lineage_publication`, `test_metadata_prefix_publication`,
  `test_terms_semantic_projector`, `test_metadata_navigation_publication`,
  `test_retained_privacy_revision`. v9 append/이력 보존·반복 처리·downgrade 거부와
  native IR→metadata→판본→의미/조항 source 읽기를 포함한다.
- `57a4a76` clean source의 전체 Python/계약 검사: Ruff format 879개 파일/check,
  mypy 354개 파일, 기본 pytest 3716건/3 subtests(33.99초; integration 803건 제외),
  계약·컨테이너 정적 정책·workflow 정책·diff를 통과했다. 명령은
  [필수 완료 검사](../docs/design/test-strategy.md#required-completion-commands)를 사용했다.
  전체 PostgreSQL·빈 DB 왕복·이미지 빌드는 최신 CI에서 추가 확인한다.
- 독립 읽기 전용 리뷰에서 Worker 생성기 기준, v1–v8 API 재현, immutable publication의
  revision 전달과 household 경계, downgrade guard, prompt/cache 경계를 확인했다.
  수정할 finding은 없었으며 동적 검증이나 실제 자료 검토로 표현하지 않는다.

보호된 0067 재처리·실제 약관 지원 범위·최종 전환 수용과 릴리스는 진행 중이다.
보호 진단의 실제 본문·개인정보·수치는 저장소 밖에만 보존한다.

## 보존된 결과와 별도 field 증명 재처리

후속 대표 입력도 한 계약 후보의 primary 인용 누락으로 REVIEW에 보존됐다. 외부 요청을
추가하기 전에 동일 응답을 프로그램 검사로 재생했으며, 계약의 이름 필드는 증명되지만
선택 날짜 필드는 현재 증명 계약을 충족하지 못함을 확인했다. 값·본문·개수·식별자는
비공개 기록에 보존한다. `262d027`은 각 field의 primary 증명과 선택 날짜의 지원 label을
명시한다. 계약 개시일과 별도 실제 계약체결일을 구분하며, 기존 날짜 origin 호환 계약과
validator를 변경하지 않는다. 이 지시의 실제 성공을 아직 주장하지 않는다.

`b7cb89e`(통합 `1ed94e1`)의 0068/retained v3는 같은 원문 generation에 별도 작업을
추가하고 이전 v2의 REVIEW·계획·provider 이력과 유효한 게시를 보존한다. DB의 허용
revision 비교만 v2/v3로 확장하며 v1은 계속 차단한다. v3 이력이 있으면 downgrade를
거부하고, 없으면 0067의 v2-only 함수와 privacy 계약을 정확히 복원한다. 0067 파일은
변경하지 않았다. 새 enqueue/target은 기본 v3이며 자동 예약은 추가하지 않는다.

- v3 부재 실패 1건(2.03초)을 먼저 확인했다. 새/역사 재처리 PostgreSQL 8건 통과
  (최종 15.07초), resubmission/publication 확장 37건(44.19초), readiness 24건
  (1.05초), scoped Ruff/mypy·안전·Git 규칙을 통과했다. 테스트용 0068만 적용했다.
- 날짜 fixture의 CASCADE 실패 수정 후 `8c8ae2c`에서 날짜 origin/native enrollment
  PostgreSQL 37건(55.56초), 앞서 0066 checkout에서 0067 DB를 해석하지 못했던
  `test_enrollment_without_classification_retains_its_document_amount`도 1건(2.66초)
  통과했다. 제품 잠금·게시 가드와 기존 판정 assertions는 유지했다.
- `1ed94e1` clean source의 전체 Python/계약 재검증: Ruff 881개 파일/check, mypy
  354개 파일, 기본 pytest 3716건/3 subtests(37.39초; integration 809건 제외),
  계약·컨테이너/워크플로 정적 정책·diff 통과. Web 입력은 앞선 238건/build와 같다.

PR84의 이전 전체 PG CI 실패와 수정은 해당 workthrough에 보존했다. 현재 두 PR의
최신 전체 CI와 보호된 0068 적용·재구성·제한된 v3 실행은 별도 수용으로 진행한다.

`e52e932`의 후속 순서 검증에서 consumers→date-origin과 앞서 환경 차이로 실행하지
못했던 enrollment migration 회귀를 함께 실행해 PostgreSQL 5건을 통과했다(7.92초).
과거 metadata 정리는 검증된 전용 test DB에서 현재 job 생성 전에 수행하며 제품 가드나
정확한 게시 건수 assertion을 변경하지 않는다. 두 PR의 CI 실패 이력은 보존한다.
