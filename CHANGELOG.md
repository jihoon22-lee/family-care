# Changelog

FamilyCare의 주요 변경사항은 이 파일에 기록합니다. 형식은 [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)를 따르고, 버전은 [Semantic Versioning](https://semver.org/spec/v2.0.0.html)을 사용합니다.

## [Unreleased]

### Added

- 프로젝트 기반 설계와 전체 단계별 로드맵
- 공개 저장소 개인정보 경계와 개발 지침
- 브랜치 및 Conventional Commits 규칙과 자동 검증
- React PWA, FastAPI API, PostgreSQL 준비 상태, Analyzer Worker의 최소 실행 기반
- 버전이 지정된 OpenAPI·Analyzer 작업 계약과 완전 합성 fixture
- Alembic `0001_foundation` 마이그레이션 기준선
- 비루트 Web/API/Worker 이미지와 PostgreSQL 기반 로컬 Docker Compose 환경
- PR과 `main`에서 문서·보안·Web·Python·DB·컨테이너를 검증하는 CI
- Semantic-version Git tags publish independently versioned Web, API, and Worker images to GHCR after full Foundation validation.
- Published images receive version and 12-character commit SHA tags; pre-1.0 releases do not create `latest`.
- Phase 1 synthetic PDF ingestion design, implementation plan, and minimum Document/Extraction/AnalysisJob model boundary.
- ADR 0006 selecting pdfplumber 0.11.10, pypdf 6.16.2, and reportlab 5.0.1 for the permissive synthetic-only parser stack.

### Changed

- 로컬 검증 명령은 WSL 임시파일 경로와 고정 pnpm 버전을 재현 가능하게 사용합니다.
- Foundation completion is recorded at PR #1 merge commit `0f632989df891ae944c012bfcce6c838009867a9`; PR and post-merge CI had seven successful jobs. Tag/GHCR, Cloud Run, and real/private-data verification remain outside that evidence.
- Phase 1 documentation uses pnpm 11.22.0 and explicitly keeps implementation and CI synthetic-only.

### Deprecated

### Removed

### Fixed

- Worker 콘솔 진입점이 `--health` 인자를 읽고 종료 코드로 준비 상태를 보고합니다.
- 생성된 PWA 산출물이 소스 포맷 검사에 다시 포함되지 않습니다.

### Security

- 실제 보험 문서와 파생 데이터의 저장소 유입을 금지하는 정책 수립
- Phase 1 parser boundary records that passwords never enter database rows, job payloads, or logs and that production acceptance waits for an approved runtime boundary.

릴리스되지 않은 비어 있는 섹션은 다음 변경을 안정적으로 분류하기 위해 유지합니다.
