# v0.5 B08: Final acceptance and release preparation

- 상태: in_progress — v0.5.0 최종 수용 묶음 준비; v14 최종 CI·새 live 이력 병합·최종 릴리스/배포 PENDING
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP10 #70](https://github.com/jihoon22-lee/family-care/issues/70), R01–R20 / S01–S14
- 선행: [B07 계획](007-private-runtime-transition.md), [merged PR #82](https://github.com/jihoon22-lee/family-care/pull/82)
- 이전 통합 기준: PR #90 source `b001788e70affbda26cc52870a113b684321b7bb`, merge `2370761613f33e833cf9e86f55cff1cedfc3e1e6`; metadata v9 / schema `0069_policy_draft_replay`. 아래 이전 소스 증거는 역사로 보존한다.
- 수용 원장: [v0.5.0 verification](../../release/v0.5.0-verification.md)

## Tasks

1. complete — B01–B08 및 후속 #84–#86의 실제 PR·소스·schema·검증 층과 제한을 수용 원장의 R01–R20/S01–S14에 연결한다. 코드/합성/실제 clone/기기 증거와 이전 실패를 구분한다.
2. complete (측정) — 고정 dev/holdout·음성 대조군과 원래 baseline 비교를 보존한다. 0.5 버전 변경에서 같은 고정 집합의 목표를 다시 통과했다. 실제 준비된 사건의 후보·부분 계산·근거와 지원 분모/질문을 별도 집계했다. 전체 자료 정확도를 주장하지 않는다.
3. complete (분모·한계 기록) / PARTIAL (자료 지원) — 제공 source identity·이전/현재 실패·native/기존 catalog 가입과 해석/설명/계산 가능 범위를 분리했다. 원문 프로필로 문서 출처 연결 3개를 확보했고 나머지 별칭 대응과 자동 약관 판본/의미 지식은 PARTIAL이다. 이전의 전량 처리 해소와 #60/#70의 지원 범위 측정을 같은 완료 조건으로 취급하지 않는다. 미지원 데이터를 분모에서 빼거나 해석 성공으로 바꾸지 않는다.
4. complete (측정) — source `b270a7d`의 고정 dev/holdout 20건 실제 HTTP 평가와 사용량 정산을 완료했다. 후보·고정 금액의 개선/새 오류는 0이며 3건의 처리 실패는 원답을 보존했다. 고정 금액 3건 미산정과 원문 범위 PARTIAL을 성공으로 바꾸지 않는다. [실제 평가 보고](../../release/v0.5-fixed-review-evaluation.md)에 분모·비용·지연·한계를 기록했다.
5. complete (명시된 환경) — 기존 clone의 인증/Windows 흐름·API 첫 조회/warm/idle·원문/native 경합에 더해 2370761 새 이미지의 합성 암호 해제/native/OCR 경합과 실제 HTTPS 전환·재시작 후 조회·짧은 세션 만료를 확인했다. cold DB/OS cache, 전체 DB intake/archive/AI 부하, 실제 비밀번호 로그인·7/30일 전체 시간 경과·모바일 실기기/PWA 설치는 미검증이다.
6. complete (버전 복구) / PENDING (최종 CI·릴리스·배포) — PR #94로 조기 v0.5.x 게시를 정리하고 개발·최종 버전을 v0.5.0으로 통일했다. PR #106과 최종 문서 묶음의 필요한 검사·이슈 판단을 완료한 뒤 실제 릴리스·배포 결과를 기록한다. 이전 게시/전환 증거는 역사이며 새 실행으로 합산하지 않는다.
7. in_progress (최종 판단 동기화) — 과거 #59/#60/#62/#63/#69/#70의 OPEN 판단과 당시 제한은 보존한다. 현재 #60/#63/#69/#70은 OPEN이며 최종 실행 결과를 반영할 예정이다. 전 자료 100% 자동 확정·넓은 AI 추가 평가·모바일 실기기 PASS를 임의의 종료 gate로 추가하지 않는다. 미지원/PARTIAL과 기기 미검증을 명시하고, 배포 성공을 전체 지급/판독 품질 PASS로 올리지 않는다.

## Earlier acceptance boundary

B01–B08 및 후속 #84–#86 코드가 병합됐으며 보호된 자료 지원은 PARTIAL이다. 아래는 앞선 단계의 기록이다. 앞선 `5483b7e`의
[CI 34316501218](https://github.com/jihoon22-lee/family-care/actions/runs/34316501218)는 필수 7/7,
합성 PG 763과 빈 DB migration 왕복을 통과했다. Korean fallback font와 독립 합성 CID fixture를
포함한 `04fbbda`의 [CI 34318679642](https://github.com/jihoon22-lee/family-care/actions/runs/34318679642)는
필수 7/7을 통과했고 PR #82는 `18dc981`로 병합됐다. 코드 통합을 보호된 전체 자료 전환 완료로 표시하지 않는다.

승인된 백업·격리 복원, 보관 원문 복호화/hash 대조, 0064까지의 기존 원본 행 보존은 확인됐다.
v8 backfill은 남은 미처리 구간·metadata·native 연결을 가진 terminal `PARTIAL`이며 전체
재구성 성공이 아니다. 별도 clone의 실제 인증·저장 결과/원문 발췌·AI-off 조회와 임시 TLS
Chromium 수용과 `04fbbda`의 후보→청구 초안 생성/새로고침 조회는 검증용 clone에서 통과했다. 실제 자료의 개수·시각/시간·경로·식별자·결과 값은
비공개 journal에만 보존한다. 당시에는 기존 v0.4/0024 live runtime을 유지했고 activation과 v0.5 tag가 없었다. 현재 태그·전환 결과는 수용 원장의 최신 릴리스 기록을 따른다.

## Reuse and completion rules

평가·검증 명령은 [수용 원장](../../release/v0.5.0-verification.md#재사용-검증-명령), 전체 필수
검사는 [검증 전략](../../design/test-strategy.md#verification-by-change)을 따른다. 새 평가 framework를
만들거나 어려운 사례를 분모에서 제외하지 않는다. 고정 목표·표본·환경을 바꿀 때는 #60에
근거를 명시한다. 미지원·부분 처리·모순·미검증은 성공에 합산하지 않는다.

각 실행 결과는 해당 시점의 소스·데이터·환경에 연결한다. 후속 문서 변경에는 문서·안전·diff
검사를 수행하며, 제품 입력이 달라지면 영향을 받는 기존 검사를 다시 선택한다. 이 계획의
진행 상태를 제품·릴리스·운영 전환의 완료로 해석하지 않는다.

현재 0069의 실제 원장 반영과 최신 Windows/성능 범위는 [B07 갱신](007-private-runtime-transition.md#0069-protected-acceptance-update)과 수용 원장의 최신 기록을 따른다. 이전의 native 직접 처리 선행 조건은 해소했지만, 과거 별칭 대응이 없다는 이유로 임의 동일 계약을 만들지 않는다.

## Final acceptance boundary — 2026-09-13

현재 후보 소스는 `54496fa`/schema 0083다. PR #103/#105는 MERGED, PR #106은
필수 CI 6/7 통과·PostgreSQL 진행 중이며 v14의 최종 CI·운영 배포는 PENDING이다. 실제 12개 금액/통화 보강과
담보 1개 추가·기존 이력 보존으로 선택 원문 native 54개를 확보했다.
`39a63bc`/0082 앱의 인증·AI-off·저장·이력 보존은 통과했다. 대표 입력의 191개 전부
미지원은 해당 구성원의 실행 규칙·계산 부재로 분류하며 중복 합산 오류가 아니다.
지원 규칙이 있는 구성원의 기존 입력 복제 1개에서 후보 6개·POINT 3개·FORMULA 1개와
근거·저장·기존 사건 보존·청구 조회를 확인했다. 마지막 helper의 정상 로컬 검색/provider
카운터 오류는 실패로 보존하고 재분석 없는 metadata 대사로 외부 작업/시도 0·이력 보존을
확인했다. 새 live 분석/청구 이력 병합과 source barrier를 거치는 최종 운영 전환은 PENDING이다. 상세 수치와 이전
실패를 [최종 workthrough](../../../workthrough/2026-09-13-source-unit-currency.md)에 한 번 모은다.

수용 판단은 R01–R20/S01–S14의 구현·검증 근거, 승인 자료의 전체 분모, 가능한 부분의
실제 사용과 미지원 사유를 함께 본다. 58개 자료의 전량 자동 확정이나 모바일 실기기
PASS는 별도 필수 조건이 아니다. 약관 신원을 UNKNOWN에서 임의 승격하지 않고
Windows·모바일·PWA 설치·운영 복구의 직접 확인 범위도 각각 유지한다. 원문에 있는
정보의 명확한 시스템 누락은 해결하고, 제한된 조사에서 미확인인 정보를 문서 부재로
단정하지 않는다. 고정 평가의 기대값을 낮추거나 실패 표본을 분모에서 제거하지 않는다.

현재 문서 묶음에서는 테스트/모델/전체 원문 검사를 재실행하지 않는다. 커밋의 문법·형식·
계획 범위만 확인하고, PR의 모든 작업이 끝난 뒤 필요한 상세 검증을 한 번 집중한다.
실패 후에는 영향받는 검사만 다시 실행하며 동일한 CI를 로컬에서 중복하지 않는다.
