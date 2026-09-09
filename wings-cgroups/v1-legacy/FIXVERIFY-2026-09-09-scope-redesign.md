# Fix-verification review — review-fix pass, 2026-09-09

Scope: `e26eec09`, `d03fe40a`, `0122594d`, `c7bb4352` — the pass that answers
`REVIEW-2026-09-09-scope-redesign.md` (`2ddadee8`).

Third independent session on this work: not the implementer's, not the
reviewer's, no prior context. Everything below was verified against the code
and by re-running things firsthand — the review's claims and the fix report's
claims were both treated as unverified.

---

## Verdict: **NOT-ACCEPT** — one blocking finding

Five of the six original findings are genuinely, verifiably fixed, and I
reproduced the two hard ones (F1's measurement, F2's mutation) from scratch
rather than trusting the pasted output. The pass is honest: I found no
overstated claim of the F2 class anywhere in it, and the one self-flagged
"known cosmetic" (`gofmt -l`) is exactly as described, four files, no more.

What blocks it is **new**, introduced by F1's own fix:

- **V1** — `InSituUpdate`'s phase inference disagrees with the real slice phase
  on the configuration this project's own `SETUP.md` tells administrators to
  use, and in that window the re-assert applies the **steady** band **one-shot,
  no ramp**, while the server is at its load-time peak — the exact eviction the
  startup band and the ramp exist to prevent. The wrong band then stands for
  the rest of that boot. The fix report identifies this as its highest-risk
  decision and states the window is narrow and that "there is nothing to walk";
  both premises are wrong, and are wrong in the same direction.

Everything else is follow-up or nit. **Disposition:** `wings-local:1.13.3-cgroup.2`
remains safe to keep locally (reproducible, green, nothing deployed). It is not
deployable until V1 has a fix or an explicit, documented decision.

---

## Reproduced firsthand

| what | result |
|---|---|
| Exported series == working branch | **EXACT.** `git am -3` of all nine `.patch` files onto `v1.13.3` in a fresh branch yields tree `f8dcae84…`, byte-identical to `cgroup/v1.13.3`. Everything below therefore describes what the *patches* produce, not a stray working-tree state. |
| Fixes land in the patches claimed | **TRUE.** `git log -S` inside the series: the `InSituUpdate` re-assert is added by 0004 (`cf9eef1`) and made phase-aware by 0006 (`f2668c1`); `memoryCurrent` is introduced by 0006, so the F2 test fix genuinely could not live in 0004 and correctly sits in 0008 (`584722f`). |
| `patchstack/scripts/test.sh pterodactyl` | **GREEN.** build, strict vet, unit tests, both integration compile checks. Re-run with `-count=1` to defeat the cache: all packages `ok`. |
| `INTEGRATION=1 patchstack/scripts/test.sh pterodactyl` | **GREEN**, `dockerintegration` 5/5 (`TestCreateWithoutCgroupParentLeavesDefault`, `…AppliesNodeWideCgroupParent`, `…AppliesAcceptedOverride`, `…RejectsDisallowedOverride`, `…InstallerUsesSameResolver`). |
| `test/e2e-systemd/run-e2e.sh` | **`SHIPPED SERIES (sections 1, 2, 4): PASS`**, **`t3a-slice-manager, separate component (section 3): PASS`**, harness exit 0. |
| `patchstack/scripts/coverage.sh pterodactyl` | **PASS**, assay 6.0.0, `"covered": 340`, `"pct": 53.208137715179966`, `fail_under: 50.0`. |
| Floor untouched | **CONFIRMED.** `patchstack/assay/assay.toml` has exactly one commit in its history (`bfb2077b`, pre-review). None of the four fix commits touch it. `fail_under = 50.0` is the value the review saw. |
| `wings-local:1.13.3-cgroup.2` on the host daemon | **PRESENT** (`6c68ac080b7b`, 31.1MB, created 2026-09-09 00:41 UTC). Built from the fixed series, not the pre-review one: the image's binary contains the string `panel-side in-situ resource update`, which only exists after `e26eec09`. |
| `gofmt -l` claim | **ACCURATE.** Exactly `server/server.go`, `internal/cgroups/phase_test.go`, `internal/cgroups/scope_test.go`, `internal/cgroups/sysd.go`. No new file was dirtied by the pass. |

