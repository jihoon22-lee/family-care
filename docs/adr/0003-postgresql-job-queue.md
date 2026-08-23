# ADR 0003: PostgreSQL job queue

- Status: Accepted
- Date: 2026-08-23

## Context

PDF 분석은 요청보다 오래 걸리고 재시도가 필요합니다. FamilyCare는 이미 계약·규칙·검색을 위해 PostgreSQL이 필요하지만 초기 규모에서 별도 broker를 운영할 근거는 없습니다.

## Decision

초기 작업 큐는 PostgreSQL 테이블, `FOR UPDATE SKIP LOCKED`, lease, heartbeat, 멱등 키를 사용합니다. 상태는 queued, running, succeeded, retryable_failed, permanently_failed, cancelled로 제한합니다.

같은 transaction에서 업무 상태와 작업 enqueue를 일관되게 기록합니다. Worker는 lease 만료 작업을 회수할 수 있고 콘텐츠·설정 해시로 중복 결과를 막습니다.

## Alternatives

### Redis queue

성숙한 라이브러리가 있지만 별도 영속 서비스, 백업, 장애 일관성이 추가됩니다.

### Managed message queue

운영 확장성은 좋지만 Cloud 환경이 아직 결정되지 않았고 로컬·CI가 복잡해집니다.

### In-process background task

프로세스 종료 시 작업을 잃고 API와 resource 격리가 되지 않습니다.

## Consequences

- 초기 운영 구성과 transaction 모델이 단순합니다.
- 높은 처리량에서는 DB lock과 polling 비용을 측정해야 합니다.
- lease·재시도·dead-letter 동작을 직접 테스트해야 합니다.
- 처리량과 지연 기준을 넘으면 같은 envelope를 유지한 채 broker 도입 ADR을 작성합니다.
