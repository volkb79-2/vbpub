# srdm-P04 — LOG

Package: per-class hold units, class memory policy, charging
Roadmap: `../roadmap.md`, Wave 1
Date: 2026-08-03

---

## What was built

Population left the daemon. It now runs in
`srdm-hold-<g8>-<class>.service`, whose worker is the srdm binary invoked on
itself (`srdm hold-worker`): it populates the class, verifies every file
against the manifest, seals the tree read-only, signals `READY=1`, and parks.

That relocation is the entire package. The pages a class costs are faulted
inside the cgroup that carries that class's memory policy, so the charge and
the policy cannot separate — which is the claim the whole design rests on and
which was, until now, simply not true: P03 populated inline and charged
everything to the daemon.

The daemon's remaining steps are all mount operations. **It never writes a
class tree.** It sets the generation aggregate's floor, mounts the op tmpfs,
starts the hold unit, waits for ready — and because the unit is `Type=notify`,
"ready" means populated, verified and sealed rather than "exec'd" (D-013) —
then binds and remounts read-only, then writes the record.

New surface: `internal/hold` (unit and slice naming, the policy renderer, the
Manager, the worker, and `sd_notify` in fifteen lines because READY=1 is one
datagram and that is what keeps srdm dependency-free);
`systemdx.SetProperty` / `Revert` / `MainExitStatus`;
`profile.Class.ZSwapWriteback`; record schema 2, which names each class's hold
unit and the slice they share, because teardown and reconciliation act on
those and the record is the only durable thing that knows them.

## The measurement that corrected the plan

The master plan draws the aggregate as `srdm-gen-<g8>.slice`. systemd reads
`-` in a **slice** name as the hierarchy separator, so that does not sit under
`srdm.slice`:

```text
ControlGroup=/srdm.slice/srdm-gen.slice/srdm-gen-b5b5b5b5.slice/srdm-hold-...
memory.min:        0     ->      0      ->     367001600      ->   157286400
```

`srdm-gen.slice` is auto-created, unowned and unconfigured, and a cgroup v2
floor is capped by every ancestor's. Every class floor beneath it is
arithmetically dead — master-plan decision 9's failure, one level down, inside
the structure whose only job is to carry those floors.

`srdm-<g8>.slice` interposes nothing:

```text
/srdm.slice/srdm-a1b2c3d4.slice/srdm-hold-a1b2c3d4-pak.service
memory.min: 536870912 -> 367001600 -> 157286400
```

Only slices nest, so `srdm-hold-<g8>-<class>.service` keeps the plan's shape
exactly. **D-015**, with the sum-not-maximum rule for the aggregate's floor,
since cgroup v2 prorates a parent's protection among its children.

This was found by probing systemd before writing the layer rather than after —
the same order P02 established, and the reason it cost one container instead
of a rewrite.

## Gated at both levels

Unit tests assert the shape with no systemd present: the exact ordered
publication sequence as one interleaved log of mounts, unmounts, holds and
slice operations (one log, not several, because the order *between* those
surfaces is the contract); the policy each class's spec carries; the argv the
daemon builds against the flags the worker parses, as one round trip.

Six new privileged oracles assert what the kernel and systemd actually do:

- a 32 MiB pak class shows `shmem=33558528` on **its own** hold unit, and that
  unit's `SubState` is `running` — a hold unit that exited has had its cgroup
  reaped and its charge reparented;
- `memory.min`, `memory.zswap.max` and `memory.zswap.writeback` read back from
  the **cgroup**, not from `systemctl show`: a property systemd accepted and
  the kernel dropped reads exactly like one that applied;
- no ancestor between the hold unit and the configured parent has a
  `memory.min` below the floors beneath it, and there are exactly two of them;
- teardown leaves no mounts, no active units, no generation cgroup, and
  `nr_dying_descendants` stable across three publish/teardown cycles using the
  same unit names — if teardown did not really clear them, systemd refuses the
  next start by name;
- a class too small to hold its content is refused end to end, through the
  worker's exit status, with no unit left in a failed state;
- reconciliation sees a stopped hold unit **with every mount intact**.

## Two bugs the tests caught

1. **`Spec.validate` accepted a `.slice` as a hold unit.** `systemdx`'s name
   check is about characters and length, so it takes either kind; a slice has
   no ExecStart, so a "hold unit" that is one holds nothing and never becomes
   ready. It also spun the readiness poll for the full wait budget, which is
   how it announced itself. Both kinds are now checked in both directions — a
   floor set on a service protects nothing below it, and that mistake is
   silent.

2. **`systemd-run` blocks on the start job.** For a `Type=notify` unit that job
   does not complete until the worker signals ready, so a failing worker
   reports through the *start call*, not the readiness wait after it. The first
   version read `ExecMainStatus` only after the wait, so a class too small to
   hold its content surfaced as a bare `exit status 1` — a fault, and the
   generation went unquarantined. Caught by the ENOSPC oracle on its first run.

And one hollow test of my own, caught by its own canary: the case asserting
that a refused start does not report a stale exit status scripted **no** status
for the mutation to wrongly believe, so removing the guard changed nothing.
It now scripts `ExecMainStatus=0` explicitly, because that is a running unit's
value and therefore the one that makes the mistake silent.

## Decisions filed

- **D-015** — the generation aggregate is `<parent>-<g8>.slice`, not
  `srdm-gen-<g8>.slice`; and its floor is the sum of the class floors.
- **D-016** — that floor is set before anything runs in the slice, with
  `set-property --runtime`; teardown stops the slice explicitly, because a
  slice stays active and keeps its cgroup after its last service exits.
- **D-017** — the worker's exit status is the refusal channel, read from
  `ExecMainStatus` and guarded on the unit having failed by exiting. Carries
  the explicit `TimeoutStartSec`, since systemd's 90-second default is not a
  failsafe for a worker that copies and hashes gigabytes — it is a limit a
  legitimate population can reach.

## Gaps

- **No consumer resolution.** Teardown still does not check whether anything
  holds a bind. P05, and it runs before teardown, not inside it.
- **Reconciliation reports; it does not repair.** `Unheld` joins
  `NeedsRepublish` and `NotReadOnly` as something surfaced and not acted on.
  P08.
- **The parked worker's own footprint is charged to the class cgroup** and is
  not measured. `debug.FreeOSMemory()` returns the copy buffers before it
  parks, but a Go runtime is not nothing, and it comes off the class floor.
  Worth a number before the floors are calibrated against a real payload (P09).
- **Nothing drives any of this from the CLI.** Publication and hold are
  libraries; the operator entry point arrives with the daemon (P08).

## Verification

```
tools/gate.sh . unit      → gofmt, build, vet, all oracles green
tools/gate.sh . e2e       → 17 privileged oracles green, 3 consecutive clean runs
tools/canary-run.sh       → 24 canaries rejected, 0 survived
tools/gate.sh . coverage  → 434/544 changed lines (79.8% >= 75.0%)
nyxloom lint              → clean
```

Three consecutive e2e runs again, for P03's reason: the charging oracles are
new and a single green run does not distinguish a correct sequence from a
lucky one.

The coverage base had to be named as an explicit SHA. `HEAD~3` drifted onto
one of this package's own commits when another agent committed between them —
which is the shared-checkout hazard the gate's NO MEASUREMENT outcome exists
for, arriving as a *wrong* measurement rather than an absent one.
