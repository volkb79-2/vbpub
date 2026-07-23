# General cgroup v2 resource policy for Wings — proposal 1, fable revision

- Status: implementation-ready clean-slate architecture + patch-stack review,
  2026-07-23
- Input: [`shared-ramdisk-update-lifecycle-cgroups-1-codex.md`](shared-ramdisk-update-lifecycle-cgroups-1-codex.md)
  (the codex proposal). This document supersedes it.
- Companion: [`shared-ramdisk-update-lifecycle-3-codex-fable.md`](shared-ramdisk-update-lifecycle-3-codex-fable.md)
  — the lifecycle/provider series of the same Wings v2 program, including the
  interview decision log (2026-07-23) that governs both documents: dual-target
  with Pelican as PR audience; **Wings v2 patches first, manager second**;
  normative depth.
- Starting point: vanilla Pterodactyl Wings v1.13.1 (tag = `e771816d`) and
  pelican-dev/wings `main @ 70f3344` (v1.0.0-beta26+1). The local `0001`–`0011`
  stack is production evidence, not an upstream dependency.
- Scope: container placement, systemd slice lifecycle, startup/steady resource
  phases, bounded transitions, online reconciliation.
- Evidence rule: Wings claims cite vanilla source (`git show v1.13.1:<path>`);
  kernel claims cite the kernel cgroup-v2 documentation; systemd claims cite
  systemd.resource-control(5) (Debian 13) or systemd source. Local documents
  (STRATEGY.md, CGROUP-SEMANTICS.md, SETUP.md) are hints and host
  measurements; where this document repeats one of their numbers it is
  labeled [measured]. See the Evidence index.

## Changes made relative to the codex input

1. **Grounded every mechanism claim upstream and separated it from local
   lore.** Notably: "Finding D" (raw cgroupfs writes wiped by
   `daemon-reload`) is *version-dependent* — the wipe did not reproduce on
   Debian 13's systemd ([verified] `_LATEST_SUMMARY.log:32`; the e2e harness
   already tolerates both outcomes, `test/e2e-systemd/inner-test.sh` §2). The
   v2 design therefore claims only what systemd guarantees by construction:
   systemd-owned unit properties are systemd state and are re-applied by
   systemd; raw cgroupfs writes are untracked and *may* be clobbered whenever
   systemd re-applies unit settings. Oracles assert survival of systemd-owned
   values and stay agnostic about the raw-write wipe.
2. **Reconciled the proposed budget modes with the shipped, measured
   semantics.** Codex invented `reserved | admission | shared`; the shipped
   stack has `clamp | refuse | distribute` ([verified]
   `config/config_docker.go:271`), where `distribute` maps exactly onto the
   kernel's proportional overcommit rule. v2 keeps the kernel-honest name:
   final modes are **`reserved | admission | distribute`**, with a normative
   migration mapping from `clamp`/`refuse` (§Floor budget) and a concrete
   recommendation for this node (move `clamp` → `distribute`; production
   today *is* the order-dependent clamp case codex criticized: two 6G floors
   against an 8G budget).
3. **Pinned the v2 configuration schema normatively** and added the complete
   migration table from the current surface — all 17 shipped env vars and
   every `docker.*` key ([verified] full inventory, `internal/cgroups/
   cgroups.go` + `config/config_docker.go`) — so implementation and egg
   migration cannot drift (§Configuration model, §Migration).
4. **Turned the codex "known gaps" sentence into a defect ledger with
   file:line anchors and a named v2 closure for each** (§Defect ledger):
   orphan-GC snapshot race, English-text D-Bus error matching, O(n)
   per-sibling budget reads inside a 10s ensure timeout, additive
   non-reconciling properties, fixed-name integration fixtures — plus two the
   codex doc missed: the `io_weight` name collision flagged as the series'
   single most likely rejection reason ([verified] `pr/README.md:84-89`,
   `pr/rfc-issue.md:52-64`), and the missing D-Bus socket mount in
   `docker-compose.example.yml`.
5. **Corrected the create-site enumeration.** Vanilla has exactly two
   container-create sites — the server environment
   (`environment/docker/container.go:138` `Create()`) and the installer
   (`server/install.go:403` `Execute()`); "transfer" is not a third Docker
   create path in v1.13.1. `HostConfig.CgroupParent` is confirmed absent in
   vanilla.
6. **Added the vanilla online-update precedent.** The environment interface
   already carries `InSituUpdate()` for live Docker resource-limit changes
   ([verified] `environment/environment.go`), and the Panel already triggers
   live config refresh via `POST /api/servers/:server/sync`
   ([verified] `router/router_server.go:145` `postServerSync`). Online slice
   reconciliation extends an existing pattern rather than introducing a new
   concept — this materially strengthens the upstream story.
7. **Specified the phase machine against the shipped evidence**: only the
   memory band is staged (weights are work-conserving and never staged,
   [verified] `internal/cgroups/cgroups.go:44-59`); create applies steady
   because `Create()` also runs on non-start paths ([verified]
   `environment/docker/container.go:187`); startup applies in
   `OnBeforeStart` ([verified] `environment/docker/power.go:51`); the grace
   backstop never counts as readiness (the shipped child-start gate already
   enforces this, [verified] `server/slice_phase.go:162,172-174`). Readiness
   itself moves to the lifecycle series' L2 event (one matcher, two
   consumers).
