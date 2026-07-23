# General cgroup v2 resource policy for Wings — rev 2, implementation go

- Status: **implementation go** (all conditional-go findings resolved),
  2026-07-23
- Supersedes: [`shared-ramdisk-update-lifecycle-cgroups-1-codex-fable.md`](shared-ramdisk-update-lifecycle-cgroups-1-codex-fable.md).
  Incorporates the resources-side findings of
  [`shared-ramdisk-update-lifecycle-4-codex-combined-final-remarks.md`](shared-ramdisk-update-lifecycle-4-codex-combined-final-remarks.md)
  (issues 4, 5, 6 and the general golden-harness / capability-and-reset
  contracts).
- Companion and master plan: [`shared-ramdisk-update-lifecycle-5-fable.md`](shared-ramdisk-update-lifecycle-5-fable.md)
  — carries the full decision log (8 decisions), the review triage of all
  twelve issues, the cross-series DAG, the contracts-first kickoff plan, and
  the `series.yaml` patchstack tooling. This document does not repeat them;
  it implements their resources-side consequences.
- Implementation bases: Pterodactyl Wings `v1.13.1` =
  `e771816d5e072b3f2a8b9383bfcaffaa8f569dfa`; Pelican Wings
  `main @ 70f3344cc588b31e1f48e10ddcb87d116b957e69`. The local 0001–0011
  stack is evidence and prototype only.
- Evidence rule: Wings claims cite vanilla source; kernel claims cite kernel
  documentation; systemd claims cite systemd.resource-control(5) (Debian 13)
  or systemd source; local docs are hints; measurements are labeled
  [measured].

## Review triage (resources-side)

| # | Review point | Disposition → where |
|---|---|---|
| 4 | Series not independent once resource phases consume L2 | **Adopted** — R6 core stands alone behind a `ReadySignal` interface with the egg-done default; integration patch `I1` (its own one-patch series) binds L2 on the combined branch; `series.yaml` + CI matrix (companion §Kickoff) → §Phases, §PR sequence |
| 5 | `reserved` ledger cannot be rebuilt from systemd units; sync "rejection" undefined | **Adopted** — two ledgers (reserved-desired from server configurations incl. Offline servers; admission-active transactional); an invalid resource selector never rolls back unrelated sync content and never overwrites the last accepted resource revision → §Floor budget |
| 6 | Sync-triggered reconcile deadlocks on the power lock | **Adopted** — normative lock hierarchy; pre-start sync passes its power-operation context; Panel-pushed sync never touches the power lock; revision-based cancellation; race gate enumerated → §Online reconciliation |
| G5 | Golden harness flaky under clocks/IDs/map order/goroutines | **Adopted** — determinism spec: fixed clock+ID sources, canonical serialization, partial-order event comparison, disabled-mode allow-list; contract reworded to "semantically identical" → §Golden harness |
| G6 | Capability/reset contracts unpinned | **Adopted** — D-Bus reset-value table, boot capability probes, `required` rejects (never merely degrades), rootless/non-systemd statement, block-device discovery from backing filesystems, selector/numeric bounds → §Systemd driver rules, §Configuration model |

(Issues 1–3 and 7–12 are lifecycle/manager-side; resolved in the
companion.)

## Changes from the previous revision (cgroups-1-fable → 2)

1. **Floor budget rebuilt around two ledgers** (issue 5). rev 1 proposed
   rebuilding the `reserved` ledger from
   `ListUnitsByPatterns(["wings-*.slice"])` — which silently undercounts
   enrolled-but-Offline servers, the exact population `reserved` exists to
   protect. Now: a **reserved-desired ledger** derived from all server
   configurations + resolved profiles (systemd is never its authority) and
   an **admission-active ledger** scoped to starting/running attempts;
   systemd enumeration is demoted to effective-state reconciliation.
   `distribute` needs accounting and diagnostics only.
2. **Sync-rejection semantics defined** (issue 5): resource-policy
   validation runs against the incoming sync payload; on failure the rest
   of the server sync proceeds, the last accepted resource revision stays
   active, and a precise `rejected_revision {id, reason}` is exposed. No
   partial application, no rollback of unrelated server settings, no
   overwrite of last-known-good before validation.
