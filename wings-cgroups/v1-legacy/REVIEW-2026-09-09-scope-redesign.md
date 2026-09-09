# Independent adversarial review — slice→scope redesign, 2026-09-09

Scope: `a30a0db6` (rebase v1.13.1→v1.13.3) and `d34969e6` (slice→scope redesign),
plus the follow-ups `ab92bf84`, `e22a6452`, `6f022dd6`, `4cb2cccf`, `bfb2077b`,
`f405c565`.

Fresh session, no prior context on this work (estate rule: never a fork of the
implementer). Everything below was verified against the code and against live
runs, not against the implementer's self-report.

---

## Verdict: **NOT-ACCEPT** — two blocking findings

The patch series itself is well built. It reproduces byte-identically from a
clean clone, it is green on the project's own gate, the D-Bus interface work is
*correct*, and the design rationale is sound and unusually well documented.

Two things block it:

- **F1** — a real, silent, newly-introduced regression: a routine Panel-side
  settings save wipes most of the properties this redesign exists to apply, and
  can *loosen* a memory ceiling. Empirically demonstrated below, not theorised.
- **F2** — the headline verification claim for patch 0008 is false. I ran the
  mutant: the patch can be fully reverted and the entire test suite, including
  the systemd e2e case named as its proof, still passes. Four separate documents
  assert the opposite.

**Disposition:** `wings-local:1.13.3-cgroup.1` is safe to *keep* as a local
image — it is reproducible, builds green, and nothing is deployed. It is **not
deployable** until F1 has an explicit decision and F2's claims are corrected.

---

## What I verified, and how

| # | Item pressed | Result |
|---|---|---|
| 10 | Full series applies from a clean clone | **PASS — strongest evidence in the set.** Fresh `clone.sh` + `apply.sh` + `test.sh` in an isolated project dir. All 9 patches applied with no fuzz; build, strict vet, unit tests, and both integration compile checks green. Applied tree hash `2b83e7e2…` is **identical** to the working branch `cgroup/v1.13.3`. The exported `.patch` files are a faithful oracle. |
| 7 | `config_docker.go` rebase merge | **CLEAN.** `git diff v1.13.3..HEAD -- config/config_docker.go` removes **zero** upstream lines. `CpuPeriod`, `CpuBurst`, `CpuShares`, `CpuPeriodMicroseconds()` all intact alongside the patch's own `CgroupParent`/`AllowedCgroupParents`/`PerServerSlices`. `ResolveServerCgroupParent` + `ScopeEnsureRequest` + the "override == node value is the opt-out" rule are correct. |
| — | Whole-series upstream deletions | **Accounted for.** Only 11 deleted upstream lines across the entire series: 2 in `go.mod` (dependency promotion) and 9 in `cmd/root.go`, where patch 0009 hoists the `IsRunning` probe into a bounded up-front pass. The error handling is preserved **verbatim** in the new location (`cmd/root.go:213-220`). No silently dropped hunk. |
| 3 | `sysd.go:memoryCurrent` reads `"Scope"` | **Source correct** (`internal/cgroups/sysd.go:257`), and `sliceMemoryMin` correctly reads `"Slice"` (`:224`). Confirmed load-bearing on live systemd: `Scope`→`t 946176`, `Slice`→`Unknown interface`, `Unit`→`Unknown interface`. **But the test does not cover it — see F2.** |
| 8 | `sliceRequest` vs `resolveScope` split | **CLEAN.** `applySliceProps` (`server/slice_phase.go:286-306`) checks `req.Owner == ""` and returns *before* touching `resolveScope`; `resolveScope` is a `Server` method that never reads `Owner` at all, so the undefined-state path does not exist. `beginSlicePhase` uses the same sentinel. |
| 6 | Installer containers lost per-server properties | **REFUTED as a regression.** `STRATEGY.md:148`'s "server **and installer**" sentence is scoped to **T1 placement** and explicitly says "Properties stay host-owned". Placement *is* still applied to installers (`server/install.go:467-473`). The property exclusion is an explicit, reasoned, documented decision at `server/install.go:460-466`. Decided, not silent. |
| 9 | Coverage lane honesty | **HONEST.** `patchstack/assay/assay.toml` documents its own exclusions in-file (sysd.go/ensure.go not in a plain `go test` profile; `cmd/` uncovered; `server/`+`internal/database/` outside `source_roots`). The floor is explicitly labelled "a regression guard sitting just under it, not a quality target… never lower it to make a red lane green." No overstatement found in the lane, `coverage.sh`, or the continuation brief. Correctly scoped as R0+R1 coverage, explicitly **not** mutation testing. |
| 5 | `ForgetServer` wiring / ledger leak | **CORRECT.** Called from `Environment.Destroy()` (`environment/docker/container.go:316`) keyed on `e.Id`, matching the `Owner` key set in `ScopeEnsureRequest`. No leak on abnormal termination — an entry surviving a crash is the documented, conservative direction. (One doc overstatement: see F6.) |
| 4 | Audit-surface regression | **REAL and UNAVOIDABLE.** A scope's lifecycle *is* the container's; there is no stopped-server unit left to query. Correctly recorded as an accepted cost (`TODO.md:44-53`). No code implicitly depends on the old always-queryable behaviour. Residual `systemctl show wings-<uuid>.slice` instructions survive only in the known-deferred doc set (`t2-per-server-placement/README.md:44`) and in the stale e2e harness (see F4). |
| 1, 2 | The two-writer collisions | **REAL, and documented** — `CGROUP-SEMANTICS.md:74-79` (memory) and `:335-366` (IO, with a measured table and an ASCII diagram) plus the egg's own `WINGS_CG_IO_WEIGHT` description. Documentation quality is high. **However, both documents assert a write-order conclusion that F1 refutes.** |
| — | Egg file cleanup | **CORRECT, verified not merely claimed.** `WINGS_CG_IO_BFQ_WEIGHT` and `WINGS_CG_RAMDISK_UNITS` variable definitions are gone; 34 total variables, of which exactly **14** are cgroup variables — matching the corrected `_comment` and `description` counts. Both retirements are explained in place. |
| — | Dangling retired symbols in Go | Clean: no `io_bfq`, no `ramdisk`, no `StartTransientUnit` in production code. **One exception — see F3.** |
| — | `SETUP.md` claimed updated | **TRUE.** Every remaining `io_bfq_weight` / `memory_min_budget` / `budget_policy` / `allowed_ramdisk_units` mention is an explicit retirement/migration note, including a troubleshooting row (`SETUP.md:453`) for the discarded-keys warning patch 0007 emits. Correctly updated. |