8. **Promoted the ramp's hidden constants into configuration** with the
   shipped values as defaults (step wait 2s, poll 100ms, max duration 10min,
   max 512 writes — [verified] `internal/cgroups/sysd.go:303,308,390`,
   `cgroups.go:186`), and carried the patch-0009 lesson (read
   `MemoryCurrent` from the *Slice* type interface, not the generic Unit
   interface) as a normative driver rule.
9. **Fixed the IO naming and grounded the BFQ math properly**: kernel
   `io.weight` is [1,10000] default 100 ([kernel] cgroup-v2 IO interface);
   BFQ's own `io.bfq.weight` is a separate 1..1000 interface; systemd's
   `IOWeight=` writes both, compressing >100 ratios ~11× — this double-write
   is **systemd source behavior** (`src/core/cgroup.c`, `set_bfq_weight()`),
   absent from systemd.resource-control(5) ([verified] man page grep), and
   confirmed by the live read-back table [measured]. v2 exposes a nested
   `io: {systemd_weight | bfq_weight}` object (mutually exclusive, exactly
   the shipped 0005 validation semantics) and never a bare `io_weight` that
   collides with the Panel's scope-level `--blkio-weight` (a third knob at a
   different level, uncompressed).
10. **Dropped the ×1024/100 CPU-weight formula** found in the local
    semantics doc: on cgroup v2, systemd `CPUWeight=` maps 1:1 onto
    `cpu.weight` (systemd.resource-control(5)); ratios are literal. (The
    formula belongs to legacy CPUShares conversion — an example of the
    "local docs are hints" rule paying off.)
11. **Added optional zswap properties to profiles**
    (`MemoryZSwapMax=`/`MemoryZSwapWriteback=` exist as systemd properties on
    Debian 13 and are already used in production unit files for the pak
    slice), closing a gap the current patches have — they set only
    Memory{Min,Low,High,Max}/CPUWeight/IOWeight ([verified]
    `internal/cgroups/sysd.go:201-215`).
12. **Wrote the golden stock-compatibility harness into the plan concretely**
    (shared with the lifecycle series) instead of naming it as an aspiration.
13. **Filled in the migration section with the real production profile**
    (`soulmask-latency`: startup high 20G; steady 6G/7G/7G/20G; registration
    matcher; ramp 64M; budget 8G) taken from the deployed egg and node
    config, with the `clamp`→`distribute` recommendation argued from the
    kernel's own overcommit rule.
14. **Recorded the patchstack `SERIES` tooling extension** (shared with the
    lifecycle document): this series lives on branch `resources/<ref>`,
    exported to `patchstack/patches/resources/<target>-<ref>/`; production
    images are built from a combined `v2/<ref>` branch.

## Executive decision (unchanged in substance)

Cgroup support is a separate proposal and a separate patch series from the
shared-release manager. Both care about lifecycle events and isolation; they
solve different problems:

```text
Wings resources series   where Wings containers are charged; what policy applies
shared-release manager   immutable releases, tmpfs generations, mount leases
```

The manager installs and controls its own maintenance/generation slices even
on vanilla Wings. Patched Wings places game containers in per-server slices
even when no manager exists. They may share the documented host hierarchy,
the L2 readiness event, and observability conventions — never configuration,
provider calls, failure domains, or upstream PRs.

From scratch, the design (i) lands small default-off placement first,
(ii) makes node-defined **resource profiles** the primary interface rather
than seventeen `WINGS_CG_*` variables, (iii) keeps systemd ownership an
optional second layer, (iv) models phase changes as a general resource-policy
state machine consuming the lifecycle series' readiness event, (v) reconciles
safe property changes online after server sync, (vi) keeps dependencies and
release preparation in the lifecycle series, and (vii) preserves complete
vanilla behavior when disabled.

## Hard compatibility requirement

- `docker.cgroups.enabled` defaults to `false`; an absent block takes no
  D-Bus connection, creates no units, changes no `HostConfig`, parses no
  cgroup egg variables, starts no goroutines.
- Creation, install, transfer, SFTP, backup, power, crash recovery, boot
  restoration: stock behavior, verified by the golden harness (below).
- Placement-only mode requires no D-Bus access at all.
- A managed-mode failure affects only servers whose profile says `required`;
  everything else continues (the shipped stack's fail-open behavior,
  promoted to explicit policy).

**Golden stock-compatibility harness (normative, shared with the lifecycle
series):** build vanilla and feature-disabled v2 binaries; run both against a
recorded fixture set (server create/start/install/transfer/restore) with the
Docker API stubbed by an in-process recorder; byte-diff the
`ContainerCreate` payloads (config, host config, networking) and the ordered
lifecycle event stream. Any diff is a review blocker. This is the acceptance
form of "completely usable as ordinary Wings".

## Three independent axes

| Axis | Question | When it can change | Owner |
|---|---|---|---|
| Placement | Which slice contains the Docker scope? | Container create only | Wings/Docker create path |
| Properties | Which min/low/high/max and weights apply? | Online | systemd driver (or host IaC) |
| Phase policy | Which property set applies during startup vs steady? | Lifecycle/readiness transition | Wings phase machine + driver |

Placement is the only irreducible Wings change ([verified] vanilla sets no
`CgroupParent`; Docker exposes it at create — `HostConfig.CgroupParent`,
docker CLI `--cgroup-parent`). Properties are host systemd state. Phase
policy benefits from Wings because Wings knows when a real start begins
(`onBeforeStart`, `server/power.go:171`), owns the console matcher that
defines Running (`server/listeners.go:149-182`), serializes power actions
(`system/locker.go`), and receives configuration updates (`Server.Sync()`).

