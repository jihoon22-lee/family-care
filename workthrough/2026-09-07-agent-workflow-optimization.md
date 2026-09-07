# Agent workflow optimization

## Change

v0.5 구현 전에 현재 마일스톤과 개발 지침을 연결하고, 작업 종류에 맞는 검증과 스킬
선택을 명확히 했다. 루트 AGENTS의 공통 경계는 유지하고 Web/API/Worker 추가 지침,
저장소 스킬 `familycare-milestone-work`·`familycare-verify`, 공식 근거를 담은 작업 안내를 추가했다.
브랜치·커밋 상세는 CONTRIBUTING, 검증 명령은 test-strategy로 옮겼다.

개인 `landing-page-guide-v2`는 내부 업무 화면을 제외하고 기존 기술 스택을 존중하도록
범위를 수정했다. 개인 `web-search`는 작업 중 다른 변경으로 경로·실행 조건과 격리 실행
안내가 이미 수정되어 덮어쓰지 않았다. 두 개인 스킬은 이 저장소 커밋에 포함하지 않는다.

## Decisions

- 사용자 지정 컨텍스트 윈도우·자동 압축 한계와 개인 설정 파일은 변경하지 않았다.
- 현재 선택 모델을 강제로 변경하지 않고 공식 모델 역할에 따른 선택 원칙만 기록했다.
- v0.4 판정 규칙을 현재 호환성 기준으로 식별했다. v0.5의 의미 변경은 #61/B01에서
  실패하는 테스트·계약·최소 구현과 함께 진행한다. 이번 변경은 제품 구현이 아니다.
- 읽기 전용 검토, 문서 변경, 개발 중 테스트, 코드/실행 설정 PR 완료를 구분한다.
  기존 required CI와 개인정보·인증·이력 보존 경계는 유지했다.
- 검사 증거를 실행 입력에 연결하고 중복 재실행을 줄인다. Web/Python/Docker 직렬 실행을 유지한다.
- 독립된 읽기 중심 위임은 최대 2개로 제한했다. 이번 독립 리뷰에서 발견한 커밋 검사
  `--range` 누락을 수정했다. 인자 없는 명령은 브랜치만 검사한다고 명시했다.

## Verification

2026-09-07 로컬 WSL에서 실행했다. 기준 소스는 `9e61ad6142c5a741d676da29a5231d2caaa9d9f1`이며
그 위의 변경은 이번 커밋의 Markdown 지침·계획·기록뿐이다. 앱 코드·생성 계약·잠금 파일·CI
설정·개인 설정은 바꾸지 않았다. 제품 테스트 이후 변경도 문서 기록과 검증 안내에 한정한다.

| 실행 명령/검사 | 실제 결과 |
|---|---|
| `python3 scripts/check_documentation.py` | PASS, 필수 문서 50개 |
| `python3 scripts/check_repository_safety.py` | PASS, 최종 기록 포함 699개 경로 |
| `corepack pnpm web:check` | PASS: format/lint/type, 23개 파일·152개 테스트, Vite/PWA build |
| `TMPDIR=/tmp uv run ruff format --check .` | PASS, Python 파일 509개 |
| `TMPDIR=/tmp uv run ruff check .` | PASS |
| `TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts` | PASS, source 파일 214개 |
| `TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q` | 1,623 passed, 186 deselected, 3 subtests passed |
| `TMPDIR=/tmp uv run python scripts/check_contracts.py` | PASS, OpenAPI/schema/생성 Web 계약 |
| `TMPDIR=/tmp uv run python scripts/check_containers.py` | PASS, 이미지 정의 3개·Compose 서비스 4개; 정적 검사 |
| `TMPDIR=/tmp uv run python scripts/check_workflows.py` | PASS |
| `TMPDIR=/tmp uv run python scripts/check_git_conventions.py` | PASS, 현재 브랜치; 커밋 전이라 제목 검사 0개 |
| skill-creator의 `quick_validate.py`를 각 스킬 디렉터리에 실행 | 저장소 스킬 2개·개인 검색/랜딩 스킬 2개 모두 PASS |
| 검색 스킬 디렉터리에서 `python3 scripts/search.py --help` | PASS, CLI 인자 확인; 검색·의존성 설치 미실행 |
| 변경 문서의 로컬 Markdown 링크·heading anchor 검사 | PASS, 최종 변경/신규 문서 12개에서 링크 24개 |
| `git diff --check` | PASS |

새 동작 코드가 없는 지침 작업이므로 문자열을 복제하는 단위 테스트는 추가하지 않았다.
스킬 구조와 명령 확인은 실제 자동 선택의 품질이나 장기 작업 효율을 증명하지 않는다.

## Remaining boundaries

PostgreSQL integration, browser E2E, 실제 이미지 빌드·운영 실행, 실제 Windows/모바일,
외부 AI·실제 검색·보험 자료 검증은 이번 작업에서 실행하지 않았다. 개인정보·실제 자료를
탐색하거나 외부 전송하지 않았고 다른 세션의 프로세스·컨테이너·변경을 정리하지 않았다.

GitHub 이슈 수정·PR·Actions 실행·merge·태그·배포는 수행하지 않는다. 로컬 커밋의
SHA와 커밋 후 범위 검사는 최종 보고로 확인한다. 다음 제품 작업은 #61/B01이다.
