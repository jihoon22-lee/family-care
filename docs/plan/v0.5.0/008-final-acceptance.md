# v0.5 B08: Final acceptance and release preparation

- 상태: in_progress — 증거 집계와 릴리스 검토 준비; 제품·마일스톤·릴리스 완료 아님
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP10 #70](https://github.com/jihoon22-lee/family-care/issues/70), R01–R20 / S01–S14
- 선행: [B07 계획](007-private-runtime-transition.md), [merged PR #82](https://github.com/jihoon22-lee/family-care/pull/82)
- 현재 통합: PR #86 source `49e5679f1763f639c9145a9dc2595c86038f8499`, merge `c8938ba8c215ac653a00f59bab696afbd5b5fb61`; metadata v9 / schema `0069_policy_draft_replay`. 아래 이전 소스 증거는 역사로 보존한다.
- 수용 원장: [v0.5.0 verification](../../release/v0.5.0-verification.md)

## Tasks

1. complete — B01–B08 및 후속 #84–#86의 실제 PR·소스·schema·검증 층과 제한을 수용 원장의 R01–R20/S01–S14에 연결한다. 코드/합성/실제 clone/기기 증거와 이전 실패를 구분한다.
2. complete (측정) — 고정 dev/holdout·음성 대조군과 원래 baseline 비교를 보존한다. 0.5 버전 변경에서 같은 고정 집합의 목표를 다시 통과했다. 실제 준비된 사건의 후보·부분 계산·근거와 지원 분모/질문을 별도 집계했다. 전체 자료 정확도를 주장하지 않는다.
3. complete (분모·한계 기록) / PARTIAL (자료 지원) — 제공 source identity·이전/현재 실패·native/기존 catalog 가입과 해석/설명/계산 가능 범위를 분리했다. 정확한 별칭 대응표와 자동 약관 판본/의미 지식은 미해결이다. 이전의 전량 처리 해소와 #60/#70의 지원 범위 측정을 같은 완료 조건으로 취급하지 않는다. 미지원 데이터를 분모에서 빼거나 해석 성공으로 바꾸지 않는다.
4. complete (제한된 표본) — 한 개발 사례의 실제 모델 진단 4회와 성공/실패·원답 보존을 기록했다. 별도 보관 증권의 로컬 초안 축소와 실제 독립 검수 1회, 프로그램 근거 검사·가입 게시를 확인했다. 넓은 모델 순효과와 통화 비용은 미검증이다.
5. complete (명시된 환경) — 0069 clone의 인증·AI-off·기존 결과/청구/원문과 Windows Chrome 320px/1280px, 뒤로 가기·연결 복귀·로그아웃/재로그인을 확인했다. 새 API 프로세스 첫 조회·warm·원문 검증 및 합성 native 추출 경합·idle을 측정했다. cold DB/OS cache, 전체 암호화 import/OCR/검수 부하, 자연 시간 경과 세션 만료와 모바일 실기기/PWA 설치는 미검증이다.
6. in_progress — B07 최종 writer barrier·전환·재시작/복구와 v0.5 source/schema/image 조합을 확인한다. package/runtime/OpenAPI/CHANGELOG 0.5 정합과 릴리스 검사를 진행하며 tag·게시·실제 배포는 실행 뒤 기록한다.
7. pending — 최종 수용과 제한을 #59/#60/#69/#70에 동기화하고 각 WP·명세·메인·마일스톤 종료를 판단한다. 자료 지원 PARTIAL을 전체 지급/판독 품질 PASS로 올리지 않는다.

## Current acceptance boundary

B01–B08 및 후속 #84–#86 코드가 병합됐으며 보호된 자료 지원은 PARTIAL이다. 아래는 앞선 단계의 기록이다. 앞선 `5483b7e`의
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

현재 0069의 실제 원장 반영과 최신 Windows/성능 범위는 [B07 갱신](007-private-runtime-transition.md#0069-protected-acceptance-update)과 수용 원장의 최신 기록을 따른다. 이전의 native 직접 처리 선행 조건은 해소했지만, 과거 별칭 대응이 없다는 이유로 임의 동일 계약을 만들지 않는다.
