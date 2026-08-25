# CIU — Design Notes (non-normative)

This file is for **considered-but-not-built** options and open design
questions — things worth writing down so the next person (or session) doesn't
re-derive them from scratch, without pretending they're implemented. It is
explicitly **not** normative: `docs/SPEC.md` is the single source of truth for
what CIU actually does. Entries are dated; nothing here should be read as a
commitment or a roadmap item unless a SPEC section says otherwise.

---

## D1 — Where per-container cgroup settings actually live (2026-08-03)

Prompted by a design discussion around S15.16 (`mem_min`): if `cgroup_parent`
only names a *shared* tier slice (e.g. `dev-background.slice`, used by many
containers at once), what is the actual per-container cgroup, and where do
`mem_limit`/`mem_reservation`/`blkio_config` land?

### The answer: Docker already creates one, for free, every time

With the systemd cgroup driver (this host: cgroup v2), Docker/containerd/runc
create a **transient systemd scope** for every container it starts —
`docker-<full-container-id>.scope` — regardless of whether `--cgroup-parent`
names anything at all. This scope is nested under whatever `cgroup_parent`
you gave (or under `system.slice` by default), and it is genuinely
per-container: every process the container runs (PID 1, an entrypoint
script, the actual workload) lives inside it, exactly as shown by a real
Wings-managed tree:

```
wings.slice
└─wings-b87c0a5b23874a1c8863ff23e6800a1d.slice      ← Wings-created, PER-SERVER slice
  └─docker-0dce21ef8da51c53ba2ae0e6532f487d....scope ← Docker-created, PER-CONTAINER scope (automatic)
    ├─19528 /usr/bin/tini -g -- /entrypoint.sh
    ├─19678 /bin/bash /entrypoint.sh
    └─20006 the actual game server process
```

Docker writes everything it supports (`--memory`/`mem_limit` → `memory.max`,
`--memory-reservation`/`mem_reservation` → `memory.low`, `--cpus`/`cpu_*` →
`cpu.max`/`cpu.weight`, `--blkio-weight`/`--device-*-iops`/`--device-*-bps` →
`io.weight`/`io.max`) directly onto **this scope** — not onto the parent
slice. So for everything CIU's S15 governance already injects today
(`mem_limit`, `mem_reservation`, `blkio_config` incl. S15.14/S15.15's
`io_weight`/`read_bps`/`write_bps`), the per-container scope Docker already
creates automatically **is** the per-container cgroup — no extra slice
needed, and CIU doesn't need to (and doesn't) create anything beyond naming
`cgroup_parent`.

### What's actually missing: keys Docker's API has no hook for at all

`memory.min` is the one governance value with **no Docker/compose field
whatsoever** — not "hard to reach," genuinely absent from `HostConfig`. The
per-container scope has the real `memory.min` file (every cgroup does), but
there is no Docker-side way to ask it to write anything there. Two ways to
attach it to something per-container:

1. **Reconfigure the scope Docker already made, synchronously, right after
   start.** `docker-<id>.scope` is a REAL (if transient) systemd unit the
   moment the container exists — `systemctl set-property`/D-Bus
   `SetUnitProperties` work on it exactly like any other unit; nothing needs
   to be pre-created. Applied synchronously, as the last step of CIU's own
   start sequence (not on a periodic timer), this closes the window a
   sweep-based approach leaves open: `mdt-host-slices.timer` polls, so a
   container can run unbounded for up to a full `SWEEP_INTERVAL` (tens of
   seconds) before its caps land — fine for the two cases that timer exists
   for (buildx workers, the devcontainer's own scope, both best-effort dev
   tooling), but "already hurt prod before the limit applied" is a real
   failure mode for anything higher-stakes. This is simpler than option 2
   below (a reconfigure, not a create-with-properties dance) and is the
   better mechanism **for attaching the value to the right per-container
   target** — but see the ancestor-chain problem below: it does NOT, by
   itself, make `memory.min` actually protect anything.
