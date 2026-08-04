# srdm roadmap

**Package order and state.** The product definition, architecture,
invariants, measured ground and acceptance oracles are in
[`PLAN.md`](PLAN.md), which is authoritative; this file is only *what gets
built in what order*. Each package becomes a handoff in `handoffs/` when it
is carved and leaves a LOG in `reports/` when it lands.

Waves 0–3 shipped the v1 MVP, whose gate was oracles **19–24** (PLAN.md
§Acceptance oracles) — all green. The wave order below starts where it does,
rather than with the next feature, because every one of those oracles needs
a privileged harness and the harness had to exist first.

Waves 4+ follow PLAN.md §Direction, which is where the program changed
course: srdm takes over node resource governance, `access: rw` moves to an
overlay, and acquisition gains the SteamCMD driver.

---

## Milestones

A wave is an ordering constraint; a **milestone is a claim about the product**
that is either true or not. Each one below names its exit condition, because
"P11 is done" is not a thing anybody outside this project can evaluate.

| | milestone | exit condition | packages |
|---|---|---|---|
| **M1** | **v1 MVP** — ✅ **reached 2026-08-04** | the loop is drivable and survives a reboot, gated end to end. *Proven by oracles, not in production* | P01–P08b |
| **M2** | **production adoption** | the Soulmask cluster actually runs on srdm on the case-study node, `soulmask_tmpfs` is retired, and a content update is performed through srdm rather than around it | P10, **P09b**, P09 |
| **M3** | **unattended acquisition** | an operator supplies an appid and nothing else; srdm acquires, verifies, records build identity and promotes a release with no hand-staging | P11 |
| **M4** | **srdm owns the node** | content and servers are one budget under one policy file with one reconciler; Wings carries **one** small patch (placement) and no resource logic | P12, P13 |

M5 — `provider` exposure, holding invariant 7 in full — is deliberately
beyond the horizon and conditional (PLAN.md §Direction 6). It becomes worth
scheduling only if M2 shows the waiver's operational bill actually hurting.

**M2 is the milestone that matters.** Everything in M1 is proven by oracles
in a gate container; nothing is proven by a game server that people play on.
Until M2, srdm is a well-tested program rather than a working system.

### P09b — the waiver's operational bill

**This is the gap the milestone review found, and it is on M2's critical
path.** `host-bind` waives invariant 7 knowingly, and PLAN.md says the bill
is "itemized rather than discovered" — but only two of its line items have
ever been addressed (the chown walk, via F1; propagation, via `rslave`). The
rest were named and never handled, and they land precisely at production
adoption:

- **Backups.** Wings archives the server volume, and under host-bind the
  class paths are *inside* it. Every backup of every server would include the
  whole shared tree — for the case-study node, ~2.4 GB duplicated per server
  per backup. The mitigation exists (`.pteroignore`), but nothing generates
  or verifies it.
- **Disk accounting.** Wings computes server disk usage by walking that same
  volume, so shared content counts against **every** server's quota. Two
  servers sharing one 2.4 GB generation are each billed 2.4 GB for the same
  pages srdm exists to stop duplicating.
- **SFTP and archive extraction** traverse it too — reads are harmless,
  writes through SFTP into a read-only class path give a confusing `EROFS`
  rather than a refusal that explains itself.

Scope: generate the ignore rules from the profile (srdm already knows every
declared class path — that is exactly what `expose.Plan` computes), a
`doctor` check that they are present and current, and documentation of the
accounting consequence with the quota guidance that follows from it.

Small, and genuinely blocking: migrating a cluster onto srdm and *then*
discovering every backup grew by 2.4 GB per server is the kind of thing that
gets a tool removed rather than fixed.

---

## The ordering decision, stated up front

The obvious next package is publication topology. It is the wrong one to
start with.

Every load-bearing claim in publication is a claim about the **kernel and
systemd**, not about Go: that a `Type=oneshot`, `RemainAfterExit=yes`
transient unit keeps its cgroup alive and its pages charged after
`ExecStart` exits; that unmounting an op tmpfs frees and uncharges those
pages; that it does **not** when a consumer still holds the bind in its own
`rprivate` namespace. None of these is assertable in the unit gate, and the
master plan says so explicitly about the first:

> **Privileged e2e oracle, not an assumption** (review): after the worker
> exits, the unit is active, `memory.current` of its cgroup ≈ class size,
> and the properties read back; **if any systemd version fails to keep an
> active-but-empty service's cgroup alive, the fallback is an explicit
> minimal hold process — the oracle decides, the spec allows both.**

So the design of the hold layer has an **open branch that only a measurement
closes**. Writing that layer first means guessing, then discovering. The
harness is small, unblocks every later package, and closes D-004 (a declared
`privileged-e2e` gate with no cases). It goes first.

---

## Waves

Packages inside a wave may run in parallel worktrees; waves are serial.

### Wave 0 — prove the ground

| id | package | state |
|---|---|---|
| **P02** | privileged systemd harness + the hold-unit probe | **done** |

**What it settled.** The probe went the way the spec allowed for but did not
expect. On systemd 257, a `Type=oneshot` `RemainAfterExit=yes` unit stays
`active` and accepts its `MemoryMin` — but systemd **reaps its cgroup** the
moment the last process exits, and the content reparents to `system.slice`
where the class floor does not apply. The plan's "populate and hold are the
same unit" shape does not survive contact with this systemd.

The fallback the plan named does work: with `Type=exec` and a worker that
parks after populating, the cgroup persists, `shmem` is exactly the written
size, and `memory.min` / `memory.zswap.max` reach the kernel. **D-011**
records both halves; both are pinned by oracles, so a future systemd that
keeps the cgroup fails the negative test and re-opens the decision rather
than being silently missed.

Two smaller corrections came out of it: the harness runs `--cgroupns=private`,
not `host` (**D-010**), and the teardown hazard is a surviving **mount**, not
an open file descriptor — an open fd makes the unmount fail `EBUSY` instead
of leaving a ghost, which is why holder resolution reads `/proc/*/mountinfo`
(**D-012**). Also **D-009**: systemd is driven through its CLI, not D-Bus,
to keep the module dependency-free.

Shipped: `internal/cgroupfs`, `internal/systemdx`, the `e2e` image target and
run path, and four privileged oracles. **Closes D-004.**

Confirm **D-003** (srdm verifies rather than writes `srdm.slice`) before
Wave 1 — it decides whether srdm ever needs host root at install time.

### Wave 1 — publication

| id | package | state |
|---|---|---|
| **P03** | mount topology: op tmpfs per class, populate, verify, seal, RO bind, teardown, mountinfo reconciliation | **done** |
| **P04** | relocate population into per-class transient hold units; class memory policy; charging | **done** |

**What P03 settled.** The topology is built and gated at both levels: unit
tests assert the exact mount sequence against an injected mounter, and six
privileged oracles assert what the kernel actually does — a published
exposure refuses writes with **EROFS** (not EACCES, which is what a
chmod-only seal would give and which would not hold against root), the op
tmpfs stays writable while only the bind is read-only, data classes carry
`noexec`, teardown leaves nothing mounted, a class too small to hold its
content is **refused** rather than half-published, and reconciliation
classifies correctly against the live mount table.

Two decisions came out of it. **D-014**: publication verifies content, not
modes — sealing changes the modes by design, so comparing them would fail on
the very hardening that makes publication safe. **D-013**, which refines
D-011 and matters for P04: `Type=exec` is not enough, because it marks a
unit active as soon as the process is *exec'd*, before it has populated
anything. The hold unit is `Type=notify` with `NotifyAccess=main`, so
"active" means "populated". That was found by an intermittently failing
oracle, and the fix was the unit shape rather than a wider tolerance.

**Why split.** P03 proved the *mount* half with population running inline in
the daemon; P04 moves that population into `srdm-hold-<g8>-<class>.service`
units so the charge and the policy can never separate. Charging to the
daemon's cgroup is wrong for production and perfectly fine for proving
topology and recovery, so P03 was a real, gateable intermediate rather than
scaffolding. The cost was real too, and is now concrete: **P04 rewrites
P03's `publishClass` populate step** into a `Type=notify` worker that
populates, verifies, signals ready, and parks (D-011, D-013).