3. **Normative lock model** (issue 6). rev 1 said "serialize the reconcile
   with power actions" — but vanilla pre-start `Sync()` already runs while
   `HandlePowerAction` holds the power lock ([verified]
   `server/power.go:56,171-173`), so a hook reacquiring it deadlocks.
   Now: a strict hierarchy with the pre-start path passing its
   power-operation context, and the Panel-push path never taking the power
   lock at all.
4. **R6 decoupled from the lifecycle series** (issue 4): the phase machine
   consumes a package-local `ReadySignal` interface whose built-in source is
   the egg `startup.done` event; the L2 binding moves to integration patch
   `I1` on the combined branch. Every commit of `resources/<ref>` builds
   against vanilla alone.
5. **Capability and reset contracts pinned** (G6): the property reset-value
   table with D-Bus types; boot-time capability probes on a scratch slice;
   the rule that a profile promising hard floors **rejects** affected starts
   under `required` when `memory_recursiveprot` or a required property is
   unavailable — degradation is only a best-effort-profile behavior;
   rootless Docker / non-systemd hosts are placement-only and stated
   unsupported for the systemd driver in v1; block-device discovery derives
   from the filesystems actually backing game volumes and the release
   store, never from enumerating `/sys/block`; selector and numeric bounds
   specified.
6. **Golden harness determinism spec added** (G5).
7. **PR ladder renumbered against the DAG** (companion): R1; R2 → R3 → R4 →
   R5 → R6core → R7; R8 after R5; I1 in the integration series. CI runs
   vanilla, every series prefix per commit, the combined result, both
   targets.
8. Carried unchanged in substance: hard compatibility requirement, three
   axes, host hierarchy, configuration model and migration mapping (bounds
   added), placement layer, defect ledger of the current stack, IO/BFQ
   design, aux-container policy, manager boundaries, observability,
   security, migration plan (`soulmask-latency` profile,
   `clamp`→`distribute`), direct answers, evidence index.

## Executive decision (carried)

Cgroup support is a separate proposal and a separate patch series from the
shared-release manager: the resources series decides where Wings containers
are charged and what policy applies; the manager owns releases, generations,
and mount leases. They share the host hierarchy, the L2 readiness event
(via I1), and observability conventions — never configuration, provider
calls, failure domains, or upstream PRs. Design order: small default-off
placement first; node-defined **profiles** as the primary interface; systemd
ownership as an optional layer; a generic phase machine; online
reconciliation on the existing sync path; complete vanilla behavior when
disabled.

## Hard compatibility requirement and golden harness

Requirements carried verbatim from rev 1 (`docker.cgroups.enabled: false`
default; absent block = no D-Bus, no units, no `HostConfig` change, no
parsing, no goroutines; placement-only mode needs no bus; `required`
failures scoped to their servers).

### Golden harness (G5 — determinism spec, shared with the lifecycle series)

Contract: **feature-disabled behavior is semantically identical to pinned
vanilla**, established by a stable mechanical comparison — not a raw byte
diff.

- **Fixed sources**: the harness injects a fixed clock and deterministic
  ID/random sources at the recorder boundary; fixture servers use constant
  UUIDs/names.
- **Canonical serialization**: Docker create payloads compared as canonical
  JSON — sorted keys, zero/default fields elided, environment and label
  maps sorted, mount lists sorted by target.
- **Event comparison**: per-server event sequences are strictly ordered;
  cross-server interleaving is compared as a partial order (concurrency is
  legitimate, reordering within a server is not).
- **Allow-list**: an explicit, reviewed file of intentional
  disabled-mode diffs (e.g. R1's discarded-key warnings — new diagnostics,
  no behavior); anything not allow-listed is a blocker.
- Fixtures cover server create/start, installer, transfer, and restore
  paths on both pinned trees.

## Three independent axes (carried)

| Axis | Question | When it can change | Owner |
|---|---|---|---|
| Placement | Which slice holds the Docker scope? | Create only | Wings create path |
| Properties | Which min/low/high/max and weights apply? | Online | systemd driver / host IaC |
| Phase policy | Which property set during startup vs steady? | Readiness transition | Phase machine + driver |

Vanilla sets no `CgroupParent` ([verified]
`environment/docker/container.go:138-260`); exactly two container-create
sites exist (server `container.go:138`, installer `server/install.go:403`).

