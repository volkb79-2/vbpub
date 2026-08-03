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

## D-007 — no changed-line coverage floor in P01's gate

**Status:** accepted, revisit when a Go evaluator exists.

nyxloom offers `coverage_gate.py`, a changed-line coverage floor. It is
Python-specific. Go has `go test -cover` and the tooling to build an
equivalent, but writing one is not P01's package.

**Decided:** `[gates.unit]` declares `asserts = ["tests-pass"]` only.
Declaring `changed-line-coverage` without enforcing it is a declaration
mismatch, and nyxloom's `gate verify` proves that claim with an
uncovered-line canary — a false claim would be caught and would have
laundered every merge until then.

**Compensating control:** `[gates.canary]` and `tools/canary.sh`. Coverage
proves a changed line *ran*; the canaries prove each oracle actually *fails*
when the contract it names is broken, which is the stronger property and the
one hollow tests evade. Eight canaries, one per contract, all currently
rejected.

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