Nothing on the live host was touched. `/etc/pterodactyl/`,
`/etc/systemd/system/wings*.slice` and `/root/ptero-wings-patched-cgroups/`
were never read or written; the live node still runs `1.13.1-cgroup.11`. All
experiments ran in throwaway privileged containers built from
`wings-cgroups-e2e`, removed afterwards. The disposable clone
`build/wings-pterodactyl` is back at tree `f8dcae84…`, clean.

### F1 — the drift, reproduced, and the repair, measured

Independent reproduction on the project's own privileged systemd harness
(systemd 257, cgroup v2, `native.cgroupdriver=systemd`). Container created
`--cgroup-parent=wings.slice --memory 512m --memory-reservation 512m
--cpu-shares 2 --blkio-weight 540`, then Wings' apply simulated as
`systemctl set-property --runtime` (the shell form of
`SetUnitProperties(runtime=true)`, which is literally what
`internal/cgroups.Apply` calls), then one `docker update`:

```
1. after Wings applyScopeProps
   MemoryMin=209715200 MemoryLow=314572800 MemoryHigh=419430400
   MemoryMax=471859200 CPUWeight=800 IOWeight=4950
2. after ContainerUpdate  <-- what the PRE-FIX code left behind
   MemoryMin=209715200 MemoryLow=536870912 MemoryHigh=419430400
   MemoryMax=536870912 CPUWeight=39  IOWeight=4950
3. after the re-assert    <-- the fix
   MemoryMin=209715200 MemoryLow=314572800 MemoryHigh=419430400
   MemoryMax=471859200 CPUWeight=800 IOWeight=4950
```

The review's numbers reproduce **exactly**: `MemoryLow` 300M→512M, `MemoryMax`
450M→512M (loosened), `CPUWeight` 800→39, `MemoryMin`/`MemoryHigh` untouched
because runc does not set them. And the re-assert restores every one of them.
The mechanism of F1's fix is sound and now has firsthand evidence behind it,
which it previously did not.

The IO half reproduces too, and settles a question the fix pass asserted
without measuring:

```
0. created with --blkio-weight 300   IOWeight=[not set]  io.weight=100   io.bfq.weight=300
1. systemd IOWeight=4950             IOWeight=4950       io.weight=4950  io.bfq.weight=540
2. docker update --blkio-weight 700  IOWeight=4950       io.weight=4950  io.bfq.weight=700   <-- audit surface diverges
3. re-assert IOWeight=4950           IOWeight=4950       io.weight=4950  io.bfq.weight=540   <-- repaired
4. IOWeight=100                      IOWeight=100        io.weight=100   io.bfq.weight=100
```

Row 2 is the review's finding, confirmed: `systemctl show` reports 4950 while
the kernel file holds the Panel's raw 700. Row 3 confirms the re-assert removes
it. Rows 1/4 also show that systemd *does* derive `io.bfq.weight` from
`IOWeight` on its own scale (4950→540, 100→100) — it just never re-derives
spontaneously, which is what made the old doc claim false. See V4 for the one
wording problem this leaves in the corrected text.

### F2 — the mutation, run here, not read

Not trusted from the report. In the disposable clone, `internal/cgroups/sysd.go:273`
was reverted `"Scope"` → `"Slice"` (patch 0008 fully undone), the
`systemdintegration` binary rebuilt by `run-e2e.sh` from that tree, and the
whole harness re-run against real systemd:

```
=== 4. SHIPPED SERIES: wings internal/cgroups systemd integration tests ===
  FAIL: wings internal/cgroups integration test
=== RUN   TestScopeMemoryCurrentIsReadableIntegration
    integration_test.go:221: memoryCurrent on a real scope: Unknown interface
    org.freedesktop.systemd1.Slice or property MemoryCurrent.
    (wrong D-Bus interface for a scope's resource-control properties?)
--- FAIL: TestScopeMemoryCurrentIsReadableIntegration (0.02s)

=== summary ===
  SHIPPED SERIES (sections 1, 2, 4): 1 FAILURE(S)
  t3a-slice-manager, separate component (section 3): PASS
E2E: 1 FAILURE(S)          e2e-systemd: FAIL (rc=1)
```

Restored, re-run, green. **The mutant dies.** F2 is genuinely closed, and the
harness now propagates the failure into the series tally and the exit code.