## Recommended host hierarchy (carried)

Node-configured root; both the current `wings.slice` + `game-releases.slice`
shape and a fresh-host `game-host.slice` umbrella are valid. Manager staging
never sits under the game tier's protected subtree (with
`memory_recursiveprot`, a parent's protection covers its whole subtree —
downloaders below `wings.slice` would compete for the live games' floor).
Note the companion's issue-7 consequence: `game-releases.slice` now carries
its own admin-owned `MemoryMin` backing the generation class floors,
reconciled against the same host budget as `wings.slice`.

## Configuration model

Schema as rev 1 (normative), with bounds added (G6):

```yaml
docker:
  cgroup_parent: wings.slice          # PR-2 primitive
  cgroups:
    enabled: false
    driver: placement-only            # placement-only | systemd
    root_slice: ""                    # empty = docker.cgroup_parent
    server_slice_template: "wings-{uuid32}.slice"
    selection_variable: WINGS_RESOURCE_PROFILE
    default_profile: ""
    unselected_policy: root           # root | stock
    failure_policy: best-effort       # best-effort | required
    floor_budget: {value: 8G, mode: distribute}   # reserved | admission | distribute
    ramp: {step: 64M, step_wait: 2s, poll_interval: 100ms,
           max_duration: 10m, max_steps: 512}
    overrides: {allow_inline: false, allowed_fields: []}
    profiles: { ... }                 # as rev 1 (steady/startup/transition/io/zswap)
```

Bounds (validated at boot for node config — fatal; at sync for overlays —
rejected revision, §Floor budget):

- profile/selector names: `[a-z0-9][a-z0-9-]{0,63}`;
- memory fields: parseable sizes, ≤ 1 TiB; `min ≤ low` warning when
  `low < min` (kernel treats them independently but the intent is ordered);
  `low > high` flagged decorative;
- `cpu_weight`, `io.systemd_weight`: 1–10000; `io.bfq_weight`: 1–1000;
  the two io forms mutually exclusive (node: fatal; overlay: neither
  applied, logged);
- ramp: `step ≥ 1M`, `step_wait 100ms–60s`, `max_steps ≤ 4096`;
- `transition.timeout`: 0 (disabled) or 10s–24h.

Selection precedence, `unselected_policy`, the field-overlay rules, and the
complete migration mapping from the shipped `docker.per_server_slices.*`
keys and all 17 `WINGS_CG_*` variables are carried unchanged from rev 1
(including: `WINGS_CG_STEADY_MATCH` → lifecycle `WINGS_READY_MATCH`;
`WINGS_CG_CHILD_SERVERS` → lifecycle `WINGS_START_AFTER`;
`WINGS_CG_RAMDISK_UNITS` and `allowed_ramdisk_units` retired).

## Placement layer (carried)

PR-2: node `docker.cgroup_parent`, boot-validated, both create sites,
default-off, integration tests with unique fixture names and guaranteed
cleanup (the early-return that let stale fixtures pass is [verified]
`container.go:145`). Placement is never online-mutable: `pending_recreate`,
resolved by the next start's recreate ([verified]
`environment/docker/power.go:26`).

## Managed slice layer

### Two ownership modes (carried)

Placement-only (no bus, permanent baseline) and systemd-managed (transient
slices over D-Bus; the t3a daemon remains the external-helper prototype if
upstream refuses in-Wings D-Bus).

### Systemd driver rules (rev-1 rules + G6 contracts)

Carried: systemd-owned properties only; error handling by D-Bus error name
(`org.freedesktop.systemd1.UnitExists`, `.NoSuchUnit`,
`org.freedesktop.DBus.Error.NameHasNoOwner`, `.NoReply`, `.AccessDenied`);
bus endpoint probing with every attempt reported; active-and-owned
verification; adopt-by-policy; GC never stops non-transient units;
reconciling full-set application; GC race closed (live re-read under the
admission lock); `MemoryCurrent` via the *Slice* type interface;
`memory_recursiveprot` probe. The "raw writes wiped by daemon-reload" claim
stays version-agnostic (did not reproduce on Debian 13 systemd — [verified]
`_LATEST_SUMMARY.log:32`); systemd-owned values are the guaranteed channel.

