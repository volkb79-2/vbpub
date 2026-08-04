# srdm-P08b — LOG

Package: boot restore, reconciliation acting, adoption and quarantine,
doctor's online half
Roadmap: `../roadmap.md`, Wave 3
Date: 2026-08-04

---

## D-025 confirmed first

The roadmap named this precondition explicitly: D-025 ("v1 has no daemon")
had to be confirmed before P08b, because it decides whether this package
builds a socket. Confirmed — every purpose the master plan lists for a v1
daemon was already v2 (the provider socket, per-start lease resolution), was
already specified as a *unit* rather than daemon work (boot restore), or was
bought more simply by an `flock` (serialization) with no "refuses when the
daemon is down" failure mode — a mode which, on the one node that most needs
repairing, refuses exactly the operations that would repair it.

Confirming it early decided the shape of everything below: the boot path is
a `oneshot` systemd unit calling an ordinary CLI verb, and reconciliation
acting on drift is another ordinary CLI verb (`srdm reconcile`) rather than
something only a running daemon could do.

## What was built

**The operation plan** (`internal/publish`, `Publish`'s `writePlan` /
`OpPlanFile`). Before P08b, a publication's only durable trace before its
final record was the mounts and hold units themselves — enough for the
process that made them (its own deferred cleanup unwinds them on any
`error != nil` return), and nothing for a LATER process to reason about if
that one never got the chance to unwind. The plan is the `Record` `Publish`
is building, written under `RunDir` before anything is mounted and removed
once the published record makes it official — under the same volatile root
its mounts and units live in, so a reboot wipes plan and referent together
and the plan can never outlive what it claims.

