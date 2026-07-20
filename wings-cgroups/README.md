# wings-cgroups — slice/cgroup support for Pterodactyl/Pelican Wings

Place Wings-created containers (and Wings itself) under named systemd slices so
cgroup-v2 resource guarantees (`memory.min`/`low`/`high`, CPU/IO weights) become
arithmetically effective — per node and per server. Deployable today via a
rebasing patch stack against upstream; PR-ready (not yet submitted, see `pr/`).

Implements the tiered strategy in [`STRATEGY.md`](STRATEGY.md); background and
review: `wings-cgroup-parent-proposal.md` in `../scripts/gstammtisch-guide/`.

## Which doc do I need?

| Goal | Doc |
|---|---|
| Understand the design — tiers, the two axes, decisions, rejected options | [`STRATEGY.md`](STRATEGY.md) |
| Understand the kernel behaviour every value here depends on — `min`/`low`/`high`/`max` down a slice chain, the budget arithmetic, worked examples | [`CGROUP-SEMANTICS.md`](CGROUP-SEMANTICS.md) |
| Build the patched image (+ fork / CI / PR prep) — in the devcontainer | [`BUILD-AND-INSTALL.md`](BUILD-AND-INSTALL.md) |
| Deploy on a node — host prereqs, compose, `config.yml`, egg, cutover, verify | [`SETUP.md`](SETUP.md) |
| A worked, host-specific rollout (incl. retiring a legacy scheme) | `../scripts/gstammtisch-guide/WINGS-CGROUPS-ROLLOUT.md` |
| Host-side dev tiers (`interactive`/`besteffort`), unrelated to Wings | `../modern-debian-tools-python-debug/host-setup/` |

Normal path: **BUILD-AND-INSTALL → SETUP**. The tier folders below explain each
mechanism in isolation and stay the reference for *why*; `SETUP.md` is the *how*.

## The two axes (why the code split looks like this)

- **Placement** (which slice a container lands under) is settable only at
  container create time via Docker `HostConfig.CgroupParent` — the only thing
  that needs a Wings patch. That patch is deliberately tiny and lives in
  `patchstack/`.
- **Properties** (floors/ceilings/weights) are host-side systemd state — unit
  files (T1/T2), the T3a daemon, or Wings-managed transient units (T3b/0004).
  Never Panel code.

Tenant-facing invariant at every tier: panel-supplied placement values are
untrusted; Wings enforces the `wings.slice`/`wings-*.slice` namespace (or an
explicit allowlist) and fails closed to the node default.

## Layout

| Path | Tier | What |
|---|---|---|
| `t0-host-baseline/t0a-wings-self/` | T0a | Limits for the Wings container itself (compose `cgroup_parent` + slice unit). Zero code. |
| `t0-host-baseline/t0b-daemon-default/` | T0b | Daemon-wide default cgroup parent for **dedicated** Wings nodes (`daemon.json`). Zero code. |
| `t0-host-baseline/t0c-property-reconciler/` | T0c | Reload-safe `systemctl set-property` reconciler (properties only; cannot fix placement). |
| `t1-node-cgroup-parent/` | T1 | Node slice unit + the node-wide `docker.cgroup_parent` mechanism. |
| `t2-per-server-placement/` | T2 | Panel-data artifacts: admin-only `WINGS_CGROUP_PARENT` egg variable; `mk-server-slice.sh` (PoC/fallback — patch 0004 automates this). |
| `patchstack/` | T1+T2+T3b | **The rebasing patch stack** — canonical patches for `pterodactyl/wings` v1.13.1 and `pelican-dev/wings` main, plus clone/apply/rebase/test/build scripts. |
| `t3a-slice-manager/` | T3a | `wings-slice-manager` — standalone Go daemon (docker events → transient slices via D-Bus, namespace guard, floor budget, orphan GC). External alternative to 0004 for nodes whose Wings stays at T2. |
| `test/` | — | Test ladder: unit, placement smoke (host daemon), Wings integration (via patch stack), privileged systemd-in-Docker e2e verifying *effective* floors. |
| `wingsctl/` | — | `wingsctl.py` — stdlib-only wrapper around the Wings node API (list/status/power/logs). Incidental to the cgroup work; kept here for convenience. |
| `ci/` | — | GitHub Actions workflows for the Wings fork and this project. |
| `pr/` | — | Upstream PR descriptions + RFC issue draft. **Prepared, not submitted.** |
| `build/` | — | Gitignored working clones (`wings-pterodactyl`, `wings-pelican`). Recreate with `patchstack/scripts/clone.sh`. |

## The patch stack

