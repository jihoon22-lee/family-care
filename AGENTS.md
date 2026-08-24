# FamilyCare development instructions

이 문서는 FamilyCare 저장소에서 작업하는 사람과 자동화 에이전트가 반드시 따라야 할 개발 규칙입니다. 사용자 요청이 이 문서보다 우선하지만, 개인정보 이동·외부 전송·삭제·운영 변경처럼 권한 범위를 넓히는 행동은 명시적 승인이 필요합니다.

## Start every task

1. `pwd`, `git status --short --branch`, `git remote -v`로 정확한 저장소와 변경 상태를 확인합니다.
2. 저장소 루트부터 현재 파일까지 적용되는 `AGENTS.md`를 읽습니다.
3. `docs/design/`의 관련 설계와 `docs/plan/`의 실행 계획을 읽습니다.
4. 기존 변경은 사용자 소유로 간주하고, 요청과 무관한 수정·정리·포맷 변경을 하지 않습니다.
5. 기능 또는 동작 변경은 테스트를 먼저 작성하고 예상한 이유로 실패하는지 확인합니다.
6. 작업 범위, 외부 의존성, 직접 검증할 수 없는 항목을 구분합니다.

## Non-negotiable privacy rules

- 실제 보험증권, 약관, 가입제안서, 의료 문서를 저장소에 복사하지 않습니다.
- 실제 문서의 추출 텍스트, 표, 스크린샷, 페이지 이미지, OCR 결과, 임베딩도 금지합니다.
- 실제 이름, 이메일, 주소, 전화번호, 생년월일, 증권번호, 보험금액, Drive 식별자를 코드·문서·fixture·테스트·로그에 기록하지 않습니다.
- 실제 값을 금지 패턴이나 테스트 fixture에 넣지 않습니다. 검사는 형식, 확장자, 경로, 합성 문자열로 작성합니다.
- 예시는 `Admin A`, `Family Member A`, `Sample Policy`, `synthetic-policy-001`처럼 처음부터 만듭니다.
- 실제 데이터를 가리거나 일부 값만 바꾼 자료는 합성 데이터가 아닙니다.
- 실제 데이터 경로는 저장소 밖 절대경로만 허용합니다. 사용자 승인 없이 해당 경로를 탐색하거나 파일을 이동하지 않습니다.
- PDF 암호 해제본과 OCR 중간 파일은 작업별 임시 디렉터리에 만들고 성공·실패·취소 시 삭제합니다.
- 브라우저 서비스 워커는 앱 셸만 캐시합니다. PDF, 보험 데이터, 의료정보, API 응답은 캐시하지 않습니다.
- 로그에는 문서 본문, 검색어, 진단명, 개인 식별자, 인증 토큰, 실제 파일 경로를 남기지 않습니다.
- 민감정보가 Git 이력에 들어갈 가능성이 발견되면 추가 변경과 push를 중단하고 사용자에게 범위와 복구 선택지를 보고합니다.

## Branch and commit conventions

### Branches

작업 브랜치는 다음 정규 형식을 사용합니다.

```text
<type>/<kebab-case-description>
```

허용 `type`:

- `feat`: 사용자 기능
- `fix`: 버그 수정
- `docs`: 문서 전용
- `build`: 빌드·환경·컨테이너
- `ci`: CI/CD와 저장소 자동화
- `chore`: 제품 동작을 바꾸지 않는 유지보수
- `refactor`: 동작을 유지하는 코드 구조 변경
- `test`: 테스트 전용
- `release`: 릴리스 준비

예: `build/project-foundation`, `feat/policy-ledger`.

`main`, 대문자, 밑줄, 공백, 의미 없는 번호만 있는 이름은 feature 브랜치에 사용하지 않습니다. 사용자 요청 없이 force push하거나 이미 공유된 브랜치 이력을 다시 쓰지 않습니다.

GitHub Dependabot이 소유한 `dependabot/<ecosystem>/<slug>` 브랜치만 자동화 예외입니다. 사람이 만드는 브랜치에는 이 예외를 사용하지 않습니다.

### Commits

모든 커밋 제목은 Conventional Commits를 따릅니다.

```text
<type>(<optional-scope>): <imperative description>
```

- 허용 type은 `feat`, `fix`, `docs`, `build`, `ci`, `chore`, `refactor`, `test`, `perf`, `style`, `revert`입니다.
- 제목은 영문 소문자 type으로 시작하고 72자를 넘지 않으며 마침표로 끝내지 않습니다.
- 하나의 커밋은 하나의 검토 가능한 목적을 가집니다.
- 호환성을 깨는 변경은 `type(scope)!:`와 `BREAKING CHANGE:` footer를 사용합니다.
- 코드 변경과 해당 테스트, 필요한 문서 변경은 같은 논리 단위에 포함합니다.
- 임시 저장, 의미 없는 메시지, 검증을 통과하지 않은 커밋은 공유하지 않습니다.

예:

```text
docs(plan): define foundation implementation
feat(api): add health endpoints
ci: validate repository safety
```

## Architecture boundaries

