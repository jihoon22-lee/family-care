# ADR 0005: GHCR-only continuous delivery

- Status: Accepted
- Date: 2026-08-23

## Context

Foundation은 재현 가능한 배포 산출물을 검증해야 하지만 Cloud Run, 운영 데이터베이스, 인증 도메인, 비밀 관리, 비용과 백업 정책은 전체 기능 개발 후 결정합니다. 운영 대상을 미리 고정하면 아직 모르는 요구를 workflow에 넣게 됩니다.

## Decision

`vMAJOR.MINOR.PATCH` Git 태그에서 전체 검증 후 Web, API, Worker 컨테이너를 GHCR에 게시합니다. workflow는 `GITHUB_TOKEN`과 `packages: write` 최소 권한만 사용합니다. Cloud Run, SSH, Kubernetes, 운영 migration은 실행하지 않습니다.

태그 생성은 사용자 요청이 필요한 별도 릴리스 행위입니다. PR 검증에서는 이미지를 build만 하고 push하지 않습니다.

## Alternatives

### Cloud Run direct deployment

빠른 운영은 가능하지만 미확정 리전, DB, 인증, rollback 결정을 암묵적으로 고정합니다.

### No release automation

초기 파일은 적지만 image build가 실제 릴리스 때 처음 검증되는 위험이 있습니다.

### Mutable latest-only image

사용은 간단하지만 버전과 커밋을 추적하기 어렵고 rollback 근거가 약합니다.

## Consequences

- 태그마다 같은 버전의 세 이미지를 추적할 수 있습니다.
- GHCR 성공은 운영 배포 성공이 아닙니다.
- 운영 설계가 승인되면 기존 이미지를 승격하는 별도 workflow 또는 시스템을 추가합니다.
- `latest` 정책과 provenance·SBOM 강화는 첫 안정 릴리스 전에 별도 결정합니다.
