# Continuation brief — scope redesign, 2026-09-08

State at hand-off after the rebase-to-v1.13.3 + slice→scope redesign session.
Read this before touching `wings-cgroups` again.

> **Amended 2026-09-09** after the independent adversarial review
> (`REVIEW-2026-09-09-scope-redesign.md`, commit `2ddadee8`) returned
> NOT-ACCEPT on two blocking findings, and again after the independent
> fix-verification (`FIXVERIFY-2026-09-09-scope-redesign.md`, commit
> `6de29c5a`) returned NOT-ACCEPT on one blocking finding **introduced by that
> first fix**. Everything below is the 2026-09-08 state *plus* corrections; the
> two review files are the authority on what was wrong. There are two fix-pass
> sections at the end — read both, in order, and note that the second one
> supersedes the first's central design decision.
>
> **Correcting note for commit `e22a6452`.** That commit's message says the
> systemd e2e run "*proves* … which is what patch 0008's retarget claims and
> would fail if it still said `"Slice"`". That was **false when written**: the
> case read `MemoryCurrent` through a hardcoded interface string in the test
> file, never through `sysd.go`, and the review demonstrated by mutation that
> the full suite passes with patch 0008 reverted. The message is left as it
> stands — it is already-published history on the shared `main` checkout and
> is never rewritten here. This note is the correction. The claim became true
> on 2026-09-09, when the case was changed to call `(*conn).memoryCurrent`;
> the mutant now dies.

## What shipped

Two commits on `main` in `/workspaces/vbpub`:

| commit | what |
|---|---|
| `a30a0db6` | mechanical rebase of the unchanged 11-patch series onto upstream `v1.13.3` |
| `d34969e6` | the redesign: series retargeted from per-server slice to container scope, renumbered `0001`–`0009` |

Plus a docs commit (see `git log wings-cgroups/`).

- Authoritative series: `v1-legacy/patchstack/patches/pterodactyl-v1.13.3/0001..0009-*.patch`
- Working branch in the disposable clone: `cgroup/v1.13.3` in
  `v1-legacy/build/wings-pterodactyl` (backup ref `backup/pre-redesign-v1133`
  holds the pure-rebase, pre-redesign tip)
- `stack.conf` `PTERODACTYL_REF="v1.13.3"`
- Image built and present on the host daemon: **`wings-local:1.13.3-cgroup.3`**
  (the second review-fix state, 2026-09-09; `cgroup.1` is the pre-review build
  and `cgroup.2` the first review-fix build, both superseded. Nothing deployed —
  no compose file was touched, the live host still runs
  `wings-local:1.13.1-cgroup.11`)

## Verified

- `scripts/test.sh pterodactyl` — build + strict vet + unit tests: green
- `INTEGRATION=1 scripts/test.sh pterodactyl` — the `dockerintegration`
  placement tests against the real daemon: 5/5 pass
- `scripts/build-image.sh pterodactyl cgroup.2` → `wings-local:1.13.3-cgroup.2`
  (2026-09-08 built `cgroup.1`; superseded by the review-fix state)
- `test/smoke-placement.sh` — PASS (run against the throwaway
  `wings-smoke.slice`, deliberately NOT against the live `wings.slice`)