**`internal/publish.AdoptOrQuarantine`** reads every plan a previous process
left behind (found because, under the operation lock, its owner cannot still
be writing to it) and asks the kernel the one question that matters: is
every class the plan named mounted, read-only, and held? If so, the
operation finished in every way that matters and only its last write never
happened — **adopted**, by performing that write now. If not, there is no
partial-credit state to resume into — **quarantined**, torn down the same
way `Publish`'s own crash cleanup tears itself down when the process
survives to run it. This is the master plan's v2 worker-contract rule
("adopts operations whose units are still active, quarantines operations
whose units are gone without a result record") read for a lock instead of a
daemon: the plan is what a "result record" becomes when nothing is listening
for one.

A plan that does not even parse falls back to sweeping its operation
directory by path alone — deliberately narrower than the general teardown
path, which derives a generation directory from the record and, for an
empty Profile/Generation, would resolve to `RunDir` itself and sweep every
OTHER operation's live mounts. Caught by a test before it shipped
(`TestAdoptOrQuarantineSweepsAnUnparsablePlanByPathAlone`), not by an
incident. The cost is real and named rather than hidden: the hold unit for
an unparsable plan cannot be stopped, because its name lived only in the
plan just declared unusable. → backlog.

**Reconciliation acting** (`internal/publish`: `IsComplete`, `RepairReadOnly`,
`ClearForRepublish`; `internal/opctl`: `Reconcile`, `Restore`). `Reconcile`
already existed (P04) and only ever reported. P08b splits what it finds by
what is safe to do without the profile document reconciliation does not
carry:

- `NotReadOnly` is always a plain remount — nothing is unmounted, so nothing
  a consumer has open changes identity.
- A `NeedsRepublish` generation nobody is currently assigned to is cleared
  for good (`ClearForRepublish`, which refuses exactly as `Teardown` does
  while a consumer holds it) — the generation-level equivalent of an orphan
  mount.
- A `NeedsRepublish` generation that IS somebody's current assignment is
  reported (`NeedsActivate`) rather than rebuilt here, because rebuilding it
  correctly needs the profile document and the re-exposure sequence only
  `Activate` carries end to end.

**The bug this exposed, not merely worked around**: `opctl.publishOrAdopt`
trusted a `LoadRecord` hit unconditionally. That is exactly wrong the
instant after a reboot, which is the state `Restore` exists to repair —
every published record survives (`StateDir` persists) naming mounts that do
not (`RunDir` does not), which is `Reconcile`'s own documented reason
`NeedsRepublish` exists. `Restore` calling the old `publishOrAdopt` would
have hung a fresh exposure off a record naming nothing real. `IsComplete` —
the same per-record completeness check `Reconcile` already computed for
every record at once, factored out so one record can be asked about in
isolation — is what makes trusting a record conditional on asking the
kernel first, which is what makes `Restore` safe to call unconditionally
rather than only when something already looks wrong.

**`opctl.Restore`** is `Activate` run once per assignment with the profile
document supplied from a durable copy instead of an operator's `--profile`
flag, under one lock held for the whole pass rather than one per profile.
Refactored `Activate`/`Rollback` to acquire the operation lock at the public
entry point instead of inside the shared `activate` helper, specifically so
`Restore` could call that helper once per profile without each call
re-acquiring a lock it already holds.

**D-026**, filed and confirmed the same day it was found, not left open:
nothing durable ever kept a profile *document*, only its id — every
operation that takes `--profile <file>` loads it fresh and forgets it.
`srdm-restore.service` has an assignment naming a profile by id and no
operator to hand it a path. `cfg.ProfilesDir()` (`<state-dir>/profiles/<id>.json`)
keeps the last document that successfully drove a state-changing operation,
written by `newOpEnv` at the same moment as everything else durable — a side
effect of the operation that made it true. A profile with an assignment but
no durable copy is reported per-profile, never guessed past.

**The boot unit** (`systemd/srdm-restore.service`), a reference unit srdm
does not install (D-003's own rule, restated: writing `/etc/systemd/system`
needs host root srdm does not have). `After=local-fs.target`, no Docker
dependency — the master plan's own review answer to a `Before=docker.service`
issue is a Wings-side bounded boot-restore retry, not an ordering edge here.

**`doctor`'s online half** (`cmd/srdm`: `checkGenerationTopology`, `--repair`).
`doctor` gained a new check reporting `Reconcile`'s findings without acting
on them — doctor stays read-only by default, because a diagnostic command
that sometimes mutates state as a side effect of being run is not one an
operator can trust to be safe. `--repair` is the explicit opt-in that runs
the same `Reconcile` pass `srdm reconcile` does.

**New CLI verbs**: `srdm reconcile` (operator-invokable, no `--profile`) and
`srdm restore` (what the boot unit calls). Both removed from `daemon, stage,
operation`'s "not implemented" list, which now names only what is actually
still pending and why.

## What the tests found

**The `publishOrAdopt` trust bug**, above — found while writing
`TestRestoreRebuildsAGenerationThatDidNotSurviveTheReboot` against the exact
state `Reconcile`'s own comment describes, before any e2e run was needed to
hit it.

**The generation-directory sweep trap**, above — a narrower, path-only sweep
for `AdoptOrQuarantine`'s unparsable-plan fallback was written specifically
because `teardownOp`'s existing generation-derived sweep resolves to
`RunDir` itself when a record's Profile and Generation are both empty, which
an unparsable plan always is.

## Gaps

- **An unparsable operation plan cannot stop its own hold unit.** Named
  above and in `sweepOpDir`'s own comment; journaled, not silent. → backlog.
- **Generation GC's "no labeled container in any state" term** is unchanged
  by P08b and stays latent — labels are v2, and P08b's boot path restores
  what an assignment already declares rather than changing what GC
  considers a hold.
- **The lock stays per node, not per profile** (D-025's stated cost, also
  unchanged): `Reconcile` and `Restore` both take the single node-wide lock.

## Verification

```
tools/gate.sh <worktree> unit       → gofmt, build, vet, all oracles green
tools/canary-run.sh                 → PLACEHOLDER — see below
tools/gate.sh <worktree> coverage   → PLACEHOLDER — see below
nyxloom lint                        → PLACEHOLDER — see below
```
