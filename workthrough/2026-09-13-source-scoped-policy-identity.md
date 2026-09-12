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

구현·관련 테스트·문서를 먼저 완성한다. 변경 파일 문법·형식은 커밋 단위로 확인하고,
PR 완료 시 전체 diff와 필요한 상세 검증을 집중한다. 같은 필수 CI를 로컬에서 중복하지 않는다.
실제 자료 처리·전환·최종 수용은 해당 source/schema와 연결하고 미실행은 구분한다.

## Remaining

- nullable insurer의 API/원장/청구/조회/UI 계약 및 exact-source 게시 경계
- 원래 검수·교정·청구 보존과 신규 부모 검수 회귀
- 승인 격리 자료 처리와 #69/#70 최종 수용·전환·릴리스
