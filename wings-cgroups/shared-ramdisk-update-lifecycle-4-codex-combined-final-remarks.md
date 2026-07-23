# Combined final review — vanilla Wings patches and shared-release manager

Date: 2026-07-23

Reviewed:

- `shared-ramdisk-update-lifecycle-cgroups-1-codex-fable.md`
- `shared-ramdisk-update-lifecycle-3-codex-fable.md`

## Review scope

This review treats the pinned vanilla trees as the only Wings implementation
bases:

- Pterodactyl Wings `v1.13.1` at
  `e771816d5e072b3f2a8b9383bfcaffaa8f569dfa`
- Pelican Wings at
  `70f3344cc588b31e1f48e10ddcb87d116b957e69`

The current host setup and the existing local cgroup/ramdisk patch stack are
evidence and prototypes only. They are not compatibility targets and should not
dictate the new APIs.

The product boundary is also correct:

1. general-purpose Wings improvements that can stand on their own upstream;
2. a general shared-release manager using those interfaces;
3. Soulmask configuration and rollout policy as one application.

Steam, Soulmask paths, MAIN/CLIENT roles, and this host's memory numbers must
not leak into the general Wings contracts or the manager's core model.

The critical lifecycle findings below were checked directly against both
pinned vanilla trees, especially:

- `server/power.go`: `HandlePowerAction()` and `onBeforeStart()`;
- `server/update.go`: `SyncWithEnvironment()`;
- `server/mounts.go`: the stock/default/custom mount composition;
- `environment/docker/power.go`: remove/recreate on the next start;
- `server/server.go`: Offline state and crash detection ordering.

## Verdict

**Conditional go, not an unconditional go.**

Development may start now on the contract harness and on the independent,
well-bounded Wings patches listed in §Safe work to start now. The latest plans
are not yet a safe implementation contract for lifecycle L1/L1b, resource
budgets/online reconciliation, or the manager. Six Wings-level issues below
must be resolved in the plan and tests before those affected patches begin.

This is not a request for another architectural reset. The principal
architecture is sound:

- immutable releases rather than an in-place shared install;
- publication backends, with tmpfs as a policy-selected backend rather than
  the universal assumption;
- a node-configured, server-selected provider;
- read-only multi-consumer mounts;
- durable selection, leases, rollback, and reconciliation;
- Wings-owned start lifecycle and user-visible power controls;
- independent placement, property, and phase axes for resource control;
- a general manager with a Soulmask profile rather than a Soulmask manager.

The remaining work is to close lifecycle seams and make several claimed
invariants mechanically true.

## Required changes before the affected Wings patches

### 1. L1 mount and label injection occurs after vanilla has snapshotted them

**Severity: blocker for L1/L1b.**

The lifecycle plan places `prepareContentProvider()` after the existing
`SyncWithEnvironment()` call and then proposes appending the provider result in
`Server.Mounts()` and merging labels into the environment configuration
(`shared-ramdisk-update-lifecycle-3-codex-fable.md`, L1 seam and mount
injection).

In both pinned vanilla trees:

1. `HandlePowerAction` holds the server power lock.
2. `onBeforeStart()` runs `Sync()`.
3. `onBeforeStart()` then calls `SyncWithEnvironment()`.
4. `SyncWithEnvironment()` evaluates `s.Mounts()` and copies mounts and labels
   into `environment.Settings`.
5. Only later does `Environment.Start()` remove and recreate the Docker
   container from those settings.

A provider plan created after step 3 is therefore absent from the settings used
for Docker create unless Wings reapplies a composed settings snapshot.

Required design:

- Introduce a per-start-attempt object, keyed by an attempt ID, containing the
  provider selection, request ID, validated lease/generation/mounts, reserved
  labels, policy revision, and callback state.
- Compose the Docker settings from the latest vanilla server configuration plus
  the active start attempt. Do not mutate Panel-derived configuration maps or
  append provider state permanently to `Server.Mounts()`.