2. **A per-container intermediate slice** — what Wings does above:
   `wings-<server-uuid>.slice` is a slice Wings itself creates (via systemd
   D-Bus `StartTransientUnit`/`SetUnitProperties`, at server-create time,
   root/host-privileged), with `MemoryMin=` set **on the slice**, and the
   container's `--cgroup-parent` points at that slice rather than directly
   at the tier slice. Docker's automatic per-container scope then nests one
   level deeper. More moving parts than option 1 (create vs. reconfigure),
   and same caveat applies.

### The ancestor-chain problem — confirmed, not hypothetical (2026-08-03)

Neither option above is sufficient by itself, and this isn't a corner case:
cgroup v2's memory protection is defined top-down. Per the kernel's own
documentation, **effective `memory.min`/`memory.low` protection at any
cgroup is bounded by the SAME property's value on every ancestor cgroup, all
the way to the root** — an ancestor with no value set (default `0`) provides
zero protection to itself, which caps everything below it to zero
regardless of what a deeper cgroup declares. Setting `MemoryMin=2G` directly
on a container's own scope (option 1) or on a purpose-built per-container
slice (option 2) has **no effect whatsoever** if anything above it in the
chain lacks its own floor — this applies identically to whichever option
attaches the value, and to a per-container OR a shared-slice target alike.

This project's own shipped dev-tier config is a live instance of the
problem: `host-setup/units/dev.slice.in` (parent of both dev-tier slices)
sets ONLY IO ceilings — no `MemoryMin`/`MemoryLow` anywhere. `units/
dev-background.slice.in` sets `MemoryHigh`/`MemoryMax`/`MemorySwapMax` but
**no `MemoryMin` and no `MemoryLow`**. `units/dev-interactive.slice.in` sets
`MemoryLow` but still no `MemoryMin`. So today, on a host running this
config as shipped, `memory.min` protection is a no-op **anywhere** under
`dev.slice` — and the identical caveat already applied to the
**pre-existing** `mem_reservation` key (`memory.low`, same ancestor rule),
which has been injecting a value into every governed container's compose
config since before `mem_min` existed, with no real kernel effect under
`dev-background.slice` for the same reason. See `docs/SPEC.md` S15.16's
warning block for the full write-up (added alongside this note).

