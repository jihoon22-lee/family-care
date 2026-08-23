# FamilyCare

FamilyCare는 가족이 가입한 보험의 증권과 약관을 연결해 상황별 청구 후보를 찾고, 각 결과에 원문 근거와 미확인 조건을 제시하는 개인용 PWA입니다.

이 앱의 결과는 보험금 지급 결정을 대신하지 않습니다. 실제 지급 여부는 계약 상태, 사고·질병 사실, 제출 서류, 보험사의 심사에 따라 달라집니다. FamilyCare는 확인 가능한 가입 담보와 약관 근거를 바탕으로 준비할 항목을 좁히는 의사결정 보조 도구입니다.

## Current status

현재는 Foundation 단계입니다. 문서, 공개 저장소 보안 경계, 최소 Web/API/Worker 실행 환경, PostgreSQL, CI 및 GHCR 태그 릴리스를 먼저 구축합니다.

Foundation에서 구현하지 않는 범위:

- 실제 보험 PDF 수집·분석
- 로그인과 운영 계정
- Google Drive 또는 외부 AI 연동
- 보험금 자격·금액 판정
- Cloud Run을 포함한 운영 배포

구현 순서와 단계별 수용 조건은 `docs/plan/000-project-roadmap.md`에서 확인할 수 있습니다.

## Privacy boundary

이 저장소는 공개 저장소입니다. 다음 자료는 코드, 문서, fixture, 테스트, 로그, Git 이력에 포함할 수 없습니다.

- 실제 보험증권, 약관, 가입제안서, 병원 문서
- 실제 문서에서 추출한 텍스트, 표, 이미지, OCR 결과, 임베딩
- 실제 이름, 이메일, 주소, 전화번호, 생년월일, 증권번호, 보험금액
- 데이터베이스, 덤프, 로그, PDF 암호, 인증정보, 서비스 계정 파일
- 실제 Google Drive 폴더·파일 식별자

테스트 자료는 `Family Member A`, `Sample Policy`처럼 처음부터 만든 합성 데이터만 사용합니다. 실제 자료를 가리거나 일부 값을 바꾼 데이터는 합성 fixture로 인정하지 않습니다.

실제 자료를 사용하는 단계는 사용자의 명시적 승인 후 저장소 밖 디렉터리에서 별도 검증하며, 실행하지 않은 실제 자료 검증을 완료로 보고하지 않습니다.

## Architecture

```text
React PWA
    |
FastAPI modular monolith
    |
PostgreSQL <-> Analyzer Worker
```

- `apps/web`: React + TypeScript PWA
- `apps/api`: FastAPI API
- `workers/analyzer`: 비동기 PDF 분석 Worker
- `packages/contracts`: OpenAPI와 JSON Schema
- `fixtures/synthetic`: 공개 가능한 합성 데이터
- `infra`: Docker Compose와 컨테이너 정의
- `docs/design`: 기능별 상세 설계
- `docs/plan`: 단계별 구현 계획

장기 구조는 `docs/architecture.md`, 현재 기반 설계는 `docs/design/project-foundation.md`에 정리되어 있습니다.

## Quick start

필수 도구:

- Node.js 24 LTS
- Corepack과 pnpm 11.22.0
- Python 3.14
- uv 0.12.x
- Docker Engine과 Docker Compose v2

Foundation 구성이 완료된 뒤 다음 명령으로 시작합니다.

```bash
cp .env.example .env
corepack pnpm install --frozen-lockfile
uv sync --all-packages --group dev
make check
make up
```

종료할 때는 다음을 사용합니다. 데이터베이스 볼륨은 자동 삭제하지 않습니다.

```bash
make down
```

세부 절차와 안전한 외부 데이터 경로 설정은 `docs/guide.md`를 따릅니다.

## Decision contract

향후 보험 담보 조건은 다음 세 값만 사용합니다.

- `MATCH`: 현재 확인된 사실이 규칙과 일치합니다.
- `NO_MATCH`: 확인 가능한 근거로 규칙과 일치하지 않습니다.
- `UNKNOWN`: 정보나 최신 계약 상태가 부족해 결론을 내릴 수 없습니다.

약관 검색 결과만으로 가입 여부를 추정하지 않습니다. 증권에서 실제 가입 Rider를 확인한 후 관련 약관 조항을 연결하며, 결과에는 문서와 페이지 근거가 필요합니다.

## Development

작업 전 `AGENTS.md`와 관련 설계·계획 문서를 읽어야 합니다. 브랜치는 `<type>/<kebab-case>`, 커밋은 Conventional Commits를 따릅니다. 기본 검증 명령은 `make check`이며, WSL 메모리 압력을 줄이기 위해 로컬 검증은 직렬로 실행합니다.

보안 문제나 민감정보의 실수 커밋은 공개 이슈로 신고하지 말고 `SECURITY.md` 절차를 따릅니다.

## License

이 공개 저장소에는 라이선스가 부여되어 있지 않습니다. 별도 허가 없이 코드나 문서를 복제·수정·배포할 권한이 자동으로 제공되지 않습니다.
