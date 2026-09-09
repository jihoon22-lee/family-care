# v0.5 B08: Final acceptance and release preparation

- 상태: in_progress — 증거 집계와 릴리스 검토 준비; 제품·마일스톤·릴리스 완료 아님
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP10 #70](https://github.com/jihoon22-lee/family-care/issues/70), R01–R20 / S01–S14
- 선행: [B07 계획](007-private-runtime-transition.md), [merged PR #82](https://github.com/jihoon22-lee/family-care/pull/82)
- 기준 소스: `04fbbda540b0002bc9dc56c04ea5aad79e68322d`, merge `18dc981f344bf9d9d656d513aa08a05da1f30b42`; metadata v8 / schema `0064_metadata_proven_prefix`
- 수용 원장: [v0.5.0 verification](../../release/v0.5.0-verification.md)

## Tasks

1. in_progress — B01–B07의 실제 PR·소스 SHA·schema·환경·결과·제한을 R01–R20/S01–S14에 연결한다. 합성 unit/PG, 브라우저 mock, 실제 clone ASGI/Chromium, 보호 자료, 실기기를 각각 기록하고 과거 실패 이력을 보존한다.
2. in_progress — B01 고정 dev/holdout과 음성 대조군을 재사용해 후보 품질, 금액/근거, 지원 범위, 출력량과 필요한 질문을 집계한다. `04fbbda`의 로컬 후보 평가·음성 대조군과 원래 `77bc1cd` 모듈의 baseline 비교는 실행해 기록했다. 금액/근거·전체 지원률·사용성 최종 집계는 남아 있다.
3. blocked (B07) — 전량 재구성의 남은 미처리 구간·metadata·native source binding을 해결하고 실제 제공 자료의 해석/설명/계산 지원 분모를 확정한다. 기존 패키지는 있으나 정확한 source PDF hash mapping이 확인되지 않았으며 이 입력 의존성이 남아 있다. 파일명·별칭 유사성, 업로드 종류, 일부 식별 필드로 연결 권위를 만들지 않는다.
4. in_progress — 한 개발용 합성 누락 후보 사례를 실제 모델로 4회 진단했다. 최초 실패·후속 2회 의견 결과를 보존하고, 원문 인용/문장 보존을 명시한 prompt v3에서 누락 후보 1개·로컬 기대액 300 복구를 확인했다. 원답은 모두 보존됐다. 실제 요청·토큰·시간과 오프라인 재생을 구분했다. SDK 경로의 정상/연결 인용 누락/문장 결합 3건과 이전 prompt의 무전송 회귀를 통과했다. 넓은 범위의 모델 순효과는 미검증이다.
5. in_progress — 별도 0064 clone의 실제 인증·AI-off ASGI와 320px/1280px Linux Chromium 및 Windows Chrome headless 수용을 기록했다. 동일 사건의 유휴/원문 검증 읽기 경합과 프로세스 RSS/CPU를 비공개 기록으로 보존했다. cold DB·전체 import/OCR/검수 부하·idle, 모바일 실기기/PWA 설치와 live gateway 수용은 남겨 둔다.
6. pending — B07 최종 source barrier·전환·재시작/복구 수용과 최신 head 전체 CI를 확인한 뒤 Web/API/Worker·schema·knowledge generation·immutable image digest 조합을 릴리스 검토 자료로 묶는다. 버전 변경·tag·게시·배포는 이 문서 작성으로 실행하지 않는다.
7. pending — 최종 수용표와 제한을 #59/#60/#69/#70에 동기화하고 각 WP·명세·메인·마일스톤의 종료 여부를 실제 수용 조건으로 판단한다.

## Current acceptance boundary

B01–B07 코드가 병합됐으며 B07의 보호된 전체 자료 수용은 진행 중이다. 앞선 `5483b7e`의
[CI 34316501218](https://github.com/jihoon22-lee/family-care/actions/runs/34316501218)는 필수 7/7,
합성 PG 763과 빈 DB migration 왕복을 통과했다. Korean fallback font와 독립 합성 CID fixture를
포함한 `04fbbda`의 [CI 34318679642](https://github.com/jihoon22-lee/family-care/actions/runs/34318679642)는
필수 7/7을 통과했고 PR #82는 `18dc981`로 병합됐다. 코드 통합을 보호된 전체 자료 전환 완료로 표시하지 않는다.

승인된 백업·격리 복원, 보관 원문 복호화/hash 대조, 0064까지의 기존 원본 행 보존은 확인됐다.
v8 backfill은 남은 미처리 구간·metadata·native 연결을 가진 terminal `PARTIAL`이며 전체
재구성 성공이 아니다. 별도 clone의 실제 인증·저장 결과/원문 발췌·AI-off 조회와 임시 TLS
Chromium 수용과 `04fbbda`의 후보→청구 초안 생성/새로고침 조회는 검증용 clone에서 통과했다. 실제 자료의 개수·시각/시간·경로·식별자·결과 값은
비공개 journal에만 보존한다. 기존 v0.4/0024 live runtime은 유지되며 activation과 v0.5 tag는 없다.

## Reuse and completion rules

평가·검증 명령은 [수용 원장](../../release/v0.5.0-verification.md#재사용-검증-명령), 전체 필수
검사는 [검증 전략](../../design/test-strategy.md#verification-by-change)을 따른다. 새 평가 framework를
만들거나 어려운 사례를 분모에서 제외하지 않는다. 고정 목표·표본·환경을 바꿀 때는 #60에
근거를 명시한다. 미지원·부분 처리·모순·미검증은 성공에 합산하지 않는다.

각 실행 결과는 해당 시점의 소스·데이터·환경에 연결한다. 후속 문서 변경에는 문서·안전·diff
검사를 수행하며, 제품 입력이 달라지면 영향을 받는 기존 검사를 다시 선택한다. 이 계획의
진행 상태를 제품·릴리스·운영 전환의 완료로 해석하지 않는다.