The loosened assertion does **not** weaken the kill. The interface mutant is
caught by the `err != nil` branch at `integration_test.go:216-222`, which was
not touched; only the *value* assertion (`cur == 0`) was walked back, and the
`!ok` (infinity) check survives. The reasoning for walking it back is also
correct and worth keeping: `itStartScope` moves an already-running process into
the scope, exactly as Docker's systemd driver does, so pages faulted before the
move stay charged where they were and a fresh scope legitimately reads 0.

---

## BLOCKING

### V1 — the phase-inference heuristic applies the steady band, one-shot, during the real startup band — on the documented recommended configuration

**Severity: blocking.** New in the fix pass. Files:
`environment/docker/container.go:174-180`, `environment/docker/scope.go:63-65`,
`internal/cgroups/ensure.go:118`, `server/slice_phase.go:406-416`.

`InSituUpdate` cannot see the slice phase, so it infers it:

```go
phase := cgroups.PhaseSteady
if e.State() == environment.ProcessStartingState {
    phase = cgroups.PhaseStartup
}
…
e.applyScopeProps(sctx, phase, "panel-side in-situ resource update", false)
```

The fix report defends this by saying the two signals "can disagree only for a
server that stages a startup band **and** either names a `WINGS_CG_STEADY_MATCH`
that fires off the running transition or has already burnt its startup grace,
and only until the next application", and that applying nothing was strictly
worse. Three parts of that are wrong.

**1. The disagreement window is the documented main path, not a corner.**
`server/slice_phase.go:406-416` is explicit that a server with an explicit
steady matcher deliberately does **not** leave the startup phase when the
environment flips to running:

> `// The egg's "done" matcher fired. When the server defines its own steady`
> `// trigger this is deliberately NOT the end of the startup phase: "done" is`
> `// whatever makes the Panel show Running, which for a world-streaming game`
> `// is routinely well before loading finishes.`

And `SETUP.md:335` tells the administrator to configure exactly that:

> **Empty falls back to the egg's own `startup.done` matcher** — which for a
> world-streaming game routinely fires *before* loading finishes, so set this
> explicitly if the two differ.

So on the target workload — the one the whole startup band exists for — the
window is the entire remaining world load, bounded only by `startup_grace`
(default `15m`). Not narrow.

**2. There *is* something to walk, so the `ramp=false` reasoning does not hold
here.** The report's justification for skipping the ramp is that
`ContainerUpdate` never touches `MemoryHigh`, so "a re-assertion writes the
value that is already on the unit". I confirmed the first half —
`environment/settings.go:105-138` `AsContainerResources()` builds `Memory`,
`MemoryReservation`, `MemorySwap`, `OomKillDisable`, `PidsLimit`, optionally
`BlkioWeight`, `CPUQuota`/`CPUPeriod`/`CPUShares`, `CpusetCpus`, and Docker's
`container.Resources` has no `MemoryHigh` field at all. But the conclusion only
follows when the inferred phase matches the real one. In this window the unit
carries the **startup** `MemoryHigh` and the re-assert writes the **steady**
one — a genuine downward step, taken in one shot because `applyScopeProps`
forces `req.RampStep = 0` and `ensure.go:118` gates the ramp on it.

**3. It is not "only until the next application".** Nothing ever re-applies the
startup band. `applyScopeProps(PhaseStartup)` is called from exactly one place,
`power.go:148`, at container start; `enterSteady` only ever dispatches
`applySliceProps(cgroups.PhaseSteady, …)` (`slice_phase.go:155`). Once the
steady band has been written early, it stands for the rest of that boot.

**Concrete failure scenario**, using this project's own documented shape
(`SETUP.md:233-238`: `startup_defaults.memory_high: "64G"  # effectively "no
ceiling yet"`, steady `defaults.memory_high` low, and the "ENGAGEMENT" note at
`SETUP.md:225-229` that the band only does anything when the startup ceiling is
above the steady one):

1. Soulmask-class server, `WINGS_CG_STEADY_MATCH` set as `SETUP.md` advises,
   startup `memory_high` 64G, steady 6G.
2. Container starts; startup band applied. World loads; RSS climbs past 6G.
3. Egg's `startup.done` fires → environment state = running. `hasSliceMatcher()`
   is true, so the phase correctly stays **startup**.
4. An administrator renames the server, edits a startup variable, or changes any
   build field. Panel → Wings → `Server.Sync` → `SyncWithEnvironment`
   (`server/update.go:47-56`) → `InSituUpdate`.
