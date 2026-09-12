# Initial policy draft normalization

## Change

최초 범위 분석은 증명된 담보명이 있어도 내부 `rider_key` 누락 때문에 후보를 보류했다.
새 자동 작업 `policy-range-normalized-v1`과 명시적 retained v6는 원본 structurer 응답을
보존한 뒤 기존 근거 검사로 초안을 정규화하고, 그 초안을 별도 verifier에 전달한다.
근거 없는 선택 필드는 제거하며, 필수값·후보·범위 손실은 REVIEW와 불변 사유로 남긴다.
검수 실패 후 재개할 때는 저장한 초안을 사용한다. 정규화 자체는 승인 권위가 아니다.

- Worker의 `policy_draft_replay.py`, `runner.py`, 범위 저장·게시 경로를 연결했다.
  실행 중인 자동 작업은 원래 item·document·extraction·현재 generation·구성원·envelope를
  다시 검사한다. retained의 종료된 원 작업 조건은 유지한다. 새 버전은 지속 요청
  예산과 범위 저장소가 없으면 호출하지 않으며 기본 문서 4회/UTC 일일 8회는 유지한다.
- `0075_initial_policy_drafts`는 기존 불변 receipt에 `origin`을 추가한다. 최초 초안은
  같은 작업의 단일 성공 요청과 원 JSON hash에 묶인다. typed schema 비교는 유효한 UUID
  표기 차이를 허용하지만 원 JSON을 고치지 않는다. API/Worker readiness는 0075와 새 열을 요구한다.
- 명시적 v5→v6 replay는 같은 최소화 v4 fingerprint와 원문을 요구하고 새 검수를 거친다.
  기존 자동/v5 동작과 v2/v3→v4 이력은 유지한다. 새 자동/v6 작업 또는 initial receipt가
  있으면 downgrade를 거부한다. 새 작업을 자동 예약하거나 과거 교정을 덮지 않는다.
- 외부 HTTP·Web 계약은 변경하지 않았다. 설계와 B02의 진행 항목을 함께 갱신했다.

## Verification

2026-09-10 UTC, base `27f70965c655cbc726eac9d2af3e32e4b1a4817e` 위의 이 PR 변경을
독립 worktree의 Python 3.14.7, Node 24.18.0, pnpm 11.22.0과 전용 합성
PostgreSQL DB에서 확인했다. 아래 진행 중인 검사는 완료 결과와 구분한다. 새 fixture는
처음부터 만든 문서·이름·금액·provider 응답만 사용한다. 외부 AI는 호출하지 않는다.
2026-09-10 전체 DB 실행 당시 입력은 `git ls-files --cached --others --exclude-standard`에서 `docs/`,
`workthrough/`를 제외한 1,044개 경로와 각 파일 SHA-256을 정렬한 JSON이다.
이 manifest의 SHA-256은 `e5997fb3fed6af17488d3cc876e483e0a7c88d91cd0315960c668038a4e7ae22`다.

- 최초 통합 RED: verifier 입력에 `rider_key`가 없어 1개 실패했다. 빈 DB migration 누락과
  fixture 최소화 목록 불일치는 별도 setup 실패였으며 기능 RED로 계산하지 않았다.
- 최초 경로 GREEN: 1개 통과. 원 응답·receipt 보존, 필드 제거 후 새 검수, 후보 2개 게시와
  API 장부 반영, 범위 REVIEW 유지까지 확인했다.
- 확장 실행의 중간 결과는 47개 통과/3개 실패, 이어서 43개 통과/1개 실패였다.
  실패는 변경 상태의 DB 제약조건과 과거 producer 기본 인자를 맞추는 fixture 수정으로
  이어졌다. 다음 실행은 38개 통과/teardown 1개 오류였다. 이전 schema가 허용한
  동일 작업 receipt의 업그레이드를 새 제약이 막아, 과거 모든 값을 보존하도록 수정했다.