Eight commits per upstream tree, kept as `git format-patch` series (the
canonical artifact — the clones under `build/` are disposable):

| # | Commit | Tier |
|---|---|---|
| 0001 | `docker.cgroup_parent` — node-wide placement, startup-validated, applied to server + installer containers | T1 |
| 0002 | `WINGS_CGROUP_PARENT` per-server override — shared resolver for both create paths, namespace/allowlist guard, fail-closed with logged attempt | T2 |
| 0003 | Build-tagged docker integration tests (droppable if maintainers object) | — |
| 0004 | `docker.per_server_slices` — in-Wings slice lifecycle: auto-derived `wings-<dashless-uuid>.slice`, transient units via systemd D-Bus, config defaults + admin-only `WINGS_CG_*` egg overrides, `memory_min` budget (clamp/refuse/distribute), delete/boot GC, fail-open. New pkg `internal/cgroups`. | T3b |
| 0005 | `io_bfq_weight` / `WINGS_CG_IO_BFQ_WEIGHT` — state IO weights on BFQ's own 1..1000 scale instead of systemd's, which compresses ratios above the default by ~11×. Converted to the equivalent `IOWeight` property, so values stay systemd-owned and reload-safe. | T3b |
| 0006 | Render slice property values in the units they were configured with — a `6G` floor logged as `6442450944` cannot be compared against the config or against cgroupfs without arithmetic. User-visible log format change. | — |
| 0007 | Staged slice properties — a startup memory band applied before the container starts and replaced by the steady band when `WINGS_CG_STEADY_MATCH` matches a console line — defaulting to the egg's `startup.done` matcher, which for a world-streaming game usually fires too early — or after `startup_grace`. A ceiling sized for the steady state otherwise evicts the server through its own floor during world load, permanently. | T3b |
| 0008 | Report configuration keys discarded while parsing — a strict re-decode used purely as a diagnostic, so a misindented or duplicated key warns instead of vanishing. Independent of the cgroup work; useful to any Wings user. | — |

The order is chosen so each planned upstream PR is a **contiguous range**:
`0001–0003` (placement), `0004–0007` (per-server slices, stacked on the first),
and `0008` alone — which is why the one commit unrelated to cgroups sits last
rather than in the middle. Submission plan and known review exposure: `pr/`.

Both targets carry the full series: `pterodactyl/wings` v1.13.1 (production) and
`pelican-dev/wings` main (needed one real port fix — Pelican made
`DefaultMapping` a pointer).

## Quick start

```bash
patchstack/scripts/clone.sh                          # 1. clone upstreams
patchstack/scripts/apply.sh pterodactyl              # 2. apply the stack
patchstack/scripts/test.sh pterodactyl               # 3. build + vet + unit tests
patchstack/scripts/build-image.sh pterodactyl cgroup.8   # 4. -> wings-local:1.13.1-cgroup.8
test/smoke-placement.sh                              # 5. placement vs the real daemon
test/e2e-systemd/run-e2e.sh                          # 6. e2e: effective memory.min
```

Details and the fork/CI/PR flow: [`BUILD-AND-INSTALL.md`](BUILD-AND-INSTALL.md).
Deploying the result: [`SETUP.md`](SETUP.md).

## Status

- [x] T0 artifacts
- [x] Patch stack 0001–0008 on both trees — compiled, vetted, unit + integration
      tested; systemd e2e verifies *effective* floors, reload survival, budget
      clamping and orphan GC. The exported series re-applies onto bare upstream
      and reproduces the tree exactly.
- [x] T3a `wings-slice-manager` daemon (18 unit tests, e2e black box) — now the
      external fallback for nodes whose Wings stays at T2, and behind the
      in-Wings path: no `budget_policy: distribute`, no `io_bfq_weight`, no
      staged startup band.
- [x] Test ladder (unit / smoke / integration / e2e), CI workflows, PR drafts
- [x] Production rollout complete (2026-07-17): `cgroup.4`, per-server slices
      enabled, all six properties verified effective in cgroupfs. Worked
      runbook: `../scripts/gstammtisch-guide/WINGS-CGROUPS-ROLLOUT.md`.
- [ ] `cgroup.8` (staged startup band, patch 0007) built and tested, **not yet
      deployed** — supersedes `cgroup.5`, which applied the startup band on
      paths that never start a server — it needs `startup_defaults` in `config.yml` and the new
      `WINGS_CG_STARTUP_*` egg variables to do anything.
- [ ] Upstream PRs submitted — deliberately not yet (see `pr/`); fork not created
      (`FORK_REPO` in `patchstack/stack.conf` is still a placeholder). `pr/README.md`
      lists the review exposure to close before submitting.
