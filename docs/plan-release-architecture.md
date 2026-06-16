# Plan: unified release architecture (vbpub monorepo + dstdns consumer)

**Status:** IMPLEMENTED & SHIPPED 2026-06-16 — all phases executed, holistically reviewed, published, and e2e-verified (see "Outcome" at the end).
**Date:** 2026-06-16.
**Driver:** consolidate to one pwmcp image, de-duplicate releases, give tls-edge a
visible artifact release, and make releases reproducible — across *every* vbpub product
and *all* consumers.

## Guiding principle

**Immutable versioned releases are the single source of truth; "latest" is *resolved*,
not duplicated; every artifact ships a checksum.** Monorepo-aware: never depends on
GitHub's repo-global native "Latest" badge.

### The core design correction (the problem the user asked me to find)
GitHub's native "Latest" badge / `GET /releases/latest` is **repo-global** — exactly one
release per repo can hold it. `vbpub` is a monorepo (ciu + pwmcp + tls-edge +
modern-debian-tools all share one Releases page), so a *per-project* native badge is
impossible. The existing moving `<project>-latest` *release* is the deliberate workaround;
its only real flaw is the **duplicated asset**. Therefore:

> Keep immutable `<project>-v<semver>` releases. Realize "latest" with a **resolver**
> (GitHub Releases API → filter `<project>-v*` → highest semver → verify `.sha256`).
> That resolver *is* the tailscale-style bootstrap installer the user asked for — so Q2
> (release policy) and Q3 (tls-edge installer) collapse into **one** shared mechanism.

`<project>-latest` is retained only as a **thin manifest-only** release (a tiny `LATEST`
file: version + asset URL + sha256) — a stable discovery URL for external consumers, with
**no** duplicated heavy asset. (Outright deletion is riskier for unknown external
consumers; thin-pointer kills the redundancy without breaking discovery.)

## Decisions (confirmed + corrected)

| # | Decision | Source |
|---|----------|--------|
| D1 | pwmcp: drop legacy two-service mode entirely; delete `pwmcp-playwright` GHCR package; re-vendor unified-only bundle into dstdns. | Q1 |
| D2 | All projects: immutable `<project>-v<ver>` releases + resolver for "latest"; `-latest` becomes a thin manifest pointer (no asset dup). Applies to ciu + pwmcp; tls-edge gains versioned releases. | Q2 (corrected) |
| D3 | tls-edge: artifact-based release (`tls-edge-v<ver>.tar.xz` + `.sha256`) + rewritten `get.sh` bootstrap (resolve→download→verify→install/update, preserving user config). | Q3 |
| D4 | Reproducibility baseline: publish `.sha256` (SHA256SUMS) for **every** release artifact; pin release-producing base images by digest; pin `@playwright/mcp`; record image digests in release notes. | "reproducible releases for everything" |
| D5 | Centralize resolver + checksum + release-create + bootstrap-template in the shared `release-manager` package — one implementation, all projects. | new finding |

## Problems / risks surfaced (and how the plan handles them)

- **P1 monorepo native badge impossible** → resolver pattern (D2). *Core.*
- **P2 `ciu-wheel-latest` vs `ciu-latest` mismatch** — dstdns CI (`ci.yml:33`) +
  devcontainer Dockerfile default to `ciu-wheel-latest`, which **does not exist** as a
  release (only `CIU_PKG_URL` secret keeps CI green). → resolver migration reconciles the
  name and removes the dependence on a missing tag.
- **P3 stale pwmcp image pin** — dstdns vendored `ciu.defaults.toml.j2` pins
  `pwmcp:1.61.0-r1` while the release is `r2`. → bump during re-vendor.
- **P4 tls-edge install is interactive + renders at install** (`install.sh` ~949 lines).
  Artifact must include the full runtime file set (manifest below); `get.sh` must **stay
  on raw GitHub, NOT inside the tarball** (self-update loop); update flow must preserve
  `ciu-stack/ciu.toml.j2` + `edge-proxy/.env`.
- **P5 devcontainer `:latest` consumption** — dstdns `.devcontainer/devcontainer.json:11`
  pulls `...-vsc-devcontainer:latest` (moving). Reproducibility gap, but this product
  ships GHCR tags, not GitHub Releases. → treat as audit finding; recommend pinning to a
  dated tag/digest. **Optional**, not forced (dev-tool `latest` is often intentional).
- **P6 deleting `-latest` releases is the riskiest irreversible step** for unknown
  external consumers → mitigated by D2's thin-pointer (keep the URL, drop the asset) and
  by gating registry deletions to the **last** phase, after resolver consumers are live.

## Reproducibility audit — gap summary (from investigation)

| Product | Base image | Deps pinned | `.sha256` asset | Versioned tag immutable |
|---|---|---|---|---|
| pwmcp image | mutable tag | playwright exact; `@playwright/mcp` **unpinned**; `supervisor` apt unpinned | **no** | yes (latest mutable) |
| pwmcp bundle | n/a | n/a | **no** (hash only logged) | both exist |
| ciu wheel | n/a | build exact; runtime `>=` bounds | **no** (hash only logged) | `ciu-v*` yes / `ciu-latest` mutable |
| modern-debian-tools img | mutable tag | most tools `latest` | partial (build-time verify only) | date tag yes / `-latest` mutable |
| tls-edge (traefik/socket-proxy) | `traefik:v3.7` (minor, mutable) | third-party | **no** | n/a |