5. `e.State()` is running, so `phase = PhaseSteady`, `ramp = false`.
   `memory.high` is written down to 6G in one shot on a cgroup currently at
   ~12G.
6. Per the package's own rationale (`server/slice_phase.go:19-36`,
   `SETUP.md:217-222`): "a cgroup is not protected from reclaim it inflicts on
   itself — exceeding its own memory.high reclaims straight through its own
   memory.min floor… permanently, because nothing faults those pages back
   except the workload." That is the eviction the startup band was built to
   prevent, now triggered by a Panel rename.
7. If the node also stages a steady `memory_max` below the load peak, the
   consequence is not throttling but an OOM kill —
   `internal/cgroups/sysd.go:302-304` states this outright: "memory.max is a
   HARD limit and is applied at once by the caller's base Ensure, so a steady
   memory.max staged below the load peak would OOM the container regardless of
   this ramp".

**Mirror case, lower severity, same root cause.** Grace expires while the
environment is still `starting` (a server slow to print its done line):
`enterSteady` has run, `sliceSteadyDone` is true, the phase is steady — but
`InSituUpdate` infers `PhaseStartup` and re-lifts the ceiling the grace backstop
deliberately lowered, and again nothing restores it for the rest of the boot.
The backstop is silently defeated.

**Remediation.** The premise that forced the heuristic — "the `Environment`
cannot see the server's slice phase" — is true of the `Environment`, but the
*caller* is `Server.SyncWithEnvironment`, which is in the layer that owns the
phase and already exposes `s.slicePhaseInProgress()`. Options, roughly in order
of size:

1. Do the re-assert in `SyncWithEnvironment` after `InSituUpdate` returns, using
   the real phase. Note `applySliceProps` cannot be reused verbatim: its
   `phase == PhaseSteady && !req.Staged()` early-return at
   `slice_phase.go:294` is correct for a transition and wrong for a re-assert,
   so it needs a re-assert variant.
2. Or have the `Server` push its current phase onto the `Environment` at each
   transition, and have `InSituUpdate` read that instead of inferring.
3. At minimum, if the heuristic is kept: pass `ramp = true` on the steady
   re-assert path, and record V1 as a known, accepted behaviour in
   `CGROUP-SEMANTICS.md` and `SETUP.md` next to `WINGS_CG_STEADY_MATCH`, since
   an operator following the current documentation has no way to know that
   editing a loading server collapses its ceiling.

---

## WORTH A FOLLOW-UP

### V2 — the unconditional re-assert emits a false alarm on every server start, and on every settings save for a stopped server

**Files:** `environment/docker/container.go:180`, `environment/docker/scope.go:56-72`,
`internal/cgroups/ensure.go:146`, `server/power.go:185`, `server/server.go:229`.

`InSituUpdate` now re-asserts unconditionally, but there are two call paths in
which the container is **not running**:

- `Server.onBeforeStart` → `Sync` → `SyncWithEnvironment` → `InSituUpdate`
  (`server/power.go:185`), which runs *before* `Environment.Start`;
- any Panel settings save on a stopped, unsuspended server
  (`server/server.go:229`).

In both, `ContainerInspect` succeeds (the container exists, stopped), so the
`IsErrNotFound` early return does not fire; `ContainerUpdate` on a stopped
container succeeds; and then `applyScopeProps` tries to set properties on a
scope unit that does not exist, because a `docker-<id>.scope` lives exactly as
long as the container's init process. Measured in the harness:

```
running: set-property ->  rc=0
stopped: docker update ->  rc=0
stopped: set-property ->  Failed to set unit properties on docker-….scope:
                          Unit docker-….scope not found.   rc=1
```

`Apply` therefore returns an error and `EnsureForServer` logs, at **Warn**:

> `cgroups: could not apply container scope properties; the container runs
> under the node tier without its own resource guarantees`

on every start of every managed server that has an existing container, and on
every settings edit of a stopped one. The message is false in both cases (the
properties land correctly a moment later at `power.go:148`), and this is the one
diagnostic the design leans on — `ensure.go:89-93`: "the emitted warning is what
tells the administrator exactly that". Making it routine trains operators to
ignore it, which is the failure class this redesign exists to remove.

Fix is one guard: `applyScopeProps` already holds the inspect result, so
`if c.State == nil || !c.State.Running { return }` before building the request
(matching the existing `c.State != nil` guard at `container.go:127`).

### V3 — `Result.TierFloorUnset` itself is untested; only the log helper is

