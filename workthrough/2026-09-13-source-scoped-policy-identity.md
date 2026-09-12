# Source-scoped policy identity

- 상태: in_progress
- 범위: #63/#69, B02 Task 3/4
- 구현 기준: PR #102 source `7ba874b7392a9fd7b1209352360ecfe2b008edb5`
- 목표: 미확인 보험사 필드를 출처가 확인된 가입·담보 전체의 전역 보류 조건에서 분리

## Change

명시적 retained v8 / normalization v3 / grounding v4에서만 보험사 미확인 초안을 허용한다.
정확한 원문 계약 locator·대상자·상품과 독립 검수를 요구하며 보험사는 null과 출처/이유로
보존한다. 원래 필드·응답·후보·검수 이력은 변경하지 않는다. 같은 출처와 필드/인용의 기존
검증된 v7 담보는 새 검수 요청에서 제외하고 원래 후보로 같은 계약에 게시한다.
수동 입력·과거 revision·약관의 보험사 일치 조건은 유지한다.

## Protected baseline

격리 schema 0076의 대상 작업에는 AI_VERIFIED 담보 6개와 NEEDS_REVIEW 계약 1개가 있다.
마지막 terms primary 17개는 PR #102 source의 실제 무호출 경로로 REVIEW에 보존했다.
전체 범위는 REVIEW 3개이며 이전 범위·후보·요청은 그대로다. 추가 비용 누계는 요청 5회,
추정 USD 0.1022572이며 승인 목표 USD 1 / 상한 USD 2를 유지한다.
보험사 OCR 진단과 최소 원문 영역 확인은 발급 보험사 근거를 확정하지 못했다.
진단을 보험사명·가입 확인으로 사용하지 않았고 임시 이미지·PDF는 삭제했다.

## Verification

구현·관련 테스트·문서를 완성한 뒤 전체 diff와 아래 변경 경계 검증을 진행했다.
2026-09-13, `9d407ff87bbd5558342157635902c17bc5263c26`와 후속 Worker 검토 보호·API
readiness fixture·설명 변경에서 다음 결과를 확인했다. 합성 PostgreSQL 18.6 전용 DB와
Python 3.14.7을 사용했고 실제 자료·외부 AI는 검사 입력에 포함하지 않았다.

- `pytest`의 source-scoped Worker/API 단위, certificate-title 호환, Worker readiness: 85개 성공.
- API runtime schema readiness: 15개 성공.
- `mypy apps/api/src workers/analyzer/src`: 299개 source 파일 성공.
- 새 PostgreSQL 통합: 부모만 신규 검수 후 원래 담보 6개 게시·원장/inventory/reconciliation,
  출처 없는 null 보험사 거부, 준비 전 사용자 거절·교정 보존 4개 성공.
- 처음 PG 2개는 의존 fixture 등록 누락으로 setup 오류였다. 수정 뒤 3개 성공·1개 실패였고,
  실패는 private knowledge current run 없는 샘플의 reconciliation 가정이었다. 최소 합성
  current run을 준비한 뒤 실패한 정상 case만 재실행하여 성공(9.91초)했다.
- 문서 계약 50개·저장소 안전 1171개 경로와 변경 Ruff/형식·diff 검사를 통과했다.

검토에서 준비 전에 거절·교정된 기존 담보를 새 review item으로 재승인할 수 있던 경로를
발견해 `PRIOR_CANDIDATE_REVIEW_PRESERVED`로 차단했다. 새 검수 여부와 사용자 이력을
섞지 않으며 위 PG의 거절·교정 case로 확인했다. 같은 전체 회귀·빌드를 로컬에서 반복하지
않고 필수 7개 최종 CI를 PR에 연결한다. 실제 자료 전환은 별도의 source/schema 증거로 남긴다.

## Protected source 455eee2 acceptance

