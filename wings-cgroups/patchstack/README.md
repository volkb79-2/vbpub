# Patch stack — T1+T2+T3b Wings patches, rebasing workflow

The **committed truth** is the patch series under `patches/`; the clones under
`../build/` are disposable working trees. One clean commit per feature so
upstream PRs can cherry-pick, and rebases stay reviewable:

| # | Commit | Tier |
|---|---|---|
| 0001 | `Add docker.cgroup_parent to place containers under a systemd slice` | T1 |
| 0002 | `Support per-server cgroup parent override via WINGS_CGROUP_PARENT` | T2 |
| 0003 | `Add docker integration tests for cgroup parent placement` | tests (build-tagged; include in PR or drop) |
| 0004 | `Manage per-server transient slices via systemd D-Bus` | T3b (`docker.per_server_slices`: auto-derived slices, defaults + `WINGS_CG_*` overrides, floor budget with clamp/refuse/distribute, GC; `internal/cgroups`) |
| 0005 | `Let administrators state IO weights on BFQ's own scale` | T3b follow-up (`io_bfq_weight`/`WINGS_CG_IO_BFQ_WEIGHT`; inverts systemd's IOWeight→io.bfq.weight compression) |
| 0006 | `Render slice property values in the units they were configured with` | log rendering only; split out because it changes user-visible output independently of any feature |
| 0007 | `Stage per-server slice properties across server startup` | T3b follow-up (`startup_defaults`/`WINGS_CG_STARTUP_*`; exits on `WINGS_CG_STEADY_MATCH`/`startup.done`/`startup_grace`; steady `memory.high` reached by a self-pacing ramp `steady_ramp_step`; optional `WINGS_CG_PHASE_EVENTS` → Panel activity log) |
| 0008 | `Report configuration keys discarded while parsing` | standalone diagnostic — strict re-decode warns about unknown/misindented/duplicate keys instead of dropping them silently. No dependency on 0001–0007, and its test fixtures use only upstream-native config keys so it cherry-picks onto stock Wings. |

The unrelated commit sits **last** on purpose: it makes each planned upstream PR
a contiguous range (`0001–0003` placement, `0001–0007` stacked slice lifecycle,
`0008` alone) instead of forcing a cherry-pick out of the middle of the series.

Targets: `pterodactyl` (tag `v1.13.1` — what production runs) and `pelican`
(`main` — the faster-merging upstream; same commits, ported). See `stack.conf`
for refs, go images, and the `FORK_REPO` placeholder.

## Workflows

**Fresh machine → deployable image**

```bash
scripts/clone.sh
scripts/apply.sh pterodactyl
INTEGRATION=1 scripts/test.sh pterodactyl   # needs /var/run/docker.sock
scripts/build-image.sh pterodactyl cgroup.10 # -> wings-local:1.13.1-cgroup.10
```

**New upstream release (the recurring ~1–2h/release chore)**

```bash
scripts/rebase.sh pterodactyl v1.13.2       # rebases commits onto the new tag
scripts/export-patches.sh pterodactyl       # refresh committed series
INTEGRATION=1 scripts/test.sh pterodactyl
scripts/build-image.sh pterodactyl cgroup.11  # bump the suffix per deployable change
# deploy per ../SETUP.md, then commit patches/ changes
```

**Editing the patches** — never edit `.patch` files by hand: change the
commits on the branch (`git rebase -i` on your own machine / amend), then
`scripts/export-patches.sh`.

**Pushing to the fork** (once `FORK_REPO` is set in `stack.conf`):

```bash
cd ../build/wings-pterodactyl
git remote add fork git@github.com:OWNER/wings.git
git push fork cgroup/v1.13.1
```

CI for the fork lives in `../ci/fork-wings-ci.yml` (copy into the fork as
`.github/workflows/cgroup-ci.yml` on the patch branch).

## Notes

- Scripts run all Go tooling inside golang containers with the source
  **tar-piped in** — works on hosts where the checkout isn't bind-mountable
  (like this devcontainer) and pins the toolchain per target.
- `go vet` runs strict except for packages with pre-existing upstream findings
  (`VET_EXCLUDE_RE` in stack.conf): our patches must add zero new warnings.
- Both series were verified end-to-end in this environment: build + vet +
  unit tests + the `dockerintegration` tests against a real systemd/cgroup-v2
  Docker daemon (placement, accepted override, fail-closed rejection), plus —
  for 0004/0005 — the `systemdintegration` test of `internal/cgroups` inside the
  privileged systemd e2e container (`../test/e2e-systemd/`): transient-unit
  creation, in-place property updates, budget clamp/refuse, orphan GC.
- The floor budget is a read-modify-write against live systemd state, so 0004
  serializes it with a package mutex held across the apply and keeps the policy
  arithmetic in a pure `applyBudget` — the D-Bus path is untestable without a
  bus, and this is the arithmetic that decides whether an admin's floor is real.
- Hard-won systemd facts encoded in 0004 (do not "simplify" them away):
  slice units are loaded on demand, so `LoadState=loaded` is meaningless as an
  existence check — only `ActiveState=active` means the slice (and its cgroup)
  exists; and transient units DO have a `FragmentPath`
  (`/run/systemd/transient/…`), so the `Transient` property is the only safe
  admin-owned-vs-wings-owned discriminator for GC.
- Hard-won **kernel** fact (host prerequisite, found the hard way on the prod
  node 2026-07-17): the floors 0004 sets live on the per-server *slice*, but
  the container's pages are charged to the `docker-*.scope` *below* it —
  protection only flows down when cgroup2 is mounted with
  `memory_recursiveprot`. That is the systemd ≥ 248 boot default, but a
  runtime remount from the init cgroup namespace can strip it (observed), and
  then every slice-level `MemoryMin`/`MemoryLow` silently protects nothing.
  Check `grep cgroup2 /proc/mounts`; fix with
  `mount -o remount,nsdelegate,memory_recursiveprot /sys/fs/cgroup` (host
  shell only — the kernel ignores flag changes from non-init cgroup
  namespaces). Worth documenting in the upstream PR as a deployment note.
- Hard-won **config-plumbing** fact (found in production, 2026-07-17): Wings
  parses `config.yml` with a plain non-strict `yaml.Unmarshal` and rewrites the
  entire file from the parsed struct at boot (`cmd/root.go` →
  `config.WriteToDisk`). A misindented or misplaced key is therefore accepted
  silently, ignored, and then **erased from the file** by the rewrite — the
  admin sees a file that looks unedited and a feature that does nothing, with no
  log line anywhere. A live deployment lost `enabled: true` exactly this way. Two
  mitigations belong in any deployment doc: (1) the post-restart file *is* the
  parse result, so read the block back instead of trusting the edit; (2) the
  Panel's `POST /api/system` (`router/router_system.go`) also writes the whole
  file from in-memory state, so on-disk edits made while Wings runs can be
  reverted by an unrelated panel action unless `ignore_panel_config_updates` is
  set. Neither is caused by our patches — 0004 just made the blast radius
  visible. **0008 is the response**: a strict re-decode used purely as a
  diagnostic, so the discarded key is named in the log instead of vanishing. It
  deliberately warns rather than failing — a full `KnownFields(true)` decode
  would turn a stale key from an older Wings into a boot failure.
- Hard-won **D-Bus** fact (found in production, 2026-07-17): do NOT use
  `sdbus.NewWithContext`. It dials the system bus via godbus's compile-time
  default `/var/run/dbus/system_bus_socket`, and upstream Wings ships a
  distroless image with no `/var/run` — so the conventional
  `-v /run/dbus/system_bus_socket:/run/dbus/system_bus_socket` mount is
  invisible and slice management degrades to placement-only. Worse, the helper
  *discards* the system-bus error and (as root) falls back to the private
  socket, so the only thing logged is `dial unix /run/systemd/private`, naming
  a path the admin never configured while hiding the one that failed. 0004
  therefore dials each candidate itself — `/run/dbus/...`, then the library
  default, then `/run/systemd/private` — honours `DBUS_SYSTEM_BUS_ADDRESS`, and
  names every attempt in the error. The systemd e2e cannot catch this class:
  its container runs systemd, so `/run/systemd/private` exists and the fallback
  always succeeds. Worth flagging in the upstream PR as a deployment note.
- **Two latent upstream `config` bugs, found while writing tests** (both in
  `config/config.go`, neither caused by this series, both trivially fixable and
  arguably worth their own tiny PR):
  1. `Get()` takes `mu.RLock()`, then dereferences `_config` — and its
     `mu.RUnlock()` is a plain call, not deferred. When no configuration has
     been set the dereference panics with the read lock still held, so every
     later `Set()` blocks forever: one stray `Get()` deadlocks the config mutex
     process-wide. Unreachable in production (config is set at boot before
     anything reads it) but it makes the package untestable without care, and
     it turns any future early-`Get()` into a silent hang rather than a crash.
     The fix is `defer mu.RUnlock()`.
  2. `Set()` panics with "jwt: HMAC key is empty" when the configuration
     carries no token, because it unconditionally builds the HS256 signer.
     Any test or tool constructing a `Configuration` in memory hits it.
- **Upstream already has an `io_weight`, and it is not the same knob** (measured
  2026-07-17, cgroup-v2 + BFQ + systemd driver). `environment/settings.go`
  carries a panel-supplied per-server `IoWeight` (10..1000) applied as Docker's
  `BlkioWeight`. Running `docker run --blkio-weight 700` writes
  `io.bfq.weight=700` on the container **scope** and leaves `io.weight` at 100 —
  runc targets BFQ's own file, on BFQ's own scale, uncompressed. So upstream's
  knob works, and it is *complementary* to ours: theirs settles containers
  under one slice, ours settles slices under the node tier, and the two compose
  multiplicatively. The hazard is naming — after this series a node has two
  different things called `io_weight` on two different scales at two different
  cgroup levels. 0005's `io_bfq_weight` is unambiguous; the slice-level
  systemd-scale key is not, and the PR should let maintainers pick its name.
  Related oddity: `blkioWeightSupported()` probes for `io.weight`, the iocost
  controller's file, which is not the path runc actually takes.