P03 carries: class size = `ceil(manifest × 1.15)` rounded to 64 M as a hard
cap, with ENOSPC quarantining the generation; the exact ordered sequence
(mkdir 0700 → mount tmpfs `nodev,nosuid[,noexec]` → populate + verify EVERY
file against the manifest → `chmod -R a-w` → `mount --bind` → `remount,ro,bind`
→ fsync the published-state record); teardown in reverse; and recovery that
trusts only the intersection of `/proc/self/mountinfo`, durable operation
state and unit state. A published record without its mounts triggers
republish; mounts without a record are torn down as orphans.

**What P04 settled.** Population now runs in
`srdm-hold-<g8>-<class>.service`, whose worker is the srdm binary invoked on
itself: it populates, verifies and seals inside its own cgroup, signals
`READY=1`, and parks. The daemon never writes a class tree — it mounts, waits
for ready, binds. Charging is measured rather than argued: a 32 MiB pak class
shows `shmem=33558528` on its own hold unit, and `memory.min`,
`memory.zswap.max` and `memory.zswap.writeback` read back from the **cgroup**
rather than from `systemctl show`.

Three decisions came out of it, and the first corrects the master plan.
**D-015**: `srdm-gen-<g8>.slice` does not nest under `srdm.slice` — systemd
reads `-` as the slice hierarchy separator, so it nests under an auto-created
`srdm-gen.slice` carrying `memory.min=0`, and a cgroup v2 floor is capped by
every ancestor's. Every class floor beneath it would be arithmetically dead:
master-plan decision 9's failure, one level down, in the very structure meant
to carry the floors. The aggregate is `srdm-<g8>.slice`, and it gets the
**sum** of the class floors beneath it, since cgroup v2 prorates a parent's
protection among its children. **D-016**: that floor is set with
`systemctl set-property --runtime` *before* the first hold unit starts, and
teardown stops the slice explicitly — measured, a slice stays active and keeps
its cgroup after its last service exits. **D-017**: the worker's exit status
is the refusal channel, read back from `ExecMainStatus`, guarded on the unit
having actually failed by exiting.

*Gated*: master-plan oracle 12 in full — charging to the hold unit, the
policy read back from the kernel, no ancestor capping the floors at zero,
teardown leaving no mounts, no units and `nr_dying_descendants` stable across
three publish/teardown cycles — plus ENOSPC quarantine end to end through the
worker's exit status, and reconciliation seeing a stopped hold unit with every
mount intact. 24 canaries, none surviving.

Reconciliation gained its third source. It now trusts the intersection of the
mount table, the durable records **and systemd**: a class can be fully
mounted and not held, which no amount of looking at mounts would show, and
which means its pages are charged to a removed cgroup carrying no policy at
all. It is reported as `Unheld` and scheduled for republish; acting on that
is still P08's.

### Wave 2 — exposure

| id | package | state |
|---|---|---|
| **P05** | consumer resolution + teardown safety | **done** |
| **P06** | the `host-bind` exposure driver, `ro`/`rw`, and doctor's mount + Wings preconditions | **done** |

**What P05 settled.** Teardown now refuses while anything holds the content,
naming it. Resolution matches on the **superblock**, because a consumer's
bind is at a path srdm has never seen and cannot predict while the
major:minor is the same number on both sides of a bind; it reads every mount
namespace but srdm's own through `/proc/*/mountinfo`, and names the container
from the holding process's cgroup path plus the Docker socket.

There is **no stored registry** — see D-018. Nothing tells srdm who mounted
what in the host-bind shape, and a table srdm kept itself could only be a
second opinion about the kernel's. The registry is the resolution.

The check lives inside `Teardown` rather than in front of it, because every
step of an unguarded teardown *succeeds* and frees nothing: there is no
failure afterwards to notice, and the only instant the difference exists is
before the first unmount.

**D-019 came out of it, and changed publication.** Matching on the superblock
means that on a systemd host — where `/run` is shared — every service with
its own mount namespace receives srdm's mounts by propagation and reads as a
holder. The obvious filter (excuse anything tagged `master:<our group>`, it
is downstream) was written and then deleted: measured, such a copy sometimes
survives the host unmount with its content intact, and nothing in
`mountinfo` distinguishes the cases. Over-filtering is a silent leak;
under-filtering is a visible refusal. So srdm does not filter, and instead
publishes into a **private** operation root so the copies are never handed
out. That root is infrastructure, not a generation's mount, and
reconciliation must not tear it down as an orphan.