Baseline we will close (D4): **publish `.sha256` for ciu wheel + pwmcp bundle + tls-edge
tarball**; **digest-pin pwmcp base image + pin `@playwright/mcp`**; **record image digests
in release notes**. Deeper pinning (every devcontainer tool, traefik patch) = flagged,
optional, separate decision.

## tls-edge release artifact manifest (runtime file set)

`tls-edge-v<ver>.tar.xz` contains: `VERSION`, `scripts/` (wrapper, install, render,
render_standalone.py, verify, certbot-deploy-hook, dev-certs, gen-guard-secret),
`ciu-stack/` (`*.j2` + `conf.d/{certs.yml.j2,options.yml,middlewares.yml}`),
`edge-proxy/` (committed defaults + `.env.example`), `consumer-examples/`, top-level docs.
**Excluded:** `get.sh` (stays on raw GitHub), `.git/`, `scripts/update-rendered.sh`
(maintainer-only), `.release-vars`, all gitignored runtime files (`ciu.toml.j2`, `.env`).
Plus a sibling `SHA256SUMS` release asset.

## Phased implementation

- **Phase 0 — shared foundation (`release-manager`):** `publish_release()` that always
  writes a `.sha256` + records checksum in notes + creates immutable versioned release +
  thin `-latest` manifest; `resolve_latest(prefix)` API resolver; one reusable `get.sh`
  bootstrap template (resolve/download/verify/install/update).
- **Phase 1 — pwmcp consolidation + repro:** remove legacy `{% else %}` branch + legacy
  defaults from vbpub bundle; digest-pin base image + pin `@playwright/mcp`; route
  publish-bundle through shared publish (versioned + `.sha256`, thin `-latest`); re-vendor
  unified-only bundle into dstdns + bump image pin to `r2` (P3).
- **Phase 2 — ciu alignment:** route ciu publish through shared publish; reconcile
  `ciu-latest`/`ciu-wheel-latest` (P2); switch dstdns `resolve_ciu_pkg_url.py` +
  devcontainer Dockerfile to the API resolver.
- **Phase 3 — tls-edge artifact + bootstrap:** package artifact per manifest + `.sha256`;
  rewrite `get.sh` (resolver/download/verify/extract + update-preserving-config, keep
  git-clone as documented fallback); update `release.sh` to build artifact + create the
  GitHub Release; publish the first visible `tls-edge-v*` release.
- **Phase 4 — docs:** pwmcp/tls-edge/ciu READMEs + dstdns `infra/pwmcp/docs/*` describe
  the resolve/verify/install model + reproducibility guarantees.
- **Phase 5 — irreversible registry ops (GATED, last, explicit confirm):** delete
  `pwmcp-playwright` GHCR package; convert/trim the old duplicate-asset `-latest` releases
  to thin pointers — only after resolver consumers verified live.
- **Phase 6 — Opus review + e2e verify:** review all diffs; dry-run resolve+download+verify
  for each project; confirm checksums end-to-end.

## Execution notes
- Model selection per policy: Sonnet implementers (≤5 parallel; sequential when same-file),
  one Opus review per phase batch.
- Registry/release deletions (Phase 5) are irreversible → never autonomous; explicit
  confirm + logging.
- Two repos: most edits in `vbpub`; consumer edits in `dstdns` (CI action, devcontainer,
  vendored pwmcp bundle).

## Outcome (2026-06-16)

Shipped and verified:
- **Keystone** `release-manager/src/release_manager/github_release.py` — versioned +
  `.sha256` sidecar + thin `latest.json` + semver `resolve_latest` (the `-rN` counter is
  ordered numerically after the holistic review caught a lexical-sort bug: `r10 > r2`).
- **pwmcp**: legacy two-service mode removed (compose/defaults/bake/Dockerfile +
  `playwright-server/` dir); `pwmcp-playwright` GHCR package **deleted**;
  `pwmcp-v1.61.0-r2` re-published from its *existing bytes* with a `.sha256`;
  `pwmcp-latest` reduced to a 385-byte `latest.json` pointer (redundancy gone). The `r2`
  image was NOT rebuilt — no phantom version bump. `@playwright/mcp@0.0.76` pinned.
- **tls-edge**: first artifact release **`tls-edge-v0.2.0`** (72 KB tarball + `.sha256` +
  thin pointer); tag pushed before publish (no commit desync); `get.sh` is now a
  resolve→download→sha256-verify→install/update bootstrap with a git-clone fallback.
- **ciu**: code aligned to the resolver contract; it activates on the next *clean* ciu
  release — only a `.dev` wheel exists today, so a versioned release was NOT faked. No
  consumer regression (the `CIU_PKG_URL` secret still backs dstdns CI, as before).
- **Consumers** (dstdns CI `resolve_ciu_pkg_url.py`, modern-debian-tools resolver +
  Dockerfile, vendored unified-only bundle) read `latest.json` / scan `<prefix>-v*` and
  verify checksums.
- **E2E verified**: for pwmcp + tls-edge, downloaded-artifact sha == `.sha256` sidecar ==
  `latest.json` sha.

Commits: vbpub `10c834d` (architecture) + `ee2ff39` (tls-edge v0.2.0); dstdns `f3f5406`.
All pushed to `main`; `tls-edge-v0.2.0` tag pushed.

Open follow-ups (non-blocking): pin `supervisor` apt version (TODO in pwmcp Dockerfile);
optionally pin the dstdns devcontainer image by digest (`...-vsc-devcontainer:latest` is
floating); the next pwmcp weekly-CI rebuild auto-publishes `r3` with the unified bundle
under the new scheme.
