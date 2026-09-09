# Event facts, source conditions and fixed review evaluation

고정 dev/holdout 20개 사례를 실제 PostgreSQL·로컬 약관 해석·검수 경로에 연결하면서
확정 진단 입력 누락, 의미 조건에서 치료 종류가 사라지는 문제, 같은 분류 조건을 서로 다른
내부 node ID 때문에 불일치로 처리하는 문제를 확인했다. 이 변경은 세 경계를 수정하고,
사례·정답·split·목표를 유지한 실제 모델 평가를 제공한다. 구현 검증의 모의 전송과 후속 실제 호출 결과를 구분한다.

## Changes

- Web/API/Worker의 `diagnosis_confirmed` 계약은 true/false/unknown을 보존한다. 명시적
  진단 문장에도 불확실성·대상자·시점·상충 검사를 적용하며 AI 제안은 사용자 확인 전
  사실이 아니다. parser v2 변경은 원래 snapshot을 고치지 않고 이전 안내를 stale로 만든다.
- 의미 조건에 명시적인 `MedicalEvent.treatment_kind` equals를 추가했다. 중립 schema에서
  API/Worker 타입을 생성한다. 원문 전체 일치와 기존 source/edition/인용 검증이 필요하며,
  불명확한 코드에서 임상 분류를 추론하지 않는다. compiler v2/meaning v3를 별도로 기록한다.
- `0070_semantic_activity`는 compiler v1과 v2를 함께 허용하고 v1 행을 변경하지 않는다.
  v2 게시 이력 또는 새 진단 fact/질문 이력이 있으면 downgrade를 거부한다. 설치된 API/Worker readiness도 함께 갱신했다.
- 원래 source anchor가 같은 검수 root의 비교에서 producer node ID만 제외한다. 코드 체계·
  판본·규칙 내용 차이는 계속 불일치이며, 독립 원문 검증을 건너뛰지 않는다.
- 합성 adapter는 정답을 읽지 않고 실제 source→metadata→edition→local semantic projection을
  수행한 뒤 원답을 보존한다. synthetic-event-kind/v1과 TST 정수 반올림은 명시한 companion
  규약이며 고정 산술 값이 변하지 않는지 별도로 확인한다. 미지원 조건도 원문 안에 남긴다.
- `scripts/run_fixed_review_evaluation.py`의 prepare/run/report는 보존할 별도 합성 DB와
  journal을 요구한다. 실제 모드는 깨끗한 commit·20개 고정 사례·원답 대조를 확인하며,
  OpenAI Standard/텍스트 전용 wire/40초/출력 4,000 token/SDK 재시도 0회로 제한한다.
  기본 $1 상한은 요청 전에 파일 잠금·fsync로 예약한다. 사용량 미상은 예약을 유지하고,
  cache-write 상세 미상은 알려진 cached input을 제외한 입력에 $2.50/M 상한을 쓴다.
  같은 case 재호출은 금지하고, 기존 job 결과를 전송 없이 회수한다. 응답 없는 retained
  queued/running 작업은 자동 재호출하지 않고 실행을 중단한다.
- v0.5.3 및 dependency lock/OpenAPI/Web 소비자를 함께 갱신한다. 실제 배포는 아직 v0.5.2다.

## Verification

2026-09-09 UTC, WSL/Python 3.14.7/PostgreSQL 18.6, base `c9e3cea`와 위 미커밋 변경.
고정 benchmark 입력 파일은 변경하지 않았다. 아래는 실제로 관찰한 결과이며 전체 완료는 아니다.

- 진단 입력: 최초 API 회귀 20 실패/5 통과 → 관련 API/Worker 198 통과(1.25s).
  사건 DB 교정·권한·버전·기록·stale 검증 10 통과(4.72s).
- Web 진단 질문: 2 실패 → 관련 입력 23 통과(5.09s). 전체 Web 검사는 아래에 추가한다.
- boolean source 의미 3 실패/25 통과 → 28 통과. 치료 종류 계약 7 실패/88 통과 →
  관련 의미/컴파일 95 통과(0.38s). 0070 이전 compiler v2 게시의 DB constraint 실패를
  확인했고, 0070에서 v1 보존·왕복·v2 이력 downgrade 거부 1 통과(3.44s).
