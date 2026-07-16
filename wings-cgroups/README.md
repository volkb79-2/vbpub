# wings-cgroups — slice/cgroup support for Pterodactyl/Pelican Wings

Implementation project for the tiered strategy in
[`../scripts/gstammtisch-guide/wings-cgroup-tiered-strategy.md`](../scripts/gstammtisch-guide/wings-cgroup-tiered-strategy.md)
(background/verification: `wings-cgroup-parent-proposal.md` and its review, same
directory).

Goal: place Wings-created containers (and Wings itself) under named systemd
slices so cgroup-v2 resource guarantees (`memory.min`/`low`/`high`, CPU/IO
weights) become arithmetically effective, per node and per server — deployable
now via a rebasing patch stack, PR-ready for upstream (not yet submitted).

## Layout

| Path | Tier | What |
|---|---|---|
| `t0-host-baseline/t0a-wings-self/` | T0a | Limits for the Wings container itself (compose `cgroup_parent` + slice unit). Zero code. |
| `t0-host-baseline/t0b-daemon-default/` | T0b | Daemon-wide default cgroup parent for **dedicated** Wings nodes (`daemon.json`). Zero code. |
| `t0-host-baseline/t0c-property-reconciler/` | T0c | Reload-safe `systemctl set-property` reconciler (properties only; cannot fix placement). |
| `t1-node-cgroup-parent/` | T1 | Host artifacts + runbook for the node-wide `docker.cgroup_parent` Wings patch. |
| `t2-per-server-placement/` | T2 | Panel-data artifacts: admin-only `WINGS_CGROUP_PARENT` egg variable, per-server slice unit tooling. |
| `patchstack/` | T1+T2 | **The rebasing patch stack** — canonical patches for `pterodactyl/wings` v1.13.1 and `pelican-dev/wings` main, plus clone/apply/rebase/test/build scripts. |
| `t3a-slice-manager/` | T3a | `wings-slice-manager` — standalone Go daemon: watches Docker events, creates/reconciles transient `wings-*.slice` units via systemd D-Bus, enforces namespace + memory-floor budget, GCs orphans. |
| `test/` | — | Test ladder: placement smoke test (host daemon), wings-level integration test (via patch stack), privileged systemd-in-Docker e2e verifying *effective* floors. |
| `ci/` | — | GitHub Actions workflows for the Wings fork and this project. |
| `pr/` | — | Upstream PR descriptions + RFC issue draft. **Prepared, not submitted.** |
| `build/` | — | Gitignored working clones (`wings-pterodactyl`, `wings-pelican`). Recreate with `patchstack/scripts/clone.sh`. |

## The two axes (why the code split looks like this)

- **Placement** (which slice a container lands under) is set only at container
  create time via Docker `HostConfig.CgroupParent` — the only thing that needs a
  Wings patch. That patch is deliberately tiny and lives in `patchstack/`.
- **Properties** (floors/ceilings/weights) are host-side systemd state — unit
  files (T1/T2), or the T3a daemon. Never Wings code, never Panel code.

Tenant-facing invariant at every tier: panel-supplied placement values are
untrusted; Wings enforces the `wings.slice`/`wings-*.slice` namespace (or an
explicit allowlist) and fails closed to the node default.

## Quick start

```bash
# 1. Clone upstreams and apply the patch stack
patchstack/scripts/clone.sh
patchstack/scripts/apply.sh pterodactyl

# 2. Build + vet + unit tests (in a golang container; no local Go needed)
patchstack/scripts/test.sh pterodactyl

# 3. Build the deployable Wings image
patchstack/scripts/build-image.sh pterodactyl

# 4. Placement smoke test against this host's Docker daemon
test/smoke-placement.sh

# 5. Full e2e (privileged systemd-in-Docker, verifies effective memory.min)
test/e2e-systemd/run-e2e.sh
```

## Status

- [x] T0 artifacts
- [x] T1+T2 patch stack, pterodactyl/wings v1.13.1 (compiled, vetted, tested)
- [x] T1+T2 patch stack, pelican-dev/wings main port
- [x] T3a wings-slice-manager daemon
- [x] Test ladder (smoke / integration / e2e)
- [x] CI workflows + PR drafts
- [ ] Upstream PRs submitted (deliberately not yet — see `pr/`)
- [ ] Production deployment (runbook: proposal Appendix A + `t1-node-cgroup-parent/`)
