# Source processing recovery

- 상태: in_progress
- 범위: #63/#69, B02 Task 3/4
- 기준: PR #101 merge `260600abc574e6afcc0e487901391ea73b6bba64`
- 기존 검증: PR CI 34692970673 필수 7/7, PostgreSQL 911개 성공

## Change

policy primary가 없는 범위를 AI로 다시 처리해도 가입 게시의 프로그램 근거 조건을 충족하지
못한다. 이 범위는 고정된 로컬 처리 이유와 함께 REVIEW로 보존하고 다음 범위로 진행한다.
미가입이나 원문 해석 완료를 확정하지 않고 과거 요청·응답·receipt·후보를 유지한다.
policy가 포함된 혼합 범위는 기존 구조화·검수·근거/대상자 검증을 그대로 사용한다.

예산 승인이나 상태 질문 뒤 중간 보고만 남기고 개발을 종료하지 않도록 작업 지침도 보완한다.
문법·형식·계획 범위는 커밋 단위, 상세 회귀와 전체 diff는 PR 작업 완료 시점에 확인한다.

## Protected processing already performed

승인된 추가 비용은 목표 USD 1 / 상한 USD 2다. 실제 요청 전에 텍스트 입력·출력 한도와
Standard 단가로 최대 비용을 예약하고, 사용량을 확인한 응답만 정산하는 비공개 기록을 사용한다.
응답 미확인은 예약액을 유지하며 기록의 누락·손상으로 예산을 초기화하지 않는다.

`260600a`의 격리 schema 0076에서 필요한 최소 필드 진단, 미처리 혼합 범위의 구조화·독립 검수,
기존 policy 텍스트 전체와 수정한 OCR 행의 발급 보험사 근거 탐색을 진행했다. 추가 요청
5회, 누계 추정 USD 0.1022572이다. 새 담보 후보 3개를 보존하여 해당 작업의 AI_VERIFIED는 6개지만,
계약 1개의 발급 보험사 근거는 보류되어 신규 원장 반영은 0이다. 기존 응답·완료 범위·후보를
보존했다. 뒤이어 source `7ba874b7392a9fd7b1209352360ecfe2b008edb5`의 무호출 경로로
마지막 순수 terms primary 17개를 REVIEW에 보존해 범위는 REVIEW 3개가 됐다.
이전 범위·후보·요청과 예산 기록은 그대로이며 운영 DB·배포·태그는 변경하지 않았다.

첫 표지 OCR의 단어 단위 집계와 행 묶음 집계는 구분한다. 행 묶음의 좌표 해석 오류를
기록·수정했으며 해당 행 집계는 근거로 사용하지 않는다. 알려진 회사 caption을 찾지 못한
사실을 실제 보험사 정보가 없다는 증명으로 확대하지 않는다. 추가 OCR 판독의 원문·개인 값과
최종 출처 증거는 저장소 밖에 유지한다.

## Verification

2026-09-13, `ad0b497d9e4402e7f8143dfdd45f4286468a784e`와 이 PR의 테스트·문서 변경에서
변경 Python 파일 Ruff 검사와 `git diff --check`를 통과했다. 범위별 호출·비용 0,
기존 응답·receipt 보존, stale source·대상자·최소화·lease 거부, 혼합 범위와 기존 검수
재개 회귀를 함께 준비했다. 전체 필수 검사와 PostgreSQL 통합은 최종 PR CI에서 실행하며
같은 전체 검사를 로컬에서 중복하지 않는다. CI 결과는 PR에 연결한다.

PR #102 첫 CI `34701005436`에서 다른 필수 6개는 성공했고, PostgreSQL은 917개 성공·2개
실패였다. 기존 retained provider 테스트가 unknown source를 사용해 새 로컬 보류에 들어갔다.
해당 테스트만 첫 generation 생성 전에 합성 policy 근거를 준비하도록 수정해 호출/예산과
provider 중 source 변경 기대값을 유지했다. 전용 합성 PostgreSQL 18.6에서
`test_targeted_runner_uses_existing_provider_budget_without_resetting_document_quota`와
`test_generation_change_during_provider_call_cannot_publish_or_retransmit`만 재실행하여
3개 성공(5.18초)을 확인했다. 첫 로컬 시도는 빈 테스트 DB에 migration을 적용하기 전이어서
setup 3개 오류였으며, 제품 테스트 결과로 간주하지 않는다. 최종 필수 CI는 수정 source에 연결한다.

## Acceptance remaining

- 로컬 보류 구현·혼합 범위 및 이력/재개 회귀·PR 상세 검증
- 검증된 소스에서만 실제 범위 진행·가입/약관 신원 연결
- #69/#70의 최종 전환·사용 수용과 최종 릴리스 조건