**Files:** `internal/cgroups/sysd.go:216-229`, `internal/cgroups/ensure_test.go:59-88`.

`TestLogTierFloorUnsetSaysSoExactlyOnce` is a good test and does what F5 needed:
it exercises the right condition (`TierFloorUnset` set vs. a tier that declares
a floor), asserts the entry names the tier, and asserts the once-per-process
suppression. Verified passing, and it is a real assertion, not a smoke test.

But the *production decision* — `Apply` setting `res.TierFloorUnset = true` when
`sliceMemoryMin` comes back not-ok — has no test at all. It cannot have a unit
test (it is past `connect`), and the e2e does not cover it either: the only
integration case that reaches this branch, `TestApplyScopePropertiesIntegration`,
runs against a `wings.slice` that section 1 created **with** `MemoryMin=128M`,
so `TierFloorUnset` is always false there. This is a much smaller instance of
exactly F2's shape — the helper is proven, the branch that feeds it is not. One
extra e2e case against a tier with no `MemoryMin` would close it.

Related wording nit, same lines: `TierFloorUnset` is also set when the tier read
*errors*, but the log line asserts "tier slice declares no MemoryMin of its
own". `Result`'s own doc comment (`sysd.go:56-64`) correctly says "unset,
infinity, or unreadable"; the operator-facing string should say the same, since
a transient D-Bus error currently burns the `sync.Once` on a false statement.

**Not a finding:** the prompt asked whether process-global `tierFloorUnsetOnce`
could suppress a second tier's notice under `WINGS_CGROUP_PARENT` overrides. It
cannot. `ScopeEnsureRequest` (`config/config_docker.go:442-458`) sets
`req.Parent` **only** when `parent == c.CgroupParent`, and `Apply` gates the
whole tripwire on `t.Parent != ""`, so a server pinned elsewhere never produces
a `TierFloorUnset` at all. Only one tier can ever reach the `sync.Once`. The
concern is real only across a live `docker.cgroup_parent` reconfiguration, which
is negligible.

### V4 — rule 7's replacement claim is imprecise in the same place the old one was wrong

**File:** `wings-cgroups/CGROUP-SEMANTICS.md`, rule 7 (`0122594d`).

The correction is substantively right and I verified its load-bearing half: the
re-assert does remove the Panel's raw `io.bfq.weight`. But the sentence