## Recommended host hierarchy

Unchanged from codex (node-configured root; both the current
`wings.slice` + `game-releases.slice` shape and a fresh-host
`game-host.slice` umbrella are valid). One hard rule, restated with its
kernel grounding: the manager's staging workloads must not sit under the
game tier's protected subtree — with `memory_recursiveprot`, a parent's
protection covers its whole subtree ([kernel] "Recursively apply memory.min
and memory.low protection to entire subtrees…"), so a downloader below
`wings.slice` would compete for the very floor meant for live games.

## Configuration model

### Node-defined profiles, server-selected IDs (normative schema)

```yaml
docker:
  cgroup_parent: wings.slice          # PR-2 primitive, unchanged from 0001
  cgroups:
    enabled: false
    driver: placement-only            # placement-only | systemd
    root_slice: ""                    # empty = docker.cgroup_parent
    server_slice_template: "wings-{uuid32}.slice"   # dash-nesting under root
    selection_variable: WINGS_RESOURCE_PROFILE
    default_profile: ""               # empty -> unselected_policy
    unselected_policy: root           # root | stock
    failure_policy: best-effort       # best-effort | required (per-profile override)
    floor_budget:
      value: 8G                       # ledger vs the root slice's own MemoryMin
      mode: distribute                # reserved | admission | distribute
    ramp:
      step: 64M                       # 0/empty = one-shot
      step_wait: 2s                   # shipped internal constants, now config
      poll_interval: 100ms
      max_duration: 10m
      max_steps: 512
    overrides:
      allow_inline: false             # migration: accept legacy WINGS_CG_* as
      allowed_fields: []              # a bounded field overlay (ranges below)
    profiles:
      latency-sensitive:
        placement: per-server
        failure_policy: required
        aux_containers: maintenance   # server | maintenance | unmanaged
        steady:
          memory_min: 6G
          memory_low: 7G
          memory_high: 7G
          memory_max: 20G
          cpu_weight: 0               # 0 = unset
          io: {bfq_weight: 0}         # or {systemd_weight: N}; mutually exclusive
          zswap: {}                   # optional: {max: 0, writeback: true}
        startup:
          memory_high: 20G            # unset fields inherit steady
        transition:
          ready: lifecycle            # lifecycle (L2 event) | egg-startup-done
          timeout: 15m                # resource backstop, NOT readiness
          timeout_action: apply-steady
      best-effort:
        placement: per-server
        steady: {memory_high: 4G, memory_max: 6G, cpu_weight: 50, io: {systemd_weight: 50}}
```

Server side:

```text
WINGS_RESOURCE_PROFILE=latency-sensitive
```

Selection precedence: node UUID override > node egg/profile mapping >
validated server selector > node default profile > unselected policy.
`unselected_policy: root` places unmanaged servers directly under the root
slice (today's useful tier-placement stage); `stock` leaves Docker's parent
untouched; with the whole block disabled both are ignored.

Field overlay (`overrides`): node config allow-lists which fields a server
may override and their ranges (e.g. steady/startup `memory_high`,
`cpu_weight`) — never the slice name, parent budget, driver, or failure
policy. Resolution: `node profile ⊕ authorized strictly-parsed overrides =
one desired policy revision`. The legacy `WINGS_CG_*` names feed this overlay
during migration only.

### Migration mapping (complete, from the shipped surface)

Config keys ([verified] `config/config_docker.go`):

| Current key | v2 disposition |
|---|---|
| `docker.cgroup_parent` (:120) | unchanged (PR-2) |
| `docker.allowed_cgroup_parents` (:134) | kept for the expert placement escape hatch (PR-3, optional) |
| `docker.allowed_ramdisk_units` (:157) | **retired** — replaced by the lifecycle provider (patch 0011's role) |
| `docker.per_server_slices.enabled` (:212) | `docker.cgroups.enabled` + `driver: systemd` |
| `.defaults.*` (:180-195) | profile `steady.*`; `io_weight`/`io_bfq_weight` → `steady.io.{systemd_weight,bfq_weight}` |
| `.startup_defaults.*` (:202-205) | profile `startup.*` |
| `.memory_min_budget` (:226) | `floor_budget.value` |
| `.budget_policy` clamp\|refuse\|distribute (:271) | `floor_budget.mode` — see §Floor budget mapping |
| `.startup_grace` 15m (:244) | profile `transition.timeout` |
| `.steady_ramp_step` (:252) | `ramp.step` |

Server variables ([verified] `internal/cgroups/cgroups.go`;
`config_docker.go:324`):

| Current variable | v2 disposition |
|---|---|
| `WINGS_CGROUP_PARENT` | expert escape hatch only (allow-listed), no longer the per-server mechanism — profiles derive the slice |
| `WINGS_CG_MEMORY_{MIN,LOW,HIGH,MAX}` | overlay fields → profile steady band |
| `WINGS_CG_CPU_WEIGHT`, `WINGS_CG_IO_WEIGHT`, `WINGS_CG_IO_BFQ_WEIGHT` | overlay fields → `steady.cpu_weight`, `steady.io.*` (same mutual-exclusion rule: both io forms set → neither applied, logged — node config both → fatal at boot) |
| `WINGS_CG_STARTUP_MEMORY_{MIN,LOW,HIGH,MAX}` | overlay fields → profile startup band |
| `WINGS_CG_STEADY_MATCH` | **moves to the lifecycle series**: `WINGS_READY_MATCH` (L2), consumed here as `transition.ready: lifecycle` |
| `WINGS_CG_STARTUP_GRACE` | overlay field → `transition.timeout` |
| `WINGS_CG_PHASE_EVENTS` | unchanged concept, renamed `WINGS_LIFECYCLE_EVENTS`, telemetry-only, lifecycle series |
| `WINGS_CG_CHILD_SERVERS` | **moves to the lifecycle series**: `WINGS_START_AFTER` on the child (inverted edge — child declares its prerequisite; the shipped parent-declares-children shape survives only as `WINGS_AUTOSTART_DEPENDENTS`) |
| `WINGS_CG_RAMDISK_UNITS` | **retired** — lifecycle provider replaces it |

### Why not raw `WINGS_CGROUP_PARENT` as the primary interface

Unchanged from codex: unit names in Panel data make topology tenant-visible
data, require the namespace/allow-list model everywhere, and conflate
placement with policy. It survives only as a node-enabled expert override
with an exact allow-list and a `pending_recreate` status.

### Installers and auxiliary containers

Every Wings-created container gets an explicit policy. Vanilla has two create
sites ([verified] server `container.go:138`, installer `install.go:403`; the
installer receives the resolved server env and its own resource limits via
`resourceLimits()`, `install.go:535`). Profile field `aux_containers`:

- `server` — installer shares the server's slice (current 0004 behavior)
  under a maintenance phase band;
- `maintenance` — installer runs under a node maintenance slice with
  aggregate ceilings (**Soulmask setting**: Steam downloads must not enjoy
  the live-game floor; complements the manager's stage slice);
- `unmanaged` — stock placement.

## Placement layer

**PR-2 (first functional PR):** node `docker.cgroup_parent`, validated at
boot, applied at both create sites, default-off, no new dependency — the
shipped 0001 is essentially this and is the model. Integration tests assert
`HostConfig.CgroupParent` (0003's oracle) with unique fixture names and
guaranteed cleanup (closing the fixed-name defect; the early-return that made
stale fixtures pass is [verified] `container.go:145`).

**Placement is not online-mutable.** Moving a live scope between slices is
not supported behind Docker/systemd. A placement change records
`pending_recreate`; since vanilla recreates the container on every real
start ([verified] `environment/docker/power.go:26` removes + recreates),
"applies on next stop/start or restart" is exact — the status exists so the
API/UI never claims a move that has not happened.

## Managed slice layer

### Two ownership modes

1. **Placement-only** — operators/IaC own persistent slice units; Wings does
   placement only; no bus access; permanent, portable baseline.
2. **Systemd-managed** — Wings ensures/reconciles transient per-server
   slices via D-Bus. The upstream RFC asks whether this belongs in Wings; if
   maintainers refuse, the same resolver/events feed a node-local helper
   (the t3a daemon is the proven prototype of that shape, with known
   deltas: no `distribute`, no BFQ scale, an accepted 1-2s startup race).
   Either way the policy model is identical; only the adapter differs.

### Systemd driver rules (v2, incorporating the defect closures)

- systemd-owned properties only (`StartTransientUnit`,
  `SetUnitProperties(runtime=true)`); never raw cgroupfs writes — those are
  untracked and may be clobbered whenever systemd re-applies unit state
  (version-dependent in practice; see Changes §1).
- **Error handling by D-Bus error name** (`org.freedesktop.systemd1.
  UnitExists`, `.NoSuchUnit`, `org.freedesktop.DBus.Error.NameHasNoOwner`,
  `.NoReply`, `.AccessDenied`), never message substrings.
- Bus endpoints probed and reported: `/run/dbus/system_bus_socket`, then
  `/run/systemd/private`; containerized Wings documents the required mount
  (and the example compose file ships it — closing the docs defect).
- Verify a unit is active and Wings-owned/adoptable, not merely loaded;
  adopt administrator-owned persistent slices only when node policy says so;
  GC never stops non-transient units ([verified] shipped rule, SETUP.md:391).
- **Budget ledger in process, O(1) per create**: admitted floors are a
  keyed in-memory ledger, rebuilt at boot with one
  `ListUnitsByPatterns(["wings-*.slice"])` call + typed property reads,
  updated transactionally under the admission lock at ensure/stop/delete —
  replacing the per-sibling `GetUnitTypeProperty` loop on every create
  ([verified defect] `sysd.go:556-577` inside a 10s ensure timeout,
  `ensure.go:12`). A periodic verify pass reconciles ledger vs systemd and
  logs drift.
- **Reconciling, not additive**: every apply computes the full desired
  property set from the resolved profile revision and explicitly resets
  removed fields to their defaults (memory protections/limits to 0/"max"
  equivalents, weights to systemd's default) — removing an egg override must
  clear the live value ([verified defect] pr/README.md:147-148).
- **GC without the snapshot race**: the sweep re-reads the live server set
  and Docker state immediately before each stop decision, under the same
  lock admission uses ([verified defect] boot snapshot + async sweep,
  `cmd/root.go:176-198`); a slice is removed only with no scope, no server
  intent, and no in-flight operation.
- Read back effective values (systemd property + cgroupfs) and expose
  degraded state; `MemoryCurrent` reads use the *Slice* type interface
  (patch-0009 lesson).
- Probe `memory_recursiveprot` in `/proc/mounts` cgroup2 flags at boot and
  in `doctor`; a profile advertising floors on a host without it is
  `degraded`, loudly — without the flag a parent's protection does not cover
  the scope's pages ([kernel] mount-option semantics; charging is to the
  leaf scope).

### Failure behavior

`failure_policy: best-effort` (default): log, mark degraded, create with
resolved placement anyway. `required`: refuse the affected start before
Docker create when the slice or its required effective properties cannot be
proven. Profiles that advertise hard floors set `required` (the Soulmask
profile does).

## Floor budget

Modes (final):

| Mode | Budgeted population | Over budget | Meaning |
|---|---|---|---|
| `reserved` | All enrolled managed servers, each accounted at its **largest floor across phases** (startup vs steady) | Reject the enrolment/sync transaction | Every declared floor is reservable; deterministic, order-independent |
| `admission` | Active + starting servers | Reject or queue the start | Deliberate overbooking with a start-time gate |
| `distribute` | No per-child gate | Apply as requested; log the overcommit | Kernel shares the parent's protection proportionally to usage below each child's floor ([kernel] overcommit rule: "each child cgroup will get the part of parent's protection proportional to its actual memory usage below memory.min") |

Migration mapping from the shipped `budget_policy`:

- `distribute` → `distribute` (identical semantics, name kept).
- `clamp` → choose: `reserved` if floors are sold guarantees (and fix the
  config so they fit), else `distribute`. `clamp`'s behavior — later
  servers permanently frozen at whatever remained at their start — is the
  order-dependence codex correctly rejected; it is not carried into v2.
- `refuse` → `admission` with reject (closest intent: no floor lie), noting
  the semantic delta: `refuse` started the server floor-less; `admission`
  refuses the start.
- **This node**: two enrolled 6G floors against `budget 8G` currently clamp
  the second server ([verified] production config + the client instance's
  own comment). Two cooperating instances of one game are the textbook
  `distribute` case: protection follows live usage instead of start order,
  and the tier total (backed by `wings.slice MemoryMin=8G`) remains the real
  guarantee. Recommendation: `mode: distribute`, keep the ledger for the
  tripwire log.

Validation warns when: a child floor exceeds the parent slice's effective
protection (dead floor); `memory_low` exceeds the same profile's
`memory_high` (decorative — protection cannot exceed what the cgroup may
hold; note the production steady band 6G/7G/7G is already shaped by this);
a ceiling exceeds physical RAM (inert); swap capacity makes a limit
meaningless.

## Startup and steady phases

### State machine (per start attempt, keyed by policy revision + attempt ID)

```text
offline/create           # steady band pre-applied at create (create also runs
    |                    # on boot/transfer paths that never start the server)
    | real start admitted (onBeforeStart)
    v
startup band applied
    | Ready(L2 event)  OR  transition.timeout
    v
transition/ramp          # only if steady ceiling < current usage
    |
    v
steady band
    |
    | stop / crash / new start attempt
    v
cancel ramp + timers by revision; readiness cleared; re-arm next attempt
```

Rules, each grounded:

- Only the memory band is staged; weights are work-conserving and never
  staged ([verified] shipped design, `cgroups.go:44-59`).
- The default ready source is the lifecycle series' L2 event; with L2 absent
  the egg's `startup.done` (the exact matcher that flips vanilla to Running,
  `server/listeners.go:182`) is the fallback. The matcher compiles at sync,
  binds to one attempt, fires once. A historical console line can never
  satisfy a matcher changed mid-run (store for next attempt unless
  explicitly re-armed).
- `transition.timeout` is a **resource backstop only**: it may apply the
  steady band; it must never emit readiness, start dependents, or mark a
  release healthy. (The shipped stack already refuses child starts on the
  grace path — [verified] `slice_phase.go:162,172-174` — v2 makes it a
  contract shared with L3.)

### Safe `memory.high` ramp

When the steady ceiling is below current usage: apply all non-ceiling
properties; read `MemoryCurrent` (Slice interface); lower `memory.high` by
`ramp.step`, never below target; wait up to `ramp.step_wait` polling at
`ramp.poll_interval` for usage to converge, watching pressure/OOM events;
repeat until target, cancellation, or `ramp.max_duration`/`ramp.max_steps`;
raising a ceiling is immediate; a `MemoryCurrent` read failure degrades to
*abort-and-report*, never to a silent one-shot clamp. Cancellation is by
policy revision, so an old ramp can never overwrite a newer profile. The
journal/log records start usage, target, steps taken, duration, and final
read-back. Rationale for ramping at all is kernel semantics: reclaim
triggered by the cgroup's own `memory.high` ignores its own protections
(protection applies relative to the reclaim root), so one hard shove evicts
the server through its own floor during the squeeze.

## Online reconciliation

Yes for properties; no for placement.

Trigger points ([verified] all `Sync()` call sites in vanilla): pre-start
(`server/power.go:173`), Panel-pushed live sync
(`router/router_server.go:145` `postServerSync`), pre-install
(`install.go:89`), boot re-sync (`cmd/root.go:264`). Extend successful sync
with an idempotent resource reconcile:

1. resolve profile + overlay → desired revision (monotonic);
2. diff against applied revision;
3. running container → apply online-safe property changes to the slice
   (precedent: vanilla already live-applies Docker-level limits via
   `InSituUpdate()`);
4. placement diff → `pending_recreate`, no process moved;
5. starting server → startup band; started-and-Ready → steady (ramp if
   lowering);
6. serialize with the power lock and phase transitions so an older revision
   can never win a race with a newer sync, a stop, or an in-flight ramp.

Online-change matrix (unchanged from codex, with two grounded refinements):

| Change | Running container | Behavior |
|---|---|---|
| CPU weight / IO weight | Yes | Apply, read back (systemd + cgroupfs; report effective `io.bfq.weight`) |
| `memory.min`/`low` | Yes | Budget transaction, then apply + read back |
| Raise `memory.high`/`max` | Yes | Immediate |
| Lower `memory.high` | Yes | Ramp unless explicitly forced |
| Lower `memory.max` | Conditional | Refuse below `current + margin` by default (OOM hazard) |
| Startup band while steady | Store | Next start |
| Ready matcher | Store / explicit re-arm only | Never retroactive readiness |
| Slice/root/placement/profile identity | No | `pending_recreate` |

Operator-visible status distinguishes: saved in Panel / received by Wings
(sync) / applied (revision) / pending recreation.

## IO/BFQ design

Grounded facts: kernel `io.weight` is [1,10000] default 100 and settles
sibling contention only ([kernel] IO interface files); weights compose
multiplicatively toward the root and only bite under contention; BFQ ignores
`io.weight` and reads its own `io.bfq.weight` (1..1000, default 100;
[kernel] BFQ documentation); systemd `IOWeight=` writes **both** files,
mapping >100 values into BFQ's range so ratios above default compress ~11×
(systemd source `src/core/cgroup.c` `set_bfq_weight()`; not documented in
the man page; [measured] read-back table: IOWeight 200→bfq 109, 500→136,
1000→181, 4500→500, 10000→1000; exact shipped inverse
`IOWeight = 100 + 11×(bfq−100)`, `cgroups.go:228,238`). The Panel's "Block
IO Weight" is a third knob at scope level: runc writes the container scope's
`io.bfq.weight` directly, uncompressed — it composes below the slice weights
and is inert in a one-container slice.

v2 rules: nested `io:` object with `systemd_weight` XOR `bfq_weight` (node
config sets both → boot fails; server overlay sets both → neither applied,
logged — shipped 0005 semantics); report requested and effective values;
warn when the active scheduler is not BFQ (`/sys/block/*/queue/scheduler`) —
without BFQ neither weight file does anything and only `io.max` still bites;
keep BFQ intent out of the placement PR.

## Relationship to the release manager

Synergies: shared host hierarchy and doctor checks; the manager's stage
slice depends on hard aggregate ceilings this series documents; rollout
logs record generation + resource profile together; the L2 readiness event
drives both the startup→steady transition and MAIN→CLIENT ordering.

Boundaries (unchanged, contract-grade): cgroup enablement never configures
or calls the provider; manager slices never require Wings slices; manager
jobs never run inside a server's protected slice; failures do not cross;
no combined egg variable or combined upstream PR.

## Review of the current patch cut

| Patch | Assessment | v2 treatment |
|---|---|---|
| 0001 node `cgroup_parent` | Good, small, general | Model for PR-2 |
| 0002 raw `WINGS_CGROUP_PARENT` | Guarded but exposes topology via egg data | Expert escape hatch only; profiles are primary (PR-3) |
| 0003 docker integration tests | Right oracle | Keep; unique names + cleanup (defect closed) |
| 0004 transient slices | Proven but monolithic (config+parse+D-Bus+budget+GC) | Split: adapter (PR-4), budget/profiles (PR-5); close ledger/GC/error-name/reconcile defects |
| 0005 BFQ scale | Correct host finding | PR-8, nested io object, after maintainer signal |
| 0006 unit-preserving rendering | Good observability | Fold into PR-4/5 |
| 0007 startup phases | Valuable, oversized (matcher+timers+ramp+telemetry) | Matcher → lifecycle L2; phase machine + ramp → PR-6; telemetry separate |
| 0008 discarded config keys | General, unrelated | PR-1, as-is |
| 0009 MemoryCurrent interface fix | Necessary | Folded into the rewritten ramp before review |
| 0010 child start + trigger fixes | Dependency logic is not cgroups | Lifecycle L3; the not-on-grace rule becomes shared contract |
| 0011 ramdisk unit trigger | Site-specific bridge | Retired; lifecycle provider replaces it |

### Defect ledger (all [verified], closed by v2 as noted)

| Defect | Anchor | v2 closure |
|---|---|---|
| Boot orphan-GC snapshot race (slice of a just-created server can be stopped) | `cmd/root.go:176-198`; pr/README.md:138-140 | GC re-reads live state under the admission lock per decision |
| D-Bus errors matched by English text | `internal/cgroups/sysd.go:280,420`; pr/README.md:141-143 | Error-name matching, closed set |
| O(n) sibling reads per create inside 10s timeout | `sysd.go:556-577`, `ensure.go:12`; pr/README.md:144-146 | In-process ledger, one pattern query at boot |
| Additive, non-reconciling `Ensure` | pr/README.md:147-148 | Full desired-set apply with explicit resets, revisioned |
| Fixed-name integration fixtures pass against stale containers | `cgroup_integration_test.go` + early return `container.go:145`; pr/README.md:149-152 | Unique names + guaranteed cleanup |
| `io_weight` name collision with Panel scope knob — top rejection risk | pr/README.md:84-89; pr/rfc-issue.md:52-64 | Nested `io:` object; no bare `io_weight` key |
| Example compose lacks the D-Bus socket mount | pr/README.md:153-155 | Ship it in PR-4 docs |
| Finding-D wipe assumed universal | `_LATEST_SUMMARY.log:32` | Version-agnostic claims + tolerant oracles (Changes §1) |

## Recommended PR sequence (series `resources`, branch `resources/<ref>`)

1. **Config diagnostics** — 0008 as-is. Gate: unit tests.
2. **Node placement** — `docker.cgroup_parent` at both create sites,
   validation, integration tests. Files: `config/config_docker.go`,
   `environment/docker/container.go`, `server/install.go`, `cmd/root.go`.
   Gate: golden harness (disabled = vanilla), placement integration.
3. **Named per-server placement** — profiles (placement only), selection
   variable, UUID overrides, `pending_recreate`, optional allow-listed
   parent escape hatch. No D-Bus. Gate: golden + placement matrix.
4. **Systemd slice adapter** — ensure/reconcile/read-back/ownership/GC,
   error-name handling, bus probing, compose docs. Gate: privileged
   systemd-in-Docker e2e (extend `test/e2e-systemd/`: effective values,
   reload survival of systemd-owned values, GC safety incl. the race
   regression test).
5. **Budgeted resource profiles** — full property set incl. optional zswap
   fields, `reserved|admission|distribute`, ledger, effective-value API.
   Gate: budget concurrency tests (two simultaneous admissions), Rule-5
   distribute assertions on a real kernel.
6. **Resource phases** — bands, phase machine, ramp (config'd constants),
   L2 consumption with egg-done fallback. Gate: e2e phase walk incl.
   timeout-is-not-readiness and revision-cancellation races.
7. **Online reconciliation** — sync-triggered diff/apply, revisioning,
   pending-recreate, guarded lowering. Gate: sync/power/ramp race matrix.
8. **BFQ intent** — nested io object + effective read-back. Gate: BFQ host
   e2e; skip-with-warning on non-BFQ.

Dual-target: exported per target via the `SERIES`-extended tooling
(companion doc, §Workstreams); expect pelican-side type deltas of the
`DefaultMapping`-pointer kind, fixed at export.

## Observability and API

Per managed server: selected profile + source; desired revision; desired vs
actual cgroup parent; phase + trigger; desired / systemd-applied /
cgroupfs-effective values; budget mode + requested + admitted; active ramp
progress; degraded/required state; pending-recreate reason; last reconcile
operation + error. `doctor` (node): cgroup v2 + controllers +
`memory_recursiveprot`; bus reachability; root/parent unit + floors;
scheduler + BFQ read-back; stale units; live placement audit; ledger vs
systemd drift. Logs use stable event names; matched console content is never
published (event name + revision only).

## Security model

Unchanged from codex, with the shipped namespace rules made explicit: egg
data selects node-known profiles; slice names derive from validated root +
canonical UUID (`wings-<32hex>.slice`, [verified] `config_docker.go:405`);
Wings manages only its derived-shape transient units; admin-owned persistent
slices are adopt-only-by-policy and never GC'd; bus access is node privilege
and containerized deployments document the mount; resource values are
non-secret but parsed strictly (ranges, ordering, ancestor constraints,
budget, unsafe decreases).

## Acceptance oracles

Stock compatibility: (1) golden harness — disabled v2 ≡ vanilla create
payloads + event streams for server/installer fixtures; (2) disabled v2
makes zero bus connections and runs without a bus mount; (3) provider
presence/absence and cgroup enablement are mutually invisible.

Placement/ownership: (4) every container lands under the resolved parent;
rejected selectors follow documented fallback or `required` refusal;
(5) placement change on a running server = `pending_recreate`, applied on
next recreate; (6) Wings cannot touch units outside its subtree; (7) GC
never removes a unit with live scope/intent/in-flight op — including the
create-during-sweep race as a regression test.

Properties/budget: (8) systemd-owned values survive `daemon-reload` and
match systemd + cgroupfs read-back (raw-write wipe observed-or-not is
logged, not asserted); (9) removing a profile field clears the live value;
(10) concurrent admissions cannot exceed `reserved`/`admission` budgets;
`distribute` logs the overcommit and the kernel's proportional split is
observed under synthetic pressure; (11) missing `memory_recursiveprot`
degrades protected profiles loudly; (12) slice weight + scope
`--blkio-weight` compose; requested and effective BFQ values both visible.

Phase/online: (13) startup band applies only on real starts; (14) Ready
applies steady exactly once; stop/restart cancels stale timers/ramps by
revision; (15) timeout applies steady but never emits readiness or starts
dependents; (16) lowering ramps and converges; `MemoryCurrent` failure
aborts-and-reports; (17) live sync applies safe changes without restart;
unsafe `memory.max` decreases refused with no partial commit; (18) an older
revision can never win against a newer sync/stop/ramp.

Failure/scale: (19) behavior driven by D-Bus error names under a
fault-injecting bus; (20) hundreds of servers reconcile without per-sibling
bus reads per create (ledger asserted by call-count instrumentation);
(21) `required` blocks only affected starts; best-effort degrades
conspicuously; (22) Wings restart reconstructs desired/effective state from
Panel + systemd + Docker without losing ownership or budget.

Gate: `tester-unified` with full run-uid identity + the privileged
systemd/Docker container (cgroup v2, `memory_recursiveprot`, transient
units, daemon-reload, scope placement, BFQ where available, concurrency,
fault injection). The devcontainer is the cockpit, not the gate.

## Migration from the current production stack

1. Production stays on the legacy `cgroup/<ref>` image until the v2 program
   gates (companion doc, §Workstreams).
2. Capture desired + effective values for both Soulmask slices (the node
   runs `enabled: true`, `memory_min_budget: 8G`, `budget_policy: clamp`,
   `steady_ramp_step: 64M`, `startup_grace: 15m`, empty node defaults —
   values live in the egg).
3. Define the node profile from those values:

   ```yaml
   soulmask-latency:
     placement: per-server
     failure_policy: required
     aux_containers: maintenance
     steady:  {memory_min: 6G, memory_low: 7G, memory_high: 7G, memory_max: 20G}
     startup: {memory_high: 20G}
     transition: {ready: lifecycle, timeout: 15m, timeout_action: apply-steady}
   ```

   (Readiness = the registration line via lifecycle L2:
   `registe server soulmask session succeed`. Host-specific numbers stay out
   of upstream defaults.)
4. `floor_budget: {value: 8G, mode: distribute}` — replacing the live
   clamp case (two 6G floors vs 8G) with the kernel's usage-proportional
   split; `wings.slice` unit (`MemoryMin=8G`, `MemoryLow=12G`,
   `MemoryHigh=14G`, `CPUWeight=800`, `IOWeight=7800` → bfq 800) remains the
   admin-owned tier declaration Wings never writes.
5. Land placement first against operator-owned slices; then the adapter with
   read-back compared against production values; then phases (moving the
   steady matcher to L2); then online changes rehearsed on a disposable
   server (including a lowering ramp and a revision rollback).
6. Egg migration per the mapping table; `overrides.allow_inline: true` only
   for the transition window, then off.
7. Retire with the lifecycle migration (same maintenance window, companion
   §Migration): `allowed_ramdisk_units`, `WINGS_CG_RAMDISK_UNITS`,
   `WINGS_CG_CHILD_SERVERS` → `WINGS_START_AFTER`, legacy host-side
   `setup-cgroups.sh` writer fully retired (single-writer rule).

## Direct answers (updated)

- **Synergy with the release manager?** Host hierarchy, contention policy,
  the L2 readiness event, and logs — no code or rollout dependency in either
  direction.
- **Clean independent proposal?** Yes; placement and resource policy stand
  alone and are reviewed without Steam/tmpfs/Soulmask concepts.
- **Different from the current patches how?** Profiles as the primary UX;
  three explicit axes; deterministic budget modes with the kernel-honest
  `distribute` retained; reconciling instead of additive property
  application; online reconciliation on the existing sync path; a generic
  phase machine consuming the lifecycle readiness event; the defect ledger
  closed; a smaller PR ladder; dependency and ramdisk hooks moved out.
- **Is the current cut good?** As local history and evidence, yes —
  placement/diagnostics are near upstream-ready; 0004+ is too coupled to
  submit unchanged, and the defect ledger is the concrete reason.
- **Can egg/server variables change a running container's cgroups online?**
  Properties yes — profile-resolved values reconcile after any server sync
  (Panel push or pre-start), with guarded lowering. Placement no —
  `pending_recreate` until the next container recreation, which every
  restart performs anyway.

## Evidence index

Wings v1.13.1 ([verified] via `git show v1.13.1:<path>`): two create sites
(`environment/docker/container.go:138`, `server/install.go:403`); no
`CgroupParent` in vanilla HostConfig (`container.go:228-260`); recreate on
every start (`environment/docker/power.go:26`); Running via done matcher
(`server/listeners.go:149-182`); `Sync()` callers (`server/power.go:173`,
`router/router_server.go:145,158`, `server/install.go:89`,
`cmd/root.go:264`); `InSituUpdate` seam (`environment/environment.go`);
power locks (`system/locker.go:34,47`); boot restoration
(`cmd/root.go:170-259`).

Current stack ([verified] patched tree + pr/): config schema
`config/config_docker.go:120-271`; env inventory
`internal/cgroups/cgroups.go`; ramp internals `sysd.go:303,308,347,387-390`,
`cgroups.go:186,196`; phase wiring `server/slice_phase.go`,
`environment/docker/container.go:187`, `environment/docker/power.go:51`;
child-start gate `slice_phase.go:162,172-174`; defect anchors as tabled;
derived slice name `config_docker.go:405`; 0011 hook `container.go:213-223`.

Kernel (docs.kernel.org admin-guide/cgroup-v2.html): memory.min/low
semantics; proportional overcommit distribution; `memory_recursiveprot`;
leaf charging ("Memory Ownership"); `memory.zswap.max` /
`memory.zswap.writeback`; io.weight range/default; reclaim-root protection
scope (mm/vmscan.c `mem_cgroup_calculate_protection`). BFQ weight interface:
kernel block/bfq documentation. systemd: property set + ranges per
systemd.resource-control(5) (Debian 13; `MemoryZSwapMax=`,
`MemoryZSwapWriteback=`, `IOWeight=`, `CPUWeight=` 1:1 onto `cpu.weight`);
IOWeight→`io.bfq.weight` double-write per systemd source
(`src/core/cgroup.c`, `set_bfq_weight()`), confirmed by the [measured]
read-back table (CGROUP-SEMANTICS.md Rule 7 — host measurement, mechanism
attributed to systemd source, not to the local doc).

Production values ([measured]/[verified] repo mirrors): egg
`WINGS_CG_*` values incl. steady 6G/7G/7G/20G, startup high 20G,
registration matcher; node `per_server_slices` block; `wings.slice` unit;
clamp-in-effect note in the client instance env file.
