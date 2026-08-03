# srdm decisions inbox

Product calls, one `D-<NNN>` each. A decision is a *product* gap — a name, a
contract, a user-facing choice — recorded and worked around, never a reason
to stop. Mechanical blockers are BLOCKED exits instead.

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

**Status:** open, default 3. Filed by P01 on arrival, per the handoff.

The master plan's default is "≥ 3 retained, immutable, hash-verified"
(§Defaults), with the count itself `[open]` (§Open questions 3).

**Worked around in P01:** retention is not implemented, so no number is
baked in anywhere. `srdm store list` shows what exists; nothing is ever
deleted. The garbage collector arrives later and will read the count from
config rather than a constant.

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