*Gated*: oracle 24 in both directions against a real second mount namespace —
refused while a consumer holds, naming it, with nothing attempted and the
charge unchanged; then after a clean stop it proceeds, leaves nothing
mounted, and `nr_dying_descendants` returns to baseline, which is oracle 15's
"the memory came back" stated so that it survives the cgroup being removed.
Plus the measurement behind the refusal: a consumer's bind survives srdm's
unmount with the content still readable.

**P06** adds the exposure interface and its first driver, plus the three
hard preconditions that must **refuse, not warn**:

1. `propagation: rslave` on the Wings container's `/var/lib/pterodactyl`,
   with a `shared` host peer group. Under Docker's default `rprivate` every
   mount is invisible to Wings and every unmount leaves a ghost — the
   2026-07-31 outage.
2. For `access: ro`, either F1 in the running Wings build or
   `system.check_permissions_on_boot: false`. Otherwise the pre-boot chown
   walk fails `EROFS` and the server cannot start.
3. Affected consumers stopped (P05).

Plus the `ro|rw` axis: `rw` permitted only with **exactly one** consumer,
refused for a second; a written-through generation marked dirty-capable and
barred from promotion, sharing and use-as-source until re-verified.

**Inherited from P05**, and not optional: srdm publishes into a **private**
operation root so its mounts are not delivered to every namespace on the host
(D-019). P06 mounts into `/var/lib/pterodactyl/volumes/**` instead, where the
requirement is the opposite — those mounts MUST propagate, or Wings never
sees them. Both facts have to hold at once, so P06 owns the propagation story
end to end, including the residue D-019 could not fix: a namespace created
*after* publication copies the whole mount table whatever its propagation, so
a service that restarts mid-generation holds it and teardown is refused
naming a process that has nothing to do with the game. Decide there whether
that wants a narrower answer or is simply true.

P06 is also where doctor gains the mount-propagation and Wings checks the
P01 handoff deferred. *Gate*: oracles 19–22, and oracle 20's requirement
that neither precondition failure is allowed to surface as a server start
error.

**What P06 settled.** `internal/expose` is the fork: an interface and, for
now, one driver. `internal/wings` reads the two facts about the node srdm
neither owns nor configures, and both fail closed (**D-021**) — the node
config is scanned for one key rather than parsed, because srdm still has no
YAML dependency, and F1 is asserted by configuration because a patch in a Go
binary is not detectable.

Every precondition refuses with a fix attached, and a test asserts that no
refusal ever ships without one — a refusal with no remedy is an outage with
extra steps.

**D-020**, found by the ephemerality oracle: an `rw` exposure cannot be a
bind of the published path, because that path is itself a read-only bind and
a bind inherits its source's flags. The two modes therefore bind different
mount points of the same superblock. The half left open is who may *write*
through it: publication seals the tree `a-w`, so root can and a game
container cannot, and deciding the ownership model belongs with `harvest`
(P07) — the only reason to write through in the first place.

*Gated*: oracle 19 against a real volume tree (content at exactly the
declared class paths, `world.db` unchanged across expose and unexpose,
per-instance state still writable underneath); oracle 20's host half against
the real mount table, its container half against an injected inspector;
oracle 21's marking, sharing refusal and doctor drift report; oracle 22 end
to end — write through `rw`, republish, and the write is gone. 31 privileged
oracles across the project now, and 44 canaries, none surviving.

### Wave 3 — acquisition and operations

| id | package | state |
|---|---|---|
| **P07** | `harvest` — adopt an in-place-updated generation as a release | **done** |
| **P08** | the operator surface: assignments, `activate`/`rollback`, retention/GC, the CLI verbs | **done** |
| **P08b** | boot restore, reconciliation acting, adoption and quarantine, doctor online | **done** |

**Why P08 is two packages.** It was carved as one, and the bullet list it
carried — retention, the daemon, the admin socket, boot restore, adoption and
quarantine, doctor online, and every CLI verb the master plan names — is not
one package. The seam is not size, though: it is that **the boot path replays
something that does not exist yet.**

