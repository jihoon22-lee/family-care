# Retained policy draft replay

기존 구조화 응답의 일부 선택 필드에 근거가 없거나 내부 Rider key가 빠지면, 충분한
근거가 있는 나머지 사실도 재구조화 요청이 필요했다. 별도 retained v4 작업에서 기존
응답을 로컬 파생 초안으로 축소하고 독립 검수만 다시 실행할 수 있게 했다.

- `ai/policy_draft_normalization.py`는 기존 grounder를 재사용한다. 필수값 미증명 후보와
  primary 인용을 잃은 범위는 미해석으로 남기고 선택 필드 손실을 receipt에 기록한다.
  빠진 내부 key만 증명된 Rider 이름의 값·인용 그대로 복사하며 승인 상태를 만들지 않는다.
- `policy_draft_replay.py`는 원본 SUCCEEDED 요청과 새 작업의 가정·구성원·문서·추출·
  현재 세대·최소화 fingerprint·전체 envelope를 고정한다. 준비·전송 직전·저장에서
  lease·구성원과 동일 초안을 다시 검증한다. 새 verifier도 일반 durable 예산을 사용한다.
- `0069_policy_draft_replay`는 v2/v3를 보존하는 v4 처리와 변경 불가능한 receipt를 추가한다.
  원본 응답은 현재 prompt의 cache hit가 아니며 과거 요청/검토 기록을 바꾸지 않는다.
  후보 generator는 파생 revision으로 표시한다. 손실이 있는 범위는 나머지 후보의
  API 게시가 가능해도 REVIEW로 남는다. API/Worker는 schema 0069를 요구한다.

## Verification

2026-09-09, base `f3333be`, normalization 통합 commit `c3d4d51`과 위 후속 파일의
미커밋 변경에서 실행했다. 모두 합성 자료이며 전용 PostgreSQL/WSL 환경을 사용했다.

- 신규 normalization 모듈 부재 RED 이후 신규/기존 grounder unit 63건 통과.
- replay repository 부재 integration RED 1건 실패(2.41s) 후 새 receipt·권한/lease·
  이력 보존 및 v2/v3/v4 migration/재처리 PG 43건 통과(62.52s).
  최초 env 미설정 실행은 collection exit 4로 결과 없음이다.
- 원본 응답 변경 시도는 기존 DB 불변 trigger에서 차단됨을 확인했다. 초기 테스트의
  변경 가능 가정을 수정했다. 새 downgrade 테스트의 미생성 plan 조회도 준비 후 비교로
  수정했다. 이후 추가 손실/초안 불일치 포함 replay PG 7건 통과(14.99s).
- 실제 runner·durable 예산·합성 verifier·프로그램 검사·API 원장 게시 PG 1건 통과(3.76s).
  근거 있는 정책/Rider는 게시되고 미지원 날짜 손실은 REVIEW, 원본 응답은 동일하다.
  초기 fixture의 original 작업이 완료 전이어서 재처리가 거부된 결과를 보존하고
  기존 작업 완료 후 시험했다. 제품의 source 가드는 바꾸지 않았다.
- replay runner/normalization/기존 runner unit 35건 통과(0.90s): 새 verifier 1회만 예약,
  stale·예산 소진·verifier 실패 때 게시하지 않음.
- `uv run pytest apps/api/tests workers/analyzer/tests scripts/tests -q`: 3744 passed,
  821 integration deselected, 3 subtests passed(42.11s). 추가 게시 PG는 이후 별도 실행했다.
- 전체 Ruff format/check, mypy 356개 소스, contracts, container/workflow 정책 통과.
- `corepack pnpm web:check`: format/lint/type, 31 files/238 tests(50.51s), build 통과.

- 문서 계약 50개·저장소 안전 1106 paths, 최종 전체 Ruff 890 files와 diff·브랜치 규약
  통과. 커밋 제목과 GitHub CI는 커밋/게시 뒤 확인한다.

이 기록은
외부 AI의 실제 품질이나 보호 자료의 완전한 지원, 최종 activation·릴리스 성공을 의미하지
않는다. 해당 수용은 B07/B08에서 별도 소스·schema·환경에 연결한다. 실제 자료·개수·
식별자·경로·키는 공개 파일에 기록하지 않는다.
