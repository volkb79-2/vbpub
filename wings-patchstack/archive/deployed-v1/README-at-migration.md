# Patch stack — T1+T2+T3b Wings patches, rebasing workflow

The **committed truth** is the patch series under `patches/`; the clones under
`../build/` are disposable working trees. One clean commit per feature so
upstream PRs can cherry-pick, and rebases stay reviewable:

| # | Commit | Tier |
|---|---|---|
| 0001 | `Add docker.cgroup_parent to place containers under a systemd slice` | T1 |
| 0002 | `Support per-server cgroup parent override via WINGS_CGROUP_PARENT` | T2 |
| 0003 | `Add docker integration tests for cgroup parent placement` | tests (build-tagged; include in PR or drop) |
| 0004 | `Manage per-server container scope properties via systemd D-Bus` | T3b (`docker.per_server_slices`: placement stays flat at `cgroup_parent`, and the node-wide `defaults` + `WINGS_CG_*` overrides are applied to the container's own `docker-<id>.scope` with `SetUnitProperties`, right after `ContainerStart()`, and **re-asserted after the in-situ resource update** because a Panel-side settings save writes the Panel's own `container.Resources` to that same scope afterwards; `internal/cgroups`) |
| 0005 | `Render slice property values in the units they were configured with` | log rendering only; split out because it changes user-visible output independently of any feature. *(was 0006)* |
| 0006 | `Stage per-server scope properties across server startup` | T3b follow-up (`startup_defaults`/`WINGS_CG_STARTUP_*`; exits on `WINGS_CG_STEADY_MATCH`/`startup.done`/`startup_grace`; steady `memory.high` reached by a self-pacing ramp `steady_ramp_step`; optional `WINGS_CG_PHASE_EVENTS` → Panel activity log). Same property sets and same ramp as before; they are applied post-`ContainerStart()` now instead of pre-`ContainerCreate()`. Also relocates 0004's panel-side re-assertion out of `environment/docker` and into `Server.SyncWithEnvironment`: once bands exist, only the layer that holds the slice phase can pick the right one, and inferring it from Docker's process state applied the steady band to a still-loading server (fix-verification 2026-09-09, V1). *(was 0007)* |
| 0007 | `Report configuration keys discarded while parsing` | standalone diagnostic — strict re-decode warns about unknown/misindented/duplicate keys instead of dropping them silently. No dependency on the rest of the series, and its test fixtures use only upstream-native config keys so it cherry-picks onto stock Wings. Doubles as the migration aid for this redesign: the removed keys are named at boot rather than vanishing. *(was 0008)* |
| 0008 | `Fix MemoryCurrent D-Bus interface in the memory.high ramp` | bugfix for the ramp — resource-control properties exist only on the unit's **type-specific** D-Bus interface, not the generic `Unit` one Wings read `MemoryCurrent` from; every read failed, so the ramp never ran and the ceiling applied in one shot instead of walking down gradually. Confirmed live in production. Note the interface name is **not** shared across the package: the ramp reads `MemoryCurrent` from `Scope` (it walks a container scope), while the overcommit tripwire reads the tier's `MemoryMin` from `Slice`. A wrong interface name is not a compile error and produces no partial behaviour — the call just always fails — which is why this patch also points `internal/cgroups`' systemd e2e case at the **production reader**: it calls `(*conn).memoryCurrent` against a live scope and asserts on what that returns. It previously read the property through the test file's own helper, which takes the interface name as an argument, so it asserted the test's string literal and not `sysd.go`'s — with this patch reverted, the case still passed (adversarial review 2026-09-09, F2). *(was 0009; retargeted from `Slice` to `Scope` by the 2026-09-08 redesign; test made mutation-sensitive 2026-09-09)* |
| 0009 | `Start listed child servers when a server reaches its steady trigger` | `WINGS_CG_CHILD_SERVERS` (admin-only, per-server list of other server UUIDs on this node): when a server reaches its steady trigger (0006's event — the same one `WINGS_CG_STEADY_MATCH` gates), Wings starts each listed child via `HandlePowerAction`, fire-and-forget, never on the grace backstop. Ordinary cluster start-ordering — a dependent server that must not come up until its main is confirmed ready. Also closes two correctness gaps: `WINGS_CG_STEADY_MATCH` is armed even with no startup band staged (0006 previously ignored it silently in that case), and Wings' own boot sequence (`cmd/root.go`) defers a child's direct restart to its parent's when both were running across a reboot, instead of racing them — see `internal/cgroups.DeferBootRestart`. *(was 0010)* |
| 0010 | `Downgrade unauthenticated SFTP connection rejection from Error+stacktrace to Warn` | log hygiene, independent of cgroups entirely — `sftp/server.go` is untouched by every other patch. A client that **attempted and failed authentication** (bad password, too many tries, or disconnecting partway through the auth loop — routine on any internet-facing port) surfaces as a typed `*ssh.ServerAuthError` and used to log at `Error` with a full stack trace, indistinguishable from a genuine unhandled bug. `logAcceptInboundFailure` routes that specific case to `Warn` under a field named `"reason"` rather than `"error"` — necessary, not cosmetic: `loggers/cli/cli.go`'s handler calls `errors.WithStackDepthIf` on any field named `"error"` unconditionally, regardless of level, so merely lowering the level would still print the trace. Everything else keeps the original `Error`+stacktrace behaviour. |
| 0011 | `Distinguish a bare connection drop from a failed auth attempt in SFTP logging` | log hygiene follow-up to 0010, filed as its own patch deliberately so each can be independently accepted upstream. A client that disconnects (cleanly or via a network-level reset) **before ever reaching the auth loop** — during the SSH version exchange, key exchange, or the initial service request, the signature of a bare TCP port scanner (masscan, zmap, a misdirected health check) — surfaces as a raw `io.EOF`/`io.ErrUnexpectedEOF`/`*net.OpError`, never as `*ssh.ServerAuthError`: nothing about credentials was ever exchanged for the ssh library to have an opinion on. `isHandshakeDisconnect` classifies this narrowly (only "the connection went away", never a protocol- or auth-level error Wings or the ssh library actively raised) and `logAcceptInboundFailure` logs it at `Warn` too, but under a **distinct message** (`"sftp: connection dropped before authentication began"` vs 0010's `"sftp: rejected unauthenticated connection"`) so connection-level scanning and credential-probing stay separately monitorable — operator's explicit ask, treated with the same seriousness as a failed-auth attempt, not silenced. |

The series is contiguous 0001–0011 with no gaps. The unrelated commits (0007,
0010, 0011) sit where renumbering happened to land them; each planned upstream
PR is still a contiguous range (`0001–0003` placement, `0001–0006` stacked
per-server properties, `0007` alone, `0008`–`0009` follow-ups, `0010` alone,
`0011` alone)
instead of forcing a cherry-pick out of the middle of the series.

### The 2026-09-08 redesign, and the two retirements

0004 was rewritten. It no longer derives, creates or garbage-collects a
per-server `wings-<uuid32>.slice`: placement is flat
(`HostConfig.CgroupParent = wings.slice`) and the identical property set —
`MemoryMin`/`MemoryLow`/`MemoryHigh`/`MemoryMax`/`CPUWeight`/`IOWeight` — is
applied with `SetUnitProperties` to the `docker-<id>.scope` that Docker created.
Wings never calls `StartTransientUnit` and never creates a unit of its own. Four
things follow, and they are why several notes below moved to the historical
section:

- properties can only be applied **after** `ContainerStart()`, because the scope
  does not exist until the container's init process launches. The resulting
  few-millisecond window is a known, accepted cost, not a defect (`../SETUP.md`
  §5);
- no orphan GC at boot — the scope's lifecycle *is* the container's, so there is
  nothing left that can be orphaned;
- no Transient-vs-adopted discrimination — that trap existed only because a
  slice could be implicitly auto-created by systemd before Wings reached it;
- no budget arithmetic. `memory_min_budget`, `budget_policy`, `clamp` and
  `refuse` are gone; what remains is what `distribute` already was — apply the
  requested floors as stated and let cgroup-v2's proportional-by-usage sharing
  arbitrate (`../../CGROUP-SEMANTICS.md` Rule 5). Wings keeps one informational
  log line as a tripwire when the applied floors sum above `wings.slice`'s live
  `MemoryMin`, and never acts on it.

**Retired: the old 0005, `Let administrators state IO weights on BFQ's own
scale`** (`io_bfq_weight` / `WINGS_CG_IO_BFQ_WEIGHT`). It computed the inverse of
systemd's `IOWeight`→`io.bfq.weight` compression so an admin could state the
number BFQ actually schedules on. Retired by operator decision on 2026-09-08:
one spelling only, the systemd-native `io_weight` / `WINGS_CG_IO_WEIGHT` on the
1..10000 scale, with the admin doing the BFQ arithmetic by hand from the table
in `../../CGROUP-SEMANTICS.md` Rule 7. That is consistent with how the node tier
has always been configured — the live `wings.slice` unit file carries a
hand-picked `IOWeight=7800` chosen to land on `io.bfq.weight ≈ 800`, for parity
with its `CPUWeight=800`. One knob, one scale, one place to look it up.

**Retired: the old 0011, `Trigger host ramdisk-setup units via systemd D-Bus
before container create`** (`WINGS_CG_RAMDISK_UNITS` /
`docker.allowed_ramdisk_units`). It targeted a ramdisk design —
`soulmask-pak-ramdisk.service` / `soulmask-static-ramdisk.service` — that was
superseded in production on 2026-07-29 by `soulmask_tmpfs.service`, a host-level
unit ordered `Before=docker.service` and toggled by the operator. That is
strictly earlier than any point Wings could reach, and it is not per-container,
so the whole trigger-before-create mechanism has nothing left to trigger.
Confirmed dead rather than merely unused: `allowed_ramdisk_units` appears
nowhere in the live `/etc/pterodactyl/config.yml`. The `StartUnit` /
start-never-restart finding it produced is preserved in the historical notes
below.

Targets: `pterodactyl` (tag `v1.13.3` — what production runs) and `pelican`
(`main` — the faster-merging upstream; same commits, ported). See `stack.conf`
for refs, go images, and the `FORK_REPO` placeholder.

## Workflows

**Fresh machine → deployable image**

```bash
scripts/clone.sh
scripts/apply.sh pterodactyl
INTEGRATION=1 scripts/test.sh pterodactyl   # needs /var/run/docker.sock
scripts/build-image.sh pterodactyl cgroup.1 # -> wings-local:1.13.3-cgroup.1
```

**New upstream release (the recurring ~1–2h/release chore)**

```bash
scripts/rebase.sh pterodactyl v1.13.4       # rebases commits onto the new tag
scripts/export-patches.sh pterodactyl       # refresh committed series
INTEGRATION=1 scripts/test.sh pterodactyl
scripts/build-image.sh pterodactyl cgroup.2   # bump the suffix per deployable change
# deploy per ../SETUP.md, then commit patches/ changes
```

The current deployable image is `wings-local:1.13.3-cgroup.1` — the first build
after the rebase from `v1.13.1` (which had reached `cgroup.11`).

**Editing the patches** — never edit `.patch` files by hand: change the
commits on the branch (`git rebase -i` on your own machine / amend), then
`scripts/export-patches.sh`.

**Pushing to the fork** (once `FORK_REPO` is set in `stack.conf`):

```bash
cd ../build/wings-pterodactyl
git remote add fork git@github.com:OWNER/wings.git
git push fork cgroup/v1.13.3
```

CI for the fork lives in `../ci/fork-wings-ci.yml` (copy into the fork as
`.github/workflows/cgroup-ci.yml` on the patch branch).

## Notes

- Scripts run all Go tooling inside golang containers with the source
  **tar-piped in** — works on hosts where the checkout isn't bind-mountable
  (like this devcontainer) and pins the toolchain per target.
- `go vet` runs strict except for packages with pre-existing upstream findings
  (`VET_EXCLUDE_RE` in stack.conf): our patches must add zero new warnings.
- The **pterodactyl** series was verified end-to-end in this environment on
  2026-09-08 against upstream `v1.13.3`: build + vet + unit tests + the
  `dockerintegration` tests against a real systemd/cgroup-v2 Docker daemon
  (placement, accepted override, fail-closed rejection), plus — for 0004/0008 —
  the `systemdintegration` tests of `internal/cgroups` inside the privileged
  systemd e2e container (`../test/e2e-systemd/`, `E2E: ALL PASS`), which stand a
  real process up in a transient `docker-<64 hex>.scope` and read the applied
  properties back off the `Scope` D-Bus interface.
- **The `pelican-main` series is stale.** It is still the pre-rebase,
  pre-redesign 11-patch series and has NOT been ported to `v1.13.3` or to the
  scope redesign. Do not treat the two series as equivalent until it is; see
  `../CONTINUATION-2026-09-08-scope-redesign.md`.
- Hard-won **kernel** fact (host prerequisite, found the hard way on the prod
  node 2026-07-17): protection declared on a *slice* only reaches the pages
  below it — which are charged to the `docker-*.scope` leaf, never to the slice
  — when cgroup2 is mounted with `memory_recursiveprot`. That is the systemd ≥
  248 boot default, but a runtime remount from the init cgroup namespace can
  strip it (observed, three times on this host), and then that slice-level
  `MemoryMin`/`MemoryLow` silently protects nothing while `systemctl show`
  still reports the configured number. Check `grep cgroup2 /proc/mounts`; fix
  with `mount -o remount,nsdelegate,memory_recursiveprot /sys/fs/cgroup` (host
  shell only — the kernel ignores flag changes from non-init cgroup
  namespaces). **Since 2026-09-08 this is no longer load-bearing for the floors
  0004 sets** — it writes them on the scope itself, the leaf that owns the
  pages, so there is nothing to recurse through. It remains a real prerequisite
  for the node tier's own `wings.slice` floors (now the entire aggregate
  guarantee) and for any admin-managed slice reached via 0002's
  `WINGS_CGROUP_PARENT`. Still worth documenting in the upstream PR as a
  deployment note.
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
  visible. **0007 is the response**: a strict re-decode used purely as a
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
  knob works. It used to be *complementary* to ours — theirs settling containers
  under one slice, ours settling slices under the node tier, composing
  multiplicatively — but since the 2026-09-08 redesign both land on the **same**
  `docker-<id>.scope`, and Wings' `IOWeight` (which systemd re-derives
  `io.bfq.weight` from) overwrites what runc wrote. That upgrades the hazard
  from naming to behaviour, and the PR should say so: after this series a node
  has two different things called `io_weight`, on two different scales, now
  writing the same file. With the old 0005 retired there is no longer an
  unambiguous spelling (`io_bfq_weight`) to point people at, so the PR should
  let maintainers pick a name for the systemd-scale key and document the
  overlap with `BlkioWeight` explicitly. Related oddity:
  `blkioWeightSupported()` probes for `io.weight`, the iocost controller's file,
  which is not the path runc actually takes.