- 원문별 모델 node ID 차이 1 실패/5 통과 → 수정 후 관련 검수·평가 guard 31 통과(1.39s).
  비용/wire 경계는 6 실패/8 통과 → 18 통과. 늦은 writer는 1 실패/18 통과 → 19 통과.
- 실제 SDK MockTransport→Worker 예약·hydration→API source proof→게시·원답 보존:
  고정 20개 사례와 독립 재개/산술 검사 총 22 통과(214.49s). 초기 adapter 수집 실패,
  source/role 연결 오류, wire envelope 필드 오류, false disagreement를 통과로 합산하지 않는다.
- `uv lock --offline`, `uv sync --frozen --offline --all-packages --group dev`, OpenAPI/Web 생성,
  Ruff format/lint 통과. `uv run mypy apps/api/src workers/analyzer/src scripts`: 361 files 통과.
- 실제 평가 단가는 2026-09-09 확인한 [OpenAI 공식 가격표](https://developers.openai.com/api/docs/pricing)를
  따른다: 입력 $2/M, cache read $0.20/M, cache write $2.50/M, 출력 $12/M. 기록은 청구서나
  계정 잔액 확인이 아니라 token 기반 추정/보수적 상한이다.

2026-09-09 UTC 추가 검증:

- 독립 정적 리뷰의 두 지적을 확인했다. 붙여 쓴 확정진단 부정/상충은 회귀 2 실패 뒤
  관련 81 통과(0.36s). diagnosis fact만 있을 때의 downgrade 회귀는 최초 드라이버 URL
  오류를 별도로 기록하고 수정했다. 구 guard만 제거한 전용 DB 대조에서 세 사례가 실제로
  잘못 downgrade됨을 확인(3 실패)하고, guard 복구 후 true/false/null 3 통과(4.39s).
- 진단 교정/질문만 있는 이력·권한·stale·compiler v1 보존을 함께 실행한 PostgreSQL
  검사 15 통과(13.05s). 원래 history와 보호 검사 실패를 삭제하지 않았다.
- 전체 Python: 3832 통과, integration 850 제외, 3 subtests 통과(33.27s).
  이후 변경한 평가 집계·비용/wire/재개 검사는 28 통과(1.70s); 계약의 새 필드 기대값
  누락으로 앞선 전체 실행이 1 실패/3826 통과였던 기록은 보존한다.
- `corepack pnpm web:check`: format/lint/type, 31 files/240 tests(48.13s), build 통과.
  `corepack pnpm --filter @familycare/web test:e2e`: Chromium mock 27 통과(27.6s).
- 문서 50 files·저장소 안전 1118 paths·계약 생성 drift·3개 container/4개 Compose 정의·
  workflow 정책·브랜치 규칙·diff 검사 통과. 컨테이너 정의 검사를 실제 이미지 빌드로
  해석하지 않는다. 새 소스의 전체 PostgreSQL 및 이미지 빌드는 PR CI에서 확인한다.
- 평가 보고는 후보 label 수정/새 오류와 고정 금액 복구/새 오류/정상 금액 손실을 구분한다.
  미산정 기대 금액을 분모에 남기고, 판정할 고정 금액 정답이 없는 출력은 별도로 센다.
  검수 범위·누락 범위·실제 응답 수·관찰된 토큰·job 지연 p50/p95와 사용량 미상을 기록한다.
  단순 동의를 개선으로 세지 않는다.

PR [#92](https://github.com/jihoon22-lee/family-care/pull/92), 첫 source `243dd0a92c888a7c1bf92f5f0ec4d3bf2e9905d4`의
[CI 34407534320](https://github.com/jihoon22-lee/family-care/actions/runs/34407534320)는 여섯 항목 통과,
PostgreSQL 850 통과/1 실패(1514.77s)였다. 위조 index 회귀의 audit row가 compiler v1에
고정돼 v2 조회에 도달하지 않은 테스트 입력 문제를 수정했다. 동일한 위조/원문 주소
검사를 현재 compiler에 적용한 PostgreSQL 10 통과(16.22s). 중간 source `a4f4ef2`의
CI 34410049721은 이 수정 이전 실행이므로 중복 작업을 중단하고 새 소스 검증으로 대체한다.
실패·중단을 전체 통과로 합산하지 않는다.

같은 bytes의 고립된 예전 `DocumentVersion`이 남은 테스트 DB에서 보조 wire 사전검사가
원문 연결 전에 실패했다(21 실패/1 통과/실제 HTTP 0). 제품 원문 검증이 잘못된 연결을 거부한
것이며 모델 실패가 아니다. adapter의 해시 단독 조회를 해당 가정·구성원·batch의 성공한
문서 연결로 한정했다. 고립된 버전 회귀 1 실패 → 1 통과(7.26s), 이전 문서 버전을 보존한
DB에서 전체 23 통과(264.89s). 실제 SDK 모의 요청 20개에 Standard tier까지 포함한
최종 wire 최대 26,354 bytes가 32,768 한도 안임을 확인했다. pytest 사전 import에 따른
assert-rewrite 경고 1건을 기록하며 실패나 실제 provider 통과로 바꾸지 않는다.
공유 endpoint/계정 HTTP 실패 뒤 후속 사례를 전송하지 않는 guard까지 포함해 평가
단위 검사 29개가 통과했다(1.91s). 실제 호출용 DB는 별도로 준비했고 아직 wire 예약은 0이다.

실제 모델 평가·최종 PR/CI·릴리스 결과는 실행 뒤 기록한다. 실제 자료는 저장소나
합성 fixture로 복사하지 않았고, 운영 DB·기존 v0.5.2 이미지·키를 변경하지 않았다. 자동 약관
역할 판독의 미지원 형식, 실제 모바일/PWA 수용은 이 합성 평가로 완료 처리하지 않는다.


## 실제 평가와 게시 전 버전 정렬

최종 source `b270a7d21f2eaaf11ee82cbfedba2eafcc7768dc`의
[CI 34410453799](https://github.com/jihoon22-lee/family-care/actions/runs/34410453799)는
필수 7/7, Python 3835/3 subtests, PostgreSQL 852 및 빈 DB 왕복, Web 240/build/mock
27과 이미지 3개를 통과했다. PR #92는 `d88936c5cf971d575684d4ca37e47abe0f394e3c`로
병합됐으며 파일 트리는 최종 PR source와 같다.

같은 b270a7d의 20건 실제 API 평가는 20 요청/20 응답·사용량 정산을 마쳤고 재호출하지
않았다. 후보·금액 개선/새 오류 0, 처리 실패 3건 원답 보존, 비용 추정 USD 0.6554364였다.
원문 범위 PARTIAL과 고정 금액 3건 미산정을 포함한 [평가 보고](../docs/release/v0.5-fixed-review-evaluation.md)를
별도로 연결한다. 이 결과는 실제 자료 품질이나 모델 단독 정확도가 아니다.

병합 후 `release_audit.py --version 0.5.3 --commit-sha d88936c5cf971d575684d4ca37e47abe0f394e3c`가
API/Worker `__version__`의 0.5.2 잔여를 거부했다. 태그/전환 전이므로 실행 중인 앱에는
영향이 없었다. 현재 저장소의 메타데이터 일치를 검증하는 회귀 1 실패(0.05s) 뒤 두
런타임 버전을 0.5.3으로 수정하고 OpenAPI를 재생성했다. 관련 release audit 8 통과(0.02s),
동일 CLI 통과를 확인했다. 이 버전 정렬은 기존 실제 모델 입력/해석/비용을 바꾸지 않는다.
새 source의 완료 검사와 PR/게시 결과는 확인 후 이어 기록한다.


2026-09-09 UTC, base d88936c와 위 버전/검사/문서 diff, WSL/Python 3.14.7:
`uv sync --frozen --offline --all-packages --group dev` 통과(파일 시스템 간 hardlink 대신
copy 경고). 전체 Python 첫 실행은 health 기대값 0.5.2 잔여로 9 실패/3827 통과였고,
기대값 정렬 후 3836 통과/852 integration 제외/3 subtests 통과(36.13s)였다.
Ruff format 903 files/lint, mypy 361 source files, 계약/OpenAPI/Web 생성 drift,
container 정의·workflow 정책·문서/저장소 안전·diff 검사를 통과했다.
`corepack pnpm install --offline --frozen-lockfile` 및 `corepack pnpm web:check`는
31 files/240 tests(49.88s), format/lint/type/build를 통과했다. 이 후속 변경에는
새 DB 동작이 없으며 전체 PostgreSQL과 세 이미지 빌드는 새 PR CI에서 확인한다.
실제 provider 입력이 같아 앞선 20건 평가를 반복하지 않았다.
