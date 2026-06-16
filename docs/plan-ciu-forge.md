# Plan: promote `release-manager` into `ciu-forge` — public CIU-family build-and-release product

**Status:** APPROVED 2026-06-16 — ready for implementation. This is the lossless handoff
artifact for implementation sub-agents (who have no access to the planning conversation).
Source plan: `~/.claude/plans/keen-sparking-church.md`.

## Context (why)

`ciu` (orchestration) is offered publicly as a product. We keep hand-writing
build/push/release logic across products (ciu, pwmcp, tls-edge, modern-debian-tools) and
satellites (dstdns, netcup-api-filter). The release-architecture refactor produced
`vbpub/release-manager` — a ~1,700-line **zero-dependency stdlib-Python** orchestrator that
already implements the hard, uncommoditized parts of a monorepo release system. No single
free tool (GoReleaser/Pro, release-please, JReleaser, semantic-release) covers
multi-artifact-type releases for a monorepo with per-project semver tags on **one** Releases
page + a semver resolver + `get.sh` bootstrap + reproducible `.sha256` sidecars. We already
built that engine → **harden-and-promote, not greenfield.**

**Locked decisions:** full public product · CIU-family **sibling wheel** (own package, NOT
inside the lean `ciu` runtime wheel) · **zero-dep Python** (reuse 100% of existing code) ·
commodity concerns (cosign sign, syft/grype SBOM, changelog, nfpm deb/rpm) as **optional
delegated steps** that call OSS, never reimplemented. Name = **`ciu-forge`**. Bump default =
conventional-commits-if-present-else-patch.

## The four differentiators (reason to exist; own these)
1. N projects' immutable `<prefix>-v<semver>` releases on ONE repo's Releases page.
2. A semver **resolver** replacing GitHub's repo-global "Latest" badge.
3. Thin `latest.json` pointer (no asset duplication).
4. An **emitted `get.sh`** bootstrap (resolve→download→sha256-verify→install/update,
   preserve user config).

## Folded-in requirements

### A. Automated versioning + release trigger
pwmcp already auto-versions (scans `pwmcp-v<pw>-r*`, increments `r<N>`). ciu (manual tag) +
tls-edge (manual `release.sh <ver>`) still need a human number. New `ciu-forge release` +
`ciu-forge status` verbs:
- **Change detection per project:** eligible iff subtree changed since last `<prefix>-v*` —
  `git log <lasttag>..HEAD -- <paths>` non-empty (`paths` = `cwd` + shared deps); no tag →
  first release.
- **Bump:** Conventional Commits if present (`feat:`→minor, `fix:`/other→patch,
  `!`/`BREAKING CHANGE`→major), else patch; `--minor/--major/--set-version` override.
- **Strategy per project:** `scm` (ciu — tag HEAD, setuptools_scm reads it), `file:VERSION`
  (tls-edge — write+commit+tag), `counter` (pwmcp `-r<N>`, generalized). Dev/CI (no tag) →
  `X.Y.Z.devN+g<hash>` automatically (the hash-based path; no commit/tag needed).
- **Commit/tag ordering:** operates on committed, clean-tree state. scm/counter → no extra
  commit (tag HEAD; hash NOT in clean version). file-versioned → one automated bump commit,
  then tag. Then build + publish. `status` previews changed products + next versions (dry).
- Auto-resolves the ciu blocker: 29 commits since `ciu-v2.0.0` → `2.0.1` (or `2.1.0` w/
  `feat:`) → tag + release, no manual number.

### B. No host lock-in — two target kinds
- **Image registry:** already not GitHub-locked (`REGISTRY` var + `docker login`). v1:
  `[targets.registry] = ["ghcr.io","docker.io",…]` → one image pushes to GHCR + Docker Hub +
  self-hosted in one run (bake tags per registry).
- **Release host:** currently GitHub-locked. Introduce `ReleaseHost` provider interface
  (`create_release/upload_asset/list_releases/resolve_latest/download_url`). **GitHub impl
  in v1** (keystone refactored behind it; `api_base` already a param → GH Enterprise nearly
  free). Resolver + get.sh consume the interface, not raw GitHub URLs. Gitea/Forgejo +
  S3/MinIO object-store are interface-ready **fast-follow, not v1**.

## Product shape
- dist `ciu-forge`, import `ciu_forge`, console script `ciu-forge`. Drops `vbpub-release`.
- **CLI verbs:** `run` (orchestrate N×steps), `build` (one project/step via unified runner),
  `publish` (versioned+sidecar+latest.json), `resolve` (print latest `{version,tag,url,
  sha256}`; `--format env|json|url`), `get-sh` (emit project get.sh), `cleanup` (age-based),
  `release` (detect→version→tag→build→publish), `status` (dry-run preview).