> "…so the last write on that file is Wings' `IOWeight` again and `systemctl
> show` agrees with `io.bfq.weight` once more"

does not survive a measurement. After the re-assert, `systemctl show` reports
`IOWeight=4950` and `io.bfq.weight` reads `540` — they are on different scales
and never agree numerically, which is precisely the nomenclature trap the same
document warns about two paragraphs later. Say instead that the file is once
again systemd's derivation from `IOWeight` rather than the Panel's raw value,
and keep the existing "read the result back off `io.bfq.weight`" advice. Also
worth folding in, since it is now measured rather than assumed: systemd *does*
re-derive the file when the property is set (4950→540, 100→100), it simply never
does so spontaneously — which is the sharper version of what rule 7 is trying to
say.

### V5 — `E2E: ALL PASS` still prints, folded over both tallies

**File:** `test/e2e-systemd/inner-test.sh:211`.

F4 is genuinely fixed: sections 1 and 2 now assert the shipped design (flat
placement under the tier with no per-server slice in the path; properties set
through the systemd-owned channel surviving `daemon-reload`), section 3 is
relabelled as the separate `t3a-slice-manager` and its tally is separated, and
the file header says so in prose. The relabelling is substantive, not cosmetic —
I read the whole script and re-ran it.

The one leftover: line 211 still emits the literal string `E2E: ALL PASS` over
the combined counter. That is the exact string whose quotability caused the
overstatement F4 was filed for. The two split lines precede it, so this is cheap
to leave — but cheaper to drop.

Also worth knowing when citing section 2: on this host its *contrast* half
degraded to a NOTE — `raw scope value survived on this systemd version
(33554432)` — so the "a raw cgroupfs write does not survive `daemon-reload`"
half of the durability argument is not actually demonstrated here. The positive
half (systemd-set properties do survive) is. The script is honest about it; a
reader quoting the section should be too.

### V6 — a failed `ContainerUpdate` skips the re-assert

**File:** `environment/docker/container.go:135-139`.

`InSituUpdate` returns on a `ContainerUpdate` error without re-asserting. Docker
restores its own `HostConfig` on that path but does not roll back cgroup writes
already made, so a partially-applied update can leave the scope carrying Panel
values with nothing to repair them until the next start. Narrow (requires the
update to fail) and strictly better than the pre-fix state, but it is the same
hole F1 described, just behind an error branch. A `defer`-based re-assert, or
re-asserting before returning the error, would close it.

---

## Verified clean, no action

- **F3** — `config/config_docker.go:158-171` now describes the shipped design
  accurately, clause by clause, against `internal/cgroups/sysd.go:165-190`:
  flat placement under `CgroupParent`, properties on the container's own
  `docker-<id>.scope`, applied after `ContainerStart()`, no unit created by
  Wings, and an explicit note that the block keeps its historical name for
  config compatibility. `grep -rn ServerSliceName` across the pterodactyl tree
  and the whole `v1-legacy/` directory returns **zero** hits outside the
  deliberately-deferred `patches/pelican-main/` series and the review documents
  themselves.
- **F6** — `internal/cgroups/scope.go:70-81` now states the restart caveat
  plainly ("holds servers that have applied a floor since this Wings process
  started, NOT servers provisioned on this node"), keeps the original
  provisioned-vs-running distinction where it *is* true (within one process
  lifetime), and records why persisting it was rejected. Accurate.
- **The bonus finding** (`c7bb4352`) — `git ls-files | grep -i cgroups.test`
  returns nothing; `wings-cgroups/v1-legacy/test/e2e-systemd/artifacts/cgroups.test`
  is 5,992,740 bytes on disk and regenerated by every `run-e2e.sh` run, and
  `git status wings-cgroups/` is clean with it present, so `.gitignore`'s
  `test/e2e-systemd/artifacts/` rule now actually applies. The diagnosis of the
  mechanism is correct: a pathspec on `git commit` implies `--only`, which
  commits the working-tree content of those paths over a staged `git rm
  --cached`.
  **Awareness sweep:** the other file removals on this shared checkout since
  2026-09-07 (`d34969e6`, `ec0bc47f`, `583faad7`, `23fd0ace`, `7a02c8a9`,
  `a4addfc5`, `12d58750`) all show as real `D` entries in
  `git log --diff-filter=D --name-status`, so the footgun did not recur
  elsewhere. It remains live for any future removal made with the estate's
  standard `git commit --only -- <paths>` gesture: stage the removal, then
  commit with **no** pathspec.
- **The `ramp=false` premise, in isolation** — `AsContainerResources()`
  (`environment/settings.go:105-138`) genuinely never sets `MemoryHigh`, and
  Docker's `container.Resources` has no such field. The reasoning is sound; it
  is only the phase mismatch in V1 that breaks its conclusion.

---

## Summary table

| ID | Finding | Severity |
|---|---|---|
| V1 | `InSituUpdate` applies the **steady** band one-shot during the real startup band whenever `WINGS_CG_STEADY_MATCH` is set — the configuration `SETUP.md` recommends — and it stands for the rest of the boot; can throttle through the floor or OOM-kill at the load peak | **blocking** |
| V2 | The unconditional re-assert Warns falsely ("no resource guarantees") on every server start and on settings saves for stopped servers; the D-Bus write to a non-existent scope was measured failing | follow-up |
| V3 | `Apply`'s `TierFloorUnset` branch is untested (only the log helper is); the log string asserts "declares no MemoryMin" where the read may merely have errored | follow-up |
| V4 | `CGROUP-SEMANTICS.md` rule 7's replacement claim ("`systemctl show` agrees with `io.bfq.weight`") measured false — 4950 vs 540, different scales; the substantive half is true | nit |
| V5 | `inner-test.sh:211` still prints the folded `E2E: ALL PASS`; section 2's raw-write contrast degraded to a NOTE on this host | nit |
| V6 | A failed `ContainerUpdate` returns before the re-assert, leaving any partial Docker write unrepaired | nit |

**Original findings:** F1 mechanism fixed and independently measured, but the
fix introduces V1 — so F1 is not closed. F2 **closed**, mutation re-run here.
F3, F4, F5, F6 **closed**. The bonus finding **closed**.

**Nothing in the repository was changed by this review** beyond adding this
file. The disposable clone is back at tree `f8dcae84…` with an empty
`git status`; the scratch branch `fixverify/reapply` and the throwaway harness
container were removed. The live production host was not touched.