## Retired notes — true, but no longer load-bearing here

Kept because the facts are real and cost work to find; moved out of the live
list because the code they describe no longer exists in this series.

- **`LoadState` is meaningless; `Transient` is the only safe GC discriminator.**
  Slice units are loaded on demand, so `LoadState=loaded` never proved a slice
  existed — only `ActiveState=active` meant the slice (and its cgroup) was
  really there; and transient units DO have a `FragmentPath`
  (`/run/systemd/transient/…`), so the `Transient` property was the only safe
  admin-owned-vs-Wings-owned discriminator. *No longer applies:* Wings creates
  no units, so it never has to tell its own from an administrator's. A
  container scope is `Transient=yes` because Docker made it, which says nothing
  about Wings.
- **Orphan GC of derived slices.** 0004 used to stop derived-shape transient
  slices with no matching server, at boot and on server delete. *No longer
  applies:* a `docker-<id>.scope` lives and dies with its container, so nothing
  can be orphaned and there is nothing to sweep.
- **Serialized budget arithmetic.** The floor budget was a read-modify-write
  against live systemd state, so 0004 serialized it with a package mutex held
  across the apply and kept the policy arithmetic in a pure `applyBudget` — the
  D-Bus path being untestable without a bus, and that arithmetic deciding
  whether an admin's floor was real. *No longer applies:* there is no budget and
  no policy; the kernel arbitrates overcommit (`../../CGROUP-SEMANTICS.md`
  Rule 5) and Wings only logs the tripwire.
- **`StartUnit` must issue "start", never "restart".** From the retired 0011:
  restarting an already-active oneshot runs `ExecStop` before `ExecStart`, which
  for a shared ramdisk-setup unit means tearing down a live sibling's bind
  mount. Verified with a real systemd e2e test that started a unit twice
  (proving the no-op) and contrasted it against an actual `systemctl restart` on
  the same unit (proving restart tears down and re-creates it). *No longer
  applies here* — Wings triggers no host units — but it is the fact to remember
  if anything ever asks systemd to run a unit on a server's behalf again.
