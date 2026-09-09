# v0.5 B05: Optional source-grounded guidance review

- 상태: completed
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP07 #67](https://github.com/jihoon22-lee/family-care/issues/67)
- 구현 기반: B04 [PR #79](https://github.com/jihoon22-lee/family-care/pull/79), merge `05325888bb52b7b6c00365fd237d7473bef1dc20`; source `02be761` CI 34289732889 필수 7/7 통과.
- 요구사항 R08/R14/R15/R17/R19, 시나리오 S06/S12/S13.

## Tasks

1. completed — 명시적 검수 요청과 불변 로컬 run 참조, 가입 담보/원문 범위 snapshot, revision별 재사용 계약을 만든다. 기본 분석·조회는 검수 작업을 예약하지 않는다.
2. completed — 해당 가족의 가입 담보 index에서 로컬 출력 밖 후보·정의·예외를 독립 탐색한다. 문서/판본/페이지/구역과 읽지 못한 범위를 보존하고 최소화된 bounded packet을 만든다.
3. completed — structured 검수의 인용·가입·가족·입력·규칙을 검증한다. 검증 가능한 교정은 같은 로컬 엔진으로 재평가하고 AI 의견/숫자를 사실·지급액으로 직접 저장하지 않는다.
4. completed — 별도 작업 lease/deadline/취소와 durable 요청 예산을 연결한다. SDK retry 0, 실제 HTTP 수·입출력 토큰·문서별/전체 한도, 응답 소실·재시작·오래된 완료를 검증한다.
5. completed — API와 생성 계약, 선택 검수 상태·범위·차이 소비를 연결한다. 실패/부분/미설정에도 로컬 결과·금액·과거 snapshot을 유지한다.
6. completed — 합성 누락/예외 개선과 새 오류를 함께 평가하고 관련 PG·HTTP transport·브라우저 및 전체 필수 검사, PR/CI/병합 증거를 기록한다.

## Decisions and acceptance

기존 `analysis_assistance_jobs`는 기본 검색 결과의 성공 상태와 후보 digest를 공유하므로
나중의 명시적 검수 요청을 별도 작업으로 식별하지 못한다. 새 검수 job은 서버의 저장된
decision run과 현재 사건/원문/지식/규칙/가정 revision을 결합한다. 완료된 원답은 수정하지 않는다.
입력 변경 후 늦게 도착한 검수는 과거 검수로만 남으며 현재 결과를 덮지 않는다.

기존 추천기는 로컬 private 후보만 재정렬하므로 누락 탐색의 입력으로 충분하지 않다.
원장/private 공통 가입 context와 원문 검증 reader를 사용하고, 탐색과 대조를 총 예산 안의
구분된 단계로 처리한다. 검수한 담보/원문과 미검수·한도·검색 실패를 응답에 표시한다.

외부 요청은 Worker만 수행한다. 정책/약관 구조화와 공유하는 일일·문서별 요청 한도를
검수가 우회하지 않게 예약한다. 요청 전 예약은 응답 소실에도 보존하고 자동 재전송하지 않는다.
취소는 이후 전송을 막으며 이미 발생한 비용을 취소한 것으로 표시하지 않는다.
모든 공개 검증은 합성 입력과 모의 HTTP를 사용한다. 실제 제공자 호출은 기존 승인과
최소 호출 제약을 따르며 모의 검수의 성공을 실제 품질 수용으로 확대하지 않는다.

B05는 PR #80, merge `c58fae77139bbe77caf3648b5d46e3eed74aaaf1`로 통합했다.
소스 `0c70e51`의 CI 34302926708 필수 7/7 통과; 실제 제공자/보호 자료/기기 수용은 B07/B08과 구분한다.