**Reset-value table (G6, normative).** Removing a field from a profile (or
an overlay) applies the explicit default through `SetUnitProperties`:

| Property | D-Bus type | Reset value |
|---|---|---|
| `MemoryMin`, `MemoryLow` | `t` | `0` |
| `MemoryHigh`, `MemoryMax`, `MemorySwapMax`, `MemoryZSwapMax` | `t` | `UINT64_MAX` ("infinity") |
| `CPUWeight`, `IOWeight` | `t` | `UINT64_MAX` (`CGROUP_WEIGHT_INVALID` → unset/default) |
| `MemoryZSwapWriteback` | `b` | `true` |

The exact constants are asserted against the deployed systemd by the e2e
read-back oracle (set → remove → read back both systemd property and
cgroupfs file); the table is the contract, the oracle is the proof.

**Capability detection (G6).** At boot (and in `doctor`), the driver probes
a scratch transient slice: property acceptance (error names distinguish
"unknown property" from denial), `memory_recursiveprot` in `/proc/mounts`
cgroup2 flags, scheduler per relevant block device. Results feed per-node
capability state:

- a profile that **promises hard floors** (`failure_policy: required` with
  `memory_min`/`memory_low` set) **rejects the affected start** when
  recursive protection is absent or the property cannot be applied —
  `required` never "degrades" a promised guarantee;
- best-effort profiles degrade loudly (health + doctor + logs);
- zswap properties absent (older systemd/kernel) → profiles using them are
  rejected under `required`, skipped-with-warning under best-effort.

**Rootless / non-systemd (G6).** The systemd driver requires the system
bus and root-equivalent unit management; rootless Docker and non-systemd
hosts are **unsupported for `driver: systemd` in v1** — `placement-only`
remains available wherever Docker accepts a cgroup parent. Stated in config
docs and enforced by the probe (actionable boot error when
`driver: systemd` meets no usable bus).

**Block-device discovery (G6).** IO ceilings/weights that need device
scoping resolve devices from the filesystems actually backing the paths in
play — `stat`/`mountinfo` of the server volume roots and (manager-side) the
release store, walked to the parent block device (dm/partition → parent) —
never by enumerating `/sys/block`.

### Failure behavior (carried)

`best-effort` (default): log, degrade, create with resolved placement.
`required`: refuse the affected start before Docker create when the slice or
required effective properties cannot be proven (now including the
capability rules above). The Soulmask profile is `required`.

## Floor budget (issue 5 — rebuilt)

### Two ledgers

```text
reserved-desired ledger
  population : every enrolled managed server (any state, incl. Offline)
  source     : server configurations + resolved profiles/overlays ONLY —
               rebuilt at boot from the Wings server manager; systemd is
               never consulted for desired amounts
  accounting : per server, the LARGEST floor across its phases
               (max of startup/steady memory_min)
  release    : unenrolment, profile change, server deletion, node-policy
               change — never a mere stop

admission-active ledger
  population : starting + running attempts
  lifecycle  : admitted transactionally before Docker create (under the
               admission lock), released on stop/abort/delete
```

Mode semantics:

| Mode | Ledger consulted | Over budget |
|---|---|---|
| `reserved` | reserved-desired | the *enrolment* is rejected (see sync semantics) — deterministic, order-independent; starts of admitted servers never fail on budget |
| `admission` | admission-active | the *start* is rejected or queued (policy) |
| `distribute` | none (accounting only) | floors applied as requested; overcommit logged; the kernel splits the parent's protection proportionally to usage below each child's floor ([kernel] overcommit rule) |

Systemd enumeration (`ListUnitsByPatterns` + typed reads) survives only as
the **effective-state reconciliation** source: detecting drift between the
ledgers and reality, ownership verification, and orphan cleanup.

### Sync-rejection semantics (issue 5, normative)

Vanilla has already fetched and applied Panel configuration when its sync
paths run ([verified] `Server.Sync()` callers: `server/power.go:173`,
`router/router_server.go:145,158`, `server/install.go:89`,
`cmd/root.go:264`). Therefore:

- resource-policy validation runs against the incoming payload **before**
  the last accepted *resource* revision is replaced;
- on validation or reserved-budget failure: the remainder of the server
  sync applies normally; the previous resource revision stays active; the
  server's resource status records `rejected_revision {id, reason}` and the
  event is logged and visible in `doctor`;
