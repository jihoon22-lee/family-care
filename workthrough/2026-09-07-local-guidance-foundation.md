# v0.5 B01 local guidance foundation

- 범위: [WP01 #61](https://github.com/jihoon22-lee/family-care/issues/61),
  [요구사항 #60](https://github.com/jihoon22-lee/family-care/issues/60)
- 기준: `77bc1cd9946328f28f2d36cbae15ffccdbc5d41e`; 브랜치 `feat/local-guidance-foundation`
- 소스: benchmark `2ca1bd0`, 최소 Web 소비자 `72ed2ac`와 이 PR의 API/계약/회귀/문서 변경
- 실행: 2026-09-07, WSL Linux, 고정 pnpm/Python 의존성, 전용 PostgreSQL 18.6 합성 DB

## Result and boundaries

준비된 가입 근거·규칙에서 최신 상태 기록이 없다는 이유로 답변 전체를 없애던 동작을
새 `guidance/engine.py`에서 분리했다. 관련성 근거가 있는 담보에 문서 유지 가정과
조건·가능한 Decimal 계산·부족한 입력을 제공한다. 가입금액을 산식 없이 지급액으로
사용하지 않으며, 다른 통화의 비용은 계산하지 않는다. 현재 상태와 사건일 적용 상태를
구분하고 기록 없는 과거 지급 횟수를 0으로 생성하지 않는다.

`POST /api/v1/medical-events/{id}/analyze`와 기존 결과 GET의 v2 응답에 optional
`local_guidance` v1을 추가했다. OpenAPI/JSON Schema/TypeScript를 원본 모델에서 생성했다.
0025 migration은 기존 decision row에 nullable immutable JSON snapshot을 추가한다.
소유 가정은 서버의 scoped row로 유지하며 가정 식별자를 응답에 노출하지 않는다.
구 v1/v2 결과는 재계산하지 않고 새 필드 null로 조회한다. Web은 후보·계산·가정·질문·
근거 페이지를 표시하며 보험 계산을 소유하지 않는다.

기본 분석은 AI 작업을 예약하지 않고 Web도 자동 구조화를 요청하지 않는다. Worker 키는
Compose의 필수 기동 변수에서 제외했다. 기존 recommendation worker 검증은 명시적 opt-in
합성 fixture로 유지한다. 실제 선택 검수 endpoint/작업은 WP07에서 연결한다.

요구사항·시나리오의 실제 B01 증거와 후속 WP는
[계획](../docs/plan/v0.5.0/001-foundation.md#requirement-and-scenario-handoff)에 연결했다.
전체 문서 추출/자동 연결/자연어 사건 해석/복수 보험 합산/청구 초안/실자료 전환 완료를
이 PR로 주장하지 않는다.

## Verification evidence

기능 부재 RED 후 최소 엔진·UI를 구현했다. 독립 정적 리뷰가 지적한 통화 불일치,
현재/과거 상태 혼용, 기록 없는 지급 0회, JSON 필수 키 NULL 우회를 각각 회귀 테스트로
확인하고 수정했다. 새로운 상태를 전역 승인 gate로 다시 합치지 않았다.

- 초기 Web browser 실행은 Chromium 설치 부재로 결과가 없었다. 잠금 Playwright가 요구하는
  브라우저 설치 후 합성 mock E2E 15개가 통과했다. 실제 backend browser/기기 검증은 아니다.
- 전체 Python 첫 실행은 추가 HTTP 차단 테스트의 잘못된 import로 수집에 실패했다.
  저장소의 `httpx2` transport를 사용하도록 수정했다.
- 이후 4개 계약 회귀 실패를 통해 새 가정 식별자 노출을 제거하고 optional 필드 기대값을
  갱신했다. 개인정보 금지 필드 검사는 완화하지 않았다. 관련 54개 테스트가 통과했다.
- 전체 Python: `TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`
  1,670 passed, 189 integration deselected, 3 subtests passed.
- Ruff format 523 files, Ruff lint, mypy 220 sources, 계약·컨테이너 정적 정책·workflow 정책,
  `actionlint -oneline .github/workflows/*.yml`, `git diff --check` 통과.
- PostgreSQL 전체 첫 실행은 184 passed/4 failed였다. 구 migration 테스트가 0024에 DB를
  남긴 문제를 head 복원으로 고치고, 자동 AI 예약을 가정한 두 검증은 명시적 opt-in으로
  바꿨다. 관련 7개 통합 테스트가 통과했다.
- 최종 수정 후 Web 전체 `corepack pnpm@11.22.0 web:check`는 159개/24파일, 타입·린트·
  포맷·빌드가 통과했다. `corepack pnpm@11.22.0 --filter @familycare/web test:e2e` 15개 통과.
- 최종 기본 Python은 1,670 passed/189 deselected/3 subtests passed, 전용 합성 PostgreSQL
  `pytest -m integration apps/api/tests workers/analyzer/tests -q`는 188 passed/1,397 deselected.
  문서 50 files·저장소 안전 718 paths·Ruff format 525 files·Ruff lint·diff 검사도 통과했다.
- `OPENAI_API_KEY`를 제거하고 합성 환경 변수·`--env-file /dev/null`로 실행한
  `docker compose -f infra/compose/compose.yaml config --quiet` 통과. 정적 구성 검증이며
  이미지 빌드 결과와 구분한다. 로컬 이미지 빌드는 수행하지 않고 PR CI의 세 빌드로 확인한다.

키 없는 API 테스트는 HTTP transport 호출 0과 QUEUED/RUNNING AI 작업 0을 검사한다.
0024 기존 결과의 0025 전환 전후 행 비교, 기존 snapshot GET, immutable JSON,
필수 키 누락/null/다른 사건·버전 거부, 사건 변경 뒤 구결과 stale/본문 보존을 검사한다.
실제 HTTP 외부 서비스나 private runtime은 사용하지 않았다.

## Frozen synthetic evaluation

목표는 [튜닝 전 #60 기록](https://github.com/jihoon22-lee/family-care/issues/60#issuecomment-5564891335)에
고정했다. `fixtures/synthetic/claim-guidance/cases.v1.json`은 개발/holdout 각 10개,
서로 다른 계약 그룹, 전체 후보 pool 48개, 관련 정답 19개, 무관 사건 6개다.
입력 adapter는 `scenario_parameters`만 소비한다. 기대 라벨을 교체해도 엔진 출력이
같다는 회귀와 전부 보류/전부 주요/전부 조건부/무관 조건부 대조군의 실패를 검증한다.

| 실제 엔진 | 개발 | holdout |
|---|---|---|
| 기준 main | recall 8/9, primary 0/0(미정의), conditional 3/9, 무관 오추천 1/3, 불필요 보류 1/10 | recall 9/10, primary 1/1, conditional 2/9, 무관 오추천 1/3, 불필요 보류 1/10 |
| local v1 | recall 9/9, primary 6/6, conditional 3/3, 무관 오추천 0/3, 불필요 보류 0/10 | recall 10/10, primary 7/7, conditional 3/3, 무관 오추천 0/3, 불필요 보류 0/10 |

명령은 `TMPDIR=/tmp uv run python -m scripts.run_claim_guidance_benchmark --engine local
--repetitions 25`이다. legacy 실행은 기준 SHA의 별도 worktree를 PYTHONPATH에 두고
동일 adapter를 `--engine legacy`로 실행했으며 기준 모듈 경로·바이트를 해당 git SHA와
대조했다. local exit 0, legacy exit 1은 고정 목표 충족/미충족의 실제 결과다.

500회 합성 context 구성+엔진 측정에서 local p50 0.271ms/p95 0.559ms,
legacy p50 0.302ms/p95 0.501ms였다. API/DB/네트워크/import 경합을 포함하지 않는
소규모 엔진 측정이며 제품 지연이나 성능 개선 주장이 아니다. 구조화된 사건·고정 규칙
평가이고 PDF 판독/자연어 정확도·전체 자료 지원률·실제 보험 정확도도 측정하지 않았다.
세부 금액 corpus와 원문 근거 품질, 대표 규모 p50/p95는 WP04/06/10에서 확장한다.

## Publication and remaining acceptance

[PR #75](https://github.com/jihoon22-lee/family-care/pull/75)의 `e57ed4a` 대상 CI
[34085797688](https://github.com/jihoon22-lee/family-care/actions/runs/34085797688)는
7개 검사와 세 이미지 빌드가 모두 통과했다. merge 전 추가 정적 UI 리뷰에서 혼합 결과의
operational 후보/청구 버튼 유실과 부분 실패 재시도 유실을 발견했다. 새 회귀 3개를 RED로
확인하고 기존 operational 그룹/계산/근거/청구 경로를 새 안내와 함께 렌더하도록 수정했다.
해당 구성원 private 담보가 없거나 실패해도 기존 operational 답변을 숨기지 않는다.
수정 후 Web 전체 162개(24파일)/포맷·린트·타입·빌드와 browser E2E 15개가 통과했다.
혼합 답변의 320px 청구 버튼도 browser에서 확인했다. 변경되지 않은 Python/API/DB/계약은
위 소스에 연결된 통과 증거를 유지한다. 최종 CI·merge는 후속 기록으로 연결한다.

실제 자료·외부 AI·Windows/모바일·운영 전환은
미실행이다. 태그/이미지 공개/배포는 이 B01에서 수행하지 않았으며 전체 마일스톤 수용 후
승인된 릴리스 범위에서 진행한다. 사용자 컨텍스트·자동 압축 설정은 변경하지 않았다.
