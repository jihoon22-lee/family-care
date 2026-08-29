# Workthrough: Private runtime readiness tooling

**Date:** 2026-08-30

## Overview

FamilyCare private runtime에 실제 자료 세션과 독립적으로 준비할 수 있는 두 운영 경계를 추가했다.
첫째, 이미 quiesce된 PostgreSQL custom dump와 encrypted archive snapshot을 인증된 backup
set으로 묶고 검증한 뒤 fresh restore input으로 materialize한다. 둘째, DB의 managed archive
reference와 archive filesystem metadata를 deletion-free count report로 대조한다.

이번 작업은 실제 보험 자료, live PostgreSQL, 현재 archive volume, master-key bytes를 읽거나
이동하지 않았다. 구현과 CI는 처음부터 만든 custom-dump bytes와 encrypted objects만 사용했다.

## Context

- private import는 ciphertext write 뒤 DB success commit이 불명확하면 안전을 위해 object를
  보존한다. 기존 설계에는 이 상태를 실제 삭제 없이 식별할 read-only reconciler가 없었다.
- 기존 운영 가이드는 DB·archive·key가 하나의 복구 단위라고 정의했지만 snapshot artifact의
  shape, integrity, key binding, fresh-destination restore-input 계약은 없었다.
- 열려 있던 Dependabot PR #31은 Node 24 runtime에 `@types/node` 26을 함께 제안했고 생성된
  90-character commit subject가 repository safety policy를 위반했다.
- 사용자는 development revision CI가 먼저 통과한 뒤 documentation revision을 같은 PR에
  추가하고, 두 번째 문서 포함 CI 후에만 merge하도록 요청했다.

## Changes

### 1. Runtime-compatible dependency updates

- compatible `@types/react-dom` `19.2.5` patch와 lockfile만 적용하고 `@types/node`는 Node 24
  runtime major에 유지했다.
- npm `@types/node` semver-major ignore를 workflow policy 검사에 추가했다.
- npm/pip Dependabot group key를 `development-dependencies`에서 `dev`로 줄여 자동 commit
  subject가 72-character policy 안에 들도록 했다.
- policy 제거·오타·긴 group name을 회귀 테스트로 고정했다.

### 2. Authenticated offline backup set

`scripts/private_runtime_backup.py`는 다음 boundary를 제공한다.

- `capture`: pre-created `PGDMP` custom dump와 quiesced flat encrypted archive snapshot을 새
  mode-`0700` directory에 packaging한다.
- `verify`: exact three-file shape, mode, dump magic, SHA-256, bounded tar shape, recovery-key
  version과 key-derived manifest HMAC을 검증한다.
- `materialize`: 검증된 DB dump와 archive objects를 존재하지 않는 외부 destination에만
  복사한다. PostgreSQL이나 Compose를 시작하거나 `pg_restore`를 호출하지 않는다.

Source와 destination은 absolute repository-external path여야 한다. symlink, overlap, existing
destination, temporary/unexpected archive entry, invalid mode/size, wrong key, artifact or manifest
tamper, post-verification replacement는 stable error code로 거부한다. 실패 cleanup은 호출이
새로 만든 destination만 대상으로 하며 source, 기존 backup, DB, archive와 key를 수정하지 않는다.

Manifest에는 fixed artifact name, SHA-256, byte size, object count, non-secret key version과
HMAC만 있다. archive object key와 filesystem path는 없고 master-key recovery copy도 backup set에
포함하지 않는다. CLI는 private path를 argv로 받지 않고 local environment에서만 읽는다.

### 3. Count-only managed archive audit

Worker package의 `familycare-archive-audit` entrypoint는 다음 값만 출력한다.

```json
{"archive_object_count":2,"database_reference_count":2,"matched":2,"missing_references":0,"size_mismatches":0,"status":"clean","temporary_entries":0,"unexpected_entries":0,"unreferenced_objects":0}
```

