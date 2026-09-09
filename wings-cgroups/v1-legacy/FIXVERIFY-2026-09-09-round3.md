# Fix-verification — round 3 (V1–V6)

Fourth session in the review→fix→verify chain on `wings-cgroups/v1-legacy/`.
Subject: `a4fc1db5` (patch series — V1/V2/V3/V6), `1b4a720f` (e2e harness — V5 +
new section 5), `3a2b8cb7` (docs — V4), `32a92d9e` + `d401fb52` (brief).

Fresh session, no prior context. Every number below is from my own run; nothing
is quoted from the round-3 report except where explicitly compared against it.

## Verdict: **ACCEPT-WITH-FOLLOWUPS**

All four code findings (V1, V2, V3, V6) are correctly fixed. V4 and V5 say what
they claim. Every gate is green on my own fresh runs, and I reproduced V1's
kernel consequence and the fix's non-eviction firsthand on the privileged
systemd harness.

**No blocking defect.** Unlike rounds 1 and 2, the fixer's self-report does not
undersell what is still wrong — the continuation brief's "Still not verified"
paragraph is accurate. There are, however, six followups, and the first is a
false claim in a commit message, which is exactly the class of thing this chain
exists to catch.

---

## Tree state

Build clone `build/wings-pterodactyl` verified byte-identical to the committed
series both before and after all mutation work:

```
git format-patch -o <tmp> v1.13.3..HEAD  &&  diff -rq <tmp> patchstack/patches/pterodactyl-v1.13.3
IDENTICAL: build clone == committed series
```

## Gates — my own fresh output

| gate | result |
|---|---|
| `patchstack/scripts/test.sh pterodactyl` | `test.sh: ALL OK (pterodactyl)`, exit 0 |
| uncached `go test -count=1 ./config/... ./environment/... ./server/... ./internal/cgroups/...` | all `ok`, exit 0 |
| `INTEGRATION=1 patchstack/scripts/test.sh pterodactyl` | `ok github.com/pterodactyl/wings/environment/docker 4.182s`, dockerintegration 5/5, exit 0 |
| `test/e2e-systemd/run-e2e.sh` | exit 0 — **`E2E: series -> PASS \| t3a-slice-manager -> PASS`**, 23 `PASS:` lines, **0 `SKIP`, 0 `FAIL`** |
| `patchstack/scripts/coverage.sh pterodactyl` | assay R1 **PASS**, 347 / 645 executable = **53.80 %**, `fail_under = 50.0`, exit 0 |
| `assay.toml` floor history | `git log -- patchstack/assay/assay.toml` → exactly one commit, **`bfb2077b`**. Floor untouched. |
| image | `wings-local:1.13.3-cgroup.3` = `c7d56b8fd034`, 2026-09-09 01:30:06 UTC. Present on the host daemon. |

Nothing deployed; the live node still runs `1.13.1-cgroup.11`. Untouched.

Note: `test.sh`'s `go test` line carries no `-count=1`, so the script's own
output is cache-served (mine printed `(cached)` for every package). I re-ran the
same package set uncached on a copy of the tree to get a real result. See **W7**.

---

## V1 — the one that matters

### The bug, reproduced firsthand

Section 5 of the harness stages a real container under a real tier, builds a
load-time working set above its own `memory.min`, lets a `docker update` clobber
the scope, and then runs both sequences. My run:

```
PASS: server is at a load-time working set of 324354048 bytes, above its own 200M memory.min
PASS: the panel save clobbered the scope: cpu.weight 800->39, memory.low 300M->512M
PASS: steady band mid-load reclaimed 324354048 -> 103272448, THROUGH its own 200M memory.min (the eviction V1 described)
PASS: the re-assertion left the working set alone: 319299584 -> 319307776, still above the 200M floor
PASS: the re-assertion did not move memory.high (still the startup 400M)
PASS: everything the panel save DID reach was repaired: cpu.weight=800 memory.low=300M memory.max=450M
```

