# Web instructions

루트 [AGENTS.md](../../AGENTS.md)에 추가되는 Web 지침입니다.

## Implementation

- 기존 React·Vite·TypeScript 구조와 화면 구성 요소를 재사용합니다. 내부 업무 화면에 마케팅 랜딩 페이지 스킬이나 새 프레임워크를 도입하지 않습니다.
- 관련 화면만 [PWA 설계](../../docs/design/event-result-pwa.md)와 해당 WP에서 확인합니다. v0.5 UI는 #68과 #61 응답 계약을 함께 읽습니다.
- `src/api/generated.ts`는 생성 결과입니다. API/계약 원본을 수정하고 `packages/contracts/README.md`의 명령으로 재생성하며 전송 타입을 수동 중복 정의하지 않습니다.
- 보험 조건·금액 계산은 API 계약으로 소비합니다. 표시를 위해 가입 사실·최신성·검수 상태를 새로 추정하지 않습니다.
- 서버 상태는 메모리에만 유지합니다. 로그아웃·세션 만료 시 표시 상태와 캐시를 지우고 API/PDF/Evidence는 `no-store`, 서비스 워커는 앱 셸만 캐시합니다.

## Verification and review

- 동작 변경은 사용자 행동을 검증하는 component test를 먼저 작성합니다. 기능 흐름이 바뀌면 해당 browser E2E도 확인합니다.
- 좁은 화면·키보드·로그인 만료·부분 실패·오래된 비동기 응답을 변경 범위에 맞춰 검증합니다.
- 전체 Web 검사는 저장소 루트에서 `corepack pnpm web:check`, E2E는 `corepack pnpm --filter @familycare/web test:e2e`입니다. 잠금 버전은 루트 `package.json`을 따릅니다.
- `web:check`는 browser E2E를 포함하지 않습니다. mock 브라우저, 실제 backend 통합, 실제 Windows·모바일 검증을 구분합니다.
- Python·Docker 검사와 동시에 실행하지 않습니다. 화면 검증은 합성 계정·자료로 하며 실자료 스크린샷·네트워크 본문을 저장소나 보고에 남기지 않습니다.
