# v0.5 B08: Final acceptance and release preparation

- 상태: in_progress — v0.5.2 게시·WSL 배포 검증 완료; 보호 자료/AI·기기 수용 잔여로 마일스톤 OPEN
- 메인/요구사항: [#59](https://github.com/jihoon22-lee/family-care/issues/59), [#60](https://github.com/jihoon22-lee/family-care/issues/60)
- 실행: [WP10 #70](https://github.com/jihoon22-lee/family-care/issues/70), R01–R20 / S01–S14
- 선행: [B07 계획](007-private-runtime-transition.md), [merged PR #82](https://github.com/jihoon22-lee/family-care/pull/82)
- 현재 통합: PR #90 source `b001788e70affbda26cc52870a113b684321b7bb`, merge `2370761613f33e833cf9e86f55cff1cedfc3e1e6`; metadata v9 / schema `0069_policy_draft_replay`. 아래 이전 소스 증거는 역사로 보존한다.
- 수용 원장: [v0.5.0 verification](../../release/v0.5.0-verification.md)

## Tasks

1. complete — B01–B08 및 후속 #84–#86의 실제 PR·소스·schema·검증 층과 제한을 수용 원장의 R01–R20/S01–S14에 연결한다. 코드/합성/실제 clone/기기 증거와 이전 실패를 구분한다.
2. complete (측정) — 고정 dev/holdout·음성 대조군과 원래 baseline 비교를 보존한다. 0.5 버전 변경에서 같은 고정 집합의 목표를 다시 통과했다. 실제 준비된 사건의 후보·부분 계산·근거와 지원 분모/질문을 별도 집계했다. 전체 자료 정확도를 주장하지 않는다.
3. complete (분모·한계 기록) / PARTIAL (자료 지원) — 제공 source identity·이전/현재 실패·native/기존 catalog 가입과 해석/설명/계산 가능 범위를 분리했다. 정확한 별칭 대응표와 자동 약관 판본/의미 지식은 미해결이다. 이전의 전량 처리 해소와 #60/#70의 지원 범위 측정을 같은 완료 조건으로 취급하지 않는다. 미지원 데이터를 분모에서 빼거나 해석 성공으로 바꾸지 않는다.
4. complete (제한된 표본) — 한 개발 사례의 실제 모델 진단 4회와 성공/실패·원답 보존을 기록했다. 별도 보관 증권의 로컬 초안 축소와 실제 독립 검수 1회, 프로그램 근거 검사·가입 게시를 확인했다. 넓은 모델 순효과와 통화 비용은 미검증이다.
5. complete (명시된 환경) — 기존 clone의 인증/Windows 흐름·API 첫 조회/warm/idle·원문/native 경합에 더해 2370761 새 이미지의 합성 암호 해제/native/OCR 경합과 실제 HTTPS 전환·재시작 후 조회·짧은 세션 만료를 확인했다. cold DB/OS cache, 전체 DB intake/archive/AI 부하, 실제 비밀번호 로그인·7/30일 전체 시간 경과·모바일 실기기/PWA 설치는 미검증이다.
6. complete — PR #90/2370761의 PR·main CI 각 7/7과 v0.5.2 foundation·이미지 게시/digest 검증을 확인했다. 자동 노트 생성의 범주 순서 오류는 기존 태그·범주 본문을 보존한 명시적 수동 메타데이터 복구로 게시했다. 원본 0024/HMAC 비교 뒤 대상 0069 전환·재시작·실제 HTTPS·새 이미지 archive 검증을 통과했다. 이전 두 태그의 게시 전 실패와 v0.5.2의 두 시도 실패 지점은 보존한다.
7. complete (종료 판단 기록) — #59/#60/#62/#63/#69/#70에 실제 게시/전환·수동 메타데이터 복구와 남은 수용을 동기화했다. 자료 지원·넓은 AI 평가·실기기 등의 필수 기준이 남아 관련 이슈와 마일스톤을 OPEN으로 유지한다. 배포 성공을 전체 지급/판독 품질 PASS로 올리지 않는다.

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
