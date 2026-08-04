# srdm decisions inbox

Product calls, one `D-<NNN>` each. A decision is a *product* gap — a name, a
contract, a user-facing choice — recorded and worked around, never a reason
to stop. Mechanical blockers are BLOCKED exits instead.

> **"The master plan"** in the entries below means
> `../../wings-cgroups/shared-ramdisk-update-lifecycle-5-fable.md`, which
> governed this project until 2026-08-04 and is now superseded by
> [`PLAN.md`](PLAN.md). Those references are deliberately left as written:
> most of these decisions exist *because* the plan said one thing and the
> kernel did another, and rewriting them would erase the reasoning. PLAN.md
> §The measured ground is the same list, collected.

---

## D-001 — `WS/Config` classification: shared or per-instance?

**Status:** open. Filed by P01 on arrival, per the handoff.

The master plan leaves `WS/Config` `[open]` pending a runtime write audit
(§Open questions 1). The legacy `soulmask_tmpfs-paths.conf` excludes it and
says why: "server config, per-instance". If any instance writes there at
runtime, sharing it is the 2026-07-29 corruption shape with a smaller
payload.

**Worked around in P01:** the profile format expresses both answers — a
class of `kind: excluded` covers per-instance, `kind: managed` covers
shared — and the test profile classifies `WS/Config` as excluded, matching
the live host. Nothing in the store depends on the answer.

**To resolve:** the runtime write audit, during the Soulmask migration.

---

## D-002 — retention count

**Status:** **closed by P08** — 3, from configuration, and a floor rather
than a cap.

The master plan's default is "≥ 3 retained, immutable, hash-verified"
(§Defaults), with the count itself `[open]` (§Open questions 3).

**Worked around in P01:** retention was not implemented, so no number was
baked in anywhere.

**Decided:** `config.Retention`, defaulting to `DefaultRetention = 3`, and
`gc` keeps that many releases **beyond** the ones it may not remove at all.
The distinction is the substance of the decision rather than a detail of it:
the number never decides whether something live survives. Four pins come
first — the assigned release, the rollback target, anything with a live
generation, and any channel target — and retention only chooses among what
is left over. So a profile can legitimately hold more than three and can
never hold fewer.

`Validate` refuses a retention below 1. Zero would mean "keep nothing beyond
what is pinned", which is a coherent policy nobody wants by accident, and it
is exactly what an unset field would produce.

The master plan's rule is "removable when not assigned, no live lease, no
labeled container in any state". Two thirds of that is v2: leases and
Wings-constructed labels arrive with the provider protocol, and in the v1
host-bind shape **a container never references a release at all** — it holds
a bind of a generation's tmpfs, whose superblock is what teardown refuses
over (D-018). So v1's rule is the same rule with the two absent terms
dropped, and `published` is what replaces them.

---

## D-003 — does srdm write `srdm.slice`, or verify it?

**Status:** proposed — **verify, never write**. Confirm before P02.

