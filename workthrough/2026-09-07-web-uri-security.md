# Workthrough: fast-uri security patch

## Change and scope

Updated only the transitive `fast-uri` lock entry from `3.1.5` to `3.1.6`, its npm
integrity, and the corresponding Ajv dependency/snapshot references. No package
manifest, override, application code, API, schema, or other package version changed.
Ajv `8.20.0` accepts `fast-uri ^3.0.1`; the registry confirms the selected `3.1.6`
release and integrity. Frozen installation validates this resolution.

The upstream advisories identify `3.1.6` as the patched v3 release:

- [GHSA-jqff-g426-hqxp](https://github.com/fastify/fast-uri/security/advisories/GHSA-jqff-g426-hqxp): encoded scheme normalization.
- [GHSA-f65p-4m7j-42xc](https://github.com/fastify/fast-uri/security/advisories/GHSA-f65p-4m7j-42xc): malformed IPv6 normalization.
- [GHSA-fph4-wmhf-6fwf](https://github.com/fastify/fast-uri/security/advisories/GHSA-fph4-wmhf-6fwf): repeated hostname decoding.
- [GHSA-5jgf-p345-68v8](https://github.com/fastify/fast-uri/security/advisories/GHSA-5jgf-p345-68v8): scheme-relative IDN handling.

[Dependabot PR #71](https://github.com/jihoon22-lee/family-care/pull/71) updates four
other development packages, including Vitest's major version. Its inspected lock
diff does not update `fast-uri`; this security patch does not adopt that PR.

## Reachability assessment

`pnpm why --recursive fast-uri` identifies one version through
`vite-plugin-pwa 1.3.0 -> workbox-build 7.4.1 -> ajv 8.20.0 -> fast-uri`.
`@apideck/better-ajv-errors` shares the same Ajv instance. The production-only
dependency query has no `fast-uri` result.

The inspected Workbox `build/lib/validate-options.js` compiles packaged option
schemas with Ajv during PWA generation. Ajv's `dist/runtime/uri.js` imports
`fast-uri`. The application supplies build configuration in `apps/web/vite.config.ts`;
its browser API client instead calls native `fetch` for `/api/v1/` paths in
`apps/web/src/api/http.ts`. No application source import of Ajv or `fast-uri` was
found. The Web runtime image copies built static assets into nginx, without
`node_modules` (`infra/containers/web.Dockerfile`).

This establishes a vulnerable development dependency, not a reproduced FamilyCare
SSRF or redirect exploit. No product input-to-`fast-uri` request sink was found in
this static trace. No live exploit or protected runtime probe was performed.

## Verification

Executed on 2026-09-07, approximately 06:08–06:12 UTC, using WSL Linux, Node
`24.18.0`, Corepack pnpm `11.22.0`, base
`b084fd9c62e3e617c4cbc15e57cf959d450349b5` plus the four-line lock change.
The checked lock SHA-256 is
`f3b2283ef0301238a7f4e489da73e34c4edb6fdcfce7ee829ebcfaa02d3bed29`.

- `corepack pnpm@11.22.0 install --frozen-lockfile`: passed, including the
  550-entry supply-chain policy check.
- `corepack pnpm@11.22.0 why --recursive fast-uri`: only `3.1.6`; the `--prod`
  variant returns no dependency path.
- `corepack pnpm@11.22.0 audit --json`: before the patch, exit 1 with the four
  named high advisories; after the patch, exit 0 with no advisories at any severity.
- Node exact-lock comparison against the base: passed; only the two package keys,
  Ajv reference, and registry integrity differ. Installed package version is `3.1.6`.
- `corepack pnpm@11.22.0 web:check`: passed formatting, lint, types, 162 tests in
  24 files, production build, and PWA service-worker generation.
- `CI=true corepack pnpm@11.22.0 --filter @familycare/web test:e2e`: 15 Chromium
  tests passed with synthetic API mocks, including 320px and browser storage checks.
- `python3 scripts/check_documentation.py`: passed, 50 required documents.
- `python3 scripts/check_repository_safety.py`: passed, 719 paths.
- `git diff --check`: passed.

The targeted pnpm update command completed without changing the transitive lock
entry. The exact registry-backed lock patch above was then applied and verified by
the successful frozen installation. No new unit test was added for this lock-only
change.

## Boundaries

Python product tests, PostgreSQL integration, container image builds, actual
backend browser tests, Windows/mobile devices, and protected data were not exercised
by this dependency task. Required remote CI, publishing, and merge are handled by
the integrating task. No release tag, deployment, private data access, or external
AI call was made here.

## Integration record

PR [#77](https://github.com/jihoon22-lee/family-care/pull/77) passed all seven required
checks, including three image builds, in
[run 34090180563](https://github.com/jihoon22-lee/family-care/actions/runs/34090180563)
on `5e3c4f3fd31dbd73a985b51242ec293278dda5e1`. It merged as
`44c20172c4f1cc5bd1a90540feff808e930cc4ba`. The integrating agent confirmed the
backend/Python/DB/contracts/container/workflow source inputs match the already
verified B01 baseline. Release and deployment remain separate milestone work.
