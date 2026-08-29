# Workthrough: WSL-native project migration

**Date:** 2026-08-29

## Summary

FamilyCare tracked source를 `origin/main`에서 `/home/jihoon/projects/family-care`로 새로
clone했다. private runtime의 실제 import root와 master key는 repository 밖에 그대로 두고,
그 값을 출력하거나 내용을 열람하지 않은 채 local environment symlink 경계만 보존했다.

## Changes

### 1. Source and local state

- source와 target initial HEAD는 `08a1037b55e59422bf3088512395edfc40ef903d`로 일치한다.
- main에 이미 포함된 local branch `fix/private-import-capacity`의 exact ref를 보존했다.
- `.env.private`의 absolute symlink target을 값 공개 없이 그대로 복원했고, target regular file이
  존재하며 owner-only `0600`인 것만 확인했다.
- ignored `work/`는 내용을 출력하지 않고 byte-for-byte 복사해 `0700`/`0600`으로 제한했다.
- `.venv`, `node_modules`, test/type/lint cache와 빈 local worktree directory는 재사용하지 않는다.

### 2. Privacy and runtime boundary

- 실제 보험·의료 자료, import directory의 항목, master-key bytes와 environment value는
  열람·열거·복사하지 않았다.
- Compose project name `familycare`와 PostgreSQL/archive/worker-work/secret-socket named volume
  identity는 유지된다. 외부 import와 master-key bind source도 변경하지 않는다.
- API와 Worker의 read-only import mount, Worker-only archive/key/OpenAI 경계는 그대로다.

## Testing

- source/target HEAD, local branch, environment symlink와 ignored work content 비교: PASS
- environment symlink resolution과 external target mode 확인: PASS(값·경로 비공개)
- source와 target worktree clean, target filesystem ext4 확인: PASS
- frozen Python/Web dependency install: PASS
- documentation contract(48 files), repository safety(557 paths): PASS
- Web format/lint/type/test/build: PASS(20 files, 112 tests)
- Python format/ruff/mypy: PASS(171 source files)
- synthetic-only Python test suite: PASS(1,255 tests, 111 deselected, 3 subtests)
- contracts, container definitions(3 images/4 services), workflow policy, diff check: PASS
- private Compose runtime은 값을 출력하지 않고 기존 named volume을 새 source path에 재연결한 뒤
  health endpoint와 DB 연결만 검증한다.

## Files Modified

- `docs/guide.md` — WSL-native local setup 경로
- `workthrough/2026-08-29-wsl-project-migration.md` — privacy-preserving 이관 경계와 검증 기록

## Notes

- 실제 자료 acceptance는 이번 이관 범위가 아니며 수행했다고 주장하지 않는다.
- 원본 프로젝트, external private roots, Docker volume과 WSL VHD backup은 target runtime 검증과
  사용자 승인 전까지 삭제하지 않는다.
