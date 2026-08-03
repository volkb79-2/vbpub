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

| id | package | why now |
|---|---|---|
| **P02** | privileged systemd-in-Docker harness + the hold-unit probe | Unblocks every later oracle; answers the spec's open fallback branch with a measurement instead of a guess. |

**P02 contract.** Make `gate/Dockerfile`'s `e2e` target actually boot systemd
as PID 1 under `--privileged --cgroupns=host`; add an `e2e` build tag and the
helpers to start and inspect transient units; then run the decisive probe:
start a `Type=oneshot`, `RemainAfterExit=yes` transient unit whose
`ExecStart` faults a known number of tmpfs pages and exits, and read back
`memory.current`, the unit's active state, and its cgroup properties.

*Observable*: the unit is `active`, its cgroup exists, `memory.current` ≈ the
faulted size, and the declared `MemoryMin`/`MemoryZSwapMax` read back.
*Negative*: the cgroup is reaped when `ExecStart` exits, or the charge lands
on the daemon's cgroup instead. Either negative is a **product decision**
(`D-<NNN>`: fall back to an explicit minimal hold process), not a BLOCKED —
the spec already allows both shapes.

Also in P02, because they cost little once the harness exists and every
later package leans on them: a helper that reads a cgroup's `memory.current`
and `cgroup.stat nr_dying_descendants`, and one that parses
`/proc/self/mountinfo`. **Closes D-004.**

Confirm **D-003** (srdm verifies rather than writes `srdm.slice`) before
Wave 1 — it decides whether srdm ever needs host root at install time.

### Wave 1 — publication

| id | package | depends on |
|---|---|---|
| **P03** | mount topology: op tmpfs per class, populate, verify, `chmod a-w`, RO bind, teardown, mountinfo recovery | P02 |
| **P04** | relocate population into per-class transient hold units; class memory policy; charging | P02, P03 |

**Why split.** P03 proves the *mount* half with population running inline in
the daemon; P04 moves that population into `srdm-hold-<g8>-<class>.service`
units so the charge and the policy can never separate. Charging to the
daemon's cgroup is wrong for production and perfectly fine for proving
topology and recovery, so P03 is a real, gateable intermediate rather than
scaffolding. The cost is honest: **P04 rewrites P03's populate call path**,
and if P02's probe forces the minimal-hold-process fallback, P04 changes
shape. That is the rework the split buys de-risking with.

P03 carries: class size = `ceil(manifest × 1.15)` rounded to 64 M as a hard
cap, with ENOSPC quarantining the generation; the exact ordered sequence
(mkdir 0700 → mount tmpfs `nodev,nosuid[,noexec]` → populate + verify EVERY
file against the manifest → `chmod -R a-w` → `mount --bind` → `remount,ro,bind`
→ fsync the published-state record); teardown in reverse; and recovery that
trusts only the intersection of `/proc/self/mountinfo`, durable operation
state and unit state. A published record without its mounts triggers
republish; mounts without a record are torn down as orphans.

P04 carries the per-class policy (`MemoryMin`, `MemoryZSwapMax`,
`MemoryZSwapWriteback`) and the `srdm-gen-<g8>.slice` aggregate.

*Gates*: master-plan oracle 12 (charging, properties read back, teardown
leaves `nr_dying_descendants` stable), topology recovery, ENOSPC quarantine.

### Wave 2 — exposure

| id | package | depends on |
|---|---|---|
| **P05** | consumer registry + teardown safety | P02 (may start in parallel with Wave 1) |
| **P06** | the `host-bind` exposure driver, `ro`/`rw`, and doctor's mount + Wings preconditions | P03, P04, P05 |

**P05** resolves holders itself, because `host-bind` has no disposal
callback: running containers by volume path via the Docker API, plus
`/proc/*/mountinfo`. It refuses `activate`, `rollback` and teardown while any
hold remains, **naming the holding container**. Its consumer-resolution half
depends only on P02's helpers, so it can run in a parallel worktree with
Wave 1 and merge before P06.

This is a correctness gate, not politeness: a game container that still has
the bind in its own `rprivate` namespace keeps the superblock — and every
page — alive across the host unmount, so the memory is never returned and
the hold cgroup does not drain. *Gate*: oracles 15 and 24, in **both**
directions — teardown after a clean stop drops `memory.current` to ~0;
teardown with a consumer running is **refused**, not attempted.

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
