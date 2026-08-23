# FamilyCare guide

이 문서는 Foundation 개발환경과 공개 저장소의 안전한 사용법을 설명합니다. 실제 보험 분석 기능은 아직 제공하지 않습니다.

## Local development

### Prerequisites

- Git
- Node.js 24 LTS
- Corepack과 pnpm 11.22.0
- Python 3.14
- uv 0.12.x
- Docker Engine과 Docker Compose v2

버전을 확인합니다.

```bash
node --version
corepack pnpm@11.22.0 --version
python3 --version
uv --version
docker --version
docker compose version
```

### Setup

```bash
cp .env.example .env
corepack pnpm install --frozen-lockfile
TMPDIR=/tmp uv sync --all-packages --group dev
```

`.env`는 로컬 전용이며 commit하지 않습니다. `.env.example`의 개발용 placeholder를 운영 비밀값으로 사용하지 않습니다.

Codex/WSL에서 `TEMP`와 `TMP`가 `/mnt/c` 아래 Windows 임시 디렉터리를 가리키면 Python의 anonymous temporary file이 올바르게 동작하지 않을 수 있습니다. 프로젝트의 Python 검증은 `TMPDIR=/tmp`를 사용하며 Make target도 같은 값을 적용합니다.

### Run checks

전체 검증:

```bash
make check
```

WSL 메모리 압력을 피하기 위해 Web, Python, 컨테이너 검사를 직렬로 실행합니다. 실패한 단계만 다시 실행할 때도 다른 대규모 빌드와 동시에 실행하지 않습니다.

### Start services

```bash
make up
```

Foundation 서비스:

- Web: `http://127.0.0.1:8080`
- API liveness: `http://127.0.0.1:8000/health/live`
- API readiness: `http://127.0.0.1:8000/health/ready`
- PostgreSQL: 로컬 개발 포트 5432

종료:

```bash
make down
```

기본 종료는 데이터베이스 볼륨을 삭제하지 않습니다. 볼륨 삭제는 복구가 어려울 수 있으므로 이 가이드가 자동 명령으로 제공하지 않습니다.

## Repository workflow

브랜치 예:

```bash
git switch -c feat/policy-ledger
```

커밋 예:

```bash
git commit -m "feat(policies): add rider review state"
```

전체 규칙은 `AGENTS.md`의 Branch and commit conventions를 따릅니다. CI는 PR 브랜치명과 커밋 제목을 검사합니다.

## Safe data handling

### Public development

기본 개발과 CI는 `fixtures/synthetic/`만 사용합니다. 실제 보험 폴더나 Drive를 마운트하지 않습니다.

합성 fixture는 다음 조건을 모두 만족해야 합니다.

- 처음부터 가상으로 작성
- 실제 상품명·문장·금액·표를 복사하지 않음
- `Family Member A`, `Sample Policy` 등 명백한 가상 식별자 사용
- fixture README에 합성 출처 명시

### External local paths

실제 자료 사용 단계가 별도 승인되면 저장소 밖 경로만 지정합니다.

```dotenv
FAMILYCARE_DOCUMENT_ROOT=/absolute/path/outside/repository
FAMILYCARE_WORK_ROOT=/absolute/path/outside/repository/work
```

위 문자열은 경로 형식 예시이며 실제 경로가 아닙니다. 실제 값을 문서, 이슈, PR, 로그에 복사하지 않습니다.

Foundation Compose에는 문서 경로 mount가 없습니다. 실제 자료를 연결하는 변경은 Private-data Acceptance 단계의 설계와 사용자 승인이 필요합니다.

### Before every commit

```bash
python3 scripts/check_repository_safety.py
git diff --check
git status --short
```

실제 데이터가 의심되면 commit과 push를 중단하고 `SECURITY.md`를 따릅니다.

## Database migrations

로컬 PostgreSQL이 실행 중이고 `.env`의 연결정보가 설정된 경우:

```bash
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini upgrade head
TMPDIR=/tmp uv run alembic -c apps/api/alembic.ini current
```

마이그레이션은 코드와 함께 검토합니다. 운영 데이터베이스 마이그레이션 절차는 Production Deployment 단계에서 별도 결정합니다.

## Contracts

OpenAPI와 작업 JSON Schema가 구현과 일치하는지 확인합니다.

```bash
TMPDIR=/tmp uv run python scripts/check_contracts.py
```

계약을 깨는 변경은 새 버전, CHANGELOG, 소비자 마이그레이션 계획이 필요합니다.

## Container builds

로컬 자원을 먼저 확인합니다.

```bash
free -h
docker ps --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
```

다른 세션의 컨테이너를 중지하지 않습니다. 이미지는 한 번에 하나씩 빌드합니다.

```bash
docker compose --env-file .env -f infra/compose/compose.yaml build web
docker compose --env-file .env -f infra/compose/compose.yaml build api
docker compose --env-file .env -f infra/compose/compose.yaml build worker
```

## Releases

Foundation 릴리스 workflow는 `vMAJOR.MINOR.PATCH` 태그에서 다음 이미지를 GHCR에 게시합니다.

- `family-care-web`
- `family-care-api`
- `family-care-worker`

태그는 되돌리기 어려운 공개 릴리스 메타데이터입니다. 사용자 요청 없이 만들거나 push하지 않습니다. GHCR 게시 성공은 Cloud Run 또는 다른 운영 환경의 배포 성공을 의미하지 않습니다.

릴리스 workflow는 정확한 `vMAJOR.MINOR.PATCH`만 허용하며, 게시 전 문서·민감정보 경계·계약·Web·Python·PostgreSQL·세 컨테이너 빌드를 다시 검증합니다. 성공 시 각 이미지에는 버전 태그와 12자리 commit SHA 태그가 생성되며 `latest`는 생성하지 않습니다.

현재 CD 범위는 GHCR 게시까지입니다. Cloud Run 설정, 운영 비밀값, 운영 데이터베이스 마이그레이션, 실제 트래픽 전환은 포함하지 않습니다.

## Troubleshooting

- 도구 버전이 다르면 `.node-version`, `.python-version`, root package manager 설정을 우선합니다.
- lockfile이 바뀌면 의존성 선언 변경과 함께 검토합니다.
- 포트가 사용 중이면 점유 프로세스를 먼저 확인하고, 다른 세션 소유 프로세스를 임의 종료하지 않습니다.
- Docker 빌드가 메모리 부족으로 실패하면 동시 작업을 줄이고 swap·가용 메모리를 기록합니다.
- 실제 플랫폼에서 확인하지 못한 PWA 설치나 브라우저 동작은 로컬 단위 테스트 성공으로 대체하지 않습니다.