- Make every settings refresh that races with an active attempt preserve that
  attempt's overlay. A concurrent Panel sync must not erase prepared mounts
  between prepare and container create.
- Clear the attempt overlay on every pre-create failure, abort, stop, and
  superseding attempt. Persist only the committed identity needed for release
  and reconciliation.
- Validate collisions against the complete effective mount set: default
  mounts, passwd/machine-id mounts, custom mounts, and provider mounts. Checking
  only provider duplicates is insufficient.
- Construct the reserved `wings.content.*` labels in Wings. A provider should
  return typed identity fields, not an arbitrary label map.

The prepare call should be as late as practical after ordinary preflight so a
lease is not created before disk, configuration-file, or permission work that
can fail. Regardless of its final location, a deferred abort must cover every
failure after successful prepare, not only Docker create/start failures.

Add race and fault oracles for:

- live sync between prepare and Docker create;
- failure at every post-prepare preflight/create/start step;
- restart after a failed attempt;
- an unselected start after a previously selected failed attempt;
- provider/custom/default mount collisions.

### 2. The documented offline cutover cannot pass while stopped containers remain

**Severity: blocker for L1 lifecycle semantics and single-generation rollout.**

The plan correctly states that a stopped Docker container retains its old
mount source paths and generation labels. It also correctly makes such a
container pin its generation. The rollout then requires no labeled stopped or
running containers before tearing down generation G.

Vanilla Wings stop waits for Offline and returns. It does not remove the
container. Vanilla removes the old definition only inside Docker
`OnBeforeStart()` on the next start. Consequently:

```text
stop all consumers
  -> all old stopped definitions still carry G labels
  -> G remains pinned
  -> H cannot replace G in single-generation mode
  -> next start cannot recreate against H because activation never completed
```

The design must choose and specify one safe ownership model. The recommended
general Wings behavior is:

- for a provider-managed server, after an explicit Wings stop has reached
  Offline, remove the stopped container definition under the existing power
  operation;
- do so only after exit state/log handling no longer needs the definition;
- release the committed provider lease after container removal, with
  idempotent retry/reconciliation;
- do not blindly apply the same removal point to a crash, where crash
  inspection and automatic restart still need ordering guarantees.

If maintainers do not accept automatic removal, add a narrowly scoped Wings
operation that disposes an Offline server's container definition and make the
rollout use it. Direct manager access to remove Docker containers is not the
preferred boundary: it bypasses Wings' power lock and makes two components
owners of the same container lifecycle.

The alternative—allowing G to be removed while an old stopped definition still
references it—must be an explicit policy accepting that direct `docker start`
is unsupported. It must not coexist with the current claim that every labeled
container pins G.

Add an end-to-end oracle that stops two consumers, disposes both definitions,
releases G, activates H, and starts them again. Also prove that crash restart
still observes exit state and reconstructs the container safely.

### 3. Provider callback and recovery semantics are not yet a protocol

**Severity: blocker for L1.**

Only prepare has a complete example schema. Exact request/response schemas and
state transitions are still needed for commit, abort, release, and reconcile.
The protocol must specify:

- maximum body sizes, content type, unknown-field handling, deadlines, and
  status-to-error mapping;
- request-ID idempotency: repeating the same prepare after a lost response
  returns the same outcome and lease, while reuse with different input is a
  conflict;
- durable retention/expiry of prepare IDs and lease tombstones;
- duplicate and reordered commit/abort/release behavior;
- the response and retry rules for every callback;
- the exact reconcile authority rules when Wings state, Docker labels, and
  manager lease files disagree;
- crash recovery at every boundary from prepare through Docker start and
  commit.

In particular, commit failure after Docker has successfully started must not
casually turn a healthy game start into an unsafe rollback. The granted lease
already pins the generation. A reasonable contract is to keep the container
running, mark the commit pending/degraded, retry it, and let boot/runtime
reconciliation converge from the Wings attempt state and reserved Docker
labels. Whatever policy is selected must be normative and tested.