- an invalid resource selector never rolls back unrelated server settings,
  and never overwrites the last known-good resource policy.

### This node

Unchanged recommendation, now expressible correctly: two cooperating 6G
floors against the 8G tier budget run `mode: distribute` (the kernel's
usage-proportional split; the tier total backed by `wings.slice
MemoryMin=8G` is the real guarantee), replacing the shipped order-dependent
`clamp` ([verified] production `budget_policy: clamp`, and the client
instance's own "clamped by budget" comment). Migration mapping: `clamp` →
`reserved` (if floors are sold guarantees — then fix the numbers) or
`distribute`; `refuse` → `admission` (semantic delta documented: `refuse`
started floor-less, `admission` refuses the start); `distribute` →
`distribute`.

Validation warnings carried: dead child floors above parent protection,
decorative `low > high`, ceilings above RAM, meaningless limits vs swap
capacity.

## Startup and steady phases (issue 4 — decoupled)

State machine, bands, and rules carried from rev 1 (only the memory band is
staged — weights are work-conserving; steady pre-applied at create because
`Create()` also runs on non-start paths; startup applied in
`OnBeforeStart`; timeout applies steady but never emits readiness — the
shipped child-start gate proved the rule, [verified]
`server/slice_phase.go:162,172-174`).

**Readiness source (new shape).** R6 defines, package-locally:

```go
type ReadySignal interface {
    // Subscribe returns a channel delivering at most one Ready event for
    // the given start attempt; the registry owns arming/reset semantics.
    Subscribe(serverID string, attemptID string) (<-chan Ready, func())
}
```

- R6 ships one built-in source: the egg `startup.done` transition that
  already drives Running ([verified] `server/listeners.go:149-182`) —
  `resources/<ref>` builds and works against vanilla alone, every commit.
- Integration patch **I1** (the `integration` series on the combined
  branch) registers lifecycle L2's event as a `ReadySignal` source and maps
  `transition.ready: lifecycle` onto it. Without I1, `ready: lifecycle`
  is a validation error naming the missing series.

Ramp algorithm and configuration carried (config'd constants; Slice-type
`MemoryCurrent`; read-failure → abort-and-report; revision-based
cancellation; journal record).

## Online reconciliation (issue 6 — lock model normative)

```text
lock hierarchy (strict order, never reversed):
  1. server power lock        — owned by power operations only
  2. server resource/attempt mutex
  3. node admission-ledger / systemd-driver lock
```

- **Pre-start sync** (inside `HandlePowerAction`, which already holds the
  power lock — [verified] `server/power.go:56,171-173`): the reconcile
  receives the power-operation context and acquires only locks 2→3. It
  never reacquires lock 1.
- **Panel-pushed sync** (`postServerSync`, outside any power operation):
  computes and validates the desired revision, then applies online-safe
  changes under locks 2→3 only. It never takes the power lock; a
  concurrently running power operation is serialized at lock 2.
- **Phase transitions and ramps** run under lock 2 with revision checks;
  a monotonically increasing revision ensures an older async reconcile or a
  stale ramp can never overwrite a newer profile (cancellation by
  revision).
- Provider prepare/commit (lifecycle) obeys the same order: it runs inside
  a power operation (lock 1 held) and takes lock 2 for attempt state.

**Race gate** (privileged e2e, all pairs): sync vs start, stop, restart,
readiness, ramp, admission, provider prepare/commit; plus
sync-vs-newer-sync revision ordering. Vanilla's `InSituUpdate()` precedent
([verified] `environment/environment.go`) and the online-change matrix are
carried from rev 1 unchanged (weights/protections live; ceilings raise
immediately, lower via ramp; `memory.max` lowering refused below
current+margin; placement pending-recreate).

## IO/BFQ design (carried)

Kernel `io.weight` [1,10000] default 100, sibling-relative,
work-conserving; BFQ reads its own `io.bfq.weight` (1..1000); systemd
`IOWeight=` writes both with ~11× ratio compression above default (systemd
source `src/core/cgroup.c` `set_bfq_weight()`; absent from the man page;
[measured] read-back table, exact inverse `IOWeight = 100 + 11×(bfq−100)`).
Panel "Block IO Weight" is a third, scope-level, uncompressed knob. v2:
nested `io: {systemd_weight | bfq_weight}` (mutually exclusive; node both →
fatal, overlay both → neither, logged); requested and effective values
reported; non-BFQ scheduler → warn, weights inert, only `io.max` bites;
BFQ intent stays out of the placement PR (R8).

## Installers and auxiliary containers (carried)

Profile `aux_containers: server | maintenance | unmanaged`; both create
sites verified; installer receives server env and its own limits
([verified] `server/install.go:403,535`). Soulmask: `maintenance`.

## Relationship to the release manager (carried)

Synergies: shared hierarchy and doctor; the manager's stage slice depends on
hard aggregate ceilings; rollout logs record generation + profile; L2 (via
I1) drives both startup→steady and MAIN→CLIENT. Boundaries contract-grade:
no cross-configuration, no cross-calls, no shared failure domains, no
combined PRs; manager slices are ordinary host units that work on vanilla
Wings.

## Review of the current patch cut and defect ledger (carried)

The per-patch assessment table and the defect ledger stand as in rev 1
(orphan-GC race `cmd/root.go:176-198`; text-matched D-Bus errors
`sysd.go:280,420`; O(n) sibling reads `sysd.go:556-577` in a 10s timeout;
additive `Ensure`; fixed-name fixtures + early return `container.go:145`;
`io_weight` collision `pr/README.md:84-89` / `pr/rfc-issue.md:52-64`;
missing compose D-Bus mount; version-dependent Finding D). Each closure is
normative in the sections above.

## PR sequence (renumbered against the DAG)

Series `resources`, branch `resources/<ref>`, exported per
`series.yaml` (companion §Kickoff). Every commit builds/vets/tests
standalone against vanilla.

1. **R1 config diagnostics** — shipped 0008 as-is. Phase-1 work.
2. **R2 node placement** — `docker.cgroup_parent`, both create sites,
   validation, integration tests (unique names + cleanup). Phase-1 work.
3. **R3 named placement profiles** — placement-only profiles, selection
   variable, UUID overrides, `pending_recreate`, optional allow-listed
   parent escape hatch. No D-Bus.
4. **R4 systemd slice adapter** — ensure/reconcile/read-back/ownership/GC,
   error-name handling, bus probing, capability probes, compose docs
   (ships the D-Bus mount example).
5. **R5 budgeted resource profiles** — full property set incl. optional
   zswap fields, reset table, two ledgers, `reserved|admission|distribute`,
   sync-rejection semantics, effective-value API.
6. **R6 resource phases (core)** — bands, phase machine, ramp,
   `ReadySignal` with the egg-done built-in. No lifecycle imports.
7. **R7 online reconciliation** — lock model, revisioning, guarded
   lowering, race gate.
8. **R8 BFQ intent** — nested io object + effective read-back; after
   maintainer signal.
9. **I1** (series `integration`, combined branch only) — binds lifecycle
   L2 into the `ReadySignal` registry.

Gates per phase in the companion's kickoff plan. Upstream: Pelican first,
cross-submit Pterodactyl; R1 and R2+R3 are the first PRs; R4+ as an RFC'd
follow-up ladder.

## Observability and API (carried + ledger state)

Per managed server: profile + source, desired revision (+ any
`rejected_revision {id, reason}`), desired vs actual parent, phase +
trigger, desired / systemd-applied / cgroupfs-effective values,
**reserved-desired and admission-active ledger entries**, budget mode and
admitted amount, ramp progress, degraded/required state, pending-recreate
reason, last reconcile operation. `doctor`: cgroup v2 + controllers +
`memory_recursiveprot`, bus, capability-probe results, root/parent units +
floors (including `game-releases.slice` protection backing), scheduler +
BFQ read-back, stale units, live placement audit, **ledger vs systemd
drift**. Matched console content is never published.

## Security model (carried)

Egg data selects node-known profiles; slice names derive from validated
root + canonical UUID (`wings-<32hex>.slice`); Wings manages only its
derived-shape transient units; admin-owned persistent slices adopt-only-by-
policy, never GC'd; bus access is node privilege (containerized deployments
document the mount); resource values are non-secret but strictly parsed
(bounds above, ordering, ancestor constraints, budget, unsafe decreases).