- 최종 관련 통합: `pytest -m integration`으로 `test_initial_policy_draft_normalization.py`,
  `test_initial_policy_draft_revision.py`, `test_policy_replay_publication.py`,
  `test_retained_label_spacing_revision.py`, `test_policy_draft_replay.py`,
  `test_retained_replay_revision.py`를 실행해 41개 통과(71.70s)했다. 이 실행은
  원본 변경 중 호출 차단, 동시 정규화 수렴, 원 응답 hash/UUID 표기 보존, 검수 실패 재개,
  v5→v6 새 검수, 잘못된 버전/최소화 거부, 기존 receipt의 모든 값 보존을 포함한다.
- `corepack pnpm install --frozen-lockfile` 뒤 `corepack pnpm web:check`: format/lint/type,
  Web 250개, production build 통과. 잠금 파일 변경 없음.
- `TMPDIR=/tmp uv run --frozen ruff format --check .`(941개), `ruff check .`,
  `mypy apps/api/src workers/analyzer/src scripts`(365개): 통과.
- `TMPDIR=/tmp uv run --frozen pytest apps/api/tests workers/analyzer/tests scripts/tests -q`:
  4,054개와 subtest 3개 통과(40.15s), integration 905개 제외.
- `check_documentation.py`(50개), `check_repository_safety.py`(1,157개), `check_contracts.py`,
  `check_containers.py`, `check_workflows.py`, 브랜치 규칙과 `git diff --check`: 통과.
  컨테이너 검사는 정적 검사이며 이미지 빌드 결과가 아니다.
- 전체 `pytest -m integration apps/api/tests workers/analyzer/tests -q`: 888개 통과,
  6개 실패, 3,678개 제외(1,872.30s). 복구용 컨테이너 환경값 누락 1개, 과거 게시
  fixture가 새 v6를 사용한 4개, 등록 테스트가 새 필수 예산·범위 경로를 누락한 1개였다.
  제품 검사를 약화하지 않고 과거 fixture를 v5에 고정하고 등록 테스트는 실제 새 경로와
  불충분한 OCR 근거의 불변 원 응답·빈 정규화 초안·PARTIAL/REVIEW 보존을 확인하도록 바꿨다.
- 수정 범위의 3개 모듈 재실행은 5개 통과/1개 실패(24.81s)였다. 마지막 실패는 부분 처리
  종료 상태의 기대값을 `permanently_failed`로 수정했다. 중단 직전 후속 실행 결과는
  복구할 수 없어 통과로 기록하지 않는다. 최종 입력의 상세 검증은 PR CI에서 확인한다.

## Verification cadence update — 2026-09-12

사용자 요청에 따라 root/API/Worker/Web AGENTS, CONTRIBUTING, 검증 전략, 에이전트 안내,
로드맵과 두 FamilyCare 스킬을 함께 변경했다. 각 커밋은 문법·형식·계획 범위만 확인하며,
계획한 PR 작업을 모두 끝낸 뒤 상세 검증을 한 번 집중 수행한다. 수정 후에는 영향받는
검사만 다시 실행하고 동일 입력의 CI 결과를 활용해 로컬 전체 suite·빌드를 중복하지 않는다.
RED/GREEN 반복 실행 의무를 제거했으며 필요한 테스트 작성과 CI required checks는 유지했다.
이 변경은 실제 CI 명령·환경과 대조했고 두 스킬의 `quick_validate.py`, 문서 구조 검사와
`git diff --check`가 통과했다. 지침 문구를 복제하는 테스트나 전체 코드 재검증은 추가하지 않았다.

## Remaining acceptance

이 변경 버전의 실제 문서·외부 provider·비공개 runtime upgrade·Windows/모바일 수용은
아직 실행하지 않았다. 실제 가입/신원·판본 연결과 최종 B02/B07/B08 수용은 해당 이슈에서
계속 추적한다. schema74의 기존 원 응답에 대한 읽기 전용 사전 점검에서는 정규화된
담보 3개가 같은 계약·회원에 연결되고 benefit_type도 남았지만, 사용할 수 있는 부모
계약이 없어 추가 verifier 호출만으로 장부에 반영할 수 없었다. 원문·응답·계획은 그대로이며
DB 쓰기·provider 호출·게시 모두 0회다. 따라서 추가 유료 검수는 실행하지 않았다.
제품 검증과 별개의 이 남은 연결 문제 때문에 마일스톤 완료·최종 태그·배포를 주장하지 않는다.