`allow: true` is redundant in a successful prepare response. Either remove it
or define a successful `allow: false` response and explain why that is
different from a typed non-2xx denial.

`openat2(...RESOLVE_BENEATH...)` validates a resolved object at one moment, but
Docker later consumes a pathname. It does not make a mutable source namespace
race-free. State the trust assumption: provider source roots and their parents
are node-admin-owned and immutable to server users; Wings validates containment
and mount policy but does not defend against a malicious privileged provider
that can replace paths afterward.

Peer credential policy must also be configurable enough for general Wings
deployments. Requiring peer UID 0 is suitable for the proposed rootful
deployment but excludes rootless/user-namespace layouts. Use a node-configured
allowed UID/GID (with secure rootful defaults), or declare those modes
unsupported for protocol v1. Filesystem permissions on the socket remain the
first boundary.

### 4. The two patch series are not independent after resource phases consume L2

**Severity: blocker for patchstack/build contracts, not for the first isolated
PRs.**

The plan says `lifecycle/<ref>` and `resources/<ref>` remain independent and
may be applied in either order. Resource PR 6 then consumes lifecycle L2
readiness. Once it imports an L2 type or event that is not in vanilla, the
resources branch cannot compile independently.

Replace the prose with an explicit dependency graph. Acceptable shapes include:

```text
vanilla
├─ lifecycle L1 -> L1b
├─ lifecycle L2 -> L3
└─ resources R1 -> R2 -> R3 -> R4 -> R5

lifecycle L2 + resources R5
└─ resource phases R6
   └─ online reconcile R7
```

or keep the resource phase core independent and put the L2 adapter in a small
combined-branch integration patch. The choice matters less than making each
branch buildable and reviewable at every commit.

Extend patchstack metadata with base/dependency information and pin the one
legal combined order. `SERIES` alone is not sufficient. CI should cover:

- vanilla;
- each standalone series/PR prefix;
- the combined DAG result;
- both pinned Wings targets.

The manager's Wings prerequisites should be stated separately. The provider
manager needs L1/L1b; the Soulmask cluster behavior benefits from L2/L3.
Resource placement/profiles and optional L4 are not protocol dependencies of
the manager.

### 5. The `reserved` budget ledger cannot be reconstructed from systemd units

**Severity: blocker for resource PR 5.**

The plan defines `reserved` as accounting every enrolled server, including
Offline servers, but proposes rebuilding the ledger from
`ListUnitsByPatterns(["wings-*.slice"])` and updating it on
ensure/stop/delete. Offline enrolled servers may have no transient slice, so
that reconstruction silently undercounts the very mode that promises
order-independent reservation.

Use two distinct ledgers:

- **reserved desired ledger** — rebuilt from all server configurations and
  resolved profiles, independent of live systemd units; stop does not release
  it, while unenrolment, profile change, server removal, or node-policy change
  does;
- **admission active ledger** — starting plus running attempts; it is
  transactionally admitted before create and released on stop/abort;
- `distribute` needs accounting and diagnostics, not an admission ledger.

Systemd enumeration is an effective-state/ownership reconciliation source, not
the authority for desired reservations.

“Reject the enrolment/sync transaction” also needs a real Wings meaning.
Vanilla has already fetched and applied Panel configuration when its sync path
runs. Define one of:

- validate resource policy before replacing the last accepted resource
  revision; or
- accept the rest of the server sync while retaining the last accepted
  resource profile and report a precise rejected resource revision.

Do not let an invalid resource selector roll back unrelated server settings,
and do not overwrite the last known-good resource policy before validation.

### 6. Online reconcile cannot reacquire the power lock from pre-start sync

**Severity: blocker for resource PR 7 and any shared start-attempt state.**

The plan says every successful sync triggers resource reconciliation and that
reconciliation serializes with the power lock. Vanilla pre-start `Sync()` is
already called while `HandlePowerAction()` holds that lock. A sync hook that
reacquires it will deadlock.

