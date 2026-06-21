# SPEC PWMCP-MATRIX — pwmcp Multi-Playwright-Version Release + py3.14 Browser-Lane Fix

**Spec ID:** PWMCP-MATRIX
**Repo:** `vbpub` (pwmcp service) + a one-line `dstdns` consumer re-pin.
**Tracks:** dstdns OPEN-WORKSTREAMS #10 (React E2E) / #8 (base-image alignment); unblocks RUE + OBS UI specs.
**Status:** ready to implement. **Publishing to GHCR is outward-facing — confirm before pushing.**

> Self-contained. Grounded in research 2026-06-21 (sources cited inline). Read the cited files first.

---

## Worktree directive
```
Worktree: create a git worktree for branch `feat/pwmcp-playwright-matrix` at
/tmp/vbpub-pwmcp-playwright-matrix and do all work there — never modify /workspaces/vbpub directly:
  git worktree add -b feat/pwmcp-playwright-matrix /tmp/vbpub-pwmcp-playwright-matrix main
```

---

## 1. Problem (verified)

The dstdns browser test lane fails with an **HTTP 428** WebSocket-upgrade error when a Python client
connects to the running pwmcp container. Root cause is a **client↔server playwright minor-version
skew**, NOT a Python 3.14 wheel/ABI problem:

- The `playwright` **Python** package is pure-`py3` (`requires_python >=3.9`); it installs on py3.14
  fine. There is no cp314 wheel barrier.
- **npm `playwright` latest = `1.61.0`; PyPI `playwright` latest = `1.60.0`** (released 2026-05-18;
  `1.61.0` returns 404 on PyPI). PyPI lags npm by one minor.
- pwmcp is built on the **npm** version (`mcr.microsoft.com/playwright:v1.61.0-noble`,
  `vbpub/pwmcp/docker-bake.hcl:17`, `Dockerfile:14`), but any consumer's `pip install playwright`
  resolves the **PyPI** version (1.60.0). Playwright enforces a **major.minor exact match** between
  the connecting client and the launching server → the 1.60↔1.61 skew yields HTTP 428.
- dstdns pins `tag = "1.61.0-r2"` (`dstdns/infra/pwmcp/ciu.defaults.toml.j2:73`), so the lane can
  never match until a 1.60 server exists.

Sources: github.com/microsoft/playwright-mcp/issues/7 ; playwright.dev/python/docs/api/class-browsertype#browser-type-connect ;
pypi.org/pypi/playwright/json ; registry.npmjs.org/playwright/latest.

## 2. Current pwmcp release scheme (verified)
- Tag = `<playwright_npm_version>-r<N>` (e.g. `1.61.0-r2`). `1.61.0` = playwright npm version;
  `-r<N>` = pwmcp's own revision (`vbpub/pwmcp/scripts/resolve-playwright-version.py:96-107`).
- `docker-bake.hcl:37-40` pushes three tags: `:<pw>-r<N>` (immutable), `:<pw>` (floating),
  `:latest` (global). Today `:latest` tracks the **npm** version — the bug.
- cmru drives it via `strategy="delegated"` + `build-push.py` (`vbpub/cmru.toml`); the matrix is
  owned by the project, exactly like `vbpub/modern-debian-tools-python-debug` (static named bake
  targets per base version, tags `<base>-<own>`). **cmru needs no extension** for a pwmcp matrix.

## 3. Tasks

### Task A — NEAR-TERM unblock (build a PyPI-matched pwmcp)
- Build a pwmcp image on **playwright 1.60.0** (`mcr.microsoft.com/playwright:v1.60.0-noble`,
  `npm install -g playwright@1.60.0`), tagged `1.60.0-r1`. Override the npm auto-resolve (which would
  pick 1.61.0) to pin 1.60.0.
- **Local-first:** build it locally and tag it for local use so the dstdns browser lane can validate
  WITHOUT a registry push. **Only push to `ghcr.io/volkb79-2/pwmcp` after explicit human approval**
  (outward-facing). Record both the local-build and the (gated) publish steps.

### Task B — Multi-version matrix + correct `:latest` semantics (the durable fix)
Model on `modern-debian-tools-python-debug`'s static-bake-target pattern. In `vbpub/pwmcp/`:
- `resolve-playwright-version.py`: also query PyPI (`pypi.org/pypi/playwright/json` → `info.version`)
  and emit BOTH `PLAYWRIGHT_VERSION_NPM` and `PLAYWRIGHT_VERSION_PYPI` to `cmru.vars`.
- `docker-bake.hcl`: add two targets sharing the Dockerfile:
  - `pwmcp-pypi-latest` → tags `:<pypi>-r<N>`, `:<pypi>`, **`:latest`** (canonical consumer alias —
    tracks the version `pip install playwright` yields).
  - `pwmcp-npm-latest` → tags `:<npm>-r<N>`, `:<npm>`, `:latest-npm`.
  - `group "all"` = both. When PyPI catches up to npm, both targets collapse to one image (BuildKit
    layer-caches; the second push is a near-no-op).
- Document the consumer contract: a consumer does `pip install playwright==<X>` and `image: pwmcp:<X>`
  (or `pip install playwright` + `image: pwmcp:latest`) — always a matching pair.

### Task C — dstdns consumer re-pin (one line, in the dstdns repo)
- In `dstdns/infra/pwmcp/ciu.defaults.toml.j2`: `playwright_version = "1.60.0"` and the image
  `tag = "1.60.0-r1"` (or `latest` once it tracks PyPI). This re-pin is what RUE/OBS browser lanes
  consume. (Do this in a small `dstdns` change, coordinated with the RUE work — see RUE spec.)

## 4. cmru assessment
**No cmru change required.** The matrix lives in `docker-bake.hcl` + `build-push.py`; cmru's
`delegated`/`none` strategy already orchestrates a project that emits multiple tags from one bake.
(Only if you wanted two independent *git* tags per playwright version would you add two
`[project.*]` entries — not needed for OCI-only releases.)

## 5. Live-stack / publish tier
- Local pwmcp build + local browser-lane validation: doable now, no push.
- **GHCR publish of `pwmcp:1.60.0-r1` + the new `:latest` semantics: OUTWARD-FACING — requires human
  approval before pushing.** Treat the build as Tier-A and the push as a gated release step.

## 6. Acceptance
1. A py3.14 client (`pip install playwright` → 1.60.0) connects to the 1.60.0 pwmcp with NO HTTP 428.
2. dstdns OBS UI specs + RUE browser slices run (not skip) against the re-pinned pwmcp.
3. Matrix: `docker buildx bake all` produces both pypi-latest and npm-latest tag sets; `:latest`
   resolves to the PyPI version; `resolve-playwright-version.py` emits both versions.
4. cmru unchanged.