- **Layout** (`vbpub/ciu-forge/`): `pyproject.toml`, `docs/SPEC.md`, `docs/VERSIONING.md`,
  `templates/get.sh.tmpl`, `examples/*.toml`, `ciu-forge.toml` (dogfood),
  `src/ciu_forge/{cli,runner,release,bundle,resolve,getsh,delegated,version,config,
  exit_codes}.py` + `hosts/{__init__,github}.py`, `tests/`. Old `release_manager` package →
  **re-export shim** so live publishers' `sys.path` import keeps resolving until P6.

## SPEC outline (`docs/SPEC.md`; CIU conventions — S-numbers, RFC 2119)
- **S0** Terminology. **S1** Project & artifact model (`<prefix>-v<semver>` immutable; types
  wheel/oci/tarball/bundle; N projects/one page — diff#1). **S2** Config schema (fail-fast
  exit 2). **S3** Single runner contract (login/required_env/clean_dirs/env_command/`bake
  --set`/no_cache/per-step logs/reproducible-env — orchestrator MUST run every step through
  it). **S4** Publish (versioned + `.sha256` + digest-in-notes; thin latest.json — diff#3;
  dev never mints `-v`; target_commitish). **S5** Resolver (highest-semver numeric-aware
  r10>r2 — diff#2; latest.json→scan; `{version,tag,asset,sha256,url}`). **S6** get.sh
  contract (emitted per project — diff#4; resolve→download→verify→install/update→preserve;
  pin via `<PREFIX>_VERSION`; git-clone air-gapped fallback "no checksum"; python3.11+).
  **S7** Delegated steps (cosign/syft+grype/git-cliff/nfpm; present-or-skip, exit 3 only if
  `required=true`; never vendored). **S8** Exit codes 0/1/2/3 (= CIU S10.3). **S9**
  Reproducibility (`SOURCE_DATE_EPOCH`/`OCI_*` from HEAD; scm pin only on tag; same commit→
  same bytes). **S10** Validation catalog. **S11** Targets & host abstraction
  (`[targets.registry]` multi-push; `ReleaseHost` interface, GitHub v1). **S12** Versioning
  & release trigger (change detection; bump conv-else-patch; strategies scm/file/counter;
  dev `devN+ghash`; commit-then-tag). **S13** Reserved/out-of-scope (macOS/Windows signing;
  FTP/S3 deploy targets like netcup `deploy.zip`; new hosts only via S11 interface).

## Unified config schema (one file per repo)
Under `[project.<name>]`; secrets in `[github]`; project MAY `include="build-push.toml"`
during migration. Tables: `[github]` (explicit `owner_type` → removes the `modern-debian-
tools` probe), `[orchestration]`, `[targets]` (`host="github"`; `registry=[…]`),
`[project.<name>]` (`prefix`,`artifact`,`scm_dist`,`cwd`), `…​.version`
(`strategy=scm|file:VERSION|counter`, `paths=[…]`, `bump=conventional|patch`),
`…​.steps.<step>` (runner contract), `…​.publish` (`source` glob, `latest_json`),
`…​.resolve` (`asset_glob`), `…​.getsh` (`install_dir`, `preserve=[…]`), `…​.delegated`
(`sign/sbom/changelog/nfpm`), `[cleanup]`. `prefix` replaces `_DIST_TAG_PREFIXES`;
`[targets]` replaces single `[registry] url`.

## Phased roadmap (strangler-fig: live `release.toml` 5-project pipeline must keep running)
Sonnet implementers (≤5 parallel; **sequential when same-file**), ONE Opus review per phase.

- **P0 Scaffold** *(parallel, all-new)* — `ciu-forge/` skeleton: pyproject, `__init__`,
  `docs/SPEC.md` skeleton, `templates/get.sh.tmpl` (parameterized copy of `tls-edge/get.sh`).
- **P1 Move + shim** *(parallel moves, then 1 seq shim)* — move
  `release_manager/{github_release,step_runner,cli,bundle_builder}.py` →
  `ciu_forge/{release,runner,cli,bundle}.py`; `release_manager/*` re-export from `ciu_forge`.
- **P2 Unify the two runners** *(SEQUENTIAL — `cli.py`+`runner.py`)* — orchestrator builds a
  per-step config and calls the runner for EVERY step. **Gate:** live 5-project `release.toml`
  → byte-identical tags/assets.
- **P3 Remove hardcodes** *(config.py+pyproject parallel; cli.py serial after P2)* — drop
  `_DIST_TAG_PREFIXES` (`cli.py:362`), drop `modern-debian-tools-python-debug` probe
  (`cli.py:563`; require `github.owner_type`), rename entry point + `vbpub-release` shim.
- **P4 First-class resolver/get-sh/delegated/version/targets** *(new modules parallel; cli
  wiring serial)* — `resolve.py` (absorb dstdns resolver), `getsh.py`, `delegated.py`,
  `version.py` (`release`/`status`), `hosts/` (`ReleaseHost` + `github.py`), `[targets]`
  multi-registry in `runner.py`.
- **P5 Dogfood** *(SEQUENTIAL — config+CI)* — add `ciu-forge` to its own `ciu-forge.toml` +
  live `release.toml` (additive); tag `ciu-forge-v0.1.0`; tool releases itself. **Hard
  prerequisite for P6.**
- **P6 Migrate consumers + remove shim** *(parallel per consumer; seq shim deletion)* —
  ciu/pwmcp/tls-edge publish scripts → config + `ciu-forge publish`/`get-sh`; dstdns resolver
  → `ciu-forge resolve --format env` (dstdns stays consumer-only, never gains `[publish]`).
  Then delete the `release_manager` shim.

**Edge cases (decided):** netcup `deploy.zip` (FTP) = out of scope (deploy, not release).
dstdns = consumer only (canonical consumer example).

## Critical files
- `release-manager/src/release_manager/cli.py` → `ciu_forge/cli.py` (hardcodes `:362`/`:563`;
  `run_commands` to unify)
- `…/step_runner.py` → `ciu_forge/runner.py` (full-featured runner to delegate to)
- `…/github_release.py` → `ciu_forge/release.py` (keystone; refactor behind `ReleaseHost`)
- `tls-edge/get.sh` → `templates/get.sh.tmpl`
- `dstdns/.github/actions/resolve_ciu_pkg_url.py` → folds into `ciu_forge/resolve.py`
- `pwmcp/scripts/resolve-playwright-version.py` → reference for the `counter` strategy
- `release.toml` (5 projects: ciu, modern-debian-tools-python-debug, pwmcp, tls-edge,
  empyrion-translation; additive `ciu-forge` block in P5)

## Verification (end-to-end)
1. **Dogfood:** `git tag ciu-forge-v0.1.0 && ciu-forge run --project ciu-forge --step build
   --step publish` → immutable release + `.whl.sha256` + `ciu-forge-latest/latest.json`.
2. **Resolve+verify per type:** wheel(ciu)/bundle(pwmcp)/tarball(tls-edge) + OCI digest;
   `sha256sum -c` passes.
3. **get.sh:** `ciu-forge get-sh --project tls-edge` ⇒ structural diff vs current script;
   run in throwaway container (install/update/preserve).
4. **No-regression gate (after P2):** live `release.toml` via shim `--dry-run` then real;
   compare tag list + sidecar digests before/after — byte-identical.
5. **Exit codes:** unit tests — 2 bad config / 3 missing PAT / 1 failed build / 0 success.
6. **Reproducibility:** same commit twice w/ seeded `SOURCE_DATE_EPOCH` → identical wheel sha.
7. **Auto-versioning:** `ciu-forge status` shows ciu changed + proposes `2.0.1`/`2.1.0`;
   `ciu-forge release --project ciu` tags+publishes, no manual number; refuses dirty tree.
8. **Multi-registry:** `[targets].registry=["ghcr.io","docker.io"]` → one build pushes both;
   pull each + compare digests.

## Risks & non-goals
- **Refuse (S13):** macOS/Windows signing; FTP/S3 deploy targets. New release hosts only via
  S11 interface; only GitHub in v1 (Gitea/object-store fast-follow).
- **Public SPEC = contract:** breaking changes bump wheel MAJOR + regression test naming the
  S-ID. `latest.json` schema + emitted `get.sh` (`curl|sudo bash`; sha256 gate non-optional)
  are compatibility/security contracts.
- **Dogfood-before-migrate is load-bearing:** P6 consumers `pipx install ciu-forge` only
  after P5 publishes the wheel + get.sh.

## Credentials / constraints
- Release PAT lives in `vbpub/release.toml` `[github].token` (gitignored). Publishers route
  through it; never print it.
- vbpub remote: `https://github.com/volkb79-2/vbpub`. Commit with `Co-Authored-By: Claude
  Sonnet <noreply@anthropic.com>`. Stage only relevant files (never `git add -A`).
