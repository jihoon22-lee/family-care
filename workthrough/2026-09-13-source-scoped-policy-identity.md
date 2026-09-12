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

## Remaining

- 최종 필수 PR CI와 승인 격리 DB의 schema 0077 전환·실제 부모 검수/게시
- 승인 격리 자료 처리와 #69/#70 최종 수용·전환·릴리스
