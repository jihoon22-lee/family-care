# FamilyCare

FamilyCare는 가족이 가입한 보험의 증권과 약관을 연결해 상황별 청구 후보를 찾고, 각 결과에 원문 근거와 미확인 조건을 제시하는 개인용 PWA입니다.

이 앱의 결과는 보험금 지급 결정을 대신하지 않습니다. 실제 지급 여부는 계약 상태, 사고·질병 사실, 제출 서류, 보험사의 심사에 따라 달라집니다. FamilyCare는 확인 가능한 가입 담보와 약관 근거를 바탕으로 준비할 항목을 좁히는 의사결정 보조 도구입니다.

## Current status

Phase 0 (Foundation)은 완료되었습니다. [PR #1](https://github.com/jihoon22-lee/family-care/pull/1)의 merge commit은 `0f632989df891ae944c012bfcce6c838009867a9`이며, PR과 post-merge GitHub Actions의 일곱 required job이 모두 성공했습니다. 이 완료는 문서·코드·CI 경계를 확인한 것이며, 태그 생성, GHCR publish, Cloud Run, 실제 자료 검증을 포함하지 않습니다.

현재는 Phase 1 (Synthetic PDF Ingestion) 계획 단계입니다. Phase 1 구현과 CI는 처음부터 만든 합성 PDF만 사용하며, 실제 PDF나 private external root를 열지 않습니다. 테스트는 합성 fixture를 checkout 밖의 임시 root에 복사해 실행합니다. 인증 provider는 Phase 7 범위이므로 Phase 1 endpoint는 local synthetic-only 개발 경계이며 production-safe endpoint로 간주하지 않습니다.

구현 순서와 단계별 수용 조건은 `docs/plan/000-project-roadmap.md`와 `docs/plan/002-synthetic-pdf-ingestion.md`에서 확인할 수 있습니다.

Foundation에서 구현하지 않은 범위:

- 실제 보험 PDF 수집·분석과 private-data acceptance
- 로그인과 운영 계정
- Google Drive 또는 외부 AI 연동
- 보험금 자격·금액 판정
- Cloud Run을 포함한 운영 배포

Phase 1에서도 OCR 실행, Policy Ledger, 보험금 자격·금액 판정은 구현하지 않습니다. 약관과 증권에 근거한 결과는 후속 단계의 명시적 규칙과 검수 경계를 거쳐야 합니다.

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
corepack pnpm@11.22.0 install --frozen-lockfile
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

## Container releases

정확한 `vMAJOR.MINOR.PATCH` Git 태그를 push하면 CI와 동일한 전체 검증 후 Web, API, Worker 이미지를 GHCR에 게시합니다. Git 태그는 되돌리기 어려운 공개 릴리스 메타데이터이므로 생성과 push는 사용자의 명시적인 릴리스 결정이 있을 때만 수행합니다.

게시 성공은 운영 배포 성공을 뜻하지 않습니다. 현재 자동화의 경계는 GHCR 이미지 게시까지이며, Cloud Run을 포함한 운영 배포는 모든 개발이 끝난 뒤 별도로 설계하고 승인합니다. `1.0.0` 이전에는 `latest` 태그를 만들지 않습니다.

## License

이 공개 저장소에는 라이선스가 부여되어 있지 않습니다. 별도 허가 없이 코드나 문서를 복제·수정·배포할 권한이 자동으로 제공되지 않습니다.
