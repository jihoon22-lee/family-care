# ADR 0001: Modular monolith

- Status: Accepted
- Date: 2026-08-23

## Context

FamilyCare는 문서 수집, 보험 원장, 약관 검색, 판정, 청구 흐름을 포함하지만 초기 사용자는 두 명이고 하나의 가족 공간만 필요합니다. 기능 경계는 중요하지만 각 경계를 독립 배포하면 인증, 네트워크, 관측성, 데이터 일관성 비용이 커집니다. PDF 분석은 CPU·메모리와 실행 시간이 API 요청과 달라 별도 프로세스가 필요합니다.

## Decision

HTTP 업무 기능은 FastAPI 모듈형 모놀리스로 구현합니다. `identity`, `documents`, `policies`, `clauses`, `decisions`, `claims` 모듈은 공개 유스케이스와 계약으로 통신합니다. PDF 분석은 같은 저장소와 PostgreSQL을 사용하는 별도 Analyzer Worker 프로세스로 실행합니다.

Web, API, Worker는 각각 독립 컨테이너 이미지를 갖지만 API 내부 모듈은 별도 네트워크 서비스로 분리하지 않습니다.

## Alternatives

### Microservices

독립 확장에는 유리하지만 현재 규모에 서비스 인증, 분산 transaction, 배포·장애 지점이 과도합니다.

### Single process

구성이 단순하지만 긴 PDF 작업이 API latency와 장애 격리를 해칩니다.

### Serverless function per feature

초기 배포는 쉬울 수 있지만 로컬 재현, 긴 작업, 공통 계약, 데이터베이스 연결 관리가 복잡해집니다.

## Consequences

- 로컬과 운영 구성 요소 수가 제한됩니다.
- 모듈 경계를 코드 리뷰와 테스트로 강제해야 합니다.
- Worker는 별도 scaling과 resource limit을 가질 수 있습니다.
- 실제 측정으로 독립 배포 필요성이 나타나면 모듈 계약을 서비스 경계로 승격할 수 있습니다.
