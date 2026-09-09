# v0.5 B08: Final acceptance and release preparation

- 상태: in_progress — 증거 집계와 릴리스 검토 준비; 제품·마일스톤·릴리스 완료 아님
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP10 #70](https://github.com/jihoon22-lee/family-care/issues/70), R01–R20 / S01–S14
- 선행: [B07 계획](007-private-runtime-transition.md), [draft PR #82](https://github.com/jihoon22-lee/family-care/pull/82)
- 기준 소스: `04fbbda540b0002bc9dc56c04ea5aad79e68322d`; metadata v8 / schema `0064_metadata_proven_prefix`
- 수용 원장: [v0.5.0 verification](../../release/v0.5.0-verification.md)

## Tasks

1. in_progress — B01–B07의 실제 PR·소스 SHA·schema·환경·결과·제한을 R01–R20/S01–S14에 연결한다. 합성 unit/PG, 브라우저 mock, 실제 clone ASGI/Chromium, 보호 자료, 실기기를 각각 기록하고 과거 실패 이력을 보존한다.
2. in_progress — B01 고정 dev/holdout과 음성 대조군을 재사용해 후보 품질, 금액/근거, 지원 범위, 출력량과 필요한 질문을 집계한다. `04fbbda`의 로컬 후보 평가·음성 대조군과 원래 `77bc1cd` 모듈의 baseline 비교는 실행해 기록했다. 금액/근거·전체 지원률·사용성 최종 집계는 남아 있다.
3. blocked (B07) — 전량 재구성의 남은 미처리 구간·metadata·native source binding을 해결하고 실제 제공 자료의 해석/설명/계산 지원 분모를 확정한다. 기존 패키지는 있으나 정확한 source PDF hash mapping이 확인되지 않았으며 이 입력 의존성이 남아 있다. 파일명·별칭 유사성, 업로드 종류, 일부 식별 필드로 연결 권위를 만들지 않는다.
4. pending — 같은 입력의 로컬 결과와 실제 선택 AI 검수 후 결과에서 올바른 수정·새 오류·검수 범위·실제 요청·시간·토큰/비용을 비교한다. 합성 제안과 SDK HTTP mock은 모델 순효과 측정을 대신하지 않는다.
5. in_progress — 별도 0064 clone의 실제 인증·AI-off ASGI와 320px/1280px Chromium 수용을 기록한다. 대표 규모의 cold/warm 지연, import/검수 부하·메모리/idle, 실제 Windows·모바일 PWA와 live gateway 수용은 남겨 둔다.
6. pending — B07 최종 source barrier·전환·재시작/복구 수용과 최신 head 전체 CI를 확인한 뒤 Web/API/Worker·schema·knowledge generation·immutable image digest 조합을 릴리스 검토 자료로 묶는다. 버전 변경·tag·게시·배포는 이 문서 작성으로 실행하지 않는다.
7. pending — 최종 수용표와 제한을 #59/#60/#69/#70에 동기화하고 각 WP·명세·메인·마일스톤의 종료 여부를 실제 수용 조건으로 판단한다.

## Current acceptance boundary

B01–B06은 병합된 코드·합성 검증 증거가 있다. B07은 진행 중이며, 앞선 `5483b7e`의
[CI 34316501218](https://github.com/jihoon22-lee/family-care/actions/runs/34316501218)는 필수 7/7,
합성 PG 763과 빈 DB migration 왕복을 통과했다. Korean fallback font와 독립 합성 CID fixture를
포함한 `04fbbda`의 [CI 34318679642](https://github.com/jihoon22-lee/family-care/actions/runs/34318679642)는
이 문서의 상태 확인 시 전체 완료 전이었다. 앞선 소스의 통과를 새 소스 전체 통과로 표시하지 않는다.

승인된 백업·격리 복원, 보관 원문 복호화/hash 대조, 0064까지의 기존 원본 행 보존은 확인됐다.
v8 backfill은 남은 미처리 구간·metadata·native 연결을 가진 terminal `PARTIAL`이며 전체
재구성 성공이 아니다. 별도 clone의 실제 인증·저장 결과/원문 발췌·AI-off 조회와 임시 TLS
Chromium 수용은 해당 경계에서 통과했다. 실제 자료의 개수·시각/시간·경로·식별자·결과 값은
비공개 journal에만 보존한다. 기존 v0.4/0024 live runtime은 유지되며 activation과 v0.5 tag는 없다.

## Reuse and completion rules

평가·검증 명령은 [수용 원장](../../release/v0.5.0-verification.md#재사용-검증-명령), 전체 필수
검사는 [검증 전략](../../design/test-strategy.md#verification-by-change)을 따른다. 새 평가 framework를
만들거나 어려운 사례를 분모에서 제외하지 않는다. 고정 목표·표본·환경을 바꿀 때는 #60에
근거를 명시한다. 미지원·부분 처리·모순·미검증은 성공에 합산하지 않는다.

현재 문서 작업은 저장소/GitHub 읽기와 두 문서 작성이다. 새 제품 검사나 보호 자료 검증을
실행한 기록이 아니며 GitHub 상태·코드·계약·version·runtime은 변경하지 않는다. 최종 소스와
검증 입력이 달라지면 영향받는 기존 검사를 다시 선택해 실행한다.