## Acceptance oracles (updated)

Stock compatibility: (1) golden harness per §Golden harness — disabled v2
semantically identical to vanilla on both trees; (2) zero bus connections
disabled; (3) provider/cgroups mutual invisibility.

Placement/ownership: (4–7) as rev 1, including the create-during-sweep GC
race regression.

Properties/budget: (8) systemd-owned values survive daemon-reload; raw-write
wipe observed-or-not is logged, never asserted; (9) field removal applies
the reset-table value, verified by systemd + cgroupfs read-back;
(10) **reserved ledger counts enrolled Offline servers** — enrolling a
third 6G floor against an exhausted budget is rejected at sync with a
precise `rejected_revision`, while a stop/start cycle of an admitted server
never re-litigates its reservation; (11) admission-mode concurrency: two
simultaneous admissions cannot exceed the budget (transactional under the
admission lock); (12) distribute logs overcommit and the kernel's
proportional split is observed under synthetic pressure; (13) missing
`memory_recursiveprot` or an unsupported property **rejects** affected
`required` starts and loudly degrades best-effort ones; (14) slice weight +
scope `--blkio-weight` compose; requested and effective BFQ values visible.

Phase/online: (15) startup band only on real starts; (16) Ready applies
steady exactly once; revision cancellation kills stale timers/ramps;
(17) timeout never emits readiness or starts dependents; (18) lowering
ramps converge; `MemoryCurrent` failure aborts-and-reports; (19) live sync
applies safe changes without restart; invalid resource payloads leave the
prior revision active and the rest of the sync applied; (20) the full race
gate (sync × start/stop/restart/readiness/ramp/admission/prepare) passes
with no deadlock and no stale-revision win — including pre-start sync
running under an already-held power lock.