---

## BLOCKING

### F1 — `InSituUpdate` silently clobbers the scope properties; a looser Panel value can win

**Severity: blocking.** New in this redesign. Not mentioned anywhere in the
handoff, `TODO.md`, or `CGROUP-SEMANTICS.md`.

**Files:** `server/update.go:52` → `environment/docker/container.go:135`
(`ContainerUpdate` with `e.Configuration.Limits().AsContainerResources()`);
properties applied at `environment/docker/scope.go:32`, called only from
`environment/docker/power.go:94` and `:148`.

`Server.UpdateDataStructure` calls `Environment.InSituUpdate()` on **every**
Panel-side settings save for a running, unsuspended server. That issues a Docker
`ContainerUpdate` carrying the full `container.Resources` struct — `Memory`,
`MemoryReservation`, `CpuShares`, `BlkioWeight`
(`environment/settings.go:105-138`). With the systemd cgroup driver, runc
translates those into `SetUnitProperties` on **the same `docker-<id>.scope`**
Wings just wrote.

Under the retired per-server-slice design this was harmless: Wings' values lived
on `wings-<uuid>.slice`, Docker's on the scope — disjoint units, both persisted,
min-along-path. On one shared unit they collide, and **Docker's write is the
later one**. Nothing re-applies: `applyScopeProps` is wired only to
`ContainerStart` and re-attach, so the drift persists until the next container
start.

**Measured** on the project's own privileged systemd harness (cgroup v2, systemd
driver), simulating Wings' apply then one `docker update`:

| property | Wings set | after `InSituUpdate` | |
|---|---|---|---|
| `MemoryMin`  | 200M | 200M | survives (runc does not set it) |
| `MemoryHigh` | 400M | 400M | survives |
| `MemoryLow`  | 300M | **512M** | **clobbered** by `MemoryReservation` |
| `MemoryMax`  | 450M | **512M** | **clobbered — and LOOSENED** |
| `CPUWeight`  | 800  | **39**   | **clobbered** — derived from `CpuShares=1024` |

And separately, for IO — `docker update --blkio-weight 700` on a scope where
Wings had set `IOWeight=4950`:

```
before:  IOWeight = 4950    io.bfq.weight = 540
after:   IOWeight = 4950    io.bfq.weight = 700     <-- panel value won
```

Note the second row carefully: **`systemctl show` still reports `IOWeight=4950`
while the effective kernel file is 700.** The audit surface reports a number
that is no longer real — precisely the class of silent divergence this whole
redesign was undertaken to eliminate (`d34969e6`'s own commit message: "*every
floor silently protects nothing while systemctl show and the cgroupfs file both
still report the configured number*").

**Concrete failure scenario.** A Soulmask server runs with
`WINGS_CG_MEMORY_MAX=7G` and `WINGS_CG_CPU_WEIGHT=800`, its Panel memory limit
set to 20G (the documented "no real ceiling" value from the egg). An
administrator renames the server, or edits any build/limit field, in the Panel.
Wings performs the on-the-fly update. The container's `memory.max` silently
moves from 7G to 20G — the deliberate ceiling is gone — and if
`docker.cpu_shares` is set (the config's own doc recommends `1024` to "restore
the old bias"), `cpu.weight` drops 800→39, a ~20x loss of scheduling priority.
No log line, no warning, no re-apply. The server keeps running and looks fine.

**This directly refutes two documented claims:**
- `CGROUP-SEMANTICS.md:76-79` — "the last one to set the property wins, and
  Wings applies its set just after the container starts". True at start; false
  for the rest of the server's life.
- `CGROUP-SEMANTICS.md:361-364` — "systemd re-derives that file from `IOWeight`
  whenever it re-applies the unit's IO settings, so **the systemd-side value is
  the one that survives**." Measured false: systemd did not re-derive, and the
  Panel's raw value survived.

**Remediation options** (a decision is needed, not necessarily all of these):
1. Re-apply the scope properties at the end of `InSituUpdate` — smallest fix,
   restores the redesign's intended invariant, and is the direction the code
   already implies (`applyScopeProps` is idempotent and cheap).
2. Additionally, correct the two `CGROUP-SEMANTICS.md` claims above, which are
   currently affirmatively wrong.
3. Optionally, the memory-ceiling guard item 2 of the brief asked about —
   refusing to apply a `MemoryMax` looser than Docker's own container config.
   Worth considering, but (1) is the load-bearing fix.

---

### F2 — Patch 0008 has **zero** test coverage; four documents claim it is e2e-proven

**Severity: blocking (on the verification claim, not on the code).**

The code is correct. The *evidence* offered for it does not exist.

**File:** `internal/cgroups/integration_test.go:193-205`
(`TestScopeMemoryCurrentIsReadableIntegration`).

That test never calls the function it claims to prove. Line 202 reads the
property through the test's own helper `itTypeProp(t, ctx, scope, "Scope",
"MemoryCurrent")` — a **hardcoded string literal in the test file**. The
production function `(*conn).memoryCurrent` in `sysd.go:256` is not invoked.
`grep` confirms `memoryCurrent`, `rampHigh` and `waitMemoryAtOrBelow` are called
by **no test in the repository** — unit or integration.

**Proved by mutation, not by reading.** I compiled the `systemdintegration` test
binary twice from the clean clone — once unmodified, once with `sysd.go:257`
reverted to `"Slice"` (patch 0008 fully undone) — and ran both against real
systemd in the privileged harness:

```
BASELINE  (reads "Scope")   --- PASS: TestScopeMemoryCurrentIsReadableIntegration
MUTATED   (reads "Slice")   --- PASS: TestScopeMemoryCurrentIsReadableIntegration

MUTATED, full suite: 0 failures, exit 0
```

The patch *is* load-bearing — verified independently with `busctl` against a
live container scope:

```
org.freedesktop.systemd1.Scope  MemoryCurrent  ->  t 946176
org.freedesktop.systemd1.Slice  MemoryCurrent  ->  Unknown interface ...
org.freedesktop.systemd1.Unit   MemoryCurrent  ->  Unknown interface ...
```

So the failure mode is exactly as patch 0008 describes — a hard error on every
call, `rampHigh` returns on the first one, the ramp silently never runs — and
**nothing in the test suite would notice if it regressed.** This is the precise
bug class the patch was written to defend against, left undefended.

**The false claim appears in four places, all needing correction:**
1. `CONTINUATION-2026-09-08-scope-redesign.md:44-46` — "*proves patch 0008's
   retargeted read actually works on a scope. Had the interface still said
   `"Slice"`, this fails.*"
2. Commit `e22a6452` message — "*which is what patch 0008's retarget claims and
   would fail if it still said "Slice"*"
3. `patchstack/README.md:16` (patch 0008 row) — "*which is why
   `internal/cgroups`' systemd e2e reads both back from real units rather than
   trusting the constants*"
4. Patch `0008-Fix-MemoryCurrent-D-Bus-interface…`'s own commit message — "*which
   is why internal/cgroups' systemd integration test now reads MemoryCurrent
   back from a real scope rather than trusting the constant*"

Note also that patch 0008 touches **only** `sysd.go` — it adds no test at all.
The test it credits was added by patch 0004 and does not exercise it.

**Remediation.** Make the test call the real function. The fix is small and
belongs inside patch 0004 (or a new tip patch), built forward per the project's
documented method:

```go
// in TestScopeMemoryCurrentIsReadableIntegration, replacing the itTypeProp read
c, err := connect(ctx)
if err != nil { t.Fatalf("connect: %v", err) }
defer c.close()
cur, ok, err := c.memoryCurrent(ctx, scope)   // the function under test
if err != nil {
    t.Fatalf("memoryCurrent on a real scope: %v (wrong D-Bus interface?)", err)
}
if !ok { t.Error("MemoryCurrent reads as infinity; accounting not enabled") }
_ = cur
```

With that, the mutant dies. I did **not** apply this myself: it requires editing
a middle patch and re-exporting the series, which is implementer work on a
shared checkout, and a partial fix to one of six findings would muddy the
handoff more than it helps.

---

## WORTH A FOLLOW-UP

### F3 — `PerServerSlices` config doc comment still describes the retired design, and cites a deleted symbol

**File:** `config/config_docker.go:158-164`.

```go
// PerServerSlices enables automatic per-server slice management: every
// server is placed under its own derived slice (see ServerSliceName)
// nested inside CgroupParent, and Wings creates/reconciles that slice as
// a transient systemd unit — with the configured default resource
// properties and any per-server WINGS_CG_* overrides — before containers
// are created under it.
```

Every clause is now false: there is no derived slice, no nesting, no transient
unit, and properties are applied *after* container start, not before. It also
references `ServerSliceName`, which no longer exists anywhere in the tree — this
is the **only** dangling retired-symbol reference left in Go code (everything
else came back clean).

This matters more than a typical stale comment because it is the in-code
documentation for a live, operator-facing config key (`docker.per_server_slices`),
and it is the first thing a reader hits when auditing that block. Cheap to fix;
the accurate description already exists at `internal/cgroups/sysd.go:156-181`.

### F4 — The e2e harness still tests the retired design; "E2E: ALL PASS" overstates what ran

**File:** `test/e2e-systemd/inner-test.sh` — last substantively touched by
`89310afa` (the `v1-legacy` archive commit), **not** by this redesign.

Only patch 0004's compiled `cgroups.test` binary (section 4) actually exercises
the new code. Sections 1–3 still assert the retired shape:

- §1 asserts per-server slice nesting `wings.slice/wings-e2etest.slice/docker-…`
- §2 asserts "slice-held memory.min SURVIVED daemon-reload (the fix)" — the
  retired fix
- §3 drives `wings-slice-manager` with `memory_min_budget: 96M` and
  `budget_policy: clamp`, and asserts a **budget clamp** — machinery this
  redesign deleted outright
- §4's own header still reads "patch 0004: D-Bus lifecycle, budget, GC"

These pass because they exercise generic systemd/Docker behaviour and a separate
`t3a-slice-manager` binary that still exists — not because the shipped series
works. `e22a6452` records "E2E: ALL PASS … This closes the brief's largest
stated gap"; in fact roughly three quarters of that harness validates code that
is no longer in the series. The gap is narrower than claimed, and F2 narrows it
further.

Not blocking — the new integration cases are real and do pass — but the harness
needs a pass before its verdict can be cited as evidence again.

### F5 — The overcommit tripwire is silently inert when the tier declares no `MemoryMin`

**Files:** `internal/cgroups/sysd.go:207-213`, `:223-233`.

`sliceMemoryMin` returns `ok=false` when the tier slice's `MemoryMin` is unset or
`MaxUint64`; `Apply` then leaves `ParentFloor=0` and `Overcommit=false`, so
`logOvercommit` never fires. Confirmed: there is no startup probe and no
one-time log anywhere (`ValidatePerServerSlices` cannot check it — it runs
without D-Bus).

The implementer flags this honestly as a judgment call. My assessment: it is a
**footgun worth one startup log line**. The failure is invisible in the worst
way — an operator who deliberately dropped the `memory_min_budget` knob in
favour of "read it live from reality" gets *no* signal that reality declares
nothing, and the feature they think is watching their node is switched off. A
single `Info` at boot ("tier `wings.slice` declares no MemoryMin; the
floor-overcommit tripwire is inactive") costs nothing and closes it.

### F6 — The floor ledger under-counts after a Wings restart, contradicting its own doc

**File:** `internal/cgroups/scope.go:61-75`.

The comment states entries are dropped "when a server is deleted, not when it
stops… it matches how an administrator sizes a node: **by what is provisioned,
not by what happens to be running**."

The ledger is an in-memory `map` with no persistence. After a Wings restart it
starts empty and repopulates only as servers start and successfully apply a
floor — so it actually accounts for "servers that have started since this Wings
process began", which is *exactly* the running-based accounting the comment
disclaims. On a node where half the servers are stopped, the tripwire will
under-report the committed total and may not fire when it should.

Consequence is bounded (the ledger feeds one informational log line and nothing
else), so this is a nit-plus: either correct the comment, or note the
restart-resets-it caveat alongside it.

---

## Known-deferred docs — accurately scoped, not actively misleading

The continuation brief lists `v1-legacy/README.md`, `v1-legacy/pr/*.md`,
`t2-per-server-placement/README.md`, `t3a-slice-manager/README.md` and
`test/README.md` as still describing the retired design. I confirmed that
listing is accurate and complete for operator-facing docs — with the two
exceptions raised above (F3, which is in-code and was not on the list, and F4,
which is executable and was not on the list).

`SETUP.md` — the one claimed updated, and the one an operator actually follows —
**is** correctly updated, including a migration row for the discarded keys.
`CGROUP-SEMANTICS.md` and `patchstack/README.md` are current except for the
specific claims called out in F1 and F2. The `t*/` and `pr/` directories are
historical rung write-ups and a not-yet-filed upstream PR narrative; leaving them
stale is defensible as long as the PR pass happens before filing, as the brief
says.

---

## Summary table

| ID | Finding | Severity |
|---|---|---|
| F1 | `InSituUpdate` silently clobbers scope properties; looser `MemoryMax` can win; `systemctl show` diverges from the kernel file | **blocking** |
| F2 | Patch 0008 untested; mutant survives full suite; four docs claim otherwise | **blocking** |
| F3 | `config_docker.go:158-164` stale doc + dangling `ServerSliceName` | follow-up |
| F4 | e2e harness §1–3 still test the retired design; "ALL PASS" overstates | follow-up |
| F5 | Tripwire silently inert with no tier `MemoryMin`; no startup log | follow-up |
| F6 | Ledger under-counts after a Wings restart, contradicting its comment | nit |

**Nothing was changed in the repository by this review.** The disposable clone at
`build/wings-pterodactyl` is untouched (tree `2b83e7e2…`, clean); all experiments
ran in an isolated scratch clone and throwaway containers, both torn down. The
live production host was not touched.