The buggy sequence reclaims 324 MB → 103 MB, straight through the server's own
200 MB `memory.min`. The fixed sequence leaves the working set alone (319.30 MB
→ 319.31 MB) while repairing everything Docker's write reached. Round 3's report
quoted 323,854,336 → 103,923,712; mine is 324,354,048 → 103,272,448 — same
shape, different bytes, which is what a live measurement should look like.

### The fix is in the right place

`Server.reassertSliceProps` (`server/slice_phase.go:366`) →
`Server.reassertRequest` (`:379`) → `s.sliceRequest(s.currentPhase())` (`:380`).
`currentPhase()` (`:259`) reads `slicePhaseActive`, the flag the server layer
owns, set in `beginSlicePhase` and cleared in `enterSteady`/`endSlicePhase`. The
Docker layer's `applyScopeProps` (`environment/docker/scope.go`) is documented
as callable only from power transitions, and `container.go:151` records why the
re-assert left it. `update.go:70` is the single caller.

### Mutation-tested against the unit suite

Baseline uncached suite green. Each mutant applied to a pristine copy, full
suite re-run with `-count=1`:

| # | mutation | result |
|---|---|---|
| M1 | `reassertRequest`: `s.currentPhase()` → `cgroups.PhaseSteady` (the retired `Environment.State()` heuristic's outcome in the steady-match window) | **KILLED** — `slice_phase_test.go:323` |
| M2 | `currentPhase()` always returns `PhaseStartup` | **KILLED** — 5 assertions, `:339 :342 :364 :367 :388` |
| M3 | `reassertRequest` drops `req.KeepMemoryHigh = true` | **KILLED** — `slice_phase_test.go:330` |
| M4 | `reassertRequest` drops `req.RampStep = 0` | *SURVIVED* — see **W3** |
| M5 | `props()` ignores `KeepMemoryHigh` | **KILLED** — `phase_test.go:189, :194` (both bands) |
| M6 | `Staged()` no longer neutralises `KeepMemoryHigh` | **KILLED** — `phase_test.go:208` |
| M7 | delete `s.reassertSliceProps(…)` from `update.go:70` | *SURVIVED* — see **W2** |
| M8 | `reassertSliceProps` → `s.applySliceProps(s.currentPhase(), reason)` | *SURVIVED* — see **W2** |
| M9 | `resolveScope` drops the not-running guard | *SURVIVED* (needs a daemon; expected) |
| M10 | `applyScopeProps` drops the not-running guard | *SURVIVED* (needs a daemon; expected) |

So the three named unit tests are **not** F2-style. They assert on
`reassertRequest()` — the real production function — and they kill the mutants
that matter: the band choice in both directions, the ceiling suppression at both
the request and the `props()` layer, and `Staged()`'s independence from
`KeepMemoryHigh`.

### W1 — section 5 is NOT a regression guard for the V1 code fix

**Severity: medium (false claim, not a code defect).**

Commit `1b4a720f`'s message says section 5 "gives V1 a permanent regression
guard." It does not, and cannot.

Section 5 contains no invocation of any Wings code. Everything in it is
`docker run` / `docker update` / `systemctl set-property` — I grepped the whole
section for a wings or Go invocation and there is none. It *hand-issues* the
property sets the two code paths would issue. And section 4's `cgroups.test`
binary is compiled from `./internal/cgroups/` only (`run-e2e.sh:28`), a package
that does not contain `Server.currentPhase`.

Reproduced, not argued. I applied M1 to the build clone —
`server/slice_phase.go:380` reverted to `s.sliceRequest(cgroups.PhaseSteady)`,
the exact outcome of the retired heuristic during the steady-match window — and
ran the harness:

```
EXIT=0
  SHIPPED SERIES (sections 1, 2, 4, 5): PASS
E2E: series -> PASS | t3a-slice-manager -> PASS
```

The e2e harness is fully green with the V1 fix reverted.

This does not devalue section 5. It is a good and genuinely load-bearing
assertion — it proves the *kernel premise* the whole design rests on (a cgroup
reclaims through its own `memory.min` when pushed below its own `memory.high`),
in both directions, so the section cannot silently stop measuring. It is simply
not what the commit message says it is. The actual V1 regression guard is
`TestReassertUsesThePhaseNotTheEnvironmentState`, which does kill M1.

Fix: correct the sentence in the record, and add a line to `inner-test.sh`'s
header saying section 5 asserts kernel behaviour, not Wings' band choice. The
`CONTINUATION` brief is already honest about this ("the wiring … is still only
covered by tests plus reading"); only the commit message overstates.

### W2 — the whole re-assert can be deleted and nothing fails

**Severity: medium (test gap; the code is correct as written).**

Two surviving mutants, both in the seam between the well-tested
`reassertRequest()` and its caller:

- **M7**: deleting `s.reassertSliceProps("panel-side in-situ resource update")`
  at `server/update.go:70` leaves the full uncached unit suite green, and the
  e2e harness cannot see it either. That is the entire feature and the entire V6
  decision, with no test at any level.
- **M8**: rewriting `reassertSliceProps` as
  `s.applySliceProps(s.currentPhase(), reason)` also leaves everything green —
  and that silently restores the `phase == PhaseSteady && !req.Staged()` early
  return at `slice_phase.go:333`, i.e. exactly the behaviour
  `TestReassertAppliesEvenWithNothingStaged` was written to forbid. The test
  asserts on `reassertRequest()`, never on the dispatch, so it is blind to it.

Nothing here is wrong today — I read the chain and it is right, and section 5
proves the kernel consequence. But the "does this feature exist at all" mutant
survives, which is worth knowing before a live deployment.

Cheapest honest fix: make `dispatchSliceProps`' applier injectable (a package
`var` holding `cgroups.EnsureForServer`) so a test can drive
`SyncWithEnvironment` with a stub environment and assert the request that came
out — phase, `KeepMemoryHigh`, and that it ran at all. Not trivial (it needs a
stub `environment.ProcessEnvironment`), so this is a followup, not a condition.

### W3 — one assertion in the V1 test cannot fail

**Severity: low.**

`server/slice_phase_test.go:332`:

```go
if req.RampStep != 0 {
    t.Error("a re-assert must not sit on a reclaim-paced ramp; it writes no ceiling to walk")
}
```

`RampStep` is populated from `docker.per_server_slices.steady_ramp_step`
(`config/config_docker.go:452`, `:462`), and `sliceTestConfig` never sets it, so
it is 0 in these tests whether or not `reassertRequest` zeroes it. M4 confirms:
deleting `req.RampStep = 0` leaves the suite green.

Harmless in production — with `KeepMemoryHigh` set, `props.MemoryHigh` is nil,
so the ramp branch at `internal/cgroups/ensure.go:148` is unreachable anyway —
but the line is currently defended by an assertion that cannot fail. One-line
fix: set `c.Docker.PerServerSlices.SteadyRampStep = "64M"` in `sliceTestConfig`.

---

## The flagged judgment call — `EnsureRequest.KeepMemoryHigh`

The fixer asked for fresh eyes on the premise that Docker's
`container.Resources` has no `MemoryHigh` field. **Checked directly against the
exact version in use. The premise is true.**

- `go.mod:21` → `github.com/docker/docker v28.3.3+incompatible`.
- `container.Resources` (`api/types/container/hostconfig.go`), full field list
  dumped from the module in the build cache: no `MemoryHigh`, and no field that
  could reach `memory.high`. Memory-side fields are `Memory`,
  `MemoryReservation`, `MemorySwap`, `MemorySwappiness`, plus the deprecated
  `KernelMemory`/`KernelMemoryTCP`.
- `grep -ri 'memoryhigh\|memory\.high'` across the **entire** `docker/docker
  v28.3.3` module: **zero hits**.
- `ContainerUpdate` takes `container.UpdateConfig`
  (`hostconfig.go:415`), which is exactly `Resources` (embedded) +
  `RestartPolicy`. Nothing else.

I also checked the half of the premise the fixer did not state, because it is
the one that would silently break the argument: Wings writes `memory.high` as a
**systemd property**, not a raw cgroupfs write (`internal/cgroups/sysd.go:167`,
`:305-307` — `sdbus.Property{Name: "MemoryHigh"}`). That matters because e2e
section 2 asserts systemd re-derives every managed attribute from its own view
the next time any property is set on a unit. Since `MemoryHigh` lives in
systemd's view of the scope, runc's `SetUnitProperties` during a Panel save
re-derives it back to Wings' value rather than clearing it. Had Wings raw-written
the ceiling, `KeepMemoryHigh` would have been unsafe. It does not. The chain
holds end to end.

### W4 — there is a cheap guard being left on the table

**Severity: low (easy win).**

The fixer said this "is a premise about someone else's struct with no
compile-time guard." A reflection test is a cheap runtime guard, and it is worth
the twenty lines. I wrote it, ran it against the real tree (**passes** on
v28.3.3), and proved it non-vacuous by pointing the field walk at
`"MemoryReservation"` instead of `"High"` — it fails with the intended message,
so the walk really does see the struct's fields.

Ready to apply to `internal/cgroups/` (its own file, no other change):

```go
package cgroups

import (
	"reflect"
	"strings"
	"testing"

	"github.com/docker/docker/api/types/container"
)

// A re-assertion sets EnsureRequest.KeepMemoryHigh and therefore writes no
// memory.high at all. That is safe only because Docker's ContainerUpdate
// cannot reach memory.high: container.UpdateConfig embeds container.Resources,
// and that struct has no MemoryHigh field, so runc never sets one.
//
// The premise is about a struct in someone else's module, and nothing in the
// compiler defends it. This does: if a docker/docker bump ever adds a field
// that could reach memory.high, this test fails and whoever bumped it has to
// re-decide whether a repair still gets to skip the ceiling.
func TestDockerCannotWriteMemoryHigh(t *testing.T) {
	rt := reflect.TypeOf(container.Resources{})
	for i := 0; i < rt.NumField(); i++ {
		name := rt.Field(i).Name
		if strings.Contains(name, "High") {
			t.Fatalf("docker's container.Resources gained %q: EnsureRequest.KeepMemoryHigh assumes a panel-side "+
				"ContainerUpdate cannot touch memory.high. Re-decide before shipping this docker bump.", name)
		}
	}
	// UpdateConfig is what ContainerUpdate actually takes; assert it adds
	// nothing beyond the embedded Resources and a restart policy.
	ut := reflect.TypeOf(container.UpdateConfig{})
	for i := 0; i < ut.NumField(); i++ {
		switch n := ut.Field(i).Name; n {
		case "Resources", "RestartPolicy":
		default:
			t.Errorf("docker's container.UpdateConfig gained field %q; re-check what a panel save can now reach", n)
		}
	}
}
```

So: I looked, and there **is** an easy win here. Also worth doing regardless —
`ensure.go:52-73`'s comment already names the type; a test makes it enforceable.

### W6 — the hand-run `systemctl set-property` gap: acceptable as-is

An administrator setting `MemoryHigh` on a scope by hand, bypassing both Docker
and Wings, is not repaired by a re-assert. I read `CGROUP-SEMANTICS.md`'s rule
as it now stands and it already bounds the promise correctly:

> **for the properties Wings manages, Wings' value is the one that persists,
> because it re-applies on every path where the other writer acts.**

"the other writer" is Docker throughout that section, and the preceding sentence
enumerates the three paths precisely (post-`ContainerStart`, reattach, post-
in-situ-update). It does not promise repair of an out-of-band write. No doc
change is required. A half-sentence naming the case explicitly would be a
courtesy to an operator who reads the bolded rule and stops there — optional.

---

## V2 — the not-running guard

**Correct, and it does not move the false-negative problem.**

- Guard placement: `server/slice_phase.go:227` (`resolveScope`) and
  `environment/docker/scope.go:66` (`applyScopeProps`). Both sit **before** any
  D-Bus call, so a genuine D-Bus failure on a genuinely running container still
  reaches `EnsureForServer`'s `Warn` at `internal/cgroups/ensure.go:156` / `:177`
  — the "runs under the node tier without its own resource guarantees" line the
  design leans on. The guard cannot mask the diagnostic it was protecting.
- Stale state: no. Both read `State.Running` from a **fresh** `ContainerInspect`
  on every call — `cli.ContainerInspect(ctx, s.ID())` and
  `e.ContainerInspect(ctx)` respectively. There is no cached process state on
  either path.
- The "10/10 create+start+inspect report Running=true" claim: independently
  reproduced. Ten `docker create` + `docker start` + `docker inspect -f
  '{{.State.Running}}'` cycles against this daemon returned `true` ten times out
  of ten.
- The path V2 actually complained about is covered: `server/power.go:185` calls
  `SyncWithEnvironment` during a server *start*, before the container is created
  — the pre-boot Sync — and `power.go:148` is the post-`ContainerStart` apply,
  which re-inspects. The guard turns the former into a Debug line and leaves the
  latter working.

**Observation, not a round-3 finding** (pre-existing since round 2, verified by
diffing `a4fc1db5^`): `resolveScope` logs an *inspect error* at **Debug**
(`slice_phase.go:224`) while `applyScopeProps` logs the same condition at
**Warn** (`scope.go:63`). A genuine Docker inspect failure on a running server
therefore skips a phase transition silently on the server-layer path. Round 3
did not touch this line; worth a look in a later round.

## V3 — `TierFloorUnset`

**Correct, and the test is genuinely mutation-sensitive.**

- `TestApplyTierFloorUnsetIntegration` (`internal/cgroups/integration_test.go`)
  runs under the `systemdintegration` tag, against **two real systemd slices**
  it creates itself (`itStartScopeIn` + `itSetSliceFloor`) — not a stub — and it
  runs live in e2e section 4. It is deliberately independent of the harness's
  own `wings.slice`.
- Both branches asserted, plus `TierFloorErr == nil` in each, `ParentFloor`,
  `Overcommit`, `FloorsTotal != 0`, and that the 16M floor really landed on the
  scope (`itTypeProp(… "Scope", "MemoryMin")`).
- `TierFloorUnset` and `TierFloorErr` are genuinely distinct conditions in
  `Apply` (`internal/cgroups/sysd.go:230-244`): `err != nil` → `TierFloorErr`
  (read failed); `!ok || limit == 0` → `TierFloorUnset` (read succeeded, nothing
  declared — systemd reports an unset `MemoryMin` as a plain 0). They feed two
  separate log helpers with two separate `sync.Once` budgets, so a transient bus
  error cannot spend the standing configuration notice's one line.
- **Live mutation test.** Forcing `res.TierFloorUnset = false` in `sysd.go` and
  re-running the harness:
  ```
  EXIT=1
  FAIL: wings internal/cgroups integration test
  === RUN   TestApplyTierFloorUnsetIntegration
      integration_test.go:320: TierFloorUnset is false for a tier with no MemoryMin; ...
  E2E: series -> 1 FAILURE(S) | t3a-slice-manager -> PASS
  ```
  Not an F2-style test. Tree restored and re-verified identical afterwards.

## V6 — re-assert runs regardless of `ContainerUpdate`'s error

**Implemented as described, no short-circuit anywhere in the chain.**

`server/update.go:52-70`: `InSituUpdate()`'s error is logged with `Warn` and
*not* returned; `s.reassertSliceProps(…)` is the next statement, unconditional
within the `!s.IsSuspended()` branch. The only early returns downstream are the
two intended ones — `req.Owner == ""` (unmanaged server, `slice_phase.go:368`)
and `req.Scope == ""` (no running container, `:399`). The suspended branch never
calls `InSituUpdate` either, so there is nothing to repair there. Correct.

Latency note, not a finding: the re-assert is synchronous inside
`SyncWithEnvironment`, which is reached from `POST /api/servers/:server/sync`
(`router/router_server.go:145`). It adds one `ContainerInspect` plus one D-Bus
round trip (bounded at `ensureTimeout` = 10 s) to that request. Round 2's
placement inside `InSituUpdate` was synchronous in the same path, so this is not
a change, and `InSituUpdate` itself already does an inspect + update there.

## V4, V5 — skimmed as instructed

**V5 verified in both directions.** The literal `ALL PASS` is gone from
`inner-test.sh`; the final line is a concatenation of the two per-tally verdicts
and nothing else. Skips are tracked per tally and appended as `, BUT NOT RUN: …`.
Proven both ways by my own runs: clean → `E2E: series -> PASS | t3a-slice-manager
-> PASS`; V3-mutant → `E2E: series -> 1 FAILURE(S) | t3a-slice-manager -> PASS`.
Both my runs had **zero** SKIPs, so nothing was hidden behind a green tally.
Section 2's restored assertion (raw write lands, then is discarded the next time
systemd touches the unit) passed on this host, and it is the statement that is
actually true here — `daemon-reload` alone does not wipe it on systemd 257.

