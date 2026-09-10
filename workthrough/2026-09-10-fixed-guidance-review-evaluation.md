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
- 당시 PR #92에서 v0.5.3으로 올렸던 메타데이터는 아래 사용자 지시에 따른 복구에서 v0.5.0으로 통일한다. 이전 실행 환경과 최종 릴리스를 구분한다.

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

[PR #93](https://github.com/jihoon22-lee/family-care/pull/93)의 source
`5e8d9b777e025efb2705d377a250bb12b1ded53d`,
[CI 34414461889](https://github.com/jihoon22-lee/family-care/actions/runs/34414461889)는
필수 7/7을 통과했다. Python 3836/3 subtests(51.31s), PostgreSQL 852(1947.57s)·빈 DB 왕복,
Web 240/build·Chromium mock 27(31.0s), 세 이미지 빌드를 확인했다.
`8a7f289abb3b5c89b4bd8e4ad23ac724aa51497b`로 병합한 뒤 동일 파일 트리·clean 상태와
release audit를 확인하고 annotated v0.5.3 tag를 게시했다. 앞선 PR #92 merge의
[main CI 34413056673](https://github.com/jihoon22-lee/family-care/actions/runs/34413056673)도
필수 7/7, PostgreSQL 852(1897.81s)를 통과했다.

## 합성 동시 실행 측정과 패치 적용 준비

2026-09-09 UTC, clean source `5e8d9b777e025efb2705d377a250bb12b1ded53d`, schema 0070의
새 합성 DB에서 고정 20개 사례의 서비스→DB 조회 200건을 측정했다. 첫 측정/반복/
검수 병행/PDF 처리 실행의 p95는 각각 0.985/0.946/1.043/1.017초였다.
기존 후보 기대값 일치, 모의 검수 20건·합성 native PDF 10개/250쪽 완료,
문서 작업 임시 파일 정리와 외부 HTTP 0을 확인했다. PDF 구간의 실제 중첩은 24/60회여서
전체 분포를 중첩한 조회만의 분포로 해석하지 않는다. 명령·환경·측정 범위는
[측정 보고](../docs/release/v0.5-service-contention-measurement.md)에 기록했다.
실제 자료/기기·암호화 batch/OCR·실제 모델의 부하 결과를 대신하지 않는다.

별도의 비공개 패치 helper는 원본 DB 보존, 중단 시 기존 앱 복구, 복원 rehearsal 후
대상 migration, migration 이후 이전 앱 자동 재시작 금지를 준비했다. 읽기 전용 검토에서
stage 이후 호출 환경/target env가 Compose의 DB를 바꿀 수 있는 경로를 발견했다.
검증된 해석 결과를 0600 Compose로 고정하고 이후 실행에서 env-file을 제거했으며,
관리 명령에도 Compose hash 검사를 넣었다. 합성 실제 `docker compose config` 왕복에서
환경/target env 변경과 literal dollar 보존을 대조하고 이전의 미고정 recipe도 거부한다.
private guard 17개 통과(0.55s),
세 helper의 `py_compile` 통과와 정적 재검토로 해당 경로 해소를 확인했다.
최종 merge `8a7f289`를 helper의 적용 소스로 고정한 뒤 같은 guard 17개가 통과했다(0.53s).
이 준비 검증을 실제 backup/migration/activation 성공으로 기록하지 않는다.

잔여 약관 형식의 읽기 전용 정적 검토에서는 region 전체의 heading 계수와 body sentence의
prefix 계수를 같은 위치의 증거로 해석할 수 없음을 확인했다. 항목 marker 제거 후에도
기존 추가 어휘 검사는 적용돼 있어 marker 누락으로 인한 진단 오류는 아니었다.
clean `2370761`/0069의 같은 후보 경계/heading 관계를 확인하는 제한된 후속 진단은 기존 승인 source snapshot과
lineage 검증, DB 쓰기·외부 요청 0으로 완료했다. 제목 뒤 본문 분리만으로 기존 지급
문법을 통과한다는 실제 원인은 입증하지 못했으며, 부분 단어에 대한 company 검색을
근거로 판독 문법을 넓히지 않았다. 별도의 순수 합성 입력에서는 독립 제목 줄은 지원되고
제목·본문을 같은 줄에 붙인 두 형식은 미지원임을 확인했다. 이를 보호 자료 문제의 해결이나
새 metadata 버전의 검증 결과로 주장하지 않는다.


## 사용자 지시에 따른 v0.5.0 버전 복구

2026-09-10 UTC, 사용자는 조기 게시한 버전을 제거하고 개발·최종 릴리스를 v0.5.0으로
맞추도록 명시했다. 릴리스 실패를 이유로 패치 버전을 늘리거나 이를 v0.5.0의 프리뷰로
표시한 판단을 철회했다. 기존 0.5.0–0.5.3 Git 태그 4개와 GitHub Release 2개를 삭제하고
원격에서 잔여 v0.5.x 태그·Release가 0임을 확인했다. 원래 태그 대상과 공개 메타데이터는
작업 기록에 보존했다. 소스 커밋이나 구현 기능은 되돌리지 않았다.

Web/API/Worker의 패키지·런타임 버전, lock, health 기대값과 생성 OpenAPI를 0.5.0으로
통일했다. CHANGELOG의 조기 패치 항목은 0.5.0 개발 기록에 합쳤고 README/로드맵/B08/
수용 원장의 현재 목표를 정정했다. schema는 기존 구현이 요구하는 0070을 유지한다.
이전 실제 모델 평가를 다시 호출하지 않았다.

v0.5.3 source `8a7f289`는 PR/main CI와 게시 실행 34417126200의 필수 7/7을 통과했던
기록이다. 게시 실행의 Python 3836/3 subtests(46.30s), PostgreSQL 852(1844.04s), Web
240/build·Chromium mock 27(31.1s), OCR/한글 glyph와 이미지·digest·노트 검증을 확인했다.
원격 태그·CHANGELOG 본문도 대조했지만 이는 삭제한 조기 게시 이력이다. 실제 앱은
전환하지 않았으며 기존 DB·자료·키를 변경하지 않았다. release workflow는 최종 0.5.0
준비 전까지 비활성화했다. 새 태그는 마일스톤 완료 후 생성한다.


버전 복구 검증은 2026-09-10 UTC, base `8a7f289`와 이 PR의 메타데이터·health 기대값·
생성 계약·문서 diff, Python 3.14.7/WSL에서 실행했다. 별도 환경의 `uv lock --offline`와
`uv sync --frozen --offline --all-packages --group dev`를 통과했다. `check_contracts.py
--write-openapi`와 Web 소비자 생성 뒤 `release_audit.py --version 0.5.0 --commit-sha
8a7f289abb3b5c89b4bd8e4ad23ac724aa51497b`를 통과했다.
`corepack pnpm install --offline --frozen-lockfile` 및 `corepack pnpm web:check`는
31 files/240 tests(54.03s), format/lint/type/build를 통과했다. 전체 기본 Python은
3836 passed/852 integration 제외/3 subtests passed(41.04s), Ruff format 904/lint,
mypy 361 files, 계약·container 정의·workflow·문서·저장소 안전·diff 검사가 통과했다.
외부 AI 호출이나 실제 데이터 migration을 수행하지 않았다. 새 소스의 필수 CI는 PR에서 확인한다.

GitHub 마일스톤 설명과 연결 이슈 12개를 전수 대조했다. 잘못된 패치 버전 기록이 있던
#59/#60/#62/#63/#69/#70의 활성 본문을 v0.5.0 기준으로 정리하고 다시 읽어 해당 번호가
남지 않았음을 확인했다. 원래 요구사항과 체크리스트는 보존했다. SECURITY와 architecture의
현재 버전도 함께 정정했다. 이미지 6개는 공개 코드 artifact만 별도 보관했으나 GHCR 삭제는
당시 토큰의 `delete:packages` 권한 부재로 403을 반환했다. GitHub Release/Git 태그 삭제와
이미지 삭제를 구분하며 인증 갱신 후 실제 결과를 추가한다.

사용자가 권한을 부여한 뒤 정확한 digest/tag 대조를 거쳐 Web/API/Worker의 조기 게시
이미지 버전 6개를 삭제했다. 세 패키지에서 v0.5.x 태그가 남지 않은 것을 재조회했다.
버전 복구 PR #94는 CI `34425055193` 필수 7/7 후 merge
`ea335ed5f3f55c4f5aa535b8d7021272fd9bea23`으로 통합했다. 최종 릴리스·배포는 아직 하지 않았다.

## Source calculation completion

#64/#66의 완료 상태를 다시 열고 원문 경로에서 누락됐던 일당 33·조건부 감액 30·실손 60을
보완했다. 원래 20개 사례·숫자·기대값·split은 수정하지 않았다. 새 합성 adapter v5는
정상 증권 필드 게시의 가입금액·KRW 근거와 정상 비용 등록의 확인된 보장대상 비용을
사용한다. TST companion을 KRW 정수 단위로 바꾼 새 평가 입력이며 과거 유료 실행과
동일한 입력으로 주장하지 않는다. 과거 유료 DB·journal·보고서는 다시 실행하거나 수정하지 않았다.

compiler v3 / meaning v4는 원문 감액 조건·계수·순서를 유지하고 확인 비용 기반 실손
산식을 생성한다. 새 `if`는 실제 Boolean과 선택된 수치 분기만 실행·기록한다. 미상·AI
제안·오래된 조건은 계산하지 않으며, 기존 private engine의 문장 normalizer도 감액의
명시적 사용자 확인을 대신할 수 없다. 화면은 해당 여부·선택값·확인 전 상태를 표시한다.
`0071_source_calculations`는 이전 게시 행을 보존하고 새 의미/검수 게시·기존 계산 저장소·
사건/결과/청구의 신규 조건 이력이 있으면 downgrade를 차단한다.

2026-09-10 UTC, base `08cf06d` + 이 절의 미커밋 변경, WSL/Python 3.14.7/전용 합성 PG 18.6:

- 원문/입력 RED 7 실패 → 13 통과; Boolean runtime RED 10 실패 → 관련 109 통과.
- 실제 증권/비용→안내 금액 8 통과(41.85s). 실손은 영수증 등록 후에도 통화 연결이 없어
  실패한 것을 확인하고, 원문 통화와 등록 비용 통화를 대조하도록 수정했다. 가입금액 권한을 만들지 않았다.
- 정상 private publication의 `if` 이력만 있어도 downgrade를 거부해야 한다는 RED를
  확인했다. 신규/기존 compiler 보존과 조건 이력의 rollback 검사 3 통과(9.21s).
- 기존 engine의 자동 감액 추정/structured alias RED 8 실패 → 관련 227 통과(0.63s).
- 중립 계약의 조건·수치 분기 검사는 RED 5 실패 → 문서 계약 회귀 포함 12 통과.
- 전체 Web 31개 파일/249 tests 및 format/lint/type/build 통과(49.80s); 새 질문/표시 9개를 포함한다.
- 전체 Python `uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`:
  **3882 passed / 862 deselected / 3 subtests**, 34.13s. 최초 기존 operator 목록 기대값
  1 실패는 신규 `if` 계약에 맞게 수정했으며 나머지 성공을 최초 전체 성공으로 표현하지 않았다.
- 고정 20건 실제 DB/SDK MockTransport와 정상 금액·의미지식→안내·비용 통합:
  `pytest test_fixed_guidance_review_fixture_integration.py test_fixed_guidance_amount_proof_integration.py
  test_semantic_local_guidance_integration.py test_guidance_expenses_integration.py -m integration -q`
  (모두 `apps/api/tests` 경로), **38 passed**, 268.26s. 외부 요청은 0건이다.
- 최종 Ruff format 912 files/lint, mypy 362 files, 계약·컨테이너 정의·workflow·문서 50·
  저장소 안전 1128 paths와 `git diff --check` 통과. 이미지 빌드와 실제 환경 수용은 별도다.
- `corepack pnpm --filter @familycare/web test:e2e`: Chromium API mock **27 passed**,
  29.4s. 실제 기기·보호 자료·배포 검증과 구분한다. PR CI는 게시 후 결과를 기록한다.