Specify a normative lock model before implementation. One workable model is:

```text
power lock (when the caller already owns a power operation)
  -> resource/provider-attempt mutex
     -> node admission/systemd-driver lock
```

Never acquire those in reverse order. A Panel-pushed sync outside a power
operation computes/validates a desired revision and applies it under the
resource mutex. Pre-start passes its existing power-operation context and does
not reacquire the power lock. Revisions cancel stale ramps and prevent an older
async reconcile from winning.

The race gate should include sync versus start, stop, restart, readiness,
ramp, admission, and provider prepare/commit.

## Required manager clarifications before Workstream B

These do not block the safe initial Wings work. They do block implementation of
the corresponding manager packages.

### 7. Model class cgroups explicitly and back their protection at the parent

The plan says there is one holder slice per generation while also assigning
different memory/zswap policy to each class. One cgroup cannot apply different
policies to pak and code pages populated by tasks in that same cgroup.

Use a generation aggregate with one child holder cgroup per class, for example:

```text
game-releases-gen-<hash>.slice
├─ game-releases-gen-<hash>-pak.slice
│  └─ populate-pak.service
└─ game-releases-gen-<hash>-code.slice
   └─ populate-code.service
```

Verify that the transient class slice remains active and owns its charged pages
after its populate worker exits. If the chosen systemd lifecycle does not
guarantee that, retain a minimal holder service. Make this a privileged e2e
oracle rather than an assumption.

Child `MemoryMin` is only a real system-level guarantee when the ancestor
hierarchy backs it. `game-releases.slice` therefore needs a protection budget
at least as large as the active class floors, reconciled with the protection
budget of `wings.slice`. Its current hierarchy lists aggregate ceilings but no
parent protection.

The pak zswap policy follows the page's current memory charge, not the file or
mount forever. The plan already notes that refaulted pages may migrate into a
game cgroup. Once that happens, the game's zswap policy applies. Describe
per-class zswap as the populate/holder-charge policy, not a permanent guarantee
for every pak page.

### 8. Make the publication mount topology and crash recovery exact

“Populate a hidden directory inside the class tmpfs, then rename it to the
final path” does not define where the tmpfs itself is mounted or how the final
path in the provider response appears atomically. It risks relying on renaming
a mountpoint or on exposing a directory before verification.

Define an exact topology such as:

1. create a private operation path;
2. mount the class tmpfs there;
3. populate and verify a root directory inside that filesystem;
4. create a read-only bind exposure at the final generation/class path;
5. only then commit published state and allow prepare responses.

Pin bind propagation, read-only remount behavior, executable policy by class,
and teardown order. Recovery must inspect `/proc/self/mountinfo`, durable
operation state, and sentinels; a `COMPLETE` file alone cannot prove mount
topology.

### 9. Replace `Before=docker.service` with ordering against the actual Wings service

The daemon design uses Docker for Steam workers and label reconciliation, while
boot publication is described as `Before=docker.service`. Those requirements
are contradictory in a single unit and too deployment-specific for a general
manager.

The hard requirement is:

```text
release store available
  -> assigned generations republished
  -> provider socket ready
  -> Wings starts/restores consumers
```

Order the manager/restore unit before the actual Wings service (host binary,
Compose wrapper, or container unit), not before Docker generically. It is fine
for Docker to be running before publication as long as Wings cannot restore
consumers early. If a separate no-Docker restore unit is desired, split it from
the daemon and document the handoff. Also define degraded startup when the
Docker socket appears late.

### 10. Reconcile “one state writer” with transient workers

Stage and publish operations run in systemd transient services, so more than one
process participates even if one binary is used. Preserve the useful
single-writer invariant by specifying that workers:

- write only to an operation-private transaction directory;
- produce an fsync'd result record;
- never mutate group, channel, lease, or journal authority directly;
- return the result to the daemon, which validates and commits it.

