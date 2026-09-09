# v0.5 B06: Guidance to claim preparation

- 상태: in_progress
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP08 #68](https://github.com/jihoon22-lee/family-care/issues/68)
- 구현 기반: B04 PR #79와 B05 [PR #80](https://github.com/jihoon22-lee/family-care/pull/80) source `0c70e51`, CI 34302926708 필수 7/7, merge `c58fae77139bbe77caf3648b5d46e3eed74aaaf1`.
- 요구사항 R01/R07/R10/R11/R13/R15/R16/R17/R19, 시나리오 S01/S05/S06/S08/S09/S10/S12/S13/S14.

## Tasks

1. completed — 저장된 현재 run과 동일 입력 검수의 연결을 서버에서 검증하고 조회 전용 재사용·재열기 계약을 만든다. 새 run이나 조회가 유료 요청을 새로 만들지 않는다.
2. completed — 검수의 저장된 프로그램 후보를 명시적 출처로 청구 준비에 연결한다. 원래 run과 검수 job/publication/결과 digest를 불변 snapshot에 보존하며 기존 초안·실제 지급 이력을 덮지 않는다.
3. completed — 저장 run/검수/담보가 실제 사용한 운영·private·semantic 근거를 공통 조회한다. 원문과 요약을 구분하고 인증·가정 범위·no-store를 유지한다.
4. completed — 가족·사건·자료 기준과 선택 사건 질문, 입력/결과/청구 간 재개 링크를 연결한다. 부분 근거·더 보기·실패 항목 재시도와 늦은 응답을 처리한다.
5. completed — 네트워크/폴링 복구에도 로컬 금액·정상 근거를 유지하고 검수 상태 갱신으로 focus를 옮기지 않는다. 동일 입력 terminal 검수 조회를 새 유료 실행처럼 표시하지 않는다.
6. in_progress — 320px·keyboard·한글·뒤로 가기·세션 만료·혼합 근거·검수 후보 청구의 합성 API/PG/브라우저와 전체 필수 검증, PR/CI/merge 증거를 기록한다.

## Decisions and boundaries

B04의 canonical 청구 참조·조건부 후보·중복/별칭 보호와 기존 소계·시나리오 계산을 재사용한다.
검수 결과는 기존 run의 근거로 가장하지 않는다. 부분 검수나 의견 차이의 전체 상태를
청구 준비의 전역 승인 조건으로 쓰지 않으며 저장된 프로그램 후보와 출처를 검증한다.
준비는 보험사 제출이나 지급/횟수 사실을 생성하지 않는다.

공통 근거 응답은 실제 저장 후보의 참조만 허용한다. private section summary를 원문 인용으로
표시하지 않으며 임의 원문 경로나 다른 가정의 식별자를 열지 않는다. 일부 조회 실패가
성공한 결과를 지우지 않고 원문은 필요할 때만 읽는다.

Worker의 재시도·일일/문서 예산은 B05 계약을 유지한다. 읽기 복구는 GET이며 기존 실패/취소
job을 새로 전송하지 않는다. 유료 재실행이 필요해도 단순한 새 run 생성으로 한도를 우회하지 않는다.

공개 테스트와 브라우저 artifact는 합성 자료만 사용한다. 실제 보호 자료·운영 전환은 B07,
전체 품질과 릴리스 판단은 B08에서 기존 승인 범위와 별도 증거로 수행한다.