Closing this for real needs every ancestor from the cgroup root down to have
its own nonzero `MemoryMin=`/`MemoryLow=` — a one-time, host-wide, shared
static-unit change, not something any per-deploy write (CIU's or otherwise)
can substitute for.

### The gap this leaves in what CIU shipped (S15.16)

Two SEPARATE gaps, not one — solving either does not solve the other:

1. **Granularity**: CIU's `mem_min` preflight checks the *already-configured*
   `cgroup_parent` slice's live `MemoryMin=`, which in CIU's actual usage is
   normally a *shared tier slice*, not a per-container one — so today it's
   honestly a whole-tier floor check ("does the shared slice have headroom
   for at least one container"), not a per-container guarantee. Fixing this
   means the per-container target discussed above (option 1 or 2) — a
   privileged write, see D2.
2. **Ancestor-chain coverage**: the preflight itself only probes the ONE
   slice `cgroup_parent` resolves to, not the rest of the chain up to root —
   so it can report "OK" even when the real effective protection is zero (as
   it always currently is under `dev.slice`, per above). Fixing THIS doesn't
   need a privileged write at all — it's a read-only check that could walk
   `systemctl show <unit> --property=MemoryMin` up each `PartOf`/parent slice
   in turn. Not implemented; flagged here as an open question rather than
   assumed-wanted, since even a fully-accurate check only ever *reports* the
   host-wide provisioning gap above, it doesn't close it.

---

## D2 — Options for CIU to write cgroup values Docker doesn't expose (2026-08-03)

Follow-on from D1: if CIU ever wanted to actually **create** a per-container
slice (or otherwise write a property Docker can't express), it needs a
privileged write somewhere in the chain — CIU itself runs unprivileged, and
in this project's devcontainer specifically: `CgroupnsMode=private`, no host
cgroupfs bind, no D-Bus socket, cgroup2 mounted `ro`, `systemctl` a
non-functional shim (see `[[devcontainer-docker-environment]]`, and the
`_systemd_is_pid1()` fix landed alongside S15.16). None of the options below
are built; this is purely a survey for whenever/if that changes.

**Context: `docker-scope-default-limits.conf.in`.** This companion ships a
*static*, install-time-only systemd drop-in
(`/etc/systemd/system/docker-.scope.d/50-default-limits.conf`, matching every
`docker-<id>.scope` via systemd's truncated-name-prefix convention) as a
generous backstop ceiling for the "typo'd/nonexistent slice fails open"
case (D-G8). It needs zero runtime coordination and proves the family's
general preference for static provisioning over live daemons wherever a
static mechanism will do — worth keeping in mind against the options below,
several of which are NOT static.

| Option | Mechanism | New host setup? | Survives `docker.sock` being withheld (DinD)? |
|---|---|---|---|
| **A. Throwaway helper container** | `docker run --cgroupns=host [+ /run/dbus mount] ...` over the *existing* `docker.sock` — the same pattern `privileged_rmtree` (`engine.py:379`) already uses for CIU-9 | No | **No** — this IS the trust `docker.sock` access already grants; removing that socket removes this option by design |
| **B. Narrow companion daemon** | A small root-owned process, one bind-mounted UNIX socket, a tiny allowlisted verb set (e.g. "create/reconfigure a per-container slice under `dev-background.slice` with this `MemoryMin=`") — evolving `mdt-host-slices.service` from a periodic sweep into request/response, or reusing wings-cgroups' own slice-manager daemon design/protocol | Yes — new daemon + protocol + install step | **Yes**, if that one narrow socket is deliberately mounted in |
| **C. sudo/polkit delegation to a wrapper script** | A `sudoers.d` rule scoped to one wrapper that validates the slice name against a strict pattern before calling `systemctl set-property`/`StartTransientUnit` | Yes — sudoers/polkit rule | Only when CIU can reach the host's own sudo directly (native host, or over SSH — CIU already has `transport_ssh.py`); not from an isolated container with no escape hatch |
| **D. Verify-only, forever** | What's actually shipped (S15.16): CIU never writes, only checks a pre-provisioned floor and fails closed if it's missing/inadequate | No | Trivially yes — nothing to escape |

**DooD vs. DinD, explicitly.** Option A is why S15.12/S15.16 work at all
*today*: this devcontainer's `docker` group membership already **is**
host-root-equivalent (trivial to get a host root shell via
`docker run -v /:/host ...`), so spawning a privileged helper adds no new
privilege. If an operator deliberately removes that access to get real
tenant isolation, the devcontainer necessarily becomes Docker-**in**-Docker
instead — its own nested dockerd, containers as children of its own cgroup,
no sibling relationship to the host's real tree. In that world:

- Everything Docker's own flags already support (`mem_limit`,
  `mem_reservation`, CPU, `blkio_config` incl. `io_weight`/`read_bps`/
  `write_bps`) keeps working unchanged — enforced by the nested dockerd
  itself, no host access needed at all, matching D1's finding that these are
  already correctly per-container via the automatic scope.
- A true per-container `memory.min` becomes unreachable — arguably
  correctly so: a hard memory floor is a **host-level, multi-tenant
  arbitration decision** (whose container gets protected memory at whose
  expense), and a sandboxed tenant self-granting one would defeat the
  isolation model's purpose. The devcontainer's own outer bound (whatever
  cgroup it was itself placed under, host-setup's doing) already caps what
  it can hand out to anything nested inside it.

**Recommendation (unchanged, not adopted as an action item):** don't build B
speculatively — it's comparable in scope to wings-cgroups' own slice-manager
daemon, and only pays for itself if a devcontainer posture that actually
revokes `docker.sock` is wanted. Leave S15.16 as D (verify-only, fail closed)
until/unless that changes; it's honest about the boundary and costs nothing
extra given A already covers today's (trusted) DooD posture without any CIU
code change. Note this recommendation is now independent of, not a
substitute for, the ancestor-chain problem in D1: even a fully privileged
write path (A, B, or C) only ever sets ONE more link in a chain that — on
this project's own hosts today — is broken at `dev.slice` itself. That part
is a host-side static-unit fix no CIU-side mechanism, privileged or not, can
stand in for.

---

## D3 — Which cgroup settings actually need the D1 ancestor-chain treatment (2026-08-03)

Follow-on question: is the "every ancestor must also budget it, or it's a
silent no-op" rule specific to `memory.min`/`memory.low`, or does it apply
more broadly? Per the kernel's own cgroup v2 documentation, it is specific
to the two **protection** knobs — everything else CIU's governance touches
falls into one of two other families that do NOT have this trap:

| Family | Examples (CIU keys) | Ancestor requirement |
|---|---|---|
| **Protection** (`memory.min`/`memory.low`) | `mem_min` (S15.16), `mem_reservation` | **Chain-wide** — effective protection is bounded by EVERY ancestor's own value; one unset ancestor zeroes everything below it (D1). |
| **Limit / ceiling** (`memory.max`/`memory.high`, `cpu.max`, `io.max`, `pids.max`) | `mem_limit`, `read_iops`/`write_iops`/`read_bps`/`write_bps` (S15.15) | **None** — a limit set at ANY level applies (the tightest wins); an unset ancestor just means "no additional restriction from here," never "zero flows through." `mem_limit` under `dev-background.slice` (no `MemoryMax` set there) already works today for exactly this reason. |
| **Proportional share** (`cpu.weight`, `io.weight`) | `cpu_weight`, `io_weight` (S15.14) | **None** — weight is arbitrated independently at each level, among siblings under the SAME immediate parent. An ancestor's own weight affects how much of *its* parent's share it gets, but doesn't zero out a descendant's relative apportionment among its own siblings. (The separate BFQ-vs-iocost `io.weight`/`io.bfq.weight` scheduler mismatch from S15.14 is a completely different, orthogonal problem — which FILE is read, not whether the chain budgets it.) |

**Practical upshot:** of everything CIU's S15 governance injects, `mem_min`
and `mem_reservation` are the ONLY two keys with the D1 silent-no-op trap.
`mem_limit`, `cpu_weight` and everything under S15.14/S15.15 already work
correctly as shipped, regardless of what `dev.slice`/`dev-background.slice`
themselves do or don't set — no host-side fix is needed for those.

---

## D4 — Auditing for ancestor-chain no-ops belongs to mdt, not CIU (2026-08-03)

Given D3, a live host can end up with an operator declaring `mem_min`/
`mem_reservation` for a stack while the dev-tier slices genuinely have no
floor anywhere in the chain (exactly this project's own current state) — a
silent misconfiguration nobody would notice without deliberately checking.

Detecting this is squarely **mdt's** job, not CIU's: mdt already owns the
host-wide slice tree (it wrote the units in the first place) and already
runs a periodic sweep for a related purpose (`mdt-host-slices.timer` →
`mdt-apply-dev-caps.sh`, catching containers/scopes created after boot).
Extending that same sweep to walk the slice tree and `[WARN]`-log to the
journal whenever a descendant requests `memory.min`/`memory.low` that no
ancestor actually budgets is a natural fit — pure diagnostics, no new
privilege surface beyond what the timer already runs with (root), and
directly in the spirit of D-G8's "never truly unbounded, no exceptions, but
loudly" philosophy.

**The walk itself is cheap.** systemd's slice-naming convention
(`systemd.slice(5)`) makes a unit's ancestor chain fully derivable from its
own name — `dev-background.slice`'s parent is always `dev.slice` (strip the
last dash-separated component), whose parent is the implicit root. No D-Bus
tree traversal is needed, just string-splitting plus one `systemctl show
<ancestor> --property=MemoryMin` (or `MemoryLow`) per level, exactly the
call `governance.check_slice_memory_min` already makes for one slice.

This is an mdt-project feature idea, not a CIU one — it would want to live
in that project's own TODO/backlog rather than here. Flagged in this file
because it directly bears on why CIU's own S15.16 check is limited (D1); not
implemented, and not this session's to build.

---

## D5 — CIU on non-mdt hosts: the read-only chain-walk is free; the write-capable daemon still isn't (2026-08-03)

D4's timer-based audit only helps on hosts that actually run mdt. CIU is
explicitly meant to work on **any** host (`governance.py`'s own docstrings:
"CIU ships as a wheel to arbitrary hosts... never requires mdt") — on a host
with no mdt at all, there is no timer to catch a D1-style no-op, and the gap
is invisible unless CIU itself looks for it. Two genuinely separate pieces
here, with very different cost:

1. **Read-only ancestor-chain walk — cheap, no new privilege.** S15.16's
   `check_slice_memory_min` currently probes only the ONE slice
   `cgroup_parent` resolves to (D1's "ancestor-chain coverage" gap). Per D4,
   walking the rest of the chain is pure string-splitting + more
   `systemctl show` calls — the exact same *read-only* mechanism already in
   use, just looped up to root. This closes the gap **without** touching the
   privileged-write question in D2 at all: it can report an honest verdict
   ("this declared floor is a no-op — `dev.slice` has no `MemoryMin=`")
   wherever mdt isn't present to catch it. This is a real, bounded candidate
   to actually build, independent of everything below.
2. **Write-capable daemon (D2 Option B) validating-while-applying —
   unchanged, not built.** If CIU ever gained a companion daemon for the
   write side, having it check the ancestor chain at the moment it applies a
   value falls out almost for free — it already has to inspect the chain to
   decide what to do, so "does this actually take effect" and "apply it" are
   naturally one piece of logic, not two. This validates the instinct behind
   the question, but doesn't change D2's recommendation: don't build it
   speculatively.

**Recommendation:** (1) is worth doing on its own merits whenever wanted —
small, safe, closes an honest gap, needs no new privilege and no daemon.
(2) stays exactly where D2 left it.

**Status: (1) implemented (2026-08-03).** `governance.slice_ancestor_chain`
+ `governance.check_memory_min_ancestor_chain`, wired into
`deploy.governance_slice_preflight` in place of the single-slice
`check_slice_memory_min` call. See SPEC.md S15.16 (updated in the same
pass) for the normative behavior.

---

## D6 — Survey: which existing warn/error sites are candidates for S10.6 (2026-08-03)

S10.6 (`warn_policy.warn_or_raise`, `CIU_EXIT_ON`) landed wired
into exactly ONE site so far: S15.16's mem_min ancestor-chain finding (D5).
Per the standing "fail first, fail early, nothing gets hidden" principle,
more of CIU's existing warn/error sites are plausible candidates — surveyed
here rather than blindly retrofitted, since each one is its own judgment
call about whether "opt-out via one blanket flag" is the right shape for
that specific failure:

| Site | Current behavior | S10.6 candidate? |
|---|---|---|
| **S15.16 mem_min ancestor-chain** (D1/D4/D5) | `warn_or_raise` (this session) | Done. |
| **S15.G9-1 missing governance slice** (`deploy.governance_slice_preflight`, the sibling check right next to S15.16) | Unconditional `raise ValueError` | **Deliberately NOT converted.** A missing slice means the container is placed nowhere governed AT ALL (S15.8) — no `mem_limit`, no `blkio_config`, nothing, since Docker/systemd auto-creates a fully unlimited transient slice. That's a strictly worse outcome than mem_min's "some protection, just not this one floor" — letting one blanket flag silently downgrade THIS to a warning feels like the wrong default to make easy. |
| **S15.13 unknown-key warning** (`governance.resolve_config`) | WARN by default, raise only via opt-in `strict_unknown_keys=True` | **Deliberately NOT converted** — this one is the OPPOSITE shape of S10.6's default and SPEC.md says so explicitly: *"the DEFAULT behavior stays a warning, never a raise... forward-compat is preserved"* for a newer stack config's key running against an older CIU. Wiring S10.6's default-enabled posture through here would silently break that documented forward-compat guarantee for every existing CIU install with a stray/newer key anywhere, the moment they upgrade — a real behavior change nobody asked for, not a bug fix. If this is ever revisited, it needs its own explicit decision, not a side effect of S10.6 existing. |
| **CIU-9-era `[WARN] declared secret consumed by no channel`** (S4.20, `engine.py`) | Unconditional WARN, never raises | Plausible candidate — a declared-but-unconsumed secret is arguably a real config mistake. Not converted this pass; needs its own look at whether every current caller is prepared for it to become fatal by default. |
| **`[WARN] configfile selector matches no rendered services`** (`composefile.py`) | Unconditional WARN, never raises | Same shape as above — plausible, not converted this pass. |
| **Optional-dependency / fio-engine-fallback warnings** (`engine.py`, `governance.py`'s `select_fio_engine`) | Unconditional WARN, informational | Not candidates — these describe a degraded-but-working mode (psync fallback, missing optional deps), not a misconfiguration; forcing them fatal by default would make a working, if suboptimal, run into a hard failure for no configuration mistake at all. |

**Pattern for adding a new one:** replace the bare `raise ValueError(msg)`
with `warn_policy.warn_or_raise(msg)` (same message text — the exception
you get when warnings-as-errors is enabled is byte-for-byte what was already
being raised) and update the SPEC section's outcome bullet to name S10.6,
exactly as done for S15.16 in this pass. The judgment call is entirely in
"should this specific finding be softenable via one global flag," not in
the mechanism itself.

---

## D7 — Where a project's test definitions belong: the where/what/how split (2026-08-06)

Prompted by a consumer-side discussion (dstdns) about which tool should own a
project's test declarations. The observed problem: dstdns's gate lived as a
single opaque argv inside its *automation* tool's config, so a developer
working the repo by hand — using CIU but deliberately not the automation tool
— had no way to discover or invoke "the project's tests" without reading the
automation tool's config. The proposed remedy was "move the test config into
CIU". That is right for part of it and wrong for part of it, and the boundary
is worth writing down before anything is built.

### The standing constraint that decides it

CIU, and the estate's other tools, are **stand-alone tools that must not
depend on each other.** Synergy by design is fine; a hard dependency is not.
The operational test for whether a capability belongs in CIU:

> **Would a project using *only* CIU still get value from this?**

Isolated instances pass (any CIU project wants them). A changed-line coverage
floor also passes, but not *as CIU* — as a library any repo can install.
Dispatch/review/merge orchestration fails: it is only meaningful once you have
adopted the automation tool.

### One gate argv is actually three questions

A gate string like

```
testing-exec.sh 'cd <worktree> && MOCK_MODE=true pytest tests/unit -q'
```

fuses three separable concerns:

| Question | Example | Whose domain |
|---|---|---|
| **WHERE** to run | which container, which network, which instance, which image | **CIU** — it already owns every one of these facts |
| **WHAT** to run | marker expressions, per-stack suites, lanes | **the project** — which, for a CIU project, means CIU's config layer |
| **HOW** to judge | coverage floor, mutation score, canary rigor | **neither** — a testing library, installable standalone |

WHERE is unambiguously CIU's: instance identity, network naming, profile
narrowing, and image resolution are already resolved facts inside CIU, and
every other consumer of them reaches in and re-derives them badly. WHAT is
project data that has to live *somewhere the project already configures*, and
for a CIU project that is the existing per-stack config layer. HOW is the one
that must stay out: CIU has no business knowing what a mutation score is.
Methodology is not topology, and folding it in would make CIU's dependency
closure grow with every testing technique a consumer adopts.

The resulting arrows, none of which point from a general tool to a specific
one: an automation tool may execute a project-declared argv (which may happen
to invoke `ciu test`) and may import the testing library; CIU imports neither;
the testing library imports neither. A project can adopt any one of the three
alone.

### Sketch, not a commitment

A per-stack `[test]` table in the stack's existing `ciu.defaults.toml.j2`,
plus a verb that resolves *where* and delegates *how*:

```toml
[test.lanes.quick]
argv    = ["pytest", "tests/unit", "-q"]
env     = { MOCK_MODE = "true" }
budget  = "60s"

[test.lanes.stack]
argv     = ["pytest", "-m", "integration", "-q"]
requires = ["postgres", "redis"]     # provisioned via the normal profile graph
budget   = "10m"
```

```console
ciu test                      # every stack in this repo, default lane
ciu test --stack worker-io    # one stack
ciu test --changed            # map changed paths -> owning stacks -> their lanes
```

What this would need that does not exist today, listed so the sketch is not
mistaken for a small change:

- **Lane resolution** — selecting a stack's lane and running it in that
  stack's own instance rather than a repo-global runner.
- **Changed-path → stack mapping** for `--changed`. Cheap only because stacks
  are already directory-scoped; still a new inference CIU does not perform.
- **A provenance precondition — DONE (S17, 2026-08-08/09; machine-readable
  output DONE, S17.3/S17.4, 2026-08-11/12).** `bake` now stamps
  `org.opencontainers.image.revision`, and `ciu provenance` (S17.2) refuses a
  live lane against a stale image at TEST time. `ciu test`'s job here shrinks
  to *calling* the existing gate rather than building one — nothing left
  to design. `ciu provenance --json` (S17.3, CIU-20) now emits the closed,
  bounded verdict document a downstream evidence consumer needs — the gap
  this bullet used to point at is closed; the follow-on assay integration it
  unblocks (a Tier-2 adjudicated evidence reader) is assay's own work, not
  named here again.
- **A judged-result contract**, so `ciu test` can surface a testing library's
  verdict without linking against it or parsing its prose. Still open, but no
  longer abstract: this is not a contract CIU has to invent. **assay**, an
  independent testing-rigor library elsewhere in this estate, already ships
  exactly this — a versioned, `jsonschema`-validated verdict artifact
  (`assay run <lane> --verdict-json <path>`) that any reader validates without
  linking against assay (its own design doctrine, A-029). This suggests the
  WHAT layer's sketch above doesn't need its own bespoke shape:
  `[test.lanes.X].argv` can simply BE `["assay", "run", "<lane>", ...]` for a
  project that has adopted assay, with no CIU-side knowledge of assay at all
  — the argv is opaque either way, exactly as the where/what/how split above
  requires. The one addition worth considering, still generic: an optional
  `[test.lanes.X]` key (`verdict_path`, naming or `--verdict-json`, unnamed)
  telling `ciu test` where to look for a judged-result JSON after the argv
  exits, purely as an opaque attachment to its own report — CIU never
  interprets the contents, so a project using a different judged-rigor tool,
  or none, costs nothing and loses nothing. Whether that key is worth adding
  now or only once a real consumer wants it is still open; recorded here so a
  future implementer starts from "there is a working example one directory
  over" instead of designing a contract from nothing.

Deliberately excluded: coverage/mutation/canary implementations, any notion of
a rigor score, and any awareness of an automation tool. If CIU ever needs to
grow one of those to make `ciu test` useful, that is the signal the boundary
above was drawn wrong — reopen this note rather than widening the tool.

---

## D8 — `governance.memory_profile`: KSM is one lever, and `ksm_optin` is the wrong shape (2026-08-06)

S15.11 exposes exactly one memory knob — `governance.ksm_optin`, a single
estate-wide path to an `LD_PRELOAD` shim. Two things make that shape too narrow,
one of them now measured rather than suspected.

### The measured part: the preload shim has a structural blind spot, and a wrapper closes it

`LD_PRELOAD` requires a dynamic loader to run. A statically-linked binary never
runs one, so injection is **inert** for it — consul, vault, otel and minio's main
binary contribute zero KSM savings today, and nothing surfaces that.

The alternative is a wrapper that calls `prctl(PR_SET_MEMORY_MERGE, 1)` itself
and then `execve`s the real program. Whether that works hinged on an open kernel
question: `execve` allocates a fresh `mm_struct`, so the flag survives only if it
is in the kernel's mm-flag init mask.

**Measured 2026-08-06 (kernel 7.1.3, `gcc:13-bookworm`, `LD_PRELOAD` empty):
the flag SURVIVES `execve`.** Three arms — baseline `/bin/sleep` → `no`,
`wrapper --no-exec` → `yes`, `wrapper /bin/sleep` → `yes` with `comm` confirming
the exec. Repeated against a genuinely static target (`ldd: not a dynamic
executable`): **`no` unwrapped, `yes` wrapped.** Reproducer and full numbers live
with the consumer that ran it: dstdns `tools/ksm-optin/ksm-exec-probe.sh` and
`docs/KSM-OPTIN-MEASUREMENTS.md`.

So the wrapper is **strictly more general** than the shim: static and dynamic,
glibc and musl, and with no `-nostdlib` / zero-`DT_NEEDED` constraint on the
injected artifact — it only has to *run*, not to load inside another process's
address space. It is a candidate to **replace** the preload path outright rather
than sit beside it.

**Its real cost is placement, not portability.** A wrapper must become the
container's entrypoint, which collides with images that already declare one or
run `tini` as PID 1. CIU can inject `entrypoint:`/`command:` — but doing so
*overrides* the image's own, which is a far more invasive act than adding an env
var and a read-only bind. That is the design question worth arguing, and it is
per-service, not estate-wide: some images tolerate it trivially, others must not
be touched. A single global key cannot express that.

### The unmeasured part: KSM is not the biggest lever, and is sometimes the wrong one

The largest single memory win this estate has recorded came from a JVM heap
setting, not from page dedup: dstdns's SkyWalking pair went **2.48 → 0.75 GiB**
by fixing an OAP `-Xms2G` default (≈1.7 GiB returned). KSM could not have
recovered any of it. Go services want `GOMEMLIMIT` for the same reason. A
governance table that can express only "KSM on/off" cannot say the thing that
actually mattered.

### Sketch (non-normative)

```toml
[governance.memory_profile.default]
ksm = "preload"            # preload | wrapper | off

[governance.memory_profile.services.consul]
ksm = "wrapper"            # static binary — preload is inert here

[governance.memory_profile.services.skywalking-oap]
ksm = "off"
jvm_heap = "512m"          # emits -Xms/-Xmx via the image's JAVA_OPTS convention

[governance.memory_profile.services.some-go-svc]
go_memlimit = "512MiB"
```

`ksm_optin` becomes the degenerate case of `memory_profile.default.ksm`, and
`exempt_services` becomes `ksm = "off"` per service — so the migration is a
rewrite of existing config into a strictly more expressive form, not a new
parallel mechanism (§4.1-style: one path, not two).

**Open, in rough priority order:** how a `wrapper` strategy composes with an
image's existing entrypoint (probably: refuse rather than override, and say so
loudly, per the CIU-14/CIU-15 fail-closed lesson); where the wrapper binary
itself comes from (the same build-and-cache question as CIU-17's `ciu ksm build`
— a host-level cache keyed by arch and source hash, so worktrees do not each
rebuild it); and whether `jvm_heap`/`go_memlimit` belong in governance at all or
are simply service env, which is a real boundary question and not obviously
CIU's to own.