Define cancellation, worker timeout, daemon death while a worker continues,
and orphan adoption/quarantine on daemon restart.

### 11. State the real privilege boundary

The daemon needs mount operations, systemd D-Bus, and Docker label
reconciliation; a containerized Steam driver also needs a controlled launcher.
That daemon is effectively a privileged node service. Calling the publisher
“narrowly privileged” while combining all of those capabilities in one process
understates the trust boundary.

Document the daemon user/capabilities, Docker socket access, filesystem owners,
systemd policy, and child-worker credentials. The downloader can and should be
unprivileged and network-facing; the control/publish daemon remains trusted.
If root is the v1 choice, say so directly and minimize parsing/network work in
that process.

### 12. Resolve the manager-v1 automation contradiction

Manager v1 includes manual `stage`, and explicitly excludes rollout
orchestration. The defaults nevertheless promise auto-detect plus auto-stage.
No scheduler/poller component, cadence, backoff, or credential/error policy is
defined.

Choose one:

- v1 is manual/background `stage`, `activate`, and `rollback`; automatic
  detection/staging is a later scheduler package; or
- automatic detection/staging is in v1 and gets configuration, state,
  journaling, failure policy, and acceptance tests.

Keep scheduled/manual rollout separate from automatic acquisition.

## General Wings and egg integration details to close

### Provider selection and adding a server

The node/server split is good for upstream:

- the node administrator registers a provider ID, socket, source/target roots,
  and allowed selectors/eggs;
- an egg or server selects only that registered ID and opaque bounded
  profile/group/channel values;
- Wings never accepts paths, unit names, shell, credentials, or mount flags
  from server variables.

The actual enrollment workflow still needs a normative happy path:

1. install/register a provider once on the node;
2. authorize an egg and allowed profile/group namespace in node config;
3. create or authorize the group in the manager;
4. set admin-only server variables for provider/profile/group/channel;
5. add the server as a group member, idempotently;
6. start from the Panel and receive an actionable error if membership or a
   published generation is missing.

Define whether group membership is manager configuration, auto-enrollment, or
both; who may create a group; how a server is removed/moved; and what happens
to a running lease if an administrator changes its group. Regex validation
alone does not authorize an arbitrary group name.

### Managed roots versus Wings-managed configuration files

Wings updates egg configuration files in the server volume before container
creation. A provider mount covering one of those paths hides the newly written
volume file inside the container. This is a general collision, not just the
open Soulmask `WS/Config` question.

Provider validation must reject a managed target that equals or contains a
Wings-managed configuration-file target unless an explicit ownership mode says
the provider owns that configuration. The Soulmask managed/mutable audit must
settle `WS/Config` before migration, but it does not block generic L1 if the
collision rule exists.

The managed egg install guard must compare the selector with the exact expected
provider (`shared-release`) and profile. “Non-empty” must not skip installation
for a typo or an unrelated future provider.

### SFTP, backup, and disk-usage claims

Provider mounts exist only in the game container. Wings SFTP, backup, and disk
accounting see the underlying server volume:

- provider content itself is absent from those interfaces;
- old in-volume content on a migrated server remains visible, counted, and
  backed up until it is archived outside the volume or removed;
- a fresh managed server whose installer skipped content will not have those
  old files.

Update the acceptance oracle accordingly. The current unconditional statement
that disk usage excludes managed content is false for a migrated volume during
the soak period.

### L3 dependency semantics

L2 readiness is a strong generic primitive. L3 still needs these decisions
before implementation:

- maintain a reverse index so a prerequisite can find dependents that declare
  `WINGS_START_AFTER`;
- define removal, suspension, transfer, and cross-node behavior;
- decide whether one or multiple prerequisites are supported in v1;
- define queue persistence/cancellation across Wings restart, timeout, and a
  server deletion;
