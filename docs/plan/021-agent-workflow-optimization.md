# Agent workflow optimization

- 상태: complete
- 요청: v0.5 구현 전 지침·스킬 최적화를 먼저 적용한다.
- 고정 경계: 사용자가 설정한 컨텍스트 윈도우·자동 압축 한계와 개인 설정 파일은 변경하지 않는다.
- 관련 설계: [검증 전략](../design/test-strategy.md), [보안·개인정보](../design/security-privacy.md)
- 제품 구현 시작점: [WP01 / B01](https://github.com/jihoon22-lee/family-care/issues/61)

## Task 1: Scope instructions and reusable workflows

- 상태: complete
- 루트 공통 규칙, Web/API/Worker 추가 지침과 상세 기여 규약을 분리한다.
- 현재 마일스톤을 안내하고 기존 판정 정책의 변경은 B01 코드·테스트와 함께 수행하도록 명시한다.
- 작업 유형별 검증과 증거 유효성, 제한된 병렬 검토, 모델 선택 원칙을 기록한다.
- 저장소 스킬 두 개와 개인 랜딩 페이지 스킬의 적용 범위를 수정했다. 검색 스킬은 작업 중 다른 변경으로 경로·적용 범위 수정이 이미 반영되어 덮어쓰지 않고 검증한다.

## Task 2: Validate and record

- 상태: complete
- 문서·지침 전용 변경이므로 구조 검사, 스킬 validator와 실제 명령 확인으로 검증한다.
- 검증 안내 자체를 개정하므로 이번 준비 작업은 전체 기본 명령도 한 번 직렬 실행했다. 기능 테스트를 새로 추가하지 않았으며 실패·미실행·통합 검증 제외를 구분한다.
- 개인정보 경계와 기존 규칙 누락을 검토하고 실제 결과를 workthrough 하나에 기록한다.

## Task 3: Commit and hand off

- 상태: complete
- 요청 범위의 변경을 Conventional Commit으로 로컬 커밋한다.
- GitHub 이슈 변경·push·merge·태그·배포·실자료 접근은 이번 준비 작업에 포함하지 않는다.
- 이번 준비 완료를 #61의 제품 구현 또는 v0.5 수용 완료로 표시하지 않는다.

실행 결과와 제한: [workthrough](../../workthrough/2026-09-07-agent-workflow-optimization.md).
