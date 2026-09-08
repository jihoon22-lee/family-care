# B03 detailed terms knowledge

[WP04 #64](https://github.com/jihoon22-lee/family-care/issues/64)의 약관 원문→의미 지식→기존
DSL 경로를 구현 중이다. B02 기반은 PR #76 merge `bc727928a0030332452fb7b3bf639beba6b9e60e`다.
원문 검증/저장과 로컬 영역 처리, Worker 입력 경계 및 legacy adapter를 구현했다.
Worker 작업 큐 연결·현재 안내 소비자 인계·전체 수용은 남았다.

## Changes

- `terms-semantic-knowledge.v1.schema.json`에서 API/Worker의 strict 소비자를 생성한다.
  조건·분류 판본·정액/비율/일당·공제·한도·각주와 DEPENDS_ON/OVERRIDES를 보존한다.
  compiler는 bounded closure와 단위/통화/반올림을 검사하고 기존 data-only DSL을 만든다.
  누락·순환·판본 불일치·미지원 계산은 영향 범위에 남긴다. 횟수 미만은 기존 `count_before`의
  실제 의미(이상)를 `not`으로 반전하며 누락 횟수를 0으로 바꾸지 않는다.
- source layout은 원래 manifest/active layer/native lineage/쪽수·주소와 연속 구역을 검사한다.
  명시적 별표·각주·종료된 예시를 보존하며 예시/표제의 처리 완료를 규칙 권위로 바꾸지 않는다.
  원문 문장 전체의 의미·수치 역할·참조 증거를 독립 검사한다. 지급일수 조정→총액→공제와
  0 하한→금액 한도→총액 반올림 순서를 원문에서 입증하지 못하면 계산을 제한한다.
- `0052_terms_semantic_knowledge`는 scoped source input, 후보, 검증 결과와 root 이력을
  append-only로 저장한다. 원문·판본·추출과 취소 상태를 잠금 안에서 재조회한다. 현재 reader는
  원문/규칙을 다시 검증하며 저장된 VERIFIED/executable 선언을 권위로 쓰지 않는다. 최근
  시도 조회와 마지막 정상 root 조회를 나누어 다수 실패 후에도 정상 결과를 보존한다.
- 정액 상수 산식은 입력 필드가 없어도 저장·반환할 수 있다. 실제로 참조한 입력과 선언의
  일치는 계속 검사한다. 검토 화면은 이 경우 ‘추가 입력 없음’을 표시한다. neutral snapshot
  계약과 OpenAPI를 재생성했으며 새 외부 HTTP endpoint는 아직 등록하지 않았다.

## Verification to date

2026-09-08 21:03~21:08 KST, 소스 `a303fa5`에 위 DSL·원문 의미/검증·0052/repository·
계약/UI와 관련 테스트/문서 변경을 더한 상태에서 실행했다. root별 마지막 정상 조회·취소,
malformed audit row와 위조 상태를 포함한 PostgreSQL 검사는 전용 합성 DB와 파괴적 검사 guard,
`TMPDIR=/tmp`를 사용했다. 운영 DB는 쓰지 않았다.

- 기본 `TMPDIR=/tmp uv run pytest -q`: **2,712 passed / 528 deselected / 3 subtests** (23.16초).
  이후 추가한 audit-row 테스트 2개는 integration 범위다. 최초 전체 실행은 새 인용 ID 회귀
  1개 실패/2,711개 통과였으며 수정 뒤 위 결과를 얻었다.
- `pytest apps/api/tests/test_terms_knowledge_repository.py -m integration -q`: **11 passed**
  (13.66초). 같은 합성 약관의 원문→compiler→불변 저장→재조회에서 100×(5−2)=300,
  잘못된 각주 후 무관 root·기존 정상 결과 보존, 65회 실패 이후 조회, 가정 거부·입력 변경,
  취소 generation, 중복 동시 처리와 이력 변경 거부를 확인했다. 초기 fixture 오류 7개 실패는
  수정했고 취소/마지막 정상 조회 결함은 RED 2개 후 수정했다.
- source/semantic/DSL 관련 pure **123 passed** (1.01초) 후 설명의 가짜 citation ID 제거를
  RED로 추가했고 최종 전체 suite에 포함했다. compiler의 독립 변경은 순서대로
  `ae41f58`/`bc398c0`/`a303fa5`, 원문 layout은 `aaece9f`로 통합했다.
- 0052 upgrade 및 빈 합성 DB의 0051 downgrade→head upgrade, generated contract 검사,
  관련 Ruff lint/format와 terms 모듈 mypy **6 files**가 통과했다.
- Web 검토 component는 새 표시가 없어서 RED 1개/기존 10개 통과를 확인한 뒤
  **11 passed** (1.58초)를 얻었다. 이는 이번 변경의 전체 Web/build/E2E 증거가 아니다.

기본 Python 성공은 전체 PostgreSQL·Web·브라우저·Worker 통합 성공을 의미하지 않는다.
전체 필수 검사, 비동기 source range/후보 경로, 원래/새 원문 각주 변경의 통합 변경 영향,
현재 질의/legacy adapter와 과거 snapshot 보존의 수용을 이어서 수행한다.

### Incremental source processing

- 기반 head `805ad9c`의 [CI 34224618429](https://github.com/jihoon22-lee/family-care/actions/runs/34224618429)는
  7개 required checks가 통과했다. PostgreSQL job은 12분 49초였다. 아래 후속 변경의 CI 증거를
  대신하지 않는다.
- `fa12034`는 원문 영역 전체를 로컬 후보 계획으로 만들고 Article별로 bounded closure를
  나눈다. 별표/각주의 공유 identity와 새 source proof를 구별하고 누락·순환·미지원·한도 초과는
  명시적인 미해결 결과로 보존한다. 독립 작업의 최소 RED 후 pure 13개(0.37초), 관련 Ruff와
  module mypy가 통과했다.
- 같은 중립 schema에 `SemanticWorkEnvelope`/`SemanticWorkRegion`을 추가하고 소비자를 생성했다.
  이는 내부 입력 계약이며 provider 전송이나 Worker 실행 완료를 뜻하지 않는다.
  2026-09-08 21:47 KST, `fa12034` + 해당 schema/생성 소비자/README 변경으로
  `TMPDIR=/tmp uv run python scripts/check_contracts.py`가 통과했다.
- `0053`은 전체 원문 manifest, graph별 불변 처리 기록, 완료와 실패/재시도 시간을 저장한다.
  중단·동시 실행은 이미 저장한 결과를 재사용하고, 원문 변경과 의미 검증기 revision 변경은
  새 처리로 구분한다. 동일 후보도 새 source proof를 추가할 수 있으며 기존 proof는 보존한다.
  원문 머리말이 미지원이면 계산 성공과 별개로 전체 상태는 PARTIAL이다. 완료 조회는 처리
  기록이 가리키는 publication/proof/root를 다시 검사한다. 실패 문서는 60초간 대기하므로
  뒤의 문서를 막지 않고, 원문이 바뀌면 과거 실패의 대기를 적용하지 않는다.
- 원래 호출자의 transaction에서 읽는 root 페이지는 현재 부분 설명과 마지막 정상 계산을
  함께 제공한다. 불완전한 audit key도 cursor를 유지한다. JSON은 건별로 읽고 페이지의
  누적 replay를 32MiB로 제한하며, 한도 부족을 명시해 조용히 완료로 취급하지 않는다.
- legacy adapter (`1ee3fea`, `18d1cf8`)는 기존 section/review/fact/clause와 실제 scoped ID를
  불변 JSON으로 보존한다. 가짜 원문 span·새 USER_CONFIRMED·실행 규칙을 만들지 않는다.
  누락/불일치 참조와 재처리 필요를 유지하고 전체 보존 JSON은 16MiB로 제한한다.
  독립 작업에서 RED 후 전용 **31개**와 Ruff/module mypy가 통과했다.
- Worker 경계 (`f51f0aa`, `56b1f6f`)는 제한된 원문만 alias로 전달하고, 응답의 원문 주소·인용·
  구역 처리를 검사한 뒤 현재 원문 ID로 복원한다. 다른 Article의 모델 node ID 충돌을 막으며
  전송하지 않은 구역은 미해결로 남긴다. cache key는 실제 prompt·schema·model·전송 내용을
  포함한다. 독립 작업의 RED 후 합성 **40개**, 관련 Ruff/module mypy가 통과했다.
  실제 provider 호출이나 작업 큐 연결의 증거는 아니다.

2026-09-08 21:48~22:15 KST의 후속 검증:

- `18d1cf8` + 당시 projector/0053/repository/소비자 변경의 기본 Python은
  **2,760 passed / 543 deselected / 3 subtests** (24.44초)였다. 이후 Worker 경계와
  재시도/읽기 예산 보완이 추가되었으므로 이를 최종 전체 suite 결과로 사용하지 않는다.
- `56b1f6f` + 위 0053/projector/repository/소비자 및 합성 fixture 변경에서
  `pytest apps/api/tests/test_terms_semantic_projector.py apps/api/tests/test_terms_knowledge_repository.py
  -m integration -q`는 **26 passed** (44.33초). 65개 실제 합성 Article의 원문부터 66개
  graph(미지원 머리말 포함) 저장과 모든 65개 계산의 페이지 조회, 의미 revision 재검증,
  scope/취소/재개/동시성/불변성, 위조 처리 기록, 프로세스 간 실패 대기와 읽기 한도를 포함한다.
  초기 미지원 머리말의 완료 기대와 두 번째 합성 문서의 ID/hash fixture 오류는 수정했다.
- 소비자 연결은 RED 1개 후 **10 passed** (2.83초), 완료와 실행 가능성 분리는 RED 2개 후
  `test_terms_processing_accounting.py` **3 passed** (0.54초)였다. 페이지·재시도 검토 지적은
  별도 RED 후 관련 PostgreSQL **3 passed** (5.08초), 위 26개 전체에 포함했다.
- 0053의 빈 합성 DB downgrade/upgrade가 통과했다. 운영 migration은 실행하지 않았다.

### Original tables and queued work

- `1176a77`은 실제 TABLE_ROW·헤더 연결·열 index를 검증한 단일 Code 열을 하나의 복수 span
  classification 근거로 묶는다. 원문 선언·헤더·모든 행이 필요하며 산문/추가 열/잘못된 연결은
  같은 권위를 얻지 않는다. 의미 검증기는 v2다. 독립 작업의 집중 pure **101개**, 전용 합성
  DB(0053)의 통합 **2개**(3.98초), Ruff/mypy가 통과했다. 새 불변 문서·generation의 표와
  각주 변경 후 400, 이전 저장 결과 300 및 무관한 담보의 semantic hash 보존을 확인했다.
  표 문법은 64행까지 보존하지만 기존 인용 한도는 유지했다. 대표 일당 경로는 N+7≤16으로
  9행까지 금액을 계산하며 그 이상은 원문을 잘라서 계산하지 않는다.
- `45e026e`는 Worker 입력에서 전체 전달 구역의 개인정보 표시 범위를 먼저 찾고 인용별로
  최소화한다. 기존 SourceWindowMinimizer와 active household 이름/별칭 전달용 인자를
  재사용한다. 기본 호출도 형식/label 개인정보를 가리고, 실제 원문은 비공개 복원에만 쓴다.
  독립 RED 후 합성 **51개**(1.00초), Ruff/mypy가 통과했다. 실제 provider 호출은 없었다.
- `0054`는 source/privacy revision에 묶인 작업, 영역별 시도, 후보 projection 기록을 정의한다.
  요청 예약 표는 policy 또는 terms job 중 하나만 소유하도록 확장해 두 경로의 문서/일일
  소비량을 공유한다. API 작업 준비는 알려진 로컬 규칙을 다시 예약하지 않고, 미지원 원문
  문맥은 별도 기록한다. primary와 필요한 원문 참조만 bounded envelope로 만든다.
  Worker claim/budget/소비자 연결은 다음 구현 범위다.
- 2026-09-08 22:48 KST, `1176a77` + 0054/work_repository/전용 테스트에서
  `pytest apps/api/tests/test_terms_semantic_work.py workers/analyzer/tests/test_policy_request_budget.py
  -m integration -q` **12 passed** (7.13초), 관련 Ruff/mypy와 빈 합성 DB의 0053
  downgrade→0054 upgrade가 통과했다. 신규 API 준비 4개와 기존 policy 예산 8개의 증거이며,
  아직 새 Worker 예산·lease 경로의 검증은 아니다.
- 0054의 privacy 잠금은 가정 FOR UPDATE와 삭제 상태를 포함한 전체 구성원 FOR SHARE를
  사용한다. 다른 구성원의 수정·복원·신규 생성을 대상으로 RED를 확인한 뒤 PostgreSQL
  **3개 통과**(3.11초)를 얻었다. 실제 요청 전 짧은 DB transaction 안에서 같은 privacy 집합을
  확인하도록 보완했으며 네트워크 호출 중 DB 잠금을 유지하는 방식은 사용하지 않는다.

- API inbox는 완료 후보의 원래 envelope·source·privacy를 재검증한 뒤 같은 transaction에서
  원문 의미 검증과 compilation을 수행한다. 새 verifier revision은 기존 후보를 재사용하며,
  범위 밖 인용은 REJECTED, 오래된 source/privacy는 STALE로 불변 기록한다.
  직접 publication도 가정→판본 순서로 잠가 inbox와의 교착을 방지한다.
- 2026-09-08 23:22 KST, `6114062` + repository/work_repository/전용 테스트에서
  `TMPDIR=/tmp uv run pytest apps/api/tests/test_terms_semantic_work.py
  apps/api/tests/test_terms_knowledge_repository.py -m integration -q`는 전용 합성 DB와
  destructive-test guard 설정으로 **28 passed** (39.61초). 동시 inbox/direct publication,
  원문 변경·privacy 변경·변조 인용·새 의미 revision의 기존 후보 재사용을 포함한다.
  관련 Ruff와 두 repository의 mypy도 통과했다. provider 예약은 0건이다.

실제 자료·식별자·provider 결과를 코드/fixture/로그에 넣지 않았다. 이 B03 단계에서 실제
자료 접근·OpenAI 호출·운영 쓰기·태그·배포·실제 Windows/모바일 검증은 수행하지 않았다.
B02의 보호된 수용과 전체 기존 자료 cutover는 #62/#63/#69의 열린 범위로 유지한다.
