# Workthrough: Supported toolchain dependency refresh

**Date:** 2026-08-30

## Overview

Dependabot PR #35, #36, #37의 Node·uv·Web lint toolchain 변경을 하나의 유지보수 PR로
통합했다. 두 container PR을 막던 patch-version 중복 검사를 승인된 지원 계열과 완전한
tag 형식을 검사하는 정책으로 바꿔, 이동 tag와 지원 계열 이탈은 계속 거부하면서 정상적인
patch/minor 갱신은 Dockerfile만 source of truth로 사용할 수 있게 했다.

이 변경은 build와 development tooling에만 적용된다. API, database schema, 보험 판정 계약,
실제 private runtime data에는 변경이 없다.

## Context

- PR #35의 Web builder Node `24.19.0-alpine` → `24.20.0-alpine` 변경은 Web container build를
  포함한 6개 job이 통과했지만 `scripts/check_containers.py`가 이전 tag를 하드코딩해
  `Repository safety`가 실패했다.
- PR #36의 API/Worker uv `0.12.5` → `0.12.7` 변경도 두 image build를 포함한 6개 job이
  통과했고 같은 중복 상수 때문에 `Repository safety`만 실패했다.
- PR #37의 ESLint `10.9.0` → `10.9.1`, typescript-eslint `8.67.0` → `8.68.0` 변경은 기존
  required CI 7개가 모두 통과했다.
- 사용자는 세 변경을 하나의 PR에 넣고 development revision CI가 먼저 통과한 뒤 문서를
  후속 commit으로 추가해 다시 CI를 통과시킨 후에만 merge하도록 요청했다.

## Changes

### 1. Dependency updates

- `infra/containers/web.Dockerfile`: Node builder를 `24.20.0-alpine`으로 갱신했다.
- `infra/containers/api.Dockerfile`, `infra/containers/worker.Dockerfile`: official uv builder
  image를 `0.12.7`로 갱신했다.
- `apps/web/package.json`, `pnpm-lock.yaml`: ESLint `10.9.1`과 typescript-eslint `8.68.0`을
  적용했다. 이 네 dependency artifact는 해당 Dependabot branch와 정확히 일치한다.

### 2. Container image policy

`scripts/check_containers.py`는 각 Dockerfile의 ordered `FROM` stage를 다음 정책과 비교한다.

- Web: full Node 24 patch with Alpine variant, exact approved nginx runtime tag
- API/Worker: full uv 0.12 patch, two full Python 3.14 patch tags with slim variant
- required stage의 추가·누락·순서 변경 또는 정책에 없는 service는 실패
- moving/partial tag와 승인 계열 밖의 Node·Python·uv version은 실패

정확한 patch 값은 Dockerfile에만 있고 checker가 이를 별도 상수로 복제하지 않는다. nginx
runtime은 별도 승인 경계이므로 `1.31.2-alpine3.23`을 계속 exact-match한다.

### 3. Regression tests

`scripts/tests/test_containers.py`에 지원 계열의 full patch 갱신 허용, `node:24-alpine` 같은
partial tag 거부, Node 25 거부를 추가했다. 기존 nginx pin, Worker local OCR package, CI OCR
language smoke 검사도 새 Dockerfile path mapping과 함께 유지했다.

## Code examples

Static container validation은 image build나 running Compose service 변경 없이 실행한다.

```bash
TMPDIR=/tmp uv run python scripts/check_containers.py
```

정책은 image tag substring 존재 여부가 아니라 ordered `FROM` reference 전체를 검사한다.

```python
if pattern.fullmatch(image) is None:
    errors.append(f"stage {index} image {image!r} must use a {description}")
```

## Verification

### Test-first evidence

- `TMPDIR=/tmp uv run pytest scripts/tests/test_containers.py -q`
  - RED: `validate_image_references`가 아직 없어 collection `ImportError`, exit `2`.
  - GREEN: 구현 후 5 passed.
- Targeted Ruff format/lint와 Mypy는 변경된 checker와 test에서 통과했다.

### Latest local development gate

- documentation contract: PASS, 48 files.
- repository safety: PASS, 567 paths.
- Web format/lint/type/test/build: PASS, 20 test files and 117 tests.
- Ruff format/lint: PASS, 395 files.
- Mypy: PASS, 176 source files.
- Python: PASS, 1,304 tests, 112 deselected, 3 subtests.
- OpenAPI/JSON Schema contract checks: PASS.
- container definitions: PASS, 3 images and 4 Compose services.
- workflow policy, Git conventions, and `git diff --check`: PASS.

### Pull request development gate

- PR #38 development revision `47bab5d`: GitHub Actions run `33263085026`, required jobs 7/7
  PASS, including three sequential build-only container jobs.
- The documentation revision must pass the same seven required jobs before merge.

## Files modified

- `apps/web/package.json`, `pnpm-lock.yaml`
- `infra/containers/web.Dockerfile`
- `infra/containers/api.Dockerfile`, `infra/containers/worker.Dockerfile`
- `scripts/check_containers.py`, `scripts/tests/test_containers.py`
- `CHANGELOG.md`
- `docs/design/project-foundation.md`, `docs/design/test-strategy.md`
- `workthrough/2026-08-30-dependency-refresh.md`

## Security and privacy boundaries

- 실제 보험·의료 자료, 파생 text/OCR/image, private path, database, archive, key를 읽거나
  기록하지 않았다.
- running FamilyCare와 다른 project container를 중지·재시작·교체하지 않았다. local에서는
  `docker compose config --services` 기반 정의 검사만 실행했다.
- PR CI는 synthetic checkout에서 image를 build-only로 검증하고 push하지 않는다.
- tag, GHCR publish, 운영 배포, Windows/mobile, 실제 자료 acceptance는 수행하지 않았다.

## Next steps

1. Documentation revision의 required CI 7개를 확인한 뒤 PR #38을 merge한다.
2. `main` push CI를 확인하고 #35, #36, #37이 superseded 상태로 정리됐는지 확인한다.
3. 실제 보험 원장 구조화 세션은 이 build-only 변경과 독립적으로 계속 진행한다.
