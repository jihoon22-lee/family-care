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