- PostgreSQL startup option과 repeatable-read transaction을 모두 read-only로 설정한다.
- 모든 durable `managed_archives` row의 opaque key와 ciphertext size만 bounded query한다.
- archive directory에서 `nofollow` metadata만 읽고 ciphertext content는 열지 않는다.
- output과 error에는 object key, path, ciphertext, document metadata, database URL이 없다.
- exit status는 `0` clean, `1` findings, `2` error다.
- delete, quarantine, repair API가 없고 audit 전후 합성 entries의 byte identity를 확인한다.

Writer가 실행 중인 report는 authoritative하지 않다. 실제 cleanup은 finding count가 아니라
별도 retention policy, exact target identification, 사용자 승인을 요구한다.

## Code examples

Private path는 아래 command에 적지 않고 operator-owned local environment에서 설정한다.

```bash
TMPDIR=/tmp uv run python scripts/private_runtime_backup.py capture
TMPDIR=/tmp uv run python scripts/private_runtime_backup.py verify
TMPDIR=/tmp uv run python scripts/private_runtime_backup.py materialize
```

Archive audit도 configured Worker environment만 사용한다.

```bash
familycare-archive-audit
```

이 명령들은 이번 작업에서 actual runtime에 실행하지 않았다. unit tests는 pytest temporary
directory, synthetic key, synthetic custom-dump magic과 application-encrypted payload만 사용했다.

## Verification

### Test-first evidence

- Dependabot policy: 3 expected failures, then 19 passing tests.
- Backup contract: missing-module RED, then capture/verify/materialize and security regression GREEN;
  final focused result 13 passed.
- Archive audit: missing-module RED, then category/non-mutation/read-only SQL GREEN; 12 passed.
- Security review regressions separately reproduced master-key-in-archive, artifact replacement and
  argv path exposure before their fixes.

### Latest local development gate

- documentation contract: PASS, 48 required files.
- repository safety: PASS, 567 Git-visible paths including this workthrough file.
- Web format/lint/type/test/build: PASS, 20 test files and 117 tests.
- Ruff format/lint: PASS, 395 Python files.
- Mypy: PASS, 176 source files.
- Python: PASS, 1,302 tests, 112 deselected integration-marked cases, 3 subtests.
- contracts: PASS for OpenAPI and all generated/versioned contracts.
- container definitions: PASS, 3 images and 4 Compose services; no local image build was run.
- workflow policy and `git diff --check`: PASS.

### Pull request development gates

- PR #34 initial development revision: GitHub Actions run `33260603380`, 7/7 jobs PASS.
- Environment-only CLI correction: GitHub Actions run `33260851606`, 7/7 jobs PASS.
- The documentation revision must pass the same seven jobs before merge.

## Files modified

- `.github/dependabot.yml`, `scripts/check_workflows.py`, `scripts/tests/test_workflows.py`
- `apps/web/package.json`, `pnpm-lock.yaml`
- `scripts/private_runtime_backup.py`, `scripts/tests/test_private_runtime_backup.py`
- `workers/analyzer/src/familycare_worker/archive/audit.py`
- `workers/analyzer/tests/test_archive_audit.py`, `workers/analyzer/pyproject.toml`
- `docs/design/private-data-runtime.md`, `docs/guide.md`
- `docs/plan/000-project-roadmap.md`, `docs/plan/015-private-local-runtime.md`
- `CHANGELOG.md`, this workthrough

## Next steps

1. Let the separate actual-data structuring session finish and establish an explicit writer-quiesced
   boundary before any real archive audit or backup acquisition.
2. With explicit approval for exact external targets, define named-volume snapshot acquisition and run
   `pg_restore` only against a fresh isolated PostgreSQL instance; record aggregate outcomes without
   private paths or data.
3. Define retention, identification, quarantine and physical-deletion policy before acting on audit
   findings. The current audit must remain deletion-free.
4. Keep Windows/mobile/other-device, actual documents, actual backup/restore, recovery-time and
   production deployment marked unverified until each is directly exercised.