- make queued status and the blocking prerequisite observable;
- define `WINGS_AUTOSTART_DEPENDENTS` in terms of user intent. Restarting a
  prerequisite must not unexpectedly start a dependent that an operator
  deliberately stopped unless unconditional autostart is explicitly the
  configured node policy.

The group-generation equality check belongs in a provider/lifecycle
integration adapter. The generic dependency engine should not know release
manager vocabulary.

### Golden compatibility harness

The proposed golden gate is valuable, but raw byte comparison and a globally
ordered event stream can be flaky when maps, generated IDs, timestamps, or
goroutines are involved.

Define:

- fixed clock and ID/random sources;
- canonical Docker request serialization with irrelevant/default-field
  normalization;
- sorted map/set representations;
- an event partial order where concurrency is legitimate;
- an explicit allow-list for intentional new diagnostics/events when a feature
  is disabled.

The contract should remain “feature-disabled behavior is semantically identical
to pinned vanilla,” with a stable mechanical comparison.

### Resource capability and reset contracts

Before the later resource PRs, pin:

- exact systemd D-Bus types and reset values for every property removed from a
  profile;
- capability detection and `required` behavior for systemd/kernel versions
  lacking zswap properties or `memory_recursiveprot`;
- rootless and non-systemd behavior;
- block-device discovery based on the filesystems actually backing game and
  release paths, not every entry under `/sys/block`;
- selector and numeric bounds;
- the rule that a profile promising a hard floor cannot merely “degrade” when
  recursive protection is absent—`required` must reject the affected start.

## Safe work to start now

The following sequence allows development to start without prematurely
freezing the unresolved contracts:

1. **Gate and repository preparation**
   - extend patchstack metadata with series bases and dependencies;
   - create the canonical vanilla compatibility fixture/harness;
   - make the dual-target and combined-DAG CI matrix executable in
     `tester-unified`.
2. **Resource PR 1: configuration diagnostics**
   - independent of lifecycle, systemd, and manager decisions.
3. **Resource PR 2: node-level cgroup placement**
   - default-off `docker.cgroup_parent`, both Docker create paths, validation,
     and golden/integration tests.
4. **Lifecycle L2: generic readiness event**
   - safe once the attempt-ID/event interface and reset semantics are captured
     in tests; no provider or Soulmask vocabulary.
5. **Contract closure for L1/L1b**
   - implement only after issues 1–3 above are written into protocol and start
     transaction tests.
6. **Resource profile/phase work**
   - proceed after issues 4–6 and the reset/capability contracts are closed.
7. **Lifecycle L3 and optional L4**
   - proceed after dependency/user-intent semantics are fixed.
8. **Shared-release manager**
   - start implementation after provider protocol conformance fixtures are
     frozen and manager issues 7–12 are resolved. Its source/profile interfaces
     should be generic from the first commit; Soulmask remains a fixture and
     shipped profile.

If the team wants one immediate implementation branch rather than beginning
with the harness, Resource PR 1 is the lowest-risk starting point. Resource PR
2 is also sufficiently specified against vanilla. L1 should not be the first
code patch until its start-attempt transaction is corrected.

## Minimum plan amendments for a full go

The plan becomes an unconditional implementation go when it contains:

- a per-attempt settings composition and cleanup model;
- a stopped-container disposal/cutover rule;
- complete idempotent provider callback and recovery schemas;
- an explicit cross-series dependency DAG and CI matrix;
- separate reserved-desired and active-admission ledgers;
- a non-deadlocking lock order for sync, power, phase, and admission;
- class-level generation cgroups with backed parent protection;
- exact publication mount topology and boot ordering;
- a single-writer worker contract and honest daemon privilege model;
- a resolved v1 acquisition-automation scope;
- managed-config collision and migration visibility rules;
- added acceptance oracles for every item above.

With those amendments, the proposal is strong enough to implement and broad
enough to present upstream: vanilla-compatible defaults, node-owned authority,
bounded server selection, generic lifecycle primitives, and no Soulmask or
tmpfs policy embedded in Wings itself.