- `test/e2e-systemd/run-e2e.sh` — **SHIPPED SERIES (sections 1, 2, 4): PASS**
  and **t3a-slice-manager (section 3): PASS**, against a real systemd over
  D-Bus in the privileged harness. Read those two tallies separately: only
  the first is evidence about this patch series. Until 2026-09-09 the
  harness printed a single "E2E: ALL PASS" over both, and three of its four
  sections tested the RETIRED slice design (review F4); §1 and §2 now test
  the shipped scope design and §3 is labelled as the separate component it
  always was. The part that matters most: the
  `systemdintegration` tests were rewritten for the new design and now stand a
  real process up in a transient `docker-<64hex>.scope` (via
  `StartTransientUnit` + `PIDs`, exactly how Docker's systemd cgroup driver
  creates a container scope) and read the properties back off the **`Scope`**
  D-Bus interface. Three new cases pass:
  - `TestApplyScopePropertiesIntegration` — `MemoryMin`/`MemoryLow`/`CPUWeight`
    land on a real scope; a second `Apply` updates in place; a property omitted
    from the second request keeps its previous value.
  - `TestScopeMemoryCurrentIsReadableIntegration` — calls `(*conn)
    .memoryCurrent` against a real scope and asserts on its return, so patch
    0008's retargeted read is exercised by the test rather than described by
    it. **This is only true from 2026-09-09.** As written on 2026-09-08 the
    case read the property through the test file's own `itTypeProp(…,
    "Scope", …)` helper and never called the production function at all, so
    reverting patch 0008 to `"Slice"` left the whole suite green — proved by
    mutation in the adversarial review (F2). Fixed inside patch 0008; the
    mutant now fails the case with `Unknown interface
    org.freedesktop.systemd1.Slice or property MemoryCurrent.`
  - `TestApplyRefusesASliceIntegration` — the guard against ever pointing a
    container property write at a slice.
- `patchstack/scripts/coverage.sh pterodactyl` (or `COVERAGE=1 test.sh`) —
  assay 6.0.0 R1 changed-line coverage: **PASS**, 336/624 changed lines =
  **53.85%** on 2026-09-08; **340/639 = 53.21%** after the 2026-09-09 review-fix
  pass (the fix adds D-Bus-path lines that a plain `go test` cannot reach),
  floor 50.0. R0+R1 only; assay's Go adapter is registered at
  `{"R1"}` and mutation testing is unconditionally UNSUPPORTED for Go, so this
  is coverage, **not** mutation testing — do not claim more.

  **What that number excludes.** It is a plain `go test` profile, so the
  `systemdintegration` tests are not in it: `sysd.go` (~99 uncovered changed
  lines) and `ensure.go` (~41) — the D-Bus code this series exists for — count
  as uncovered even though the e2e harness above does exercise them and
  passes. `cmd/root.go` is uncovered outright (~95 lines, no test in the
  package); excluding `cmd/` the same run is 63.5%. `server/` and
  `internal/database/` (~964 changed lines) are deliberately outside
  `source_roots`. Merging the e2e profile in (`go test -c -cover` +
  `GOCOVERDIR` + `go tool covdata textfmt`) touches `run-e2e.sh`, the e2e
  Dockerfile and the artifact hand-off, and is open follow-up.

## NOT verified — the honest gaps

1. **Nothing was run against a live Wings.** The post-`ContainerStart()`
   application path (`environment/docker/scope.go`) has never executed against
   a real Pterodactyl server. The D-Bus layer beneath it is now e2e-proven, but
   the wiring from `Environment.Start` through `ScopeEnsureRequest` to `Apply`
   is only covered by unit tests plus reading.
2. **The pelican series is untouched.** See below.

## Deferred: the pelican series

`v1-legacy/patchstack/patches/pelican-main/` is still the **old 11-patch,
pre-redesign, pre-rebase** series and is now inconsistent with pterodactyl.
It was deliberately not ported — the task ranked it explicitly lower than the
pterodactyl series, and the pterodactyl redesign consumed the session.

To port it: `scripts/clone.sh pelican`, `scripts/apply.sh pelican`, then repeat
the same forward-build the pterodactyl series got (below). The pelican tree
differs mainly in `config/` layout, so expect the `config_docker.go` edits to
need re-siting; the `internal/cgroups` package is portable almost verbatim.

## How the series was built, and how to rebuild it

**Do not scripted-rebase this stack and do not `--amend` a non-HEAD commit** —
both are recorded failure modes (memories `git-patchstack-rebase-care`,
`wings-patchstack-amend-care`). The whole series was built **forward**, so the
commit being edited was always HEAD:

```bash
cd v1-legacy/build/wings-pterodactyl
git checkout -b <branch> v1.13.3
P=../../patchstack/patches/pterodactyl-v1.13.3
git am -3 $P/0001-*.patch ... $P/0004-*.patch
#   ... edit, then: git add -A && git commit --amend
git am -3 $P/0005-*.patch ... $P/0009-*.patch
../../patchstack/scripts/export-patches.sh pterodactyl
```

That is exactly how the stale `internal/cgroups` package doc comment was fixed
*inside* 0004 rather than smeared onto the tip. Reuse it for any further edit
to a middle patch.

## Load-bearing seams

| file:symbol | why it matters |
|---|---|
| `internal/cgroups/scope.go:ScopeName` | derives `docker-<64 hex>.scope`; returns `""` for anything else. A short id or a name would produce a unit systemd does not know, and every property call would be a silent no-op. |
| `internal/cgroups/scope.go` floor ledger | keyed by **server UUID**, not scope name, so a restart replaces its own entry instead of leaking one per start. Feeds only the log line. `ForgetServer` is called from `Environment.Destroy`. |
| `internal/cgroups/sysd.go:Apply` | the whole D-Bus write. Refuses any non-scope unit name up front. |
| `internal/cgroups/sysd.go:memoryCurrent` | reads `MemoryCurrent` from the **`"Scope"`** interface. This is patch 0008's entire subject. A wrong interface name is not a compile error, produces no partial behaviour, and shows up only as a ramp that silently never runs. |
| `internal/cgroups/sysd.go:sliceMemoryMin` | reads the tier's `MemoryMin` from the **`"Slice"`** interface — different unit type, different interface, on purpose. |
| `environment/docker/scope.go:applyScopeProps` | the post-`ContainerStart()` application point and the accepted timing window. |
| `environment/docker/power.go` | two call sites: `PhaseStartup` after `ContainerStart`, `PhaseSteady` when re-attaching to an already-running container. |
| `server/slice_phase.go:reassertSliceProps` | the third call site, added 2026-09-09, **moved here from `environment/docker/container.go` later the same day** (V1). Docker's `ContainerUpdate` writes the Panel's `Resources` to the SAME scope, after Wings, on every Panel-side settings save; without the re-assert the redesign's properties are silently replaced and a memory ceiling can be *loosened*. Any future path that issues a Docker resource write must re-assert too — **and must do it from a layer that can see the slice phase.** `Environment.State()` is NOT the phase. |
| `server/slice_phase.go:currentPhase` | the single answer to "which band is live". `slicePhaseActive`, not the environment's process state. Anything that reads the environment's state to decide a band is the V1 bug again. |
| `internal/cgroups.EnsureRequest.KeepMemoryHigh` | set only by a re-assertion. Drops `MemoryHigh` from the resolved band, because Docker cannot have damaged it (`container.Resources` has no such field) and writing it is the one thing a repair can do harm with. |
| `config/config_docker.go:ResolveServerCgroupParent` | placement + `managed`. Override == node value is the opt-out. |
| `config/config_docker.go:ScopeEnsureRequest` | `ApplyDefaults`/`Parent` are set only when the server is on the node tier. |
| `server/slice_phase.go:sliceRequest` / `resolveScope` | deliberately split: `sliceRequest` decides *whether* to apply without needing Docker (so it stays unit-testable); `resolveScope` does the inspect at apply time. `req.Owner`, not `req.Scope`, is the "no request" sentinel. |

## Open questions a reviewer should press on

> **All four were pressed on by the 2026-09-09 review.** 1 and 2 turned out to
> be one blocking bug (F1) and are now fixed and re-documented; 3 was confirmed
> as an accepted, correctly recorded cost; 4 became F5 and now emits a startup
> notice. Left here as written for the record — read the fix-pass section at
> the end for where each landed.

1. **IO weight now has two writers on one file.** The Panel's per-server
   "Block IO Weight" reaches `io.bfq.weight` on `docker-<id>.scope` via runc;
   Wings' `IOWeight` reaches the same unit and systemd re-derives
   `io.bfq.weight` from it. Under the retired shape these composed
   multiplicatively across two cgroup levels. They no longer do. Documented in
   `CGROUP-SEMANTICS.md` Rule 7 as "use one or the other" — **not verified by
   experiment.** Worth an actual measurement on the e2e harness.
2. **Same collision for the memory ceiling.** The Panel's memory limit reaches
   the scope via Docker; Wings' `MemoryHigh`/`MemoryMax` now reach the same
   unit. Under the old shape a *looser* Wings value was inert (min-along-path);
   on one unit a looser value **replaces** the Panel's. That is a real change in
   blast radius for a misconfigured `WINGS_CG_MEMORY_MAX` and deserves an
   explicit decision — clamp to the Panel's value, or document loudly.
3. **The audit surface moved and shrank.** `systemctl show wings-<uuid>.slice -p
   MemoryMin` used to answer "what floor is this server committed to?" whether or
   not it was running. The answer now lives on `docker-<id>.scope`, which exists
   only while the container runs. `TODO.md` records this as an accepted cost, not
   a refuted one.
4. **The tripwire's threshold is read live** from the tier slice's own
   `MemoryMin` rather than from a config knob. That is deliberate (a knob can
   drift from reality; this cannot) but it is a judgment call beyond the literal
   brief, and it means the warning silently does not fire on a node whose
   `wings.slice` declares no `MemoryMin`.

## Still to do

- [ ] Merge the e2e coverage profile into the assay lane (see above) — the
      lane's headline number understates the series precisely where it matters
- [ ] Port the pelican series
- [x] Independent adversarial review of both commits (estate rule: **every**
      merged change gets one, no size exception) — DONE 2026-09-09,
      `REVIEW-2026-09-09-scope-redesign.md` (`2ddadee8`), verdict NOT-ACCEPT on
      F1 + F2; all six findings answered in the fix pass below
- [x] A fresh fix-verification pass over the 2026-09-09 review-fix commits
      (never the reviewer's own session, never a fork of the fixer) — DONE,
      `FIXVERIFY-2026-09-09-scope-redesign.md` (`6de29c5a`), verdict NOT-ACCEPT
      on V1; all six findings answered in the second fix pass below
- [ ] A fresh fix-verification pass over the SECOND review-fix pass (V1–V6).
      Same rule: not the fixer's session, not a fork of it. The thing to press
      hardest on is `KeepMemoryHigh` — it is the one place this pass chose to
      write *less* than the previous design did, on the strength of "Docker's
      `container.Resources` has no `MemoryHigh` field"; if that premise is ever
      false, memory.high silently stops being repaired
- [ ] `v1-legacy/README.md`, `v1-legacy/pr/*.md`,
      `v1-legacy/t2-per-server-placement/README.md`,
      `v1-legacy/t3a-slice-manager/README.md`, `v1-legacy/test/README.md` still
      describe transient slices and `io_bfq_weight`. The `pr/` set in particular
      tells the upstream-PR story in retired terms and needs its own pass before
      any PR is filed.
- [ ] Pre-existing broken relative links: `SETUP.md` links `CGROUP-SEMANTICS.md`
      and `STRATEGY.md` links `SETUP.md` / `patchstack/…` without the
      `v1-legacy/` prefix — stale since the `v1-legacy/` move.

---

## 2026-09-09 review-fix pass

Answers the six findings in `REVIEW-2026-09-09-scope-redesign.md`. The series
was rebuilt **forward** (see "How the series was built" above) — never a
scripted rebase, never an `--amend` on a non-HEAD commit. All nine patches
were re-exported; the branch tip in `build/wings-pterodactyl` is
`cgroup/v1.13.3` and the pre-fix tip is kept as
`backup/pre-reviewfix-20260909`.

| finding | where the fix lives | what it does |
|---|---|---|
| F1 (blocking) | patch 0004 + patch 0006, `environment/docker/container.go` | `InSituUpdate` re-asserts the scope properties after `ContainerUpdate` returns. 0004 adds the call; 0006 makes it phase-aware and ramp-free once phases exist. |
| F2 (blocking) | patch 0008, `internal/cgroups/integration_test.go` | the e2e case calls `(*conn).memoryCurrent` instead of the test's own `itTypeProp("Scope", …)`. Mutation-verified: reverting `sysd.go` to `"Slice"` now fails the case. |
| F3 | patch 0004, `config/config_docker.go` | `PerServerSlices` doc comment rewritten to the shipped design; the dangling `ServerSliceName` reference is gone. |
| F4 | `test/e2e-systemd/inner-test.sh` (not in the series) | §1 and §2 retargeted at the shipped scope design; §3 relabelled as the separate `t3a-slice-manager`; the summary reports the two tallies separately so "ALL PASS" cannot be read as validating the series alone. |
| F5 | patch 0004, `internal/cgroups/{sysd,ensure}.go` | `Result.TierFloorUnset` + one `Info` per Wings process when the tier declares no `MemoryMin` and the overcommit tripwire is therefore inert. |
| F6 | patch 0004, `internal/cgroups/scope.go` | ledger comment now states the restart-empties-it caveat plainly. |

**Decisions taken, with reasons.**

- *Which phase the `InSituUpdate` re-apply uses.* **SUPERSEDED — this reasoning
  was wrong, and the fix-verification found it (V1). Read the next section.**
  The `Environment` cannot see
  the server's slice phase (that lives in `server/slice_phase.go`, a layer up),
  so it reads its own process state: still starting → startup band, otherwise
  steady. That is the same signal the phase machinery keys on. The two can
  disagree only for a server that stages a startup band **and** either names a
  `WINGS_CG_STEADY_MATCH` that fires off the running transition or has already
  burnt its startup grace, and only until the next application. Applying
  nothing was the strictly worse option: the Panel's values then stand until
  the container is next started.
- *No steady ramp on the re-apply.* **PARTLY SUPERSEDED: the premise
  (`ContainerUpdate` never touches `MemoryHigh`) is correct and was
  independently confirmed, but the conclusion held only when the inferred phase
  matched the real one. The second pass keeps the premise and draws the
  stronger conclusion from it — the re-assert writes no ceiling at all — and
  the `ramp bool` is gone again, because both remaining callers pass true.**
  `applyScopeProps` gained a `ramp bool`.
  The ramp exists for the startup→steady handoff, where the ceiling genuinely
  drops below a load-time peak. A re-assertion writes the value that is
  already on the unit (`ContainerUpdate` does not touch `MemoryHigh`), so
  there is nothing to walk — and a reclaim-paced loop bounded by
  `rampMaxDuration` (10 min) would otherwise sit on the Panel-sync path.
- *The optional memory-ceiling clamp the review raised — NOT implemented, and
  deliberately so.* The review already calls the re-apply the load-bearing fix
  and the clamp optional. Refusing to apply a `MemoryMax` looser than Docker's
  own container config would (a) duplicate a rule the kernel already enforces
  from the other direction — a limit looser than the tier's is inert by
  min-along-path (Rule 2), so the only case the clamp catches is a `WINGS_CG_
  MEMORY_MAX` *deliberately* set above the Panel's number; (b) invert the
  documented precedence, where a node-admin-only `WINGS_CG_*` variable
  overrides Panel data rather than being capped by it; and (c) silently drop a
  configured property, which is the class of behaviour this redesign spent its
  whole rationale removing. If a guard is wanted later, the honest shape is a
  **warning** when `MemoryMax` exceeds the Panel limit, not a refusal. Filed
  here rather than built.

**Re-verified after the fixes** (real output in the fix-pass report):

- `patchstack/scripts/test.sh pterodactyl` — build + strict vet + unit tests +
  both integration compile checks: green.
- `INTEGRATION=1 patchstack/scripts/test.sh pterodactyl` — `dockerintegration`
  placement tests 5/5.
- `test/e2e-systemd/run-e2e.sh` — `SHIPPED SERIES (sections 1, 2, 4): PASS`,
  `t3a-slice-manager (section 3): PASS`.
- F2 mutation check — `sysd.go` reverted to `"Slice"`, e2e re-run:
  `--- FAIL: TestScopeMemoryCurrentIsReadableIntegration`, harness exit 1.
  Restored, green again.
- `patchstack/scripts/coverage.sh pterodactyl` — assay R1 **PASS**, 340/639 =
  53.21%, floor 50.0.
- `patchstack/scripts/build-image.sh pterodactyl cgroup.2` →
  `wings-local:1.13.3-cgroup.2`.

**Known cosmetic, deliberately not fixed:** `gofmt -l` flags a pre-existing
trailing blank line at EOF in `internal/cgroups/sysd.go`, plus
`internal/cgroups/{phase_test,scope_test}.go` and `server/server.go`. All
predate this pass; fixing them means another forward rebuild of the series for
no behavioural gain. Fold them into the next patch that touches those files.
*(Done on 2026-09-09 in the second review-fix pass below — `gofmt -l` is now
clean on the whole tree.)*

---

## 2026-09-09 second review-fix pass (answers the fix-verification, V1–V6)

Answers `FIXVERIFY-2026-09-09-scope-redesign.md` (`6de29c5a`), which returned
NOT-ACCEPT on **V1**: the first pass's re-assertion inferred its property band
from `Environment.State()`, which is not the slice phase. Series rebuilt
**forward** again (patches 0004 and 0006 amended in place, 0005/0007/0008/0009
re-applied on top, all nine re-exported). Pre-fix tip kept as
`backup/pre-reviewfix2-20260909`; the exported series reproduces the branch
byte-identically (tree `fe3104b7`).

| finding | where the fix lives | what it does |
|---|---|---|
| V1 (blocking) | patch 0006 — `environment/docker/container.go`, `server/{update,slice_phase}.go`, `internal/cgroups/ensure.go` | The re-assertion moves OUT of `Environment.InSituUpdate` and into its caller, `Server.SyncWithEnvironment`, as `Server.reassertSliceProps`. The band comes from `currentPhase()` — the phase this layer holds — not from Docker's process state. It also stops writing `memory.high` at all (`EnsureRequest.KeepMemoryHigh`). |
| V2 | patch 0004 (`environment/docker/scope.go`) + patch 0006 (`server/slice_phase.go:resolveScope`) | Both apply paths bail out at Debug when the container is not running, so the "runs under the node tier without its own resource guarantees" Warn no longer fires on every server start and on settings saves for stopped servers. |
| V3 | patch 0004 — `internal/cgroups/{sysd,ensure,integration_test,ensure_test}.go` | `TestApplyTierFloorUnsetIntegration` exercises `Apply`'s own classification against two real tier slices (one declaring `MemoryMin`, one not). `Result` now separates `TierFloorUnset` (read succeeded, no budget) from `TierFloorErr` (could not read), with a line and a `sync.Once` each, so a transient D-Bus error no longer asserts something about the administrator's configuration. |
| V4 | `wings-cgroups/CGROUP-SEMANTICS.md` rule 7 | The "`systemctl show` agrees with `io.bfq.weight` once more" claim is replaced by the measured table: 4950 derives 540, they are different scales and never agree numerically; what the repair restores is systemd's derivation rather than the Panel's raw value. |
| V5 | `test/e2e-systemd/inner-test.sh` | The folded `E2E: ALL PASS` line is gone — the final line is a concatenation of the two per-tally verdicts and nothing else. Skips are tracked and named in the verdict, so a PASS cannot quietly cover a section that did not run. Section 2's degraded NOTE is replaced by a real assertion (see below). |
| V6 | patch 0006 — `server/update.go` | Decided deliberately: the re-assertion is **not** gated on `InSituUpdate`'s error. Docker restores its own `HostConfig` when `ContainerUpdate` fails but does not roll back cgroup writes runc already made, so a half-applied update is the state that most needs repairing. Documented at the call site. |

**Why V1's fix is shaped this way.** `applySliceProps` could not be reused: its
`phase == PhaseSteady && !req.Staged()` early return is right for a transition
and wrong for a re-assert. So the two entry points now build their requests
separately and share `dispatchSliceProps`. The re-assert additionally sets
`KeepMemoryHigh`, which is the second half of the fix and worth keeping
straight: `memory.high` is the **only** property Docker's write cannot reach
(`container.Resources` has no such field) and the **only** one whose
re-application can hurt — it reclaims immediately if the workload is above it,
and a cgroup reclaims straight through its own `memory.min`. Suppressing it also
removes a race the first pass would still have had: a settings save landing
while `enterSteady`'s ramp is walking the ceiling down would have written the
resting value in one shot and cut the ramp short.

**V1 reproduced, then fixed, measured on the e2e harness** (systemd 257,
cgroup v2, `native.cgroupdriver=systemd`; now a permanent assertion as
`inner-test.sh` section 5, so it cannot regress silently):

```
server at its load-time working set, startup band applied
    memory.min=200M  memory.high=400M  memory.current=324,079,616

panel settings save (docker update)          cpu.weight 800->39, memory.low 300M->512M

BUGGY  steady band, one-shot, mid-load
    memory.current  323,854,336 -> 103,923,712   reclaimed THROUGH its own 200M floor
    memory.events   high +174

FIXED  startup band, memory.high left alone
    memory.current  315,379,712 -> 315,523,072   untouched
    memory.events   high +0
    memory.high     419,430,400 (the startup 400M, unchanged)
    repaired        cpu.weight=800  memory.low=300M  memory.max=450M
```

**Section 2's contrast, fixed rather than softened.** The old assertion — a raw
cgroupfs write does not survive `daemon-reload` — is false on systemd 257 and
had degraded to a NOTE. The reason: a `docker-<id>.scope` is transient and has
no unit file, and `daemon-reload` re-reads unit files. Measured here:

```
set-property MemoryMin=64M MemoryLow=96M   ->  67108864 / 100663296
raw cgroupfs write                          ->  11108352 / 33554432
daemon-reload                               ->  11108352 / 33554432   (raw value survives)
any later set-property on the unit          ->  67108864 / 100663296  (systemd's view wins)
```

So the section now asserts the true and load-bearing statement instead: systemd
re-derives every managed attribute from its own view the next time a property is
set on the unit, which for a Wings-managed server is every property application.

**Re-verified after the fixes** (real output in the round-3 report):

- `patchstack/scripts/test.sh pterodactyl` — green; `gofmt -l` now clean.
- `INTEGRATION=1 patchstack/scripts/test.sh pterodactyl` — `dockerintegration` 5/5.
- `test/e2e-systemd/run-e2e.sh` — `E2E: series -> PASS | t3a-slice-manager -> PASS`,
  exit 0, including the new section 5 and the new
  `TestApplyTierFloorUnsetIntegration`.
- `patchstack/scripts/coverage.sh pterodactyl` — assay R1 **PASS**, 347 covered,
  53.80%, floor 50.0 (untouched, still one commit in its history).
- `patchstack/scripts/build-image.sh pterodactyl cgroup.3` →
  **`wings-local:1.13.3-cgroup.3`** (`c7d56b8fd034`). Nothing deployed; the live
  node still runs `wings-local:1.13.1-cgroup.11`.

**The one risk V2's guard introduces, checked.** Both apply paths now skip a
container whose `State.Running` is false, and the primary apply
(`power.go`, immediately after `ContainerStart`) would silently do nothing if
Docker did not report the container running by then. Measured against the real
daemon: 10/10 create+start+inspect cycles report `Running=true` with no delay,
and the only `false` is a container that has already exited — which has no scope
to write to either way, so skipping is the correct answer there.

**Still not verified.** The honest gap from the first hand-off stands unchanged:
nothing has run against a live Wings. The band decision now has unit tests and
the kernel consequence has an e2e assertion, but the wiring from a real Panel
sync through `SyncWithEnvironment` to `EnsureForServer` is still only covered by
tests plus reading.