**V4 verified against measurement.** Rule 7's table claims `IOWeight=4950`
derives `io.bfq.weight=540`. My own clean e2e run printed:
`NOTE: IOWeight=4950 derives io.bfq.weight=default 540`. Matches. Both stale
`InSituUpdate` pointers are corrected (`CGROUP-SEMANTICS.md` and
`patchstack/README.md`'s 0004 row), and 0006's row now records the relocation.

---

## Followups (none blocking)

| id | severity | what |
|---|---|---|
| W1 | medium | `1b4a720f`'s "gives V1 a permanent regression guard" is false — section 5 contains no Wings code and stays green with the V1 fix reverted. Correct the claim; note in `inner-test.sh`'s header that section 5 asserts kernel behaviour, not the band choice. |
| W2 | medium | Deleting the re-assert from `update.go:70` (M7), or rewiring it through `applySliceProps` (M8), leaves every test green. Add a seam so `SyncWithEnvironment`'s re-assert is assertable. |
| W3 | low | `slice_phase_test.go:332`'s `RampStep` assertion cannot fail — `sliceTestConfig` never sets `SteadyRampStep`. Set it to `"64M"`. |
| W4 | low | Add the reflection guard for the `container.Resources` premise (code above; verified passing and non-vacuous). |
| W5 | low | Narrow race: a re-assert that reads `currentPhase()==Startup` and loses to a concurrent `enterSteady`'s dispatched steady apply leaves the *startup* `MemoryMin/Low/Max/CPUWeight/IOWeight` with the *steady* ceiling. Window ≈ one `ContainerInspect`; consequence is looser protection, never eviction; strictly better than round 2. Probably not worth fixing — noted so it is a decision, not an oversight. |
| W6 | low | Optional half-sentence in `CGROUP-SEMANTICS.md` naming the out-of-band `systemctl set-property` case. The rule as written already bounds the promise correctly. |
| W7 | low | `test.sh`'s `go test` has no `-count=1`, so a gate script can report green entirely from cache. One-word fix. |

## Has the chain converged?

**On the code: yes.** V1's fix is right, it is in the right layer, its band
decision is mutation-tested at the unit level, and its kernel consequence is
reproduced end to end on real systemd. V2, V3 and V6 are correct, and V3 in
particular is now defended by a live, mutation-sensitive integration test. The
premise the fixer flagged for fresh eyes checks out against the exact pinned
docker version, and against the second half of the chain he did not state
(systemd owns the ceiling, so runc's write re-derives it rather than clearing
it). Rounds 1 and 2 each found a blocking bug; I could not find one here, and I
went looking with ten mutants and two live harness runs.

**Before live deployment I would still want one specific thing.** W2 is the gap
that matters operationally: nothing at any level notices if the re-assert stops
running. Combined with round 3's own honest note that the wiring has never
executed against a live Wings, that means the single most consequential path —
a real Panel settings save reaching `EnsureForServer` on a real server — is
covered by reading only. I would not block on it, but I would either add the
seam (W2) or do one live smoke on a throwaway server before the production
node, rather than treat the green harness as covering it. W1 should be corrected
in the record regardless, since it is precisely the kind of overstated evidence
line this chain has twice had to walk back.

Everything else is small enough to fold into whatever ships next.

---

Verified by a fresh session with no prior context, 2026-09-09.
Reproduction artefacts: 2 clean + 2 mutated `run-e2e.sh` runs, 10 unit mutants
over the uncached suite, a live docker `Running=true` sample, and a direct read
of `docker/docker v28.3.3`'s `container.Resources`.