Failure/scale: (21) behavior driven by D-Bus error names under a
fault-injecting bus; (22) hundreds of servers reconcile with O(1) bus
traffic per create (ledger call-count instrumentation); (23) `required`
blocks only affected starts; (24) Wings restart reconstructs desired +
effective state and both ledgers from Panel/node + systemd + Docker without
losing ownership or reservations.

Gate: `tester-unified` + the privileged systemd/Docker e2e container
(cgroup v2, recursiveprot, transient units, daemon-reload, scope placement,
BFQ where available, capability probes, concurrency, fault injection).

## Migration from the current production stack (carried)

As rev 1: production stays on the legacy `cgroup/<ref>` image until the v2
program gates; the `soulmask-latency` profile captures the deployed values
(steady 6G/7G/7G/20G, startup high 20G, ready = registration line via I1,
timeout 15m, `aux_containers: maintenance`, `failure_policy: required`);
`floor_budget: {value: 8G, mode: distribute}`; `wings.slice` stays the
admin-owned tier declaration; egg migration per the mapping table with
`overrides.allow_inline: true` only for the transition window; legacy
`setup-cgroups.sh`, `allowed_ramdisk_units`, `WINGS_CG_RAMDISK_UNITS`, and
`WINGS_CG_CHILD_SERVERS` retire in the same maintenance window as the
lifecycle migration (single-writer rule).

## Direct answers (carried, one update)

As rev 1, with the independence answer sharpened: the two series are
independent **by construction** — R6 core carries its own readiness
source, and the only cross-series code lives in the one-patch integration
series applied solely on the combined branch. The manager depends on
lifecycle L1/L1b only; it has no dependency on this series at all.

## Evidence index

As rev 1 (vanilla anchors: two create sites, no vanilla `CgroupParent`,
recreate-on-start, Running via matcher, Sync callers, `InSituUpdate`,
locker, boot restoration; current-stack anchors: config schema, env
inventory, ramp internals, phase wiring, child-start gate, defect anchors,
derived slice name; kernel: min/low + proportional overcommit,
`memory_recursiveprot`, leaf charging, zswap knobs, reclaim-root protection
scope, io.weight; BFQ kernel doc; systemd.resource-control(5) Debian 13 +
`set_bfq_weight()` source; production values). **New this revision**:
`server/update.go:21-31` (`SyncWithEnvironment()` settings snapshot — the
shared issue-1/issue-6 context), and `server/power.go:56,171-173` re-cited
as the proof that pre-start sync runs under the held power lock.