- `apps/web`은 UI와 브라우저 경계만 담당하며 보험 판정 규칙을 소유하지 않습니다.
- `apps/api`는 HTTP 계약과 모듈형 모놀리스의 유스케이스를 제공합니다.
- `workers/analyzer`는 비동기 문서 분석만 담당하며 사용자 응답 문구를 소유하지 않습니다.
- `packages/contracts`는 언어 중립 계약의 배포 경계입니다. 구현 타입을 수동으로 중복 정의하지 않습니다.
- PostgreSQL을 초기 영속성과 작업 큐로 사용합니다. 측정된 필요 없이 Redis, Kafka, 별도 검색 서비스를 추가하지 않습니다.
- 앱 사용자 `AppUser`와 보험 대상 `FamilyMember`·`PolicyParty`를 분리합니다.
- 삭제는 soft delete와 휴지통을 기본으로 하며, 물리 삭제는 별도 보존 정책과 승인 후 수행합니다.

## Insurance decision rules

- 약관에 존재한다는 이유만으로 가입 Rider로 판단하지 않습니다.
- 증권과 최신 계약 상태에서 실제 가입과 유효성을 먼저 확인합니다.
- 판정 값은 `MATCH`, `NO_MATCH`, `UNKNOWN`만 사용합니다.
- 정보 부족, 계약 상태 미확인, 갱신 상태 미확인은 오류가 아니라 `UNKNOWN`입니다.
- `NO_MATCH`는 결정적인 불일치 근거가 있을 때만 사용합니다.
- 정액형과 실손형을 분리합니다. 실손 금액은 영수증과 자기부담 조건 없이 추정 확정하지 않습니다.
- AI는 구조화와 설명을 보조할 뿐, 핵심 자격 판정과 계산을 단독 수행하지 않습니다.
- 결과에는 증권·약관 문서와 페이지·조항 근거가 있어야 하며, 근거 없는 확정 표현을 금지합니다.

## Implementation workflow

1. 계획의 현재 Task를 `in_progress`로 표시합니다.
2. 실패하는 최소 테스트를 먼저 작성합니다.
3. 테스트가 기능 부재 때문에 실패하는지 확인합니다.
4. 테스트를 통과하는 최소 구현을 작성합니다.
5. 관련 전체 테스트와 정적 검사를 직렬로 실행합니다.
6. `git diff --check`와 저장소 안전 검사를 실행합니다.
7. 변경을 검토한 뒤 Conventional Commit으로 커밋합니다.
8. 다음 Task로 이동합니다.

설정 파일과 순수 문서처럼 직접 단위 테스트하기 어려운 항목은 구조 검사 스크립트, 실제 빌드, `docker compose config`, workflow 정책 검사로 검증합니다.

## Required verification

완료 주장은 같은 작업 턴에서 실행한 최신 증거가 있어야 합니다. 일부 검사 통과를 전체 통과로 확대 해석하지 않습니다.

기본 순서:

```bash
python3 scripts/check_documentation.py
python3 scripts/check_repository_safety.py
corepack pnpm@11.22.0 web:check
TMPDIR=/tmp uv run ruff format --check .
TMPDIR=/tmp uv run ruff check .
TMPDIR=/tmp uv run mypy apps/api/src workers/analyzer/src scripts
TMPDIR=/tmp uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q
TMPDIR=/tmp uv run python scripts/check_contracts.py
TMPDIR=/tmp uv run python scripts/check_containers.py
TMPDIR=/tmp uv run python scripts/check_workflows.py
git diff --check
```

- 프런트엔드와 Python 검사는 동시에 실행하지 않습니다.
- WSL에서 `TEMP` 또는 `TMP`가 Windows mount를 가리키면 Python 명령에 `TMPDIR=/tmp`를 사용합니다.
- 컨테이너 이미지는 하나씩 빌드합니다.
- Docker 작업 전에 메모리·swap과 기존 컨테이너·포트를 확인합니다.
- 다른 세션이 소유한 프로세스나 컨테이너를 중지하지 않습니다.
- Windows 브라우저, 실제 모바일 PWA, 실제 보험 자료, 운영 환경처럼 직접 확인하지 않은 항목은 미검증으로 남깁니다.

## CI and release

- PR과 `main` CI는 실제 비밀값, 외부 AI, Google Drive, 실제 PDF 없이 실행되어야 합니다.
- GitHub Actions는 전체 커밋 SHA로 고정하고 Dependabot으로 갱신합니다.
- `pull_request_target`에서 외부 코드를 실행하지 않습니다.
- 릴리스 workflow만 `packages: write`를 사용합니다.
- `vMAJOR.MINOR.PATCH` 태그는 Web/API/Worker 이미지를 GHCR에 게시하지만 운영 배포를 의미하지 않습니다.
- 사용자 요청 없이 태그를 만들거나 push하지 않습니다.
- Cloud Run과 운영 배포는 별도 승인된 설계 전까지 구성하지 않습니다.

## Security review expectations

- 취약점을 주장할 때 입력 source에서 권한 검사·정규화·저장·출력 sink까지 실제 경로를 추적합니다.
- 정적 분석만 수행했으면 동적 검증을 수행한 것처럼 표현하지 않습니다.
- 실패하거나 중단된 검사는 결과 없음으로 보존하고 깨끗한 검토로 보고하지 않습니다.
- 비밀값 검사를 우회하는 allowlist는 구체적인 합성 fixture와 최소 범위에만 허용하며 이유를 문서화합니다.

## Completion report

최종 보고에는 다음을 포함합니다.

- 목적과 사용자 영향
- 생성·수정한 주요 파일
- API·스키마·명령 계약
- 실행한 검증 명령과 실제 결과
- 미실행 또는 접근 불가능한 검증
- 보안·개인정보 경계 확인
- PR URL, GitHub Actions 결과, merge commit
- 태그·배포·실제 자료 검증 여부
