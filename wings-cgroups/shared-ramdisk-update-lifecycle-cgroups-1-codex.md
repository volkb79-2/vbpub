# General cgroup v2 resource policy for Wings — proposal 1

- Status: clean-slate architecture and patch-stack review, 2026-07-23
- Starting point: vanilla Pterodactyl Wings v1.13.1; the current local
  `0001`–`0011` stack is production evidence, not an upstream dependency
- Scope: container placement, systemd slice lifecycle, startup/steady resource
  phases, bounded transitions, and online reconciliation
- Relationship to release management: operational synergy, no code or rollout
  dependency

## Executive decision

Cgroup support should be a separate Wings proposal and a separate patch branch
from the shared-release manager. Both features care about lifecycle events and
resource isolation, but they solve different general problems:

```text
Wings cgroup support
  decides where Wings containers are charged and what resource policy applies

shared-release manager
  creates immutable releases, publishes tmpfs generations, and leases mounts
```

The manager should install and control its own maintenance slice hierarchy even
on vanilla Wings. Patched Wings should place game containers in per-server
slices even when no release manager exists. They may share a documented
top-level host hierarchy and observability conventions, but not configuration,
provider calls, failure domains, or upstream PRs.

The current local stack proves that the requested controls work, including
per-server placement, `memory.min/low/high/max`, CPU/IO weights, BFQ mapping,
startup/steady bands, console matching, a grace backstop, and a descending
`memory.high` ramp. It is a useful production branch, but it is not the clean
upstream series I would submit. From scratch I would:

1. land small, default-off placement support first;
2. select node-defined **resource profiles**, not expose raw unit names and
   seventeen unrelated `WINGS_CG_*` values as the primary product schema;
3. make systemd slice ownership an optional second layer;
4. implement phase changes as a general resource-policy state machine, not as
   a collection of cgroup-named console features;
5. reconcile safe property changes online after a server sync;
6. keep dependency startup and release preparation outside the cgroup series;
7. preserve complete vanilla behavior whenever the feature is disabled.

## Hard compatibility requirement

A patched Wings must remain completely usable as ordinary Wings:

- `docker.cgroups.enabled` defaults to `false`;
- an absent block takes no systemd D-Bus connection, creates no units, changes
  no `HostConfig`, parses no cgroup egg variables, and starts no goroutines;
- server creation, installation, transfers, SFTP, backups, power actions, crash
  recovery, and boot restoration retain stock behavior;
- no release manager, provider socket, systemd bus mount, special egg, or slice
  unit is required;
- enabling placement-only mode requires no systemd D-Bus access;
- a managed-mode failure affects only servers/policies explicitly configured to
  require it; unrelated servers continue normally.

This needs a golden compatibility suite comparing the Docker create request and
lifecycle events produced by vanilla and feature-disabled patched Wings.

## Source and local evidence

The clean-slate design relies on these facts:

- Docker exposes a create-time cgroup parent; its CLI documents
  [`--cgroup-parent`](https://docs.docker.com/reference/cli/docker/container/run/#cgroup-parent),
  and Wings builds the Docker resource configuration in
  [`environment/docker/container.go`](https://github.com/pterodactyl/wings/blob/v1.13.1/environment/docker/container.go).
- Vanilla Wings synchronizes current Panel configuration before every start in
  [`server/power.go`](https://github.com/pterodactyl/wings/blob/v1.13.1/server/power.go),
  which is the correct point to resolve current server resource intent.
- The Panel can also trigger a live server sync through Wings' server API; the
  local vanilla source path is `router/router_server.go` → `Server.Sync()`.
- cgroup v2 protection, throttling, limits, and weights are hierarchical; the
  kernel behavior is defined in the
  [cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).
- The production patch stack and `CGROUP-SEMANTICS.md` demonstrate the
  `memory_recursiveprot` prerequisite, parent-floor budget, systemd/BFQ weight
  translation, distroless D-Bus socket trap, and create-time placement rule.

## Three independent axes

The earlier strategy correctly separated placement from properties. The phase
work reveals a third axis that should be explicit.

| Axis | Question | When it can change | Required owner |
|---|---|---|---|
| Placement | Which cgroup/slice contains the Docker scope? | Container create only | Wings/Docker create path |
| Properties | What min/low/high/max and CPU/IO weights apply? | Online | systemd controller or host IaC |
| Phase policy | Which property set applies during startup versus steady operation? | Lifecycle/readiness transition | Wings plus property controller |

Placement is the only irreducible Wings change. Static slice properties can be
owned by unit files or a host reconciler. Dynamic phase policy benefits from
Wings because Wings already knows when a real start begins, consumes the egg's
startup matcher, serializes power actions, and receives server configuration
updates.

## Recommended host hierarchy

Do not hard-code the product to one root name, but use a node-configured root
whose default examples are safe systemd slice names. The current production
shape remains valid:

```text
-.slice
├─wings.slice
│ ├─wings-mgmt.slice
│ │ └─Wings service/container
│ ├─wings-<uuid>.slice
│ │ └─docker-<server-container>.scope
│ └─wings-<uuid>.slice
│   └─docker-<server-container>.scope
└─game-releases.slice
  ├─game-releases-control.slice
  ├─game-releases-stage.slice
  └─game-releases-publish.slice
```

For a fresh host, a common ancestor can make total game-host policy clearer:

```text
game-host.slice
├─game-host-control.slice             Wings and local control helpers
├─game-host-servers.slice
│ ├─game-host-servers-<uuid>.slice
│ └─game-host-servers-<uuid>.slice
└─game-host-maintenance.slice         release/update/backup jobs
  ├─game-host-maintenance-control.slice
  ├─game-host-maintenance-stage.slice
  └─game-host-maintenance-publish.slice
```

The manager hierarchy is host policy described in the v3 release proposal. It
must not inherit the servers' `memory.min` accidentally: with recursive
protection, putting downloaders under a protected game subtree can let cold
update pages compete for the very protection intended for live games.

## Clean configuration model

### Node-defined profiles, server-selected IDs

The node owns all resource values and unit structure. An admin-only egg/server
variable selects a named profile; it cannot name a systemd unit or write raw
cgroup values by default.

Illustrative node configuration:

```yaml
docker:
  cgroups:
    enabled: false                    # zero value preserves vanilla behavior
    driver: placement-only            # placement-only | systemd
    root_slice: wings.slice
    server_slice_template: "wings-{uuid32}.slice"
    failure_policy: best-effort        # best-effort | required
    selection_variable: WINGS_RESOURCE_PROFILE
    default_profile: ""               # empty uses unselected_policy
    unselected_policy: root            # root | stock

    profiles:
      latency-sensitive:
        placement: per-server
        steady:
          memory_min: 3G
          memory_low: 4G
          memory_high: 7G
          memory_max: 14G
          cpu_weight: 1000
          io:
            systemd_weight: 4500
        startup:
          memory_min: 4G
          memory_low: 4G
          memory_high: 14G
          memory_max: 14G
        transition:
          ready: egg-startup-done
          timeout: 15m
          timeout_action: apply-steady
          memory_high_ramp_step: 64M
          ramp_interval: 1s

      best-effort:
        placement: per-server
        steady:
          memory_high: 4G
          memory_max: 6G
          cpu_weight: 50
          io:
            systemd_weight: 50

    floor_budget:
      value: 8G
      mode: reserved                 # reserved | admission | shared
```

Server configuration becomes small and understandable:

```text
WINGS_RESOURCE_PROFILE=latency-sensitive
```

Selection precedence:

```text
node UUID override
  > node egg/profile mapping
  > validated server selector
  > node default profile
  > node unselected policy
```

With the feature enabled, `unselected_policy: root` places an otherwise
unmanaged server directly under `root_slice` without a per-server child or
property profile. This preserves the useful node-wide placement stage.
`unselected_policy: stock` leaves its Docker cgroup parent untouched. With the
entire feature disabled, both settings are ignored and stock behavior is
unconditional.

The MVP may retain the current direct `WINGS_CG_*` variables behind an
`allow_inline_overrides: true` compatibility flag for this deployment. They
should not be the upstream-facing default. A profile ID is easier to validate,
audit, change atomically, and evolve into a Panel-native schema.

If maintainers/operators require field-level egg variables, make them a bounded
overlay rather than an alternate ungoverned schema. Node config allow-lists the
fields a server may override and their ranges, for example steady
`memory.high`, startup `memory.high`, and CPU weight but not the slice name,
parent budget, driver, or failure policy. Wings resolves one complete desired
policy as:

```text
node profile
  + node-authorized, strictly parsed field overrides
  = one validated desired policy revision
```

The current `WINGS_CG_*` names can feed that overlay during migration. A future
Panel-native resource block would carry the same typed fields without placing
dozens of implementation-named variables in the game process environment.
Online reconciliation is identical whether a value changed through profile
selection or an allowed field override.

### Why not raw `WINGS_CGROUP_PARENT` as the primary interface?

A raw unit-name override makes Panel data part of host topology and requires a
namespace/allow-list security model. It also lets a resource change alter both
placement and policy implicitly. Prefer a node profile that resolves to a
derived UUID slice. If maintainers want expert placement overrides, keep them
as a separate node-enabled escape hatch with an exact allow-list and a clear
“container recreation required” status.

### Installers, transfers, and auxiliary containers

Every Wings-created Docker container must have an explicit policy; otherwise an
installer can escape the tier that is supposed to bound Wings workloads.
Profiles should state one of:

- `server`: installer/transfer container uses the server's slice and a
  maintenance property phase;
- `maintenance`: it uses a node maintenance slice with aggregate limits; or
- `unmanaged`: explicit stock placement for compatibility.

Soulmask should put Steam/install work in the bounded maintenance tier, not in
the latency-protected live-server slice. This complements the release-manager
stage slice and avoids granting download/unpack pages a game memory floor.

## Placement layer

### Minimal upstream PR

The first upstream PR should only:

1. add a validated node `docker.cgroup_parent` or equivalent nested cgroup
   block;
2. apply it consistently to server, installer, transfer, and other relevant
   Docker create paths;
3. add integration tests inspecting `HostConfig.CgroupParent`; and
4. remain entirely default-off with no new dependency.

This is essentially the strongest part of current patches `0001` and `0003`.
A follow-up can add named per-server placement profiles. Do not make the first
PR depend on systemd D-Bus, transient units, BFQ, console parsing, or the
release manager.

### Placement is not online-mutable

Changing a server's selected slice cannot move an existing Docker container
safely behind Docker/systemd. Wings records the new desired placement as
`pending_recreate`; the next stop/start recreates the container in the new
slice. A restart already recreates containers in vanilla Wings, but the UI/API
must still report that placement did not change until recreation succeeded.

## Managed slice layer

### Two supported ownership modes

1. **Placement-only:** operators/IaC create persistent slice units and own all
   properties. No system bus access from Wings. This is the portable baseline
   and can be permanent.
2. **Systemd-managed:** Wings, or a narrowly scoped local helper, creates and
   reconciles transient per-server slices via systemd D-Bus. This enables
   zero-touch server enrollment, phases, budgets, and online changes.

The upstream RFC should ask whether systemd control belongs in Wings. If not,
retain the same policy resolver and lifecycle events but send declarative
requests to an optional local resource-controller socket. The helper must be
independently useful and node-configured; no egg variable chooses its socket.

Direct D-Bus is operationally simpler but expands Wings' coupling to systemd
and requires a bus mount in containerized deployments. A helper narrows the
systemd interface and can enforce one subtree, but adds another service and
protocol. Both are viable adapters beneath the same resource-policy model.

### Systemd driver correctness requirements

The new implementation must retain the local stack's hard-won rules and close
its known gaps:

- use systemd-owned transient/persistent properties, never raw cgroupfs writes;
- identify D-Bus failures by error name, never English message substrings;
- configure/probe the actual bus path and report every attempted endpoint;
- verify the unit is active and owned/adoptable, not merely loaded;
- batch sibling/property reads rather than one D-Bus round trip per server on
  every create;
- reconcile removals as well as additions—clearing a profile field must reset
  the prior live property;
- serialize budget admission and property commit under one transaction;
- avoid snapshot-then-late-GC races by reconciling against current server and
  Docker state immediately before deletion;
- retain a generation/revision on every desired and applied policy;
- read back effective values and expose degraded state;
- require/probe `memory_recursiveprot` before claiming that parent/slice floors
  protect Docker-scope pages.

The driver may adopt an administrator-owned persistent slice only when node
policy says so. Garbage collection must never stop such a unit. Wings-owned
transient units carry an ownership marker and are removed only when no
container/scope, server intent, or in-flight operation references them.

### Failure behavior

`failure_policy` is explicit:

- `best-effort`: log and mark health degraded, then create with the resolved
  placement even if properties could not be ensured;
- `required`: refuse the affected start before Docker create when the slice or
  its required effective properties cannot be proven.

The default for an opt-in first release can be `best-effort`, matching current
availability behavior, but production profiles that advertise a hard
`memory.min` guarantee should use `required`. Silent placement-only degradation
must be visible in `doctor`, logs, metrics, and the server's effective policy.

## Floor-budget design

The current `clamp` policy is operationally convenient but order-dependent: a
server starting late receives whatever floor remains and may keep that smaller
floor even after earlier servers stop. A clean design offers explicit modes:

| Mode | Budgeted population | When over budget | Meaning |
|---|---|---|---|
| `reserved` | All enrolled managed servers | Reject config/sync transaction | Every declared floor is reservable; deterministic |
| `admission` | Active + starting servers | Reject or queue the new start | Higher density; a later start may wait |
| `shared` | No hard per-child admission | Apply requested floors and warn | Parent guarantee is real; child guarantees are proportional under overcommit |

Recommended general default for profiles promising guarantees: `reserved`.
Recommended option for a hosting fleet that deliberately overbooks: `admission`.
`shared` corresponds to the kernel's usage-proportional distribution and must
not present child `memory.min` values as independent guarantees.

`reserved` accounts for the largest declared floor across a server's phases,
not merely its current steady value, so two servers starting together cannot
invalidate a budget that looked sound while they were idle or steady.

The parent protection is part of validation. A child floor above the parent's
effective floor is either rejected or reported as unbacked. The controller
should also warn when `memory.low` exceeds the effective `memory.high`, when a
child ceiling is looser than an ancestor and therefore inert, or when physical
and swap capacity make a configured maximum meaningless.

## Startup and steady phases

### Generic state machine

Do not name this subsystem after cgroups. Define a small resource-policy state
machine consumed by the systemd driver:

```text
offline/create
    |
    | actual start admitted
    v
startup policy
    | ready event OR resource-phase timeout
    v
transition/ramp
    |
    v
steady policy
    |
    | stop/crash/new start
    v
cancel transition; clear readiness; begin again on next start
```

The default ready event is the egg's existing `startup.done` signal that drives
Wings' Running state. A future generic readiness matcher may be selected instead
when “Panel running” is earlier than “safe steady state.” That matcher belongs
to a lifecycle/readiness patch reusable by dependency coordination, not inside
the cgroup package.

That reusable matcher is how the clean design preserves the current explicit
steady-phase console match. It should accept the same literal/regular-expression
forms Wings already understands, compile/validate them at sync, bind each match
to one start-attempt ID, and emit a one-shot `Ready(kind, attempt, timestamp)`
event. A profile selects `egg-startup-done` or the named readiness predicate;
the resource controller consumes the event without owning console parsing.
Soulmask selects its verified registration line. Changing the matcher while a
server runs stores it for the next attempt unless an explicit re-arm operation
is requested—an arbitrary historical console line must not satisfy it.

The phase timeout is only a resource-policy backstop. If it applies the steady
band, it must **not** emit application readiness, start child servers, or mark a
release healthy. This corrects the conceptual coupling in the current stack:
grace can bound elevated startup resources without pretending MAIN is ready.

### Safe `memory.high` ramp

When steady `memory.high` is below current usage:

1. apply non-ceiling properties that are safe immediately;
2. read `MemoryCurrent` from the slice interface;
3. lower `memory.high` by the configured step, never below the final target;
4. wait for usage to converge below the step threshold or for a bounded step
   timeout while observing pressure/OOM events;
5. continue until target, cancellation, or policy failure;
6. on restart/stop/profile update, cancel the old ramp by revision and resolve a
   fresh desired phase.

Raising a ceiling is immediate. A pathological tiny step is rejected or capped
to a maximum number of writes. The operation log records start usage, target,
steps, duration, timeout, and final readback. A ramp is a controlled reclaim
transition, not a readiness signal.

## Can egg/server-variable changes apply online?

Yes for properties; no for placement.

Vanilla Wings already updates its in-memory server configuration through
`Server.Sync()`, and performs a sync before every start. Extend successful sync
with an idempotent resource reconcile:

1. resolve the selected node profile and a monotonically increasing desired
   policy revision;
2. compare desired placement/properties with the currently applied revision;
3. if the container is running, apply online-safe property changes to its
   ancestor slice;
4. if placement changes, expose `pending_recreate` without moving the process;
5. if the server is starting, apply the startup version; if its current start
   has reached readiness, apply/ramp to the steady version;
6. serialize the reconcile with power and phase transitions so an old ramp
   cannot overwrite a newer profile.

Online-change matrix:

| Change | Running container | Required behavior |
|---|---|---|
| CPU weight | Yes | Apply and read back immediately |
| IO weight | Yes | Apply and read back; report active scheduler/effective BFQ value |
| `memory.min`/`low` | Yes | Budget transaction, then apply/read back |
| Raise `memory.high`/`max` | Yes | Apply immediately |
| Lower `memory.high` | Yes | Use controlled ramp unless explicitly forced |
| Lower `memory.max` | Conditional | Refuse below current usage + safety margin by default |
| Startup policy while already steady | Store | Applies next start; does not move server back to startup |
| Ready matcher | Store/re-arm only by explicit policy | Never retroactively invent readiness |
| Slice/root/placement profile | No | Mark pending; apply on container recreation |

Changing an egg variable in the Panel is online only when the Panel sends the
server sync. Wings should expose an explicit reconciled revision/status so an
operator can distinguish “saved in Panel,” “received by Wings,” “applied,” and
“pending recreation.” If a Panel version does not push the sync immediately,
the next start still fetches it; a scoped reconcile endpoint can provide a
deterministic operator action.

Direct raw overrides, if retained locally, must be admin-only, non-secret,
strictly parsed, and node-bounded. End users should select only profiles the
node/egg mapping authorizes.

## IO/BFQ design

The current patch correctly discovered that systemd's `IOWeight=` scale and
BFQ's `io.bfq.weight` scale do not preserve intuitive ratios above the default.
It also coexists with Wings' existing container-scope `io_weight`; weights at
the slice and scope levels compose down the tree.

For upstream clarity:

- name the portable field inside a `slice`/`io` object, avoiding a second
  top-level `io_weight` with a different meaning;
- let the systemd driver use `IOWeight` and report both requested systemd weight
  and read-back effective `io.weight`/`io.bfq.weight`;
- make an explicit BFQ-scale request a driver-specific optional field, mutually
  exclusive with the portable value;
- reject or warn when the requested controller/scheduler is not active;
- keep BFQ conversion out of the placement PR and submit it only with focused
  tests and maintainer agreement.

For example:

```yaml
steady:
  io:
    systemd_weight: 4500
    # bfq_weight: 500             # alternative, not simultaneous
```

The exact-ratio BFQ feature is valuable on this host but should not hold the
portable cgroup-parent work hostage.

## Relationship to the release manager

### Real synergies

- Both benefit from a documented top-level hierarchy and cgroup-v2 health
  checks.
- Release staging needs hard aggregate IO/CPU/memory limits so Docker image or
  Steam activity cannot evict or stall live game working sets.
- Wings server profiles can give live game slices high weight/protection while
  the manager's stage slice receives low weight and hard device ceilings.
- The rollout log can record both release generation and effective resource
  profile, making performance regressions attributable.
- A generic readiness event can later drive both the server's startup→steady
  resource transition and MAIN→CLIENT dependency coordination.

### Boundaries that must remain

- Wings cgroup enablement does not configure or call the release provider.
- Manager slice installation does not enable per-server Wings slices.
- Manager jobs never run inside a server's protected slice.
- Cgroup failure cannot select or mutate a release; release failure cannot
  rewrite server resource policy.
- No combined “Soulmask/cgroup/ramdisk” egg variable or upstream PR.

The release manager's parent and child slices are ordinary host units and work
with vanilla Wings. If a future generic host resource controller manages both
trees, that is a deployment adapter, not a reason to merge product contracts.

## Review of the current patch cut

The current history is coherent for local evolution and contains valuable
tests, but only its earliest pieces are near an upstream-ready boundary.

| Patch | Assessment | Clean-v2 treatment |
|---|---|---|
| `0001` node `cgroup_parent` | Good, small, general | Keep as first opt-in PR |
| `0002` raw `WINGS_CGROUP_PARENT` | Security-aware but exposes topology through egg data | Replace primary path with named profile; keep exact allow-list escape hatch only if requested |
| `0003` Docker placement tests | Good behavioral oracle | Keep; generate unique names and guaranteed cleanup |
| `0004` transient slices | Too large: config, parsing, D-Bus, budgets, GC, hooks, tests | Split policy model, systemd adapter, budget/admission, and lifecycle integration |
| `0005` BFQ scale | Correct host finding but scheduler-specific and naming-sensitive | Optional follow-up after portable properties |
| `0006` property rendering | Useful observability, but part of feature quality | Fold into the property/reconcile PR rather than market alone |
| `0007` startup phases | Valuable but very large; mixes matcher, timers, activity, ramp, database, and cgroups | Split generic lifecycle readiness, resource phase machine, and ramp; keep telemetry separate |
| `0008` discarded config keys | General and unrelated | Submit alone against vanilla |
| `0009` `MemoryCurrent` fix | Necessary bug fix | Squash into the rewritten ramp before review |
| `0010` child startup plus steady-trigger fixes | Child dependency is not cgroups; trigger fixes expose the coupling | Move child logic to lifecycle/dependency proposal; fold trigger semantics into the rewritten phase state machine |
| `0011` ramdisk unit trigger | Site-specific bridge with broad systemd semantics | Do not upstream in cgroup series; replace with selected provider contract |

Specific open issues already documented locally must be fixed, not merely
carried forward: orphan-GC race, text-matched D-Bus errors, O(n) D-Bus budget
reads per create, additive-not-reconciling properties, and stale fixed-name
Docker integration fixtures.

## Recommended new patch/PR sequence

Create a v2 branch from the chosen vanilla tag/develop head; do not layer the
new upstream series on the current eleven commits.

1. **Config diagnostics** — current `0008`, independent and optional to this
   work.
2. **Node placement** — validated node cgroup parent applied to every relevant
   Docker create path, default-off, integration tests, no new dependency.
3. **Named per-server placement selection** — node profiles/UUID mapping and
   optional admin-only selector; pending-recreate status; no systemd control.
4. **Systemd slice adapter RFC/PR** — ensure/reconcile/readback, ownership,
   health, cleanup, required/best-effort policy.
5. **Budgeted resource profiles** — min/low/high/max and weights, deterministic
   floor modes, effective-value API/metrics.
6. **Resource phases** — actual-start hook, stock Running signal, timeout that
   does not imply readiness, cancellation-safe transition.
7. **Online reconciliation** — sync-triggered diff/apply, revisioning,
   pending-recreate, safe lower-limit policy.
8. **BFQ-specific intent** — only after maintainer agreement; otherwise keep in
   a local/systemd policy adapter.

Generic readiness and dependency coordination form a separate lifecycle series.
The release-provider patch is another independent series. They can reuse a
small internal event abstraction after each feature is accepted; they should
not be stacked merely because this deployment uses all three.

## Observability and API

For each managed server expose:

```text
selected profile
desired policy revision and source (node / egg selector / UUID override)
desired and actual cgroup parent
current resource phase and trigger
desired, systemd-applied, and cgroupfs-effective property values
floor-budget mode, requested amount, and admitted amount
active ramp operation/progress
degraded/required failure state
pending recreation reason
last reconcile operation ID and error
```

Logs use stable event names and fields, not only prose. A node `doctor` command
checks cgroup v2, active controllers, `memory_recursiveprot`, systemd driver,
bus/helper access, root/parent units, parent floors/ceilings, scheduler, BFQ
readback, stale units, and live Docker placement.

Phase-event activity logging may be useful, but it is telemetry and should not
be required for applying cgroup properties. Never publish arbitrary matched
console content if it can contain player or secret data; record the configured
event name and server/revision.

## Security model

- Egg/server data selects a node-known profile, never a raw socket, command,
  cgroupfs path, systemd property name, or unit by default.
- Slice names are derived from a validated root plus canonical UUID, and Wings
  may manage only that subtree.
- A systemd helper/socket, if used, is registered in node config and validates
  the request again.
- System bus access is node privilege. Container deployment documentation must
  make the mount and implications explicit; do not describe it as harmless
  merely because Wings already controls Docker.
- Resource values are non-secret but still untrusted: parse ranges, ordering,
  ancestor constraints, floor budgets, and unsafe online decreases.
- An operator-owned persistent slice is never garbage-collected unless it is
  explicitly delegated to Wings.
- Server deletion and transfer use current-state reconciliation and cannot stop
  another server's or manager's slice.

## Acceptance oracles

### Stock compatibility

1. With the cgroup block absent/disabled, patched and vanilla Wings issue
   equivalent Docker create resources for server, installer, transfer, and
   restore fixtures.
2. Disabled patched Wings neither connects to systemd nor creates cgroup
   goroutines/units and runs without a mounted system bus.
3. Release-manager presence/absence has no effect on cgroup behavior, and
   cgroup enablement has no effect on provider selection.

### Placement and ownership

4. Every relevant Wings container receives the correct node/profile parent;
   rejected selectors fall to the documented stock/default path or block under
   required policy.
5. A placement change on a running container reports pending recreation and
   does not move PIDs; stop/start applies it.
6. Wings cannot create/adopt/modify a unit outside its configured subtree.
7. Cleanup never removes a unit with a live Docker scope, current server
   intent, in-flight start, or administrator ownership.

### Properties and budget

8. Desired properties survive daemon-reload and match systemd plus cgroupfs
   readback on a real cgroup-v2 host.
9. Removing a property from a profile clears the old live value.
10. Concurrent sync/start admissions cannot exceed a reserved/admission floor
    budget; shared mode reports proportional semantics honestly.
11. Missing `memory_recursiveprot` makes protected-profile health fail/degrade
    rather than report a working guarantee.
12. Existing container-scope IO weight and slice weight compose as expected;
    BFQ requested/effective values are both visible.

### Phase and online change

13. Startup policy is applied before Docker create only on a real start, never
    a generic offline environment-create pass.
14. Ready applies the steady policy exactly once; stop/restart cancels stale
    timers/ramps by policy revision.
15. Resource timeout can apply steady limits but never emits readiness or
    starts a dependent server.
16. A lowered `memory.high` follows the bounded ramp and reaches the target;
    `MemoryCurrent` read failures do not silently collapse to an unsafe
    one-shot transition.
17. A live server sync applies safe property changes without container restart;
    unsafe `memory.max` decreases are rejected with no partial policy commit.
18. A sync racing readiness, stop, and a newer sync cannot let an older
    revision win.

### Failure and scale

19. Named D-Bus errors drive behavior; localized message text is irrelevant.
20. Hundreds of server policies reconcile without one serial D-Bus round trip
    per sibling per create.
21. Required-mode property failure prevents only affected managed starts;
    best-effort mode starts with conspicuous degraded status.
22. Wings/helper restart reconstructs desired/effective state from Panel/node,
    systemd, and Docker without losing ownership or budgets.

### Gate

Run the real suite in `tester-unified` with a full run-uid identity. Add a
dedicated privileged systemd/Docker test container or VM for cgroup v2,
`memory_recursiveprot`, transient units, daemon-reload, Docker scope placement,
BFQ where available, concurrent admission, and fault injection. The
devcontainer remains the cockpit, not the ship gate.

## Migration from the current production stack

1. Keep the current branch/image as the production baseline while the clean v2
   branch is built from vanilla.
2. Capture desired and effective values for both Soulmask slices, including
   startup/steady bands, current parent budget, BFQ readback, grace, matcher,
   and ramp behavior.
3. Define a node `soulmask-latency` resource profile representing those values;
   keep host-specific numbers outside upstream defaults.
4. Land/test placement first with operator-owned slices; this proves the small
   upstream patch independently.
5. Add the managed systemd adapter and compare desired/effective readback
   without changing production limits.
6. Exercise online CPU/IO/min/low/high changes on disposable servers, including
   lowering-high ramp and rollback to the prior profile revision.
7. Move steady matching to the generic resource-phase signal. Keep child
   startup on the old branch until the separate dependency feature exists.
8. Replace `WINGS_CG_RAMDISK_UNITS` with the selected shared-release provider;
   do not carry that unit trigger into the cgroup v2 branch.
9. Cut over one Soulmask server in a maintenance window, verify cgroupfs and
   workload behavior, then the cohort.

A local compatibility translator may accept current `WINGS_CG_*` variables
during migration, but upstream documentation and new eggs should select the
named profile.

## Direct answers

- **Synergy with the release manager?** Yes at host hierarchy, contention
  policy, readiness events, and logs. No implementation dependency; the
  manager owns its aggregate maintenance slices independently.
- **Clean independent cgroup proposal?** Yes. Placement and resource policy are
  broadly useful to every Wings node and should be reviewed without Steam,
  tmpfs, Soulmask, or mount-provider concepts.
- **How would it differ from the current patches?** Profiles instead of raw
  values as the main UX; three explicit axes; deterministic floor admission;
  reconciling rather than additive properties; online updates; generic phase
  state; a smaller PR ladder; dependency and ramdisk hooks removed.
- **Is the current cut good?** Good for local history and evidence. Upstream-
  ready for placement/config diagnostics after cleanup; too coupled and large
  from managed slices onward.
- **Can egg/server variables change a running container's cgroups?** Profile-
  selected slice properties can be reconciled online after Wings receives a
  server sync. Placement cannot; it is pending until Docker recreates the
  container. Dangerous ceiling reductions require guarded transitions.

## Recommended Soulmask application

Use a node-owned `soulmask-latency` profile selected by both MAIN and CLIENT.
Keep its actual values calibrated from the current measured deployment rather
than becoming Wings defaults. Preserve:

- one parent budget backed by the parent slice's effective protection;
- per-server startup and steady memory bands;
- verified registration/steady matcher;
- a bounded, observable `memory.high` ramp;
- explicit BFQ-effective IO intent on this host;
- online profile reconciliation with placement changes deferred; and
- a separate low-priority, aggregate-limited release-manager hierarchy.

This retains the production behavior while turning the upstream proposition
into a general resource-policy feature: patched Wings works normally when it is
off, ordinary servers can opt into named QoS profiles, host operators retain
authority over guarantees, and unrelated lifecycle products remain separate.