`srdm-restore.service` republishes "assigned generations". Nothing in P01–P07
records an assignment. `expose` is called with a server id and remembers
nothing; a published record names a release and no consumers. So the boot
path has no input until somebody writes down what an operator asked for —
and that record is also exactly what `activate` re-points and what `gc` must
not collect. It is the first thing, not a detail of the last.

So **P08** is the operator surface: the durable statement of intent, the
operations that change it, and the verbs that drive them, all synchronous in
a one-shot root process. **P08b** is what makes that survive a reboot and a
crash: the boot unit, reconciliation acting on its own findings rather than
reporting them, adoption and quarantine of operations whose units outlived
the process that started them, and doctor's online half.

**P07** is today's manual procedure automated: refuse if any consumer is
running → re-walk and re-hash → classify (an unclassified new path blocks
promotion, exactly as on the staged path) → probes → transaction into the
store → journal with harvested provenance. The store already accepted this
shape; `ProvenanceHarvested` existed and was unused on purpose.

**What P07 settled.** Everything from classification down is `store.Promote`,
unchanged and shared with the staged path — which is the point, because a
harvested release has to be indistinguishable from a staged one and the
cheapest way to guarantee that is for it to be made by the same code. What
`internal/harvest` adds is the two things staging never has to think about:
establishing that nobody can still write, and ASSEMBLING one tree out of the
N tmpfs mounts publication spread a generation across.

Assembly is where the decisions are. Every path is re-classified before it is
copied, and the check is not promotion's: promotion asks "does any rule match
this path", assembly asks "does the rule that matches it still name the tmpfs
it came off". The first catches new content, the second catches a **profile
that moved under a live generation** — which is the only way, under
host-bind, that a harvest can meet a path it should refuse. An exposure binds
only the declared class paths, so every write that reaches a class tmpfs
lands inside that class and classifies.

**D-022 closes D-020's open half**, and it is a measurement rather than an
argument: `rw` now unseals each bound class tree and hands it to a declared
`wings.write_owner`, with `rw` refused when none is declared. The oracle
performs the write as an unprivileged uid through both modes, because root
writing through proves nothing — root could always write through, and that
was exactly the problem. **D-023** records why a harvested release carries no
per-instance state, and that this is the absolute state rule holding rather
than a gap.

Two mode bugs surfaced, both invisible until harvest made a published tree
comparable with the release it came from: `hold.Seal` and `fsx.CopyTree` both
chmod'd through `Perm()`, which is blind to setuid, setgid and sticky. Nothing
in publication compares modes again (D-014), so a setgid directory lost its
bit at publication and again at every stage, silently.

*Gated*: oracle 23 end to end — update in place through `rw` as the game's
uid → unexpose → harvest → the resulting manifest's content digest equals a
from-scratch stage of the same content, and republishing from the harvest
keeps the update where republishing from the source discards it (oracle 22
with the remedy attached); harvest refused against a real second mount
namespace and lifting when it stops; harvest refused once the generation is
torn down, against the real mount table. Plus 15 unit oracles and 16 new
canaries, none surviving.

**What P08 settled.** `internal/assign` is the record that did not exist:
one document per profile naming the active release, the release before it,
and the servers. **D-024** states why that is not the consumer registry
D-018 refused — they answer different questions, and the pair is the design.
Intent is recorded because nothing else knows it; reality is measured because
the kernel owns it; reconciliation is the comparison.

`internal/opctl` is the order, and the order is the content. Activate
publishes the new generation, moves every assigned server onto it one at a
time, records the assignment, and only then tears the old one down — the one
position with no bad crash in it. Written earlier, a crash leaves an
assignment naming a release nothing has mounted; written later, a crash after
the teardown leaves one naming a generation that no longer exists. Attach and
detach use the opposite order from each other for the same reason, and both
are pinned by oracles.

Every operation runs under an `flock`, and **D-025** proposes what that
implies: v1 has no daemon. Serialization was the only live purpose one would
serve — the provider socket and lease resolution are v2, boot restore is a
`oneshot` unit — and a lock buys it without a "refuses when the daemon is
down" mode that would refuse exactly the operations needed to fix a node
whose daemon has died. **Confirm before P08b.**

