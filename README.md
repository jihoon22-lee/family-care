# FamilyCare

FamilyCare는 가족이 가입한 보험의 증권과 약관을 연결해 상황별 청구 후보를 찾고, 각 결과에 원문 근거와 미확인 조건을 제시하는 개인용 PWA입니다.

이 앱의 결과는 보험금 지급 결정을 대신하지 않습니다. 실제 지급 여부는 계약 상태, 사고·질병 사실, 제출 서류, 보험사의 심사에 따라 달라집니다. FamilyCare는 확인 가능한 가입 담보와 약관 근거를 바탕으로 준비할 항목을 좁히는 의사결정 보조 도구입니다.

## Current status

Web/API/Worker의 소스 버전은 `0.5.2`, 지원 DB schema는 `0069_policy_draft_replay`입니다.
기본 사건 조회는 외부 AI 없이 로컬 서버에서 관련 후보·지원되는 금액/식·가정·부족한 정보를
제공합니다. 선택 AI 검수는 원래 답변과 분리하며, 검증된 근거만 같은 결정론적 엔진으로
다시 계산합니다. 결과에서 근거와 조건을 확인하고 청구 준비 기록으로 이어갈 수 있습니다.

문서별 보관 구조·범위·가입 원장·약관 지식과 계산 규칙은 출처와 버전을 유지합니다. 명시적
재처리는 원본 응답과 교정을 보존하며, 로컬 근거 검사를 통과한 파생 초안만 새 독립 검수에
보낼 수 있습니다. 앱 사용자와 보험 대상자를 분리하고 session·가정 범위·CSRF·암호화·
앱 셸만 캐시하는 경계를 유지합니다.

기존 자료의 백업/격리 복원과 원본 행 보존, 제한된 실제 가입 원문의 자동 원장 반영,
AI-off 인증/결과/청구/원문 조회, Windows Chrome headless 사용 경로를 확인했습니다.
**전체 자료 지원은 PARTIAL**입니다. 읽지 못한 자료와 미처리 구간, 자동 약관 판본/의미 지식,
과거 별칭의 정확한 원문 연결은 미지원·미해결 범위를 보존합니다. 일부 성공을 전체 자료의
해석 또는 지급 정확도로 표시하지 않습니다. 모바일 실기기/PWA 설치와 전체 재해 복구 훈련은
미검증입니다.

현재 릴리스·실제 적용 상태와 소스별 검증은
[v0.5 수용 원장](docs/release/v0.5.0-verification.md)에서 확인합니다. 공개 태그의 변경사항,
workflow와 Web/API/Worker의 immutable digest는
[GitHub Releases](https://github.com/jihoon22-lee/family-care/releases)에 기록합니다.
실행체의 데이터 전환·재시작 확인은 이미지 게시와 별도로 기록합니다.

제품 기준은 [v0.5 설계](docs/design/v0.5-local-first-claim-guidance.md), 실행 순서와 과거
수용 기록은 [로드맵](docs/plan/000-project-roadmap.md)을 따릅니다. 기존 v0.1~v0.4의
기록과 snapshot 호환성은 유지합니다.

현재 범위에서 제외하는 항목:

- Google Drive 자동 연동
- 보험사 직접 청구와 의료 문서 file 보관
- Cloud Run과 공개 인터넷 배포
- LUKS, BitLocker, WSL swap과 고정 크기 암호화 volume 변경
- 다중 가정, 공개 가입, 계정 초대와 역할 관리

선택적 OpenAI 연동은 Worker의 기존 키 설정을 사용합니다. AI는 구조화와 설명을 보조하며
가입 판정이나 지급액을 단독 결정하지 않습니다. 공개 CI는 합성 자료·모의 provider만 사용합니다.

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
Web gateway -> FastAPI modular monolith
                    |
       PostgreSQL <-> Analyzer Worker
                          |
              local OCR (OCR_REQUIRED only) + OpenAI verification
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

개발 의존성과 검증은 다음 명령으로 시작합니다.

```bash
cp .env.example .env
corepack pnpm@11.22.0 install --frozen-lockfile
uv sync --all-packages --group dev
make check
```

Compose 실행은 저장소 밖 import root, Worker 전용 32-byte key file, 미사용 Web port와 DB migration 준비가 필요합니다. 실제 실행 전 `docs/guide.md`의 **Private local Docker runtime** 절차를 따릅니다.

종료할 때는 다음을 사용합니다. 데이터베이스 볼륨은 자동 삭제하지 않습니다.

```bash
ENV_FILE=.env.private make down
```

세부 절차와 안전한 외부 데이터 경로 설정은 `docs/guide.md`를 따릅니다.

## Decision contract

보험 담보 조건은 다음 세 값만 사용합니다.

- `MATCH`: 현재 확인된 사실이 규칙과 일치합니다.
- `NO_MATCH`: 확인 가능한 근거로 규칙과 일치하지 않습니다.
- `UNKNOWN`: 정보나 최신 계약 상태가 부족해 결론을 내릴 수 없습니다.

약관 검색 결과만으로 가입 여부나 지급 여부를 추정·확정하지 않습니다. 증권에서 실제 가입 Rider를 확인한 후 관련 약관 조항을 연결하며, 검색 결과는 bounded Evidence와 1-based physical page 근거만 제공합니다.

## Development

작업 전 `AGENTS.md`와 관련 설계·계획 문서를 읽어야 합니다. 브랜치는 `<type>/<kebab-case>`, 커밋은 Conventional Commits를 따릅니다. 기본 검증 명령은 `make check`이며, WSL 메모리 압력을 줄이기 위해 로컬 검증은 직렬로 실행합니다.

보안 문제나 민감정보의 실수 커밋은 공개 이슈로 신고하지 말고 `SECURITY.md` 절차를 따릅니다.

## Container releases

정확한 `vMAJOR.MINOR.PATCH` Git 태그를 push하면 CI와 동일한 전체 검증 후 Web, API, Worker 이미지를 GHCR에 게시합니다. Git 태그는 되돌리기 어려운 공개 릴리스 메타데이터이므로 생성과 push는 사용자의 명시적인 릴리스 결정이 있을 때만 수행합니다.

게시 성공은 운영 배포 성공을 뜻하지 않습니다. 현재 자동화의 경계는 GHCR 이미지 게시까지이며, Cloud Run을 포함한 운영 배포는 모든 개발이 끝난 뒤 별도로 설계하고 승인합니다. `1.0.0` 이전에는 `latest` 태그를 만들지 않습니다.

현재 릴리스 계열은 `v0.1.0`부터 `v0.4.0`까지입니다. `v0.4.0`은 정리된 CHANGELOG, 일치하는
Web/API/Worker 버전, 전체 SHA tag와 서로 다른 세 immutable image digest를 tag workflow와
GitHub Release에서 함께 확인합니다.

## License

이 공개 저장소에는 라이선스가 부여되어 있지 않습니다. 별도 허가 없이 코드나 문서를 복제·수정·배포할 권한이 자동으로 제공되지 않습니다.