The master plan says the parent slice's `MemoryMin` is admin-owned
(§Generation slices: "the admin-owned `srdm.slice` unit file carries
MemoryMin ≥ Σ active class floors"). nyxloom reached the same conclusion for
its own slices and stated the boundary plainly: setting cgroup values on a
container the daemon starts is a Docker API call it already has rights for,
but writing `/etc/systemd/system` and reloading systemd needs host root,
which the daemon does not and must not have.

**Decided for P01:** `doctor`'s `parent-slice` check *verifies* — LoadState
is `loaded`, and `MemoryMin` covers the profile's summed managed class
floors — and names the fix when it does not. srdm writes no unit files.
`systemd/srdm.slice` ships as a **reference** an operator installs, and says
so in its own header.

**Why this must be confirmed rather than assumed:** it decides whether srdm
ever needs host root at install time, which is the difference between a
service an operator can adopt incrementally and one that demands the whole
node up front.

---

## D-004 — the `privileged-e2e` gate is declared but empty until P02

**Status:** **closed by P02.** The gate now carries four oracles that
measure real kernel and systemd behaviour (O6a, O6b, O7, O8, O9), and one of
them immediately falsified a design assumption — see D-011. The guard below
is kept for the record of why it was declared empty in the first place.

---

### Original entry (P01)

**Status:** accepted, with a guard.

The handoff requires `nyxloom.toml` to declare a `privileged-e2e` gate. P01
has no e2e cases: publication topology, hold services and charging are P02,
and there is nothing yet for a privileged harness to be about.

A declared gate that cannot fail is worse than none — it launders every
merge as "verified". So:

- the gate is declared and its image target (`gate/Dockerfile` `e2e`) is
  real and buildable, rather than a promise;
- `nyxloom.toml` states in the gate's own comment that a green
  `privileged-e2e` is **not evidence of anything** until P02 adds cases;
- P02's first act is to add one, and to check it rejects a known-bad canary
  before anyone trusts it.

---

## D-005 — profile documents are JSON in P01; YAML is additive later

**Status:** accepted.

The master plan specifies YAML for the profile document (Layer 3). P01 needs
classification and probes but not the file format — the profile engine and
the Soulmask profile are a later phase.

**Decided:** `internal/profile` ships a JSON loader and zero third-party
dependencies, which keeps the module hermetic (no module downloads at gate
time, nothing vendored). Every document carries `schema_version`, and the Go
types are format-neutral, so adding a YAML front-end is purely additive and
changes no stored data.

**Cost, stated:** anyone hand-writing a profile before the YAML front-end
lands writes JSON. Given v1 acquisition is strictly manual and there is one
profile, that cost is small and reversible; a vendored YAML parser in the
diff of a bootstrap package is not.

---

## D-006 — srdm gets its own gate container, not a Go-enabled `tester-unified`

**Status:** accepted (operator-authorized, 2026-08-03).

The handoff names `tester-unified` as the gate. It cannot host srdm's
oracles: it is a Python 3.14 venv closure built from four projects'
pyprojects, with **no Go toolchain**, running unprivileged as uid 1003, with
no systemd.

Two options were put to the operator: add Go to `tester-unified`, or build a
gate container tailored to srdm. **Tailored container chosen**, because:

1. **Scope.** P01's declared touch-set is `shared-ramdisk-depot-manager/**`.
   `tester-unified` is shared by ciu, cmru, topos and nyxloom — adding a Go
   toolchain rebuilds and re-risks four unrelated Python gates.
2. **Shape.** srdm's P02+ oracles need privileged systemd-in-Docker
   (transient hold units, mounts, cgroup charging). The master plan's §Gate
   already calls for that harness. `tester-unified` is structurally unable to
   be it, so the split happens eventually regardless — doing it now avoids
   paying twice.

`gate/Dockerfile` ships both targets. Cockpit doctrine is unchanged: the
gate is `srdm-gate`, never the devcontainer.

---

## D-007 — changed-line coverage floor: built for Go, not skipped

**Status:** **closed** by `tools/covergate` and `[gates.coverage]`
(superseding the original "accepted, no floor" position).

nyxloom offers `coverage_gate.py`, a changed-line coverage floor, and it is
Python-specific. P01 originally declared `asserts = ["tests-pass"]` only,
on the grounds that claiming `changed-line-coverage` without enforcing it is
a declaration mismatch — which nyxloom's own `gate verify` would catch with
an uncovered-line canary, after laundering every merge until it did.

**Reopened and closed** on the observation that nyxloom's gate is *already*
language-agnostic in its design; only two seams are Python-bound:

1. `_load_coverage` reads coverage.py JSON;
2. `evaluate` hard-filters `if not npath.endswith(".py"): continue`.

The second is the dangerous one for any non-Python consumer: it would skip
every `.go` file, report `0/0 → 100%`, and **pass vacuously**, because
`_check_measurable` does not fire on a clean tree.

**Decided:** srdm ships `tools/covergate`, a Go implementation of the same
semantics, and `[gates.coverage]` enforces it. Deliberately carried over
from the Python original, because each is a lesson rather than a detail:

- **Three-way line classification** (executed / missing / neither). A
  changed line in neither set is a comment or a brace; editing it is not an
  uncovered-code event.
- **Three outcomes, not two.** Pass, fail, tool error — and NO MEASUREMENT
  (exit 3). "0/0 changed lines covered (100.0%)" reads identically whether
  the gate measured everything and found nothing or measured nothing at all.
  A dirty tree (the `base..HEAD` diff cannot see uncommitted work) and a
  base resolving to HEAD both produce that exact string. Both are ruled out
  before a percentage is computed.
- **Base resolution serves both phases**: a merge commit diffs against its
  first parent, anything else against `merge-base(base, HEAD)`.

**Found while building it** — a false-positive class the Python original
documents in a different form (its B63 note, "unmeasur*able* is not
unmeasur*ed*"). `go test -cover` instruments function *bodies* only, so a
`.go` file declaring no functions produces no blocks and is absent from the
profile for exactly the reason a JSON schema is. The first run flagged **94
lines across four comment-only `doc.go` files** as uncovered — a verdict no
test could ever clear. `HasExecutableCode` settles it by parsing for a
function declaration with a body. Any port of this to nyxloom needs the
same guard, and it does not fall out of the extension filter.

**Floor: 75%**, set from measurement rather than aspiration — and the
distinction is not rhetorical. The floor was first written as 80, then
measured: srdm's whole delta to date is **78.0%** (1409/1807 changed
executable lines under `internal/`). An 80 floor would have been a number
nobody had demonstrated was reachable, sitting under a decision record
claiming it came from measurement. Lowered to 75 so the claim stays true
and the gate binds without failing retroactively.

Raise it as the suite matures; it is a floor, not a target. The residue is
`if err != nil { return err }` passthroughs and one genuine exec boundary
(`doctor.systemctlShow`). 100 would buy fault-injection scaffolding for
error returns that a reviewer reads faster than a test asserts.

**A rendering trap worth knowing about**, found measuring the above: a
test-only commit legitimately reports `0/0 changed executable lines covered
(100.0%)` — textually identical to what a measurement that never happened
prints, which is the exact ambiguity exit 3 exists to remove. `Report` now
states the reason (`nothing to cover — N changed file(s) under "internal",
none contributing executable non-test lines`). The verdict is unchanged;
only its readability is. nyxloom's original has the same trap.

**Suggested nyxloom generalization** (not done here — nyxloom has its own
trove and package loop; this is the reference implementation to copy):
a `--source-ext` flag replacing the hardcoded `.py`, a pluggable report
loader (Go cover profiles and lcov cover Go, Rust and JS between them), and
the code-free-file guard above. The pure core — `parse_added_lines`,
`evaluate` — needs no change at all, which is the point: it was already
written against plain data.

**Still the stronger control:** `[gates.canary]`. Coverage proves a changed
line *ran*; the canaries prove each oracle actually *fails* when the
contract it names is broken. That is the property hollow tests evade, and
no percentage can express it.

---

## D-008 — fsync ordering is not observable in the unit gate

**Status:** accepted, deferred to the privileged harness.

`COMPLETE` is written after the content is fsync'd, so the file means "this
release is whole *and durable*". The unit gate can prove the **ordering** —
the kill-at-every-phase oracle SIGKILLs a real process at each boundary and
asserts what survives — but it cannot prove the **durability** half. A
process kill leaves the page cache intact; only power loss or a fault-
injecting block device distinguishes "written" from "durable".

**Decided:** P01 asserts ordering by oracle and durability by construction
(`fsx.WriteFileSync` — temp file, fsync, rename, fsync the parent
directory), and records the gap here rather than implying the gate covers
it. A `dm-flakey`-style oracle belongs with the privileged e2e harness
(D-004), where a device can be made to lie.

**Update (P02):** that harness now exists and runs privileged, so the
`dm-flakey` oracle is buildable. Still not built — it is its own package,
not a rider on P02.

---

## D-009 — srdm drives systemd through its CLI, not D-Bus

**Status:** accepted for v1, revisit if the CLI proves insufficient.

The master plan's privilege table names "systemd D-Bus (transient units)".
Every Go D-Bus client is a third-party dependency, and srdm's zero-dependency
property is what keeps its gate hermetic: no module downloads at gate time,
nothing vendored, an offline build.

**Decided:** `internal/systemdx` shells out to `systemd-run` and `systemctl`,
which are stable, documented interfaces. The runner is injected, so argv
construction and output parsing are unit-tested with no systemd present, and
swapping in a D-Bus implementation later changes that one file and nothing
above it.

**The cost, stated:** parsing CLI output is looser than a typed API, and
`systemctl show` reports a property as an empty string both when it is unset
and when it does not exist. `Property` therefore distinguishes absent from
empty, and the e2e oracles read the **cgroup files** rather than
`systemctl show` wherever the question is "did the kernel apply this" — show
reports what was requested.

---

## D-010 — the e2e harness runs `--cgroupns=private`

**Status:** accepted.

The roadmap first said `--cgroupns=host`. The recipe already proven on this
host (`wings-cgroups/v1-legacy/test/e2e-systemd/run-e2e.sh`) uses
`private`, and it is the right choice for two reasons: systemd wants its own
cgroup namespace root, and a private namespace keeps the measurement
self-contained instead of reading a tree that other containers are moving
under it.

`tools/cgroup-parent.sh` still uses `--cgroupns=host` — deliberately, and
for the opposite reason: its whole job is to inspect the **host's** tree.

---

## D-011 — populate-and-hold needs a parked worker, not `RemainAfterExit`

**Status:** **decided by measurement.** This is the branch the master plan
left open, and it went the other way.

The plan specifies one transient unit per class, `Type=oneshot` with
`RemainAfterExit=yes`, so that "populate and hold are the *same* unit
precisely so the charge and the policy can never separate" — and it named
the fallback in advance:

> if any systemd version fails to keep an active-but-empty service's cgroup
> alive, the fallback is an explicit minimal hold process — **the oracle
> decides**, the spec allows both.

**Measured on systemd 257 (Debian trixie), 2026-08-03.** The oneshot unit
behaves as advertised at the *unit* level and not at the *cgroup* level:

| | oneshot + RemainAfterExit | Type=exec, worker parks |
|---|---|---|
| `ActiveState` | `active` | `active` |
| `SubState` | `exited` | `running` |
| `MemoryMin` accepted by systemd | yes (`33554432`) | yes |
| `ControlGroup` | **empty** | `/system.slice/<unit>` |
| cgroup directory | **gone** | present |
| `MemoryCurrent` | **`[not set]`** | `67465216` |
| the 64 MiB of content | **reparented to `system.slice`** | `shmem 67108864` on the unit |
| `memory.min` in the cgroup | n/a — no cgroup | `33554432` |
| `memory.zswap.max` in the cgroup | n/a | `0` |

systemd reaps the cgroup as soon as the last process exits. The charge then
lands on the parent slice, where the class floor does not apply — the floor
would be **arithmetically dead**, which is the exact failure the
single-token slice name (decision 9) exists to prevent, arriving by a
different route.

**Decided:** the hold worker **must not exit after populating**. It
populates, verifies, then parks; `systemdx.HoldBaseProperties` returns
`Type=exec` and says why. P04 builds the real worker to that shape.

**Consequences to carry into P04:**

- The worker is a long-lived process, so it needs a shutdown path and its
  own memory footprint is charged to the class cgroup alongside the content.
  Keep it minimal — after populating it should hold nothing but a wait.
- `SubState` for a healthy hold unit is `running`, not `exited`. Anything
  that waits for `exited` is waiting for a failure.
- Teardown still unmounts before stopping the unit, and that ordering is now
  measured rather than assumed (O7).

**Both halves are pinned by oracles.** `TestOneshotRemainAfterExitDoesNotKeepItsCgroup`
asserts the negative, so a future systemd that *does* keep the cgroup makes
that test fail — which is good news and means re-opening this decision to
drop the parked worker, not editing the test.

---

## D-012 — an open file descriptor is not the teardown hazard; a surviving mount is

**Status:** accepted, and it confirms an existing design choice.

Written up because the first version of oracle O8 was built on a wrong
premise and the harness caught it.

The design's teardown rule exists because "a game container that still has
the bind in its own `rprivate` namespace keeps the superblock — and every
page — alive across the host unmount". The obvious way to model that is an
open file descriptor. **It does not work:** an open file makes the mount
busy, so the unmount fails outright with `EBUSY` rather than succeeding and
leaving a ghost.

The mechanism is a **second mount** of the same superblock. Two mounts, one
superblock: dropping the host-side one leaves the consumer's alive, the
pages stay charged, and only the last unmount frees them. Measured:
`shmem` stayed at exactly 67108864 across the host unmount and went to 0
when the consumer's bind went away.

**Why this matters beyond the test:** it is why holder resolution reads
`/proc/*/mountinfo` rather than scanning for open descriptors — the master
plan already specifies mountinfo, and this is the measurement behind that
choice. It also explains the production symptom of the 2026-07-31 outage: a
**ghost mount**, not a failed `umount`.

---

## D-013 — the hold unit is `Type=notify`; "active" must mean "populated"

**Status:** decided by a failing oracle, refining D-011.

D-011 established that the hold worker must park rather than exit, and
settled on `Type=exec`. That is not sufficient, and the difference is a race
rather than a preference.

**`Type=exec` marks a unit active as soon as the process has been EXEC'D**,
which is before it has populated anything. Anything that waits for the unit
to be `running` and then acts on the content is racing the worker.

**Found by P03's teardown oracle**, intermittently: the test waited for the
charge to reach the written size (within a tolerance), then unmounted — and
the unmount landed while the worker still had the file open, failing with
`EBUSY`. That is D-012's mechanism arriving from the other direction, and
the tolerance is what turned a correct sequence into a race.

**Decided:** `Type=notify` with `NotifyAccess=main`. The worker signals
`READY=1` only after population and verification are complete, so `active`
means "the content is there and I am now only holding it" — which is the
state srdm actually needs to observe before binding, exposing or tearing
down. `NotifyAccess=main` because a helper that exits must not be able to
declare a class populated.

**The wrong fix, explicitly rejected:** widening the charge tolerance, or
sleeping before the unmount. Both would have made a real race pass on a fast
machine, which is the anti-pattern the authoring guide names first. The
oracle was correct; the unit shape was not.

**For P04:** the worker populates, verifies, calls `sd_notify(READY=1)`,
then parks. `HoldBaseProperties` returns this shape and
`TestHoldBasePropertiesAreProductionShaped` pins it, so the decision cannot
quietly regress into a comment.

---

## D-014 — publication verifies content, not modes

**Status:** accepted; a direct consequence of sealing.

`Manifest.Verify` compares permission bits, because for a release in the
store the mode IS part of what was promoted. Publication cannot use it: it
populates a class tree and then makes it read-only (`chmod -R a-w`), so the
published copy's modes differ from the release's *by design*.

**Decided:** `Manifest.VerifyClass` compares type, size, digest and symlink
target — everything about what the pages *are* — and not the permission
bits. Comparing them would fail on exactly the hardening that makes
publication safe.

It still refuses a tree containing anything the manifest does not name.
Dropping that check alongside the mode check would have left content
smugglable into a published class, which is a much bigger hole than the one
being avoided.

---

## D-015 — the generation aggregate is `<parent>-<g8>.slice`, not `srdm-gen-<g8>.slice`

**Status:** decided by measurement, and it corrects the master plan.

The plan draws the per-generation aggregate as:

```text
srdm-gen-<g8>.slice
├─ srdm-hold-<g8>-pak.service
└─ srdm-hold-<g8>-code.service
```

systemd reads `-` in a **slice** name as the hierarchy separator, so that
name does not sit under `srdm.slice`. It sits under an auto-created
`srdm-gen.slice` that nobody declares, nobody owns and nobody configures —
and which therefore carries `memory.min=0`. Measured on systemd 257,
2026-08-03:

```text
ControlGroup=/srdm.slice/srdm-gen.slice/srdm-gen-b5b5b5b5.slice/srdm-hold-b5b5b5b5-pak.service
memory.min:        0     ->      0      ->     367001600      ->     157286400
```

A cgroup v2 floor is **capped by every ancestor's** floor. A zero anywhere on
the chain makes every floor below it arithmetically dead, no matter what the
leaf says. This is the exact failure the single-token root name (master-plan
decision 9) exists to prevent, arriving one level further down — and it
arrives in the part of the design whose entire purpose is to carry the class
floors.

**Decided:** the aggregate is the configured parent's stem plus the
generation — `srdm.slice` + `a1b2c3d4` → `srdm-a1b2c3d4.slice` — which nests
directly under the parent and interposes nothing:

```text
/srdm.slice/srdm-a1b2c3d4.slice/srdm-hold-a1b2c3d4-pak.service
memory.min: 536870912 -> 367001600 -> 157286400
```

Service names are unaffected: only slices nest, so
`srdm-hold-<g8>-<class>.service` keeps the plan's shape exactly.

**Why not set a floor on `srdm-gen.slice` instead.** Its correct value would
be the sum over *all* live generations — a number with no single owner, which
changes as generations come and go, and which two concurrent publications
would race to write. The naming removes the problem rather than managing it.

**The aggregate still needs its own floor**, whatever it is called: an
implicitly created slice starts at zero like any other. `EnsureSlice` gives it
the **sum** of the class floors beneath it, not the maximum — cgroup v2
prorates a parent's protection among its children, so an aggregate protecting
less than the sum silently shrinks every floor inside it.

**Pinned by three oracles**, because the trap is structural rather than
behavioural: a unit test asserts the derived name interposes nothing, using a
helper that encodes systemd's naming rule and is itself pinned against the
`ControlGroup` above; a privileged oracle walks the live cgroup chain from the
hold unit to the configured parent and fails on any ancestor whose
`memory.min` is below the floors beneath it; and a canary renames the slice
back to the plan's shape and must make both go red.

---

## D-016 — the generation slice gets its floor before anything runs in it

**Status:** accepted; measured.

A slice systemd creates implicitly, because a service named it, starts at
`memory.min=0`. If srdm set the floor afterwards, the window in which the
aggregate is unprotected would be exactly the window in which the worker is
faulting every page of the class into it.

`systemctl set-property --runtime` works on a slice with **no unit file and no
running instance** — measured 2026-08-03: it writes
`/run/systemd/system.control/<slice>.d/50-MemoryMin.conf`, returns 0, and the
value is in the kernel by the time the first service starts. So the order is
set-property, then start the hold units.

`--runtime` and not a persistent drop-in: the floor describes one live
generation. Surviving the reboot after which srdm republishes from its own
records would leave a floor protecting content nobody has.

**Teardown has to stop the slice explicitly.** Also measured: a slice stays
`ActiveState=active` and keeps its cgroup directory after its last service
exits. Stopping only the services leaves the generation's aggregate — and its
cgroup — behind. `ReleaseSlice` stops it and then `systemctl revert`s the
drop-in; srdm only ever set-properties units it names itself, so reverting one
cannot discard an operator's configuration.

---

## D-017 — the worker's exit status is the daemon's refusal channel

**Status:** accepted, and half of it was found by a failing oracle.

Population now happens in a separate process, in a transient unit the daemon
did not fork. There is no wait status to read, so "this class does not fit"
and "something is broken" have to be told apart across that boundary — and
they must be, because the first quarantines a generation and the second does
not.

**Decided:** the worker exits with a code that names the kind — `3` out of
space, `4` content does not match the manifest, `2` malformed invocation, `1`
anything else — and the daemon reads it back from systemd's own
`ExecMainStatus`. systemd keeps it durably, which also matters for P08, where
the daemon adopting units that outlived it has nothing else to go on.

**Found by the ENOSPC oracle:** `systemd-run` **blocks on the start job**, and
for a `Type=notify` unit that job does not complete until the worker signals
ready. A failing worker therefore reports through the *start call*, not
through the readiness wait that follows it. The first version only consulted
the status after the wait, so a class too small to hold its content surfaced
as a bare `exit status 1` — a fault, unquarantined. Both paths now go through
the same lookup.

**With a guard, and the guard is the subtle part.** `ExecMainStatus` is only
meaningful for a unit that failed *by exiting*: `ActiveState=failed` and
`Result=exit-code`. A start refused before the worker ever ran — a unit name
already taken, say — would otherwise hand back the status of whatever holds
that name, and for a healthy running unit that is **0**. A failed start would
be reported as a worker that succeeded. The canary that removes this guard
survived the first version of its own test, which had scripted no status for
it to wrongly believe; the test now scripts `ExecMainStatus=0` explicitly,
because that is the value that makes the mistake silent.

**One property comes with it.** `TimeoutStartSec` is set explicitly to 30
minutes rather than left at systemd's 90-second default. For a `Type=notify`
unit that default bounds the time until READY — and READY here means a
multi-gigabyte class has been copied and hashed. 90 seconds is not a failsafe
for that, it is a limit a legitimate population can reach. The value is a
hang failsafe and never an oracle: expiry means a worker making no progress,
which is a true failure however long you wait, and no plausible class on any
plausible disk comes near it.

---

## D-018 — the consumer check is inside teardown, not a precondition on it

**Status:** accepted.

The P03 code said teardown "does NOT decide whether it is safe to run", and
left resolving live consumers to whatever called it. That was the wrong
layering, and the reason is specific rather than stylistic.

Every step of a teardown performed while a consumer holds the content
**succeeds**. The unmounts return 0. The hold units stop. The generation
slice goes. The record is removed. And not one page comes back, because the
consumer's own namespace still holds the superblock — measured, D-012 and
again here. There is no error to detect afterwards, no state that looks
wrong, and nothing in the journal that reads differently from a healthy
teardown. The only instant at which the difference exists is before the first
unmount.

A check with that property cannot be a caller's responsibility. So
`Publisher` takes a `Guard` the same way it takes a `Holder`, defaulting to
the real resolver, and `Teardown` consults it first. `Holders` is exported
for `activate` and `rollback`, which have the same problem and arrive with
P08.

**There is no stored consumer registry, deliberately.** In the v1 host-bind
shape nothing tells srdm who mounted what: Wings creates the container,
Docker resolves the bind source in the host namespace, srdm is not in the
conversation. A table srdm maintained itself could only be a second opinion
about the kernel's, and the kernel is what holds the pages. The registry is
the resolution, done fresh at every ask — which is what the master plan means
by oracle 24 being "oracle 15 without the protocol's help, the mode that has
to get it right by inspection".

**Degrading is reported, never assumed.** Docker names the holders and sees a
container that is configured but not yet running; the mount table answers the
question that costs memory. If Docker is unreachable the answer is still
usable, but the check says so in the journal — "nobody is holding this" and
"I could not ask" are different answers, and only one of them is safe to have
acted on.

---

## D-019 — publication mounts into a private root, because propagation cannot be filtered

**Status:** decided by measurement, after the obvious answer was implemented
and deleted.

Resolution matches on the **superblock**: a consumer's bind is at a path srdm
has never seen and cannot predict, while the major:minor is the same number
on both sides of a bind. That immediately raises a problem. On a systemd host
`/run` is shared (verified on the case-study node, `shared:5`), so a mount
created beneath it is **delivered** to every mount namespace that is a slave
of the root — every service with `PrivateTmp`, `ProtectSystem`, or its own
`unshare`. Each of those copies matches the superblock, so each reads as a
consumer, and teardown would be refused forever by services that want nothing
from srdm.

**The filter that looked obvious, and was wrong.** Such a copy carries
`master:<srdm's peer group>`, which says it is downstream: srdm's unmount
ought to take it away again, so it could be excused. That filter was written.
Then it was measured, on kernel 7.1 / systemd 257, 2026-08-03: a copy
carrying exactly that tag **survived** the host unmount, with the content
still readable through it, when the namespace also held its own bind of the
same superblock. In other arrangements the same tag is removed as expected.
**Nothing in `mountinfo` tells the two cases apart.**

The asymmetry settles it. Over-filtering is a silent leak — the exact failure
this check exists to prevent. Under-filtering is a refusal an operator can
see, understand and act on. So srdm does not filter: every mount of the
superblock in another mount namespace is a hold.

**Which makes not handing the copies out the actual fix.** Publication binds
its operation root onto itself and marks it `MS_PRIVATE` before mounting
anything beneath it, so nothing it creates is delivered anywhere. Marking a
mount private *after* creating it is too late — also measured; the copy has
already gone out.

Two consequences worth stating plainly:

- The operation root is srdm's own infrastructure, not a generation's mount.
  Reconciliation must not tear it down as an orphan, or the isolation
  disappears from under every live generation and the next publication starts
  handing out copies again.
- Isolation does not help against a namespace created *after* publication:
  `unshare` copies the whole mount table, whatever its propagation. A service
  that restarts while a generation is published will hold it, srdm will
  refuse the teardown, and srdm will be right — the memory really would not
  come back. The operator is told which process. Whether that wants a
  narrower answer is a question for P06, which owns propagation properly.

---

## D-020 — `access: rw` binds the writable side; who may write through it is open

**Status:** half decided by measurement, half deliberately left open.

**The measured half.** An `rw` exposure cannot be a bind of the published
path. Publication's exposure is itself a read-only bind, and a bind inherits
its source's per-mount flags — so binding it and asking for `rw` produces
something still read-only. Found by the ephemerality oracle on its first run:
the write returned `EROFS` before it could prove anything about ephemerality.

So the two access modes bind **different mount points of the same
superblock**: `ro` binds the published read-only exposure, `rw` binds the
operation tmpfs's own content root. Same pages, same hold unit, same charge —
only the mount differs. `sourceBase` is the whole of it, and a canary flips it
back.

**The open half.** Publication seals a class tree `chmod -R a-w` before
anything binds it, and P06 does not unseal it. So an `rw` exposure permits
writes at the MOUNT level while the MODES still refuse them: root can write
through it (root bypasses mode checks), an unprivileged game container
cannot.

That is enough for the oracles P06 owes — ephemerality is observable, the
single-consumer rule holds, the generation is marked — and it is not enough
for a real Soulmask update-in-place. Closing it needs answers srdm does not
have yet: which uid writes (the store normalizes to `srdm:srdm`, the game
container runs as something else), whether unsealing is per-class or
per-generation, and whether an unsealed tree may be re-sealed or only
republished.

**Deferred to P07 deliberately**, because `harvest` is the only reason to
write through in the first place — the master plan's oracle 23 is "update in
place through `rw` → `harvest`". Deciding ownership without the consumer of
that decision in front of us is how it gets decided wrongly.

**Update (P07): the open half is closed by D-022.**

---

## D-022 — `access: rw` unseals the class tree and hands it to a declared owner

**Status:** decided, and the measurement is an oracle. Closes the open half
of D-020.

Three questions were left open: which uid writes, whether unsealing is
per-class or per-generation, and whether an unsealed tree may be re-sealed or
only republished. `harvest` is now in front of us, so they can be answered
against something rather than in the abstract.

**Which uid: one the operator declares, and rw is refused without it.**

The alternative was to leave the tree owned by root and let the modes stay as
publication left them. That is what P06 shipped, and it does not work for the
only writer `rw` exists for. Publication seals a class tree `chmod -R a-w`,
so an `rw` exposure permits writes at the MOUNT level that the MODES refuse
to every uid except root — and the game's own updater, running in the
container as an unprivileged uid, is not root. `AUTO_UPDATE=1` would report
success and change nothing, which is the 2026-07-21 incident in a new
costume: "steamcmd reported Success" and "the content changed" are different
claims.

So exposure **unseals**: it restores the owner write bit and `lchown`s the
tree to `wings.write_owner`. Not detected — declared, exactly as F1 is
(D-021). The number is Wings' `system.user.uid` / `system.user.gid`, which
lives one level deeper in the YAML than the single key `wings.ReadChownWalk`
scans for, and srdm has no parser. Guessing it wrong hands the tree to a uid
the container is not, which fails exactly as not unsealing at all does — so
there is nothing to be gained by guessing, and the refusal names where to
look. **Undeclared refuses**; a node that has not said who writes does not
get a writable exposure.

Owner write only, never group or other. Exactly one consumer may write to a
generation, so a world-writable tree would hand it to precisely the processes
the single-consumer rule just refused.

**Per class, and the mechanism matters more than today's granularity.** The
unit of unsealing is the class tree, because a class tree is what was
populated, verified and sealed as one — and because the game rewrites paths
inside it that no individual binding names. In v1 every managed class is
unsealed, because the access axis is per exposure rather than per class; a
per-class axis is additive and changes nothing here.

**Never re-sealed. Republish, or harvest.** Re-sealing would restore the
MODES of a tree whose CONTENT is no longer any release's — the appearance of
a sealed generation without the property, which is worse than an obviously
dirty one. A generation exposed writable stays `DirtyCapable`, is not shared
and is not used as a source. There are exactly two ways out, and `harvest` is
the new one: republish from the store and discard the writes (oracle 22), or
harvest the writes into a release and republish from that.

**Measured, because the whole point is an assertion about a uid srdm is
not.** `TestAnUnprivilegedWriterCanWriteThroughAnRWExposureAndNotThroughRO`
performs the write as uid 65534 through both modes: `EROFS` through `ro`
(the mount refusing, which holds even for root) and success through `rw`. The
two failures it tells apart are the whole decision — `EROFS` would mean rw
bound the read-only side (D-020's measured half), `EACCES` would mean the
tree was never unsealed (this one). Root writing through proves neither, and
that is exactly what P06 was able to observe.

**One consequence, stated because it looks like a bug from the outside.** The
`ro`/`rw` seal is not what makes a read-only exposure safe — the MOUNT is,
and that has been an oracle since P03. Unsealing therefore does not weaken
`ro` in any way, and a read-only exposure never unseals: the seal is the
second lock, and an exposure that quietly removed it would leave the next
consumer writing into a tree nobody marked.

---

## D-023 — a harvested release is managed content only, and that is the state rule holding

**Status:** accepted; it falls out of the design rather than being chosen.

Oracle 23 says a harvested release's manifest matches "a from-scratch stage
of the same build identity, byte for byte". A from-scratch stage of a real
game install contains per-instance state — `WS/Saved`, `WS/Config` — because
the game creates it. A harvest cannot contain any, because publication never
carries any: only managed classes are published, so only managed classes
exist to be read back.

**Decided: the harvest is right and the comparison is what needs stating.**
Carrying the source release's excluded entries into the harvested one was the
alternative, and it is the absolute state rule inverted — `WS/Saved` is never
shared, never used as content input, never modified by a transaction. A
release whose excluded content came from one particular server's disk is a
release that shares one server's saves with every other.

So oracle 23 compares against a from-scratch stage of the **managed**
content, which is what a clean acquisition of game content actually is, and a
unit oracle asserts separately that nothing excluded can enter a harvest at
all. Both are true statements; only their conjunction is the claim worth
making.

---

## D-021 — srdm reads Wings' config with a scanner, and F1 is asserted rather than detected

**Status:** accepted; both halves fail closed.

The host-bind driver needs two facts about a Wings deployment that srdm
neither owns nor configures.

**`system.check_permissions_on_boot`** lives in Wings' YAML, and srdm has no
YAML parser — D-005 declined that dependency for the store's own documents,
and the reason is unchanged: srdm's gate is hermetic because its dependency
set is empty. What is needed is one boolean at a known place in a document
srdm does not otherwise care about, so `wings.ReadChownWalk` scans for it
rather than parsing the file.

A scanner can be defeated by YAML it does not understand — flow mappings,
anchors, a second `system:` block, an unusual indentation. So it **fails
closed**: anything that is not an unambiguous `false` is reported as not
known, the caller treats that as the walk being enabled, and `access: ro` is
refused. Absence is treated as enabled too, because that is Wings' own
default. Guessing the other way produces a server that will not start, which
is the failure the check exists to prevent.

**F1 cannot be detected at all.** It is a patch in a Go binary; there is
nothing srdm can read to tell a patched Wings from an unpatched one. So it is
an assertion — `wings.chown_skip_patch` — made by whoever installed that
build, defaulting to false. A node that has not said otherwise is treated as
unpatched.

Claiming F1 falsely corrupts nothing: it produces exactly the `EROFS` start
failure this refusal exists to prevent, with srdm's warning removed from in
front of it. That asymmetry is why the assertion is acceptable where a guess
would not be.

---

## D-024 — an assignment is declared intent, and it is not the registry D-018 refused

**Status:** accepted. Filed by P08, because it looks like a contradiction and
is not.

D-018 refused to keep a consumer **registry** — a table of who is holding
what — and the reasoning was specific: in the v1 host-bind shape nothing
tells srdm who mounted what, a consumer's bind is at a path srdm never chose,
and a table srdm maintained itself could only be a second opinion about a
fact the kernel owns. `internal/consumer` answers that question fresh at
every ask, and the registry IS the resolution.

P08 writes `internal/assign`, which is a durable per-profile document listing
servers. It has to be said why that is a different thing, because the next
person to read both will otherwise conclude one of them is wrong.

**They answer different questions.** "Who is holding this generation" is a
fact about the kernel — the pages are held by a mount, and only the mount
table knows. "Which servers should be reading this profile's content" is a
fact about **what an operator asked for**, and nothing else in the system
knows it. A mount table can say a server HAS a generation; it can never say
it SHOULD.

Three things need the second question answered and cannot derive it:

- `activate` re-points every assigned server. Without a record it would have
  to guess the cohort from the mounts, which means an operation's effect
  would depend on which servers happened to be running when it ran.
- the boot path republishes "assigned generations" — the master plan's own
  words — and after a reboot there are no mounts at all to infer from.
- `gc` must not collect a release a profile is on, which is a statement
  about intent even when nothing is published.

**The pair is the design.** Intent is recorded, reality is measured,
reconciliation is the comparison. The failure D-018 exists to prevent is
recording reality; the failure this exists to prevent is having to measure
intent. Neither implies the other, and a system that got them the wrong way
round would be both unable to restore itself and confidently wrong about what
it was holding.

---

## D-025 — v1 has no daemon; the CLI is one-shot under a lock

**Status:** accepted. Confirmed 2026-08-04, before P08b — which is why P08b
builds a boot-restore `oneshot` unit and a repair path invoked from the CLI
and `doctor`, not a socket. Every purpose the master plan lists for a daemon
was v2, already a unit, or bought nothing over an `flock`; see below.

The master plan lists `daemon` among the CLI subcommands, puts an admin
socket at `/run/srdm/admin.sock` (0600, root), and says "the CLI refuses when
the daemon is down except `doctor --offline`" (§Process and package layout).
P08 implements every v1 operation as a one-shot root process instead, and the
question is whether that is an omission or the right answer.

**What a v1 daemon would actually be for**, taken one purpose at a time:

- **The provider socket** — v2. Nothing in the host-bind shape speaks a
  protocol to anybody.
- **Per-start lease resolution** — v2, for the same reason.
- **Boot restore** — a `oneshot` unit ordered `After=local-fs.target`, which
  the master plan itself specifies as a *unit* rather than as daemon work.
- **Serialization** — real, and the whole of it in v1. Two operations
  interleaving would each succeed at steps that contradict the other's.
- **Being the thing the CLI talks to** — which is a consequence of having a
  daemon, not a reason to have one.

Serialization is the only live purpose, and an `flock` on `/run/srdm.lock`
provides it with no long-lived process, no socket, no protocol, and no
"refuses when the daemon is down" failure mode — a mode which, on a node
where the daemon has crashed, refuses exactly the operations an operator
needs in order to fix it.

**Proposed:** v1 ships no daemon. `internal/adminapi` stays a doc-only
package, `daemon` stays a named verb that says what it is waiting for, and
the socket arrives with the provider protocol that needs it.

**Why this must be confirmed rather than assumed**, in the same sense D-003
had to be: it decides whether srdm is a service an operator installs and
supervises or a command they run. That is the difference between adopting it
by running one command and adopting it by taking on a daemon — and it is
much cheaper to add a daemon in front of these operations later than to
discover the operations were shaped around a socket that bought nothing.

**Cost, stated:** the lock is per-node rather than per-profile, so two
profiles cannot be operated on concurrently. On a node with one game that is
free; on a node with several it is a serialization nobody asked for, and the
fix is a per-profile lock file rather than a daemon.

---

## D-026 — a durable copy of the profile document, keyed by id

**Status:** accepted. Filed and confirmed 2026-08-04, discovered while
building P08b's boot restore.

Every operation that takes `--profile <file>` loads it fresh from the path
an operator gave and never keeps a copy; nothing durable holds a profile
document, only its `id` — in the assignment, in a published record, in the
journal. That was never a problem, because every reader of an id so far has
also been an operator who could be handed the file again.

`srdm-restore.service` breaks that. It runs `After=local-fs.target`, with no
operator and no `--profile` flag — its only input is `internal/assign`'s own
statement that a profile has a release, and the assignment names the profile
by id alone. Reaching `internal/opctl.Restore` from a boot unit means
answering "what does profile `soulmask` classify its content as" with
nothing but the id, and there was no answer to give.

**Accepted:** `cfg.ProfilesDir()` (`<state-dir>/profiles/<id>.json`) keeps
the last profile document that successfully drove a state-changing
operation, written by `newOpEnv` at the same moment as everything else
durable — as a side effect of the operation that made it true, the same rule
`internal/assign` and the published record already follow. No new verb, no
separate "register this profile" step an operator can forget to run before
the one boot that needs it.

**Why this belongs here and not as an assumption:** a wrong guess here is
silent in exactly the way D-025's was not. Guessing `daemon` down would have
produced `EROFS` starts and drift — an error at the door. A profile document
that goes stale (an operator edits the source file and forgets to re-run any
verb, or the state dir predates the last edit) would republish the WRONG
classification with no chown-walk-style refusal anywhere to catch it — the
class boundaries, the memory floors and the noexec bit all come from the
profile, and every one of them would be silently the old ones. Boot restore
therefore treats a missing durable copy as a hard failure per profile
(reported, not guessed past), and republishing is the one path that keeps
the durable copy honest going forward: every successful `activate` (and
boot's own reuse of it) rewrites it.

---

## D-027 — overlayfs copies up on a NO-OP chown; rejected for `ro`, adopted for `rw`

**Status:** accepted, from measurement. Probed 2026-08-04 on the case-study
host (kernel `7.1.3+deb13-amd64`) in a privileged container on the host
kernel, because the answer decides an architecture and could not be reasoned
out — the same rule that produced D-011, D-015 and D-019.

**The proposal.** Put a writable overlay layer on top of the read-only
generation, so vanilla Wings' pre-boot chown walk sees a writable filesystem
and unexpected consumer mutations land harmlessly in the upper layer instead
of failing the server's start. It would remove F1 from the MVP path.

**The measurement.** `lowerdir` = the generation, `upperdir`+`workdir` on
ext4 and on tmpfs, 8 MiB file already owned exactly as the chown would set
it. Verdict column is **allocated blocks**, not apparent size: a metacopy'd
upper file must *report* the lower file's size for the merged view to be
correct, so `st_size` cannot distinguish the two and `st_blocks` can.

| operation | default opts | `metacopy=on` |
|---|---|---|
| chown to the **same** owner | **full 8 MiB copied** | metadata only (0 KiB tmpfs / 4 KiB ext4 inode overhead) |
| chmod to the **same** mode | **full 8 MiB copied** | metadata only |
| `touch` (mtime only) | **full 8 MiB copied** | **full 8 MiB copied** |
| chown to a different owner | full 8 MiB copied | metadata only |
| read | no copy-up | no copy-up |
| write | full copy-up | full copy-up |

**overlayfs does not compare values.** Any `setattr` touching uid, gid or
mode forces copy-up whether or not it changes anything; the no-op case is
not special-cased. Confirmed end to end by simulating the walk: a 40-file,
11 MiB tree **already owned `1000:1000`**, walked with
`find … -exec chown 1000:1000 {} \;` exactly as vanilla Wings does,
duplicated **all 40 files and the entire 11 MiB** into the upper layer.

Two further measured facts:

- **`metacopy` is OFF by default** (`/sys/module/overlay/parameters/metacopy`
  = `N`). It is a mount option that must be asked for, and it does **not**
  cover timestamps — `touch` still forces a full data copy even with it on.
- **An overlay cannot be its own upper**: `upperdir` on an overlayfs
  (a container's own root) is refused outright — *"filesystem on … not
  supported as upperdir"*. Upper must be ext4/xfs/tmpfs.

`st_ino` in the merged view stayed **stable** across copy-up, so the
inode-identity test this idea was proposed to be judged on actually passes.
It is the wrong test: what matters is whether the *page cache* is shared,
and after a data copy-up it is not, whatever the inode number says.

**Rejected for `ro`.** srdm exists so that N containers share one copy of
the pages. A chown walk over a merged view duplicates the whole tree per
server, synchronously, at every server boot — the precise failure srdm is
built to prevent, reintroduced at the worst moment. `metacopy=on` reduces it
to metadata, but it is off by default, has to be enabled host-wide, does not
cover `touch`, and buys back only the ownership self-repair that
`check_permissions_on_boot: false` gives up for free — while F1 gives it up
for nothing. Not worth a mount layer per server plus re-deriving teardown
safety (D-012/D-018/D-019) against a mount that pins lower inodes.

**Adopted for `rw`.** The same measurement is a *recommendation* here, and
the "write → full copy-up" row is the feature rather than the cost: a
generation exposed writable today has to be UNSEALED and chowned in place
(D-020/D-022), which marks it `dirty_capable`, bars it from being shared or
used as a source, and limits it to exactly one consumer. Under an overlay
the updater's writes land in a per-server upper, bounded by what it actually
touches; the lower generation stays sealed, pristine and shared; other
servers reading it are unaffected; and `harvest` reads the merged view. The
review that rejected "overlayfs with tmpfs upper then commit"
(`../wings-cgroups/shared-ramdisk-update-lifecycle-1-codex.md:237`, scored
48/100) rejected the **commit** half — whiteouts, upper/lower
reconciliation, non-atomic disk commit. That does not apply: srdm's commit
path is `harvest`, which re-walks, re-hashes and promotes through the same
`store.Promote` a staged release uses.

**aufs is not an option and does not need evaluating.** The kernel offers
only `overlay` in `/proc/filesystems`; aufs was never merged into mainline
and Debian carries no module for it. It is not a fallback, it is absent.

---

## D-028 — an overlay holder is INVISIBLE to superblock matching; teardown must learn `lowerdir`

**Status:** accepted, from measurement. Probed 2026-08-04 while carving P10,
before writing any of it. `tools/overlay-holder-probe.sh` re-runs it.

D-027 adopted an overlay for `access: rw`. This is the constraint that
adoption carries, and it would have shipped as a silent leak if the shape had
been assumed instead of measured.

**`internal/consumer` matches on the superblock** — `major:minor` — because a
consumer's *bind* is at a path srdm never chose while the device number is the
same on both sides of a bind (D-012, D-018). **An overlay is not a bind, and
that reasoning does not transfer.**

Measured:

```
the RO bind  : 2554 2468 0:380 /root /t/expose  ro - tmpfs   tmpfs
the overlay  : 2557 2468 0:390 /     /t/merged  rw - overlay overlay
                          ^^^^^                       lowerdir=/t/expose,...
```

The overlay reports **its own device** (`0:390`), never the lower's
(`0:380`). A file read through it has `st_dev` of the overlay. The lower
device number appears **nowhere** in the mountinfo line — the only trace of
the generation is the `lowerdir=` **path** in the mount options. So the guard,
as written, sees nothing.

And the consequence, measured end to end rather than inferred:

```
generation tmpfs unmounted OK          ← the unmount SUCCEEDS
content still readable through the overlay?  payload   ← and frees nothing
```

That is precisely the D-019 shape: an operation that reports success, returns
no memory, and leaves the content readable to a consumer — with the guard
reporting "nothing is holding this". Teardown would be refused by nothing.

**Accepted:** `internal/consumer` gains a second recognizer. An `overlay`
mount whose `lowerdir=` (any element of a colon-separated list) resolves under
an srdm root counts as a holder of that generation, exactly as a bind of its
superblock does. The two recognizers are complementary and the asymmetry is
the point:

- a **bind** is matched by *device*, because its path is unpredictable;
- an **overlay** is matched by *path*, because its device is uninformative —
  and the path is reliable here precisely because `lowerdir` is *chosen by
  the mounter* and names the source, which a bind's target never does.

**Residual, stated rather than discovered:** an overlay whose `lowerdir`
points at some *other* bind of an srdm path, made at a path srdm does not
recognize, is still invisible. D-019's rule applies unchanged — over-filtering
is a silent leak, under-filtering is a visible refusal — so the recognizer
must err toward counting a holder, and publication's private op root
(`MS_PRIVATE`, D-019) is what keeps such copies from being obtainable in the
first place. An oracle asserts the recognizer sees a real overlay in a real
second mount namespace, which is the only way this is known rather than
believed.