**D-002 closes**: retention 3, from configuration, and a floor on what is
kept rather than a cap on what exists. Four pins come first — assigned,
rollback target, published, channel target — and retention only chooses among
what is left. The master plan's "no live lease, no labeled container in any
state" is v2 in both terms; in v1 a container never references a release at
all, and `published` is what replaces them.

*Gated*: the swap end to end on a real node — activate, attach, activate a
second release, and the server's volume holds the new bytes with nothing left
of the old, its own `WS/Saved` untouched throughout; rollback putting the
actual bytes back; oracle 24 for activate against a real second mount
namespace, refused with the holder named and lifting after a clean stop;
detach removing one server's mounts and not the other's; teardown leaving a
profile assigned-but-unpublished, which is the state P08b is defined against.
Plus 27 unit oracles and 9 new canaries, none surviving.

**What P08b settled.** D-025 confirmed first, as required: v1 ships no
daemon, so the boot path is a `oneshot` unit and reconciliation-acting is a
CLI verb (`srdm restore`, `srdm reconcile`) rather than daemon work. That
decided the shape of everything else — no admin socket, no long-lived
process, `internal/opctl.Restore` and `.Reconcile` are ordinary lock-holding
operations exactly like `Activate`.

Restoring after a reboot exposed a bug reconciliation-acting then had to fix
rather than merely add to: `Activate`'s `publishOrAdopt` trusted a
`LoadRecord` hit unconditionally, which is exactly wrong the instant after a
reboot — every published record survives (`StateDir` is persistent) naming
mounts that do not (`RunDir` is not), and Reconcile's own doc comment says so
in as many words. `Publisher.IsComplete` (the same per-record completeness
check `Reconcile` already computed, factored out so one record can be asked
about in isolation) is what makes `publishOrAdopt` — and therefore `Restore`,
which is `Activate` run once per assignment with nobody watching — safe to
call unconditionally instead of only when something already looks wrong.

**D-026** came out of this, filed and confirmed the same day: nothing
durable ever kept a profile *document*, only its id, and `srdm-restore.service`
has an id and no `--profile` flag to hand it. `cfg.ProfilesDir()` keeps the
last profile document that successfully drove a state-changing operation,
written by the CLI at the same moment everything else durable is — a side
effect of the operation that made it true, not a step an operator can
forget to run before the one boot that needs it.

Adoption and quarantine turned out to need their own durable, but
volatile, record: `Publish` now writes an operation *plan* under `RunDir`
(wiped by a reboot together with everything it describes, which is what
makes it safe to trust unconditionally once found) before it mounts
anything, and removes it once the published record makes the plan's account
official. `AdoptOrQuarantine` is the v1, lock-instead-of-daemon reading of
the master plan's own worker-contract rule ("adopts operations whose units
are still active, quarantines operations whose units are gone without a
result record") applied to the DAEMON's own process dying rather than the
worker's — the worker outliving the process that started it is D-011/D-013's
normal shape, not a crash.

Reconciliation-acting splits by what it is safe to do without a profile
document: `NotReadOnly` is always a plain remount, safe against any holder
because nothing is unmounted; a `NeedsRepublish` generation nobody is
currently assigned to is cleared for good, the same as an orphan one level
up; a `NeedsRepublish` generation that IS somebody's current assignment is
left to `Activate`/`Restore`, which alone carry what rebuilding it needs.
Every clearing step refuses exactly as `Teardown` does when a consumer still
holds the content — reconciliation repairs drift, it does not evict a
running server to do it.

*Gated*: `TestRestoreRebuildsAGenerationThatDidNotSurviveTheReboot` for the
publishOrAdopt fix against the exact reboot-shaped state Reconcile's own
comment describes; `TestAdoptOrQuarantineAdoptsAFullyRealizedPlan` /
`...TearsDownAnIncompletePlan` for both halves of the worker-contract rule;
`TestReconcileClearsUnassignedGenerationsAndReportsAssignedOnes` for the
assigned/unassigned split; held-consumer refusal for both `ClearForRepublish`
and the boot path. 5 new canaries, none surviving (`P08b-adopt-ignores-kernel`,
`P08b-quarantine-discards-good-work`, `P08b-restore-trusts-a-stale-record`,
`P08b-reconcile-clears-what-is-assigned`, `P08b-clear-ignores-holders`).

**Inherited obligations**, each already built but unreachable until P08b
gave it a loop:

- ~~`activate` and `rollback` must call `Publisher.Holders` and refuse
  exactly as teardown does~~ — **done in P08**, and gated against a real
  second mount namespace.
- ~~Publication, hold, exposure and harvest have no operator entry point~~ —
  **done in P08**. `stage` remains a named verb pointing at `srdm store
  promote`, and `operation` at `srdm journal show --op`; `daemon` remains
  named and pointing at D-025, now confirmed rather than proposed.
- ~~Reconciliation reports and does not repair~~ — **done in P08b**:
  `internal/opctl.Reconcile` acts on `NeedsRepublish`, `NotReadOnly` and
  `Unheld`; `srdm-restore.service` (`systemd/srdm-restore.service`, a
  reference unit per D-003) is the boot path that runs it and then activates
  every assignment.
- **Generation GC's remaining term** (D-002 is otherwise closed) is
  UNCHANGED by P08b and stays latent rather than urgent: the master plan's
  "no labeled container in any state" is v2 (labels), and P08b's boot path
  does not change what GC considers a hold — it only restores what an
  assignment already declared. Still open for whichever package brings
  labels.
- **The lock is per node, not per profile** (D-025's stated cost) is
  UNCHANGED: `Reconcile` and `Restore` both take the single node-wide lock,
  same as every other verb. Two profiles still cannot be operated on
  concurrently; the fix, if it is ever wanted, is a per-profile lock file.
- **New, found while building this**: an operation plan that fails to parse
  (practically only disk damage) cannot recover the class names it would
  need to stop its own hold unit — `AdoptOrQuarantine` sweeps its mounts by
  path and leaves the unit orphaned, journaled as a residual. → backlog.

### Wave 4 — capability, then the real acceptance test

| id | package | state | milestone | depends on |
|---|---|---|---|---|
| **P14** | `srdm update` — the ordered cluster update, with a readiness gate | **next** | M2 | P08b |
| **P10** | `access: rw` via an overlay upper layer (D-027), and the overlay holder recognizer (D-028) | **next** | M2 | P08b |
| **P09b** | the waiver's operational bill: backup ignore rules, disk accounting, SFTP | queued | M2 | P06 |
| **P09** | Soulmask profile, managed egg, install guard, migration rehearsal | queued | M2 | P10, P09b, and F1 or `check_permissions_on_boot: false` |
| **P11** | build-identity recording, then the containerized SteamCMD driver | queued | M3 | P09 |

**P14 is M2's exit condition, not an addition to it.** M2 says "a content
update is performed through srdm rather than around it", and today it cannot
be: `activate` refuses while a consumer holds the generation (oracle 24, and
rightly), so the real procedure is *stop every server by hand in the Panel,
run srdm, start every server by hand*.

**It is an ordered cohort cycle, not a rolling update**, and that correction
came from the operator rather than from the design. A cluster has a **main**
and **slaves that connect to it**, so a slave running against a main on a
different content version is a broken cluster — "one at a time, at most one
down" is the wrong shape however attractive its downtime figure looks:

```
stop every SLAVE → stop MAIN → switch the release →
start MAIN → wait for MAIN readiness → start every SLAVE
```

Three existing things change. The **assignment gains roles** (`main`/`slave`,
exactly one main, schema v2; a v1 document is refused with a fix rather than
migrated, because guessing which server is main is guessing which one takes
the cluster down). srdm **gains a power surface** — it cannot stop or start a
server, and must not do it through Docker despite holding that socket, since
Wings owns the container lifecycle and a container dying underneath it is a
crash it will act on, racing the swap; so `internal/power` is an interface
with a Panel-API implementation, the shape `expose.Driver` already uses. And
srdm **gains a readiness gate**, which matters well beyond this package —
PLAN.md §Direction 2.

Two generations still coexist briefly, because publish precedes teardown so
a failed publish leaves something to restart on. The headroom constraint is
softer than a rolling design would need (the servers' own memory is freed
while they are stopped) but it is not gone, and it is refused up front rather
than discovered with a cohort half down.

**P10 goes before P09**, which reorders the old plan. P09 is the migration of
two live servers onto one generation, and `rw` as it stands is
single-consumer: unsealing the shared generation in place marks it
`dirty_capable` and bars sharing (D-020/D-022). Rehearsing a cluster
migration against a mode that cannot serve a cluster would rehearse the wrong
thing. The overlay removes that limit by construction rather than by
relaxing a check — sealed generation as `lowerdir`, per-server `upperdir`,
lower stays pristine and shared. Known-hard parts, none of which transfer by
assumption: whiteout handling when an update deletes files, `harvest` reading
a merged view, and teardown safety re-derived against a mount that pins lower
inodes (D-012/D-018/D-019 all apply).

**P11 moved behind P09**, which is a change from the earlier sketch. It is
M3 work, and M3 is worth nothing until M2 holds: an unattended acquisition
path that feeds a system nobody is running in production automates a
hypothetical. It is also the package most likely to change shape *because*
of what M2 teaches — the build identity worth recording is whatever the
migration turns out to need to tell two releases apart.

P11 is two things in order. Build identity has no representation at all
today — no profile can express "capture this version string" — and both the
staged path and Steam need it, so it is one piece of work rather than a
Steam rider. Then the driver itself: appid in, prepared tree out, as an
unprivileged uid inside the egg's own runner image handing back one fsync'd
typed result record (PLAN.md §Direction 5).

**P09** is where **D-001** (`WS/Config` shared or per-instance) is answered
by a runtime write audit rather than assumed, and where `soulmask_tmpfs` is
retired. The migration runbook is the gate; nothing about it is a unit test.

### Wave 5 — srdm takes over node resources

| id | package | depends on |
|---|---|---|
| **P12** | server slice governance host-side: pre-create and configure per-server slices from the assignment, apply policy, reconcile drift, `doctor` the node budget | P09 |
| **P13** | the Wings contract, now a single patch: `HostConfig.CgroupParent` placement | P12 |

PLAN.md §Direction 3 is the argument. The order matters: **P12 before P13**,
because properties are host-side and need no Wings change, so the whole
resource engine can be built and gated against slices srdm creates itself
before a single line of Wings is touched. P13 then adds only what srdm
provably cannot do — `HostConfig.CgroupParent` placement. **Readiness is no
longer part of that contract**: PLAN.md §Direction 2 retires it, because the
signal is already a log line the egg names and Wings itself gates on such a
line, so reading it is not inference. That replaces the `resources` R1–R8
series in `../wings-patchstack/` with **one** small patch — about as easy an
upstream ask as exists.

The first thing P12 should produce is the check nothing performs today: the
host's actual RAM against the **sum** of `srdm.slice`'s floors and the
servers' floors. Content and servers are one budget and no component
currently knows both numbers.

---

## Not srdm, but on srdm's path

**F1** — the Wings chown-skip patch, in `../../wings-patchstack/`. Parallel
to all of the above and owned by that stack, not this project. It is the
MVP dependency for `host-bind` + `access: ro`: without it a node must set
`system.check_permissions_on_boot: false`. P06 was built and gated against
the config workaround, so F1 never blocked srdm — it only decides how much a
node has to concede to adopt it. `gofmt`/`build`/`test` green 2026-08-04;
oracles 17–18 and the pelican export are still open.

**The Wings v2 contract** — reduced by PLAN.md §Direction 3 from the eight
`resources` patches to two: placement (`HostConfig.CgroupParent`) and a
readiness signal. Carved as **P13** above rather than left to the patch
stack, because P12 has to exist first for either to be worth anything.

**`provider` exposure** — still optional, still justified by invariant 7
(PLAN.md §Direction 6): it is what makes Wings' disk accounting, backups,
SFTP and archive extraction structurally unable to see managed content.
Not blocking, and not scheduled. Its protocol is specified in full in the
historical plan; open that document only if this is actually being built.

---

## Gate debt to retire along the way

- Raise `[gates.coverage]`'s floor as the suite matures. It is 75 today
  against a measured 78.0%; it is a floor, not a target (D-007).
- Add a canary per new oracle. A gate that has never been seen to fail for
  the right reason is not known to test anything.
- **D-008**: durability (as distinct from ordering) is unobservable in the
  unit gate. Once P02's harness exists, a `dm-flakey`-backed power-cut
  oracle becomes possible — the one place `COMPLETE`-means-durable can
  actually be proven.