승인 격리 DB만 schema 0077로 전환했고 새 backup과 기존 행/열 보존·API/Worker readiness를
확인했다(155.869초). 새 부모 1개만 독립 검수해 기존 담보 6개와 함께 게시했다. 추가 호출은
1회이며 이번 예산의 누계는 6회, USD 0.1222802이다. 나머지 혼합/약관 범위는 기존 응답
재사용·무호출 보류로 끝냈고 이전 작업·요청·후보·게시 이력을 보존했다.

인증된 실제 API의 원장·담보·inventory·reconciliation·청구 조회와 AI-off 저장 결과 경로를
확인했다(6.235초). 새 담보 6개를 로컬 안내가 소비했지만 합성 사건에 대한 관련 후보는
0개였으므로 안내 품질이나 지급액 지원 향상을 주장하지 않는다. 외부 HTTP·새 AI 작업은
0개였고 임시 합성 사건은 soft delete했다. 운영 source/schema와 runtime은 바꾸지 않았다.

## Focused currency recovery

같은 PR #103에 explicit retained v9 / normalization v4 / schema 0078을 추가한다. 기존
6개 담보는 가입금액이 있고 통화만 누락되었다. 동일 금액 행·헤더의 통화 근거로만 복구하고
변경 없는 부모와 기존 검토 이력은 재검수하지 않는다. API는 native 근거를 별도로 확인해
동일 담보의 통화·version만 갱신하고 기존 source evidence와 후보/청구/게시 이력을 유지한다.

첫 PR CI `34703159421`은 Web·안전·컨테이너 3개가 통과했다. Python은 4134 성공·1 실패:
null 보험사 허용 뒤 낡은 거부 fixture를 빈 문자열 거부로 고쳤고 관련 5개가 통과했다.
PostgreSQL은 904 성공·19 실패였으며 최초 historical fixture가 0067로 내린 상태에서 현재
API publisher를 호출해 새 열을 찾지 못한 뒤 schema 미복원으로 18개가 연쇄 실패했다.
현행 baseline 게시를 downgrade 전에 준비하고 finally에서 schema를 복원하도록 고쳤다.
실패한 결과는 보존하고 전체 로컬 suite 대신 영향을 받는 경로만 확인한다.

`ddcb87b`와 후속 Worker/fixture 변경에서 통화·기존 정규화·API 금액·readiness·청구 단위
검사는 첫 실행 118 성공·5 실패였다. 새 표 fixture의 범위가 담보 행 대신 문서 헤더를
가리킨 것이 원인으로, 실제 인용 행에 할당한 뒤 해당 모듈 12개가 통과했다(0.82초).
`mypy apps/api/src workers/analyzer/src`는 300개 파일을 통과했다. 별도 읽기 전용
source→sink 검토에서는 구체적 회귀를 발견하지 못했고 동적 검증으로 취급하지 않는다.

2026-09-13 source `ddcb87b`와 현재 `a7f3711` 변경의 관련 PostgreSQL 검사는 38개 모두
통과했다(106.45초). 명령은 전용 합성 DB에서 `pytest -m integration`에
`test_policy_currency_enrichment_integration.py`, `test_source_scoped_policy_identity_integration.py`,
`test_retained_field_proof_revision.py`, `test_retained_policy_resubmission.py`를 지정했다.
동일 ID/금액/원문 인용 보존, 6개 통화 보강, 원장 버전 충돌·부모/담보 사용자 검토 보호,
과거 schema 왕복과 기존 후보 게시 권한을 확인했다. 문서 50개·안전 1176 경로와 Ruff/diff도
통과했다. 이 결과를 필수 CI 전체 성공으로 확대하지 않는다.

## Remaining

- 최종 필수 PR CI
- 승인 격리 DB 0078 적용·변경 담보 검수와 실제 통화 소비 확인
- 실제 원문 신원/약관 연결, #69/#70 최종 수용·전환·릴리스


## Canonical citations and omitted Rider names in the same PR

PR #103의 승인 격리 DB 0078에서 같은 6개 담보의 통화를 복구했다. HTTP 2회, 기존
추가 예산 누계 8회/USD 0.1778952이며 원래 처리·후보·가입·게시·청구 이력을 보존했다.
인증된 API의 금액·통화 조회와 native 독립 금액 증명 소비 6/6을 확인했고 조회 HTTP는 0이다.

