# API instructions

루트 [AGENTS.md](../../AGENTS.md)에 추가되는 API 지침입니다.

## Implementation

- 기존 router → use case/service → repository 경계와 PostgreSQL 구조를 재사용합니다. 관련 모듈의 설계만 읽습니다.
- 모든 객체 조회·변경은 서버에서 가정·구성원 범위를 확인합니다. URL의 식별자나 UI 필터를 권한 검사로 간주하지 않습니다. session·CSRF·민감 응답 `no-store`를 유지합니다.
- migration은 기존 원문·교정·청구 snapshot을 보존합니다. 동시 수정, 재시도, lease와 transaction 경계는 실제 PostgreSQL로 검증합니다.
- 외부 계약 변경은 `packages/contracts/README.md`의 원본·생성 절차를 따르고 Web/Worker 소비자와 함께 검토합니다.
- v0.5 결과 정책은 #60/#61을 기준으로 기존 사전조건부터 추적합니다. 문구만 바꾸거나 새 상태 필드를 다시 전역 승인 조건으로 묶지 않습니다.

## Verification and review

- 필요한 domain/API 테스트를 구현과 함께 작성하고 PR 계획 완료 시점에 실행합니다. 커밋마다 RED/GREEN 실행을 요구하지 않습니다. 권한·경로·데이터 변경에는 성공/거부 쌍과 동시성·실패 복구 사례를 포함합니다.
- migration/repository 변경은 [PostgreSQL 통합 규칙](../../docs/design/test-strategy.md#integration-tests)을 따릅니다. 전용 합성 test DB만 사용하며 runtime DB URL로 fallback하지 않습니다.
- 기본 pytest는 integration을 제외합니다. 기본 suite 성공을 migration·DB 통합 성공으로 표현하지 않습니다.
- 검증 명령과 재실행 기준은 [검증 전략](../../docs/design/test-strategy.md#verification-by-change)을 따릅니다. Web·Docker 검사와 직렬 실행합니다.

## Code Review Rules

- 입력 → 권한 확인 → 정규화 → transaction/storage → 응답까지 추적합니다. 다른 가정 참조, 교정 덮어쓰기, 근거 없는 가입·금액 확정을 우선 확인합니다.
- 오류와 로그에 요청 본문·개인 경로·인증정보가 노출되지 않아야 합니다.
