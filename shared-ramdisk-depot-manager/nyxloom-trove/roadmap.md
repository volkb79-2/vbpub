# srdm roadmap

Phases as the master plan orders them (§Kickoff plan, reordered by decision
10 so the vanilla-Wings product ships first). This file is the *plan*;
each package becomes a handoff in `handoffs/` when it is carved.

The MVP gate is master-plan oracles **19–24**. Phase 1 does not ship
without them, and every one of them needs a privileged harness — which is
why the wave order below starts where it does rather than with the next
feature.

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
| **P06** | the `host-bind` exposure driver, `ro`/`rw`, and doctor's mount + Wings preconditions | next (needs P03, P04, P05) |

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

### Wave 3 — acquisition and operations

| id | package | depends on |
|---|---|---|
| **P07** | `harvest` — adopt an in-place-updated generation as a release | P06 |
| **P08** | retention/GC, the daemon, the admin socket, boot restore, doctor online | P06 |

**P07** is today's manual procedure automated: refuse if any consumer is
running → re-walk and re-hash → classify (an unclassified new path blocks
promotion, exactly as on the staged path) → probes → transaction into the
store → journal with `harvested-from-<generation>` provenance. The store
already accepts this shape; `ProvenanceHarvested` exists and is unused on
purpose. *Gate*: oracle 23 — the harvested manifest matches a from-scratch
stage of the same build identity, byte for byte.

**P08** closes the operational loop: `srdm-restore.service`
(`After=local-fs.target`, no Docker dependency) republishing assigned
generations at boot; retention (**resolves D-002**, default 3, leased always
kept); `internal/adminapi` on `/run/srdm/admin.sock` (0600, root) and the
`daemon` subcommand; the worker-contract rules for adoption and quarantine
of operations whose units outlived the daemon. *Gate*: reboot republish
before consumer starts; orphan adoption and quarantine.

**Inherited obligations**, each already built but unreachable until P08 gives
it a verb or a loop:

- `activate` and `rollback` must call `Publisher.Holders` and refuse exactly
  as teardown does (P05). They swap what a consumer is reading underneath it,
  which is the same hazard by a different route.
- Reconciliation reports and does not repair. `NeedsRepublish`, `NotReadOnly`
  and `Unheld` are surfaced by P03/P04 and acted on by nobody; the boot path
  is where acting on them belongs.
- Generation GC needs the stricter rule the master plan states — removable
  when not assigned, no live lease, **no labeled container in any state**.
  P05 deliberately implements only the narrower running-container question,
  because that is what teardown safety is about; a stopped definition holds
  no pages but still pins what it will need on its next start.
- Publication and hold have no operator entry point at all. They are
  libraries; `cmd/srdm` says so by name rather than pretending otherwise.

### Wave 4 — the real acceptance test

| id | package | depends on |
|---|---|---|
| **P09** | Soulmask profile, managed egg, install guard, migration rehearsal | P07, P08, and F1 or `check_permissions_on_boot: false` |

Master-plan Phase 3. This is where **D-001** (`WS/Config` shared or
per-instance) is answered by the runtime write audit rather than assumed,
and where `soulmask_tmpfs` is retired. The migration runbook is the gate;
nothing about it is a unit test.

---

## Not srdm, but on srdm's path

**F1** — the Wings chown-skip patch, in `../../wings-patchstack/`. Parallel
to all of the above and owned by that stack, not this project. It is the
MVP dependency for `host-bind` + `access: ro`: without it a node must set
`system.check_permissions_on_boot: false`. P06 can be built and gated
against the config workaround, so F1 never blocks srdm — it only decides
how much a node has to concede to adopt it.

**v2** (master-plan Phases 4–7) — provider protocol freeze, L1/L1b, the
`provider` exposure driver, cutover. Everything Waves 0–4 build is reused
unchanged; the cutover is a config flip, not a migration.

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
