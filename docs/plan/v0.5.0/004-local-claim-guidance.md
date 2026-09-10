# v0.5 B04: Local candidates and explainable estimates

- 상태: in_progress — 기존 승인 규칙의 판본 근거 부족을 조건부 관련성으로 유지하는 후속 보완; PR #95/#65/#66 완료 기록 보존
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP05 #65](https://github.com/jihoon22-lee/family-care/issues/65), [WP06 #66](https://github.com/jihoon22-lee/family-care/issues/66)
- 구현 기반: B03 [PR #78](https://github.com/jihoon22-lee/family-care/pull/78), merge `3f4a47cc2d1b4e649d202850020b94882dc4adfe`, 필수 CI 7/7 통과.
- 관련 요구사항: R01/R06/R07/R08/R09/R10/R11/R12/R13/R17/R19
- 선택 AI 검수는 B05 #67에서 연결하며 이번 기본 분석은 외부 요청 0건이다.

## Tasks

1. completed — 원장/private 공통 안내 context와 semantic 출처/버전 계약을 연결했다. 실제 Rider→Clause→사건일 약관→원문 root와 가입금액 출처를 대조하고, private import 없이 합성 원문의 300/400 계산·과거 300 보존을 확인했다.
2. completed — 제한된 로컬 사건 해석에 동의어·부정·예정/실시·가족/시점 범위를 반영했다. 명시 사실·파생 사실·가정·미상을 구분하고 판본이 없는 분류 코드를 임의 확정하지 않는다. 원문 activity와 관련성, 예정 일수의 별도 계산을 연결했다.
3. completed — 가입/대상자/사건일과 관련성·개별 조건을 분리했다. 무관 후보·결정적 불일치는 제외하고 최신성 미확인만으로 후보·금액을 보류하지 않는다. AI 미확정 입력을 사실로 쓰지 않고, canonical 자료도 출처별 지급 경우와 상태·금액을 보존한다.
4. completed — 원문에 근거한 정액/실손 산식, Decimal trace·부분 비용·지급 경우·시나리오를 연결했다. 소계는 같은 사건/명시한 가정·통화의 독립 정액 계약만 묶고, 미확인 합산 전제와 미산정 부분을 남긴다.
5. completed — API/UI/snapshot과 stale 판정을 새 계약으로 연결했다. 과거 청구/실수령/계산 snapshot을 보존하고 사건·가입/연결·원문·지식·가정 revision으로 재현한다. private/operational 청구 생성·복원의 중복·경합·실패 롤백을 검증했다. 재import 후 과거에 검증된 원문 연결을 다시 검증해 기존 청구·지급 이력에 사용한다.
6. completed — 합성 원문→가입 담보→실제 분석 응답의 300/400·과거 300 보존, 거부/부분 계산·AI-off·동시성과 고정 dev/holdout 후보 목표를 통과했다. source `02be761`의 CI 34289732889 필수 7/7: PostgreSQL 644, Python 3398/3 subtests, Web 210/build, Chromium mock 20, 이미지 3개. PR #79 merge `05325888bb52b7b6c00365fd237d7473bef1dc20`으로 통합했다. 실제 자료 전환·기기·전체 부하 수용은 후속 범위다.
7. completed — 고정 일당 33·조건부 감액 30·실손 60을 정상 증권·비용 근거와 원문 compiler를 거쳐 확인했다. 고정 20건을 포함한 관련 DB 38개, 금액/근거 거부 8개, 전체 Python/Web·브라우저 mock 검증을 마쳤다. [검증 기록](../../../workthrough/2026-09-10-fixed-guidance-review-evaluation.md#source-calculation-completion)의 PR #95와 main CI 필수 7/7 후 `ee732db`로 통합했고 #66을 완료 처리했다. 보호 자료의 지원률로 확대하지 않는다.
8. in_progress — #63/#70 후속 감사에서 기존 승인 operational rule의 판본 UNKNOWN을
   인용 무효와 합쳐 관련 후보까지 제거하는 경로를 확인했다. 원문의 동일 보험사/상품코드와
   현재 평가·승인 연결이 검증되고 적용 근거만 부족한 경우 조건부 후보를 유지한다.
   교정·제외·불일치·변경 관계는 보존하고, 산식 근거 검증과 금액/시나리오 미확정을 분리한다.
   [후속 실행 기록](../../../workthrough/2026-09-10-uncertain-terms-guidance.md)에서 검증·통합한다.

## First vertical acceptance

private import 없이 합성 증권의 실제 Rider와 승인된 Clause 연결을 사용한다. 사건일에 적용되는
판본과 완전한 원문 구역을 다시 검증하고, B03의 별표/각주 산식에 가입금액 100·입원 5일을
넣어 300을 반환한다. 원문 각주 변경 후 새 분석은 400이며 과거 run/청구는 300을 유지한다.
다른 가족·미가입·인접 조항·다른 판본은 같은 이름이더라도 연결하지 않는다.
키 미설정·provider 호출 금지 상태에서 실제 API 응답과 저장 snapshot을 확인한다.

## Boundaries

semantic citation은 publication과 원문 주소를 가진 별도 출처다. 기존 Evidence/약관 section의
UUID로 가장하지 않는다. B03 executable은 일부 규칙/산식의 실행 가능성이며 가입 사실이나
모든 조건 일치를 뜻하지 않는다. latest partial과 같은 source의 last usable을 구별한다.
실제 자료·추가 원문 형식·운영 전환 수용은 #62/#63/#69에 남아 있으며 합성 검증으로 대체하지 않는다.