같은 원문의 실제 표제, 이름/금액/통화 6행, 각 physical page와 대상자를 직접 대조하여
한 private 계약의 동일 alias를 현재 PDF에 선언했다. 경쟁 private/native 계약은 없었으며,
옛 PDF bytes hash를 복원했다고 주장하지 않는다. 기존 manifest 적용으로 source binding
1개를 추가했지만 canonical은 예상 6개 중 2개만 생성됐다. 3개 담보는 이름 인용에 포함된
primary 표 헤더를 별도 이름 위치로 세는 코드 때문에 제외됐고, 나머지 1개는 원문 전체
이름 위치 유일성 검사를 통과하지 못해 원인을 분리한다. 최초 적용은 기대 개수 미달로
실패로 기록하고 이미 추가된 유효한 binding/2개 연결을 삭제하거나 완료로 바꾸지 않는다.

해당 private 계약 등록 담보 9개 중 native 미등록 3개도 따로 대조했다. 2개는 provider가
이름의 공백을 다르게 썼고 1개는 원래 행과 다른 이름을 반환했다. 3개 모두 이미 인용한
native 행에 원래 이름·가입금액·통화가 있고 private 인용 페이지와 일치했다. 원문 정보
부재가 아니라 처리 누락이다. private 값을 정답으로 주입하지 않고 같은 source 행에서
새 초안을 만들며 원래 응답과 제외 이력은 유지한다.

## Implementation

전체 name-field 인용으로 기존 독립 locator가 하나의 물리 위치를 입증한 뒤, 그 위치를
재현하는 primary 이름 근거를 선택한다. 머리글과 equivalent view 인용은 삭제하지 않으며
서로 다른 이름 위치는 거부한다. 명시적 v10/normalization v5/schema 0079는 이미 인용된
native 표의 명확한 이름 열과 같은 금액 proof로만 잘못된 이름을 복구한다. 기존 독립 검수
의미와 기본 처리 버전은 유지하고 새로 복구한 후보만 fresh verifier에 전달한다.

다른 한 이름 위치는 검증된 이름 열이 아닌 명시적 다른 열의 참조 문구였다. 전체 이름 셀의
native locator, 일관된 헤더와 겹치지 않는 열 기하·텍스트 대응을 확인한 경우에만 참조로
구분한다. 모호한 헤더·병합/겹친 셀·OCR·실제 별도 가입 행은 계속 거부한다. 이 조건을
증명하지 못한 원문 위치는 버리지 않으며, 참조만 있는 페이지도 유일한 가입 위치로 승인하지 않는다.

## Verification

구현·관련 테스트·문서를 완성했으며 필요한 검사만 한 번 모아 실행한다. 상세 검사와 실제
v10 복구 결과는 실행 뒤 아래에 기록한다. PR #103의 통과 결과를 이번
추가 코드의 증거로 확대하지 않는다. 실제 값·본문·Drive 식별자는 저장소에 포함하지 않는다.


`e04ff562d84ccbe5c76b36bdb239483ac95f88a5`와 후속 문서 변경에서 이름 복구·과거 정규화·
primary header/equivalent view·비이름 열/전체 원문 유일성·API/Worker readiness의 관련 단위
검사 205개가 통과했다(3.92초). 첫 선택 명령은 파일명 오타로 0개 실행 후 종료했고 정확한
파일명으로 실행했다. `mypy apps/api/src workers/analyzer/src`는 300개 파일을 통과했다.
같은 PR #103에 새로 확인한 처리 누락을 포함하며, 이전 코드의 상세 CI 완료와 별도 PR의
전체 검사를 겹쳐 기다리지 않도록 최종 수정 묶음으로 검증을 통합한다.

PostgreSQL 18.6 전용 합성 DB의 `pytest -m integration`에
`test_cited_rider_name_integration.py`, `test_source_scoped_policy_identity_integration.py`,
`test_canonical_links_integration.py`, `test_retained_field_proof_revision.py`를 지정한
관련 통합 38개가 모두 통과했다(105.87초). 원래 3개 정상 담보와 부모를 보존한 채 같은
raw 응답에서 누락 3개만 새 검수·게시하는 경로, 사용자 검토 보존, canonical source 신원,
과거 schema 왕복·원래 게시 권한을 확인했다. 문서 50개·안전 1182 경로·Ruff/diff도 통과했다.


## Protected 0079 follow-up

source `b4187b37020eb66202ea02155974652a92041371`의 0079 함수/constraint 전환은
기존 검증 backup을 재사용하고 처리·원장·binding·청구 행만 집중 대사하여 1.125초에
완료했다. 전체 원문 fingerprint·복원·백업을 다시 수행하지 않았다.

v10 첫 새 담보 1개의 검수는 MISSING_EVIDENCE로 보류됐고 원래 결과를 유지했다.
나머지 구간은 별도로 계속하여 새 담보 2개를 게시했다. 기존 6개와 모든 원장/처리/
후보/청구 이력은 보존됐으며 추가 예산 누계는 10회/USD 0.2312927이다. 계속 실행의
첫 시도는 canonical 이력 개수 전제(2→6)를 갱신하지 않아 요청 전 종료됐고, 확인된
현재 6개를 지정한 뒤 실행했다. 이 실패도 provider 호출로 집계하지 않는다.

기존 6개 canonical 연결은 모두 성공했고, 추가 2개 중 1개가 연결돼 현재 7개다. 남은
하나는 명확한 이름 셀의 여러 줄 native block 기하와 private/native 공백 차이에 막혔다.
완전한 이름 셀의 다중행 증명과 공백 변형 경쟁 검사를 보완하며 일반 raw 줄 결합은 유지한다.

보류한 첫 후보를 같은 최소화 행·헤더만으로 필드별 진단한 결과 5개 모두 SUPPORTED였고
누계 11회/USD 0.2387072였다. 이 진단에는 게시 권한이 없다. 원래 batch 검수의 구체적
내부 판단은 관찰할 수 없으므로 어느 필드가 최초 거부 원인이라고 단정하지 않는다.
명시적 v11은 관련된 문맥 전체를 유지한 필드 범위와 unknown/내부 key의 실제 의미를
전달하고, 기존 실패·승인 이력을 보존한 새 독립 검수만 허용한다.

source45의 CI `34705581442`는 취소 요청 전에 필수 7개가 모두 성공했다. PostgreSQL은
926개 성공/4169 deselected/2109.07초다. 취소되지 않은 성공 결과로 보존하고 후속 코드까지
검증됐다고 확대하지 않는다. 새 범위/기하 변경 완료 후 영향을 받는 검사만 추가한다.


## Final scoped-verifier and geometry checks

`2250870681e8e676baea18ef908c25c655041fd3`와 후속 v11/replay/runtime/PG 변경에서 관련
단위 검사는 246개 성공·15개 실패였다. 새 scope helper fixture의 필수 schema_version
누락을 고친 뒤 실패 모듈 15개가 모두 통과했다(0.83초). default prompt 보존, 구체적 거부·
잘못된 출력 유지, 재귀 각주/예외 문맥·최소화 유지와 fallback, 기존/두 줄/공백 변형/
미게시 경쟁 이름 위치, readiness를 확인했다. mypy는 API/Worker 301개 파일을 통과했다.

관련 PostgreSQL 29개 중 28개가 통과하고 새 v11 fixture 한 개는 완료된 lease를 그대로
사용해 충돌했다. 현재 job을 다시 claim하는 fixture 수정 뒤 해당 1개만 재실행하여
14.17초에 통과했다. 원래 보류 결과·요청·원장/후보 이력을 유지하면서 변경 없는 5개는
재검수하지 않고 하나만 새 검수·게시하는 실제 runner/replay/projector 경로를 검증했다.
나머지 canonical DB/과거 source 게시 권한·schema 왕복 28개는 69.81초 실행의 성공 결과를
재사용하며 전체 suite를 반복하지 않는다.
