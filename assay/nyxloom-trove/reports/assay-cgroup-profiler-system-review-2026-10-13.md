# Independent Assay and cgroup-profiler system review

- Reviewer: Claude Opus 5.5 (`claude-opus-5-5`), high effort
- Claude Code CLI: 2.1.290
- Persistent Claude session: `28227ce4-cbdc-471a-8b69-a430af63fb67`
- Branch: `cgprofile-cp20-contract2-20261012`
- Reviewed base: `aa41988e8b58edb815568f727b3607a7ab59e5c0`
- Reviewed committed tip: `aec06c3e6de048597d0c61a6c1cee8cc18f4aed0`
- Snapshot also included the then-uncommitted `assay/tests/core/test_mutation_witness_unit.py` change.
- Snapshot diff SHA-256: `6e8fde2789c1c5cd8da6ebbacc8c22e779add88e4bb71cd5e2a4e1f40c3ba10c`
- Reviewer reported read-only tools only; no tests, gates, containers, or edits.
- Scope limitation reported by reviewer: skimmed (did not fully review) the SQL `sql.py` change and the large CMRU image test.

The report below is the reviewer’s original output. Its findings are review claims to validate against the implementation and evidence, not accepted changes.

---

# Hostile review: Assay / cgroup-profiler contract-2 integration wave (snapshot `aa41988e8…` → `aec06c3e6` plus the uncommitted test change)

This was a read-only review using Read, Grep and Glob only. I edited nothing and ran no tests, gates or containers. The checkout matches the snapshot for every file I compared against it (`mutation.py`, `mutation_witness.py`, `resource_limits.py`, `serve.py`, and the dirty `test_mutation_witness_unit.py`).

I read in full the Assay execution and attribution path, B158, the cgroup-profiler observer, the CMRU release-identity change (KI-69), the Run-Gate changes, the tester-unified changes, the docs, and the backlog. I only skimmed the 1.5k-line `sql.py` change (B151/B153/B154/B160) and the 3k-line CMRU image test. Neither is covered below.

## 1. Findings

### HIGH-1: `memory.events max` makes every candidate "crashed" under the estate's own tight-RAM-plus-swap pattern (needs a probe; likely blocking)

**Where:**
- `assay/src/assay/resource_limits.py:203-214`: `limit_hit` includes `memory_events_max`.
- `resource_limits.py:344-350`: an event at an ancestor with nothing at the candidate is treated as ambiguous and raises.
- `mutation.py:3005-3011` and `:4314-4321`.
- `DESIGN-GUIDE.md:667-669` deliberately says `max` counts even when the OOM path does not run.

**Trigger:**
- The B105 pilot container runs with `--memory=2g --memory-swap=8g` (`assay/tools/self-qualification-container.sh:536-537`).
- cli-extended R2 runs with `memory = "1536m"`, and the cgroup-profiler `gate.sh` uses 3g.
- In the kernel's memory charge path (`try_charge`), the `max` event fires when a charge would exceed `memory.max`, before reclaim runs, whether or not reclaim then succeeds.
- A leaf full of page cache (git snapshots of a large tree, a 9k-test pytest run), or anonymous memory being pushed to the 6 GiB of swap, raises `max` continuously.

**Consequence:**
- If the candidate cgroup's own count rises, the candidate is "crashed" and R2 ends `ERROR/EXEC_FAILED` for the campaign.
- `dev-gates.slice` has a finite `memory.max` (`run-gate.py:3942`), and admission repeatedly measured only 1.0–1.3 GiB headroom. That suggests it often sits near its limit.
- In that state a sibling's reclaim raises the slice's own (local) `max` count. That is the ambiguous case, and it aborts the whole lane.
- Every resume then reruns the R0 baseline, so a campaign can churn without making progress.
- The B110 plan itself (§8.1: "memory-full stall ≤ 5%") expects the pilot to run at the memory ceiling.

**Counter-evidence that does not transfer:** the contract-1 cgroup-profiler campaign (581 records) ran with only `cpus = "3"` (`scripts/cgroup-profiler/run-gate.toml:73-76`), so its own cgroup had no `memory.max`. No candidate has ever run under B145 in a 2 GiB container.

This is a superset refusal: benign successful reclaim falls into the same status as a real allocation failure.

**Fix direction:**
- Count `oom`, `oom_kill`, `oom_group_kill` and `pids max` as invalidating.
- Treat `memory max` as pressure telemetry, or let it invalidate only kills, followed by a retry run alone.
- The remaining gap is real but small: an allocation that is not allowed to retry can return `ENOMEM` after a `max` event without an `oom` event. Close it with the retry, not by blanket invalidation.

### HIGH-2 (plan/design): B157 rests on a premise that does not hold, and the v16 hard cut buys no safety

**Where:** `config.py:3123-3127`, `cli.py:741-747`, `mutation.py:3177-3199`, `verdict.py:3201-3207`, `verdict.schema.json` (`jobs.const = 1`), `reuse.py:14`.

**Why the premise fails:**
- The counters only ever increase, and a candidate's processes run inside its own before/after window. Every event that candidate causes is therefore inside its delta.
- Overlapping windows can only add other candidates' events to a delta. They can produce false invalidations; they can never hide a candidate's own event or create a false kill.
- B157's own text concedes this ("changing a valid mutation outcome into an infrastructure failure").

**Cost of the current choice:**
- `jobs = 1` is a throughput and false-invalidation trade, yet it is encoded as a wire-schema constant with a hard cut to v16.
- `assay verify` now refuses every v15 artifact, and every consumer must edit its lane files and regenerate evidence.
- v15 `--reuse-from` cold-starts, although reuse never carries outcomes anyway (B106 only chooses a test node to replay).
- `verdict.py` shows v16 exists *only* for this constant.
- B110's GO projection assumed three workers (`assay-B110-PLAN-2026-09-28.md:394-396, 416`) and was not re-derived.
- cli-extended's 1,400 candidates now clearly exceed its 720-minute window.

**Fix (before release, while it is still cheap):**
- Drop the v16 bump.
- Either restore `jobs ≥ 1` with retry-alone-on-overlap (alternative 1 below), or keep `jobs = 1` only as a loader/CLI policy so it can be relaxed later without another schema cut.

### MEDIUM-3: an ambiguous event aborts the whole lane, and five different conditions share one status

**Where:** `resource_limits.py:344-350` raises; `mutation.py:2866-2893` wraps everything as `ERROR/EXEC_FAILED`.

**Five conditions that all become `ERROR/EXEC_FAILED`:**
1. The candidate's own resource limit was hit (a test-suite fact the consumer can act on).
2. An event at a shared ancestor that cannot be attributed.
3. The observer timed out (5 s) or was unreachable while the host was loaded.
4. Counters were malformed.
5. A limit changed during the window.

The same "observation unavailable" condition gives `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` at preflight, but `ERROR` mid-run.

This conflicts with the estate's contention-agnostic rule: a pressure-affected result should be classed as infrastructure and retried. Nothing here is a false kill, but operators cannot tell "your suite exhausts pids" from "the daemon was slow".

**Fix:**
- Mark the candidate as unattributed and retry it in-run, a bounded number of times, alone.
- If it stays ambiguous, end R2 `INCONCLUSIVE` with a new reason code, never ERROR or PASS.
- Split the reason codes: `RESOURCE_LIMIT_HIT`, `RESOURCE_EVENT_UNATTRIBUTED`, `CGROUP_OBSERVATION_UNAVAILABLE`.

### MEDIUM-4: a failure in an R2-only dependency wipes out R0 and R1

`tests/fixtures/verdicts/r2_no_measurement_cgroup_observation_unavailable.json` makes the R0 claim `NO_MEASUREMENT` with the cgroup reason. B148 refuses before R0 starts (`DESIGN-GUIDE.md:749-758`).

So a cgroup-profiler outage removes R0/R1 evidence, which needs no cgroup observation. This wave makes the daemon a hard dependency (contract 2 only; every private-namespace gate goes through it), which widens the effect.

**Fix:** run R0/R1 and mark only R2 unavailable. If the time saving matters, make early refusal an explicit lane opt-in.

### MEDIUM-5: dropping observer contract 1 on the single shared host daemon is a breaking change with no safety gain

**Where:** `serve.py`, the `_observer_dispatch` change (diff 14183-14215); `resource_observer.py:58-59`.

**Why there is no safety gain:** contract-1 clients compared hierarchical counters at every ancestor. Those include every local event, so contract 1 over-invalidated but was never unsound. RW-569's rationale ("a client [could] treat hierarchical counters as local") describes a client bug that Assay already guards against.

**Trigger:** redeploying the daemon breaks any observer consumer running Assay from before B155. That includes released Assay 9.0.0, and in-flight worktrees based before `8c874ce8`. All worktrees share one daemon.

**Also:** a contract-1 request now gets an error tagged `"contract": 2`. A strict old client sees "malformed error" instead of the unsupported-version message.

**Fix:** keep serving contract 1, or at least echo the requested version in the refusal. Inventory observer consumers before the 1.0.0 deploy.

### MEDIUM-6: B158 allows exactly one Hypothesis version per Assay release

**Where:** `mutation_witness.py:1890-1899` and `:1946-1958`; `assay/pyproject.toml` test extra (`hypothesis==6.169.0`).

**Problem:**
- Any consumer on any other Hypothesis release loses cold-witness entirely, and Hypothesis releases often. B158 was itself caused by exactly this drift.
- The version-equality check adds nothing beyond the module-file digest, which is the actual reviewed object.

**Fix:**
- Allow a set of reviewed `(module digest, callable code)` entries and drop version equality.
- Confirm that 6.169.0 meets the 14-day freshness rule; I could not verify that here.
- The CMRU `markdown-it-py[linkify]>=4,<5` upper bound has no recorded reason, which the version policy requires.

### LOW-7: B158's diagnostic still misses the causes that matter

Several unsupported causes record no hook name:
- xdist active (nyxloom `session-extract` uses `-n 4`);
- Assay's own plugin registry not trusted;
- the report constructor not trusted;
- liveness-hook registry drift;
- hook-monitor install failure (`mutation_witness.py:2509-2537` and `:2599-2601`, plus `_mark_hook_registry_changed`, which filters on `_HOOKS`).

In those cases stderr says "no unsupported pytest hook names were recorded". That is the same non-actionable refusal B158 was meant to fix.

**Fix:** a closed `unsupported_reasons` vocabulary.

### LOW-8: an observation failure can replace a deadline or termination error

At `mutation.py:4300-4304`, the after-sample is read in `finally`. If it raises, the stored `execution_error` is lost: an operator SIGTERM or lane deadline becomes `ERROR/EXEC_FAILED` instead of `BUDGET_EXCEEDED/LANE_TIMEOUT` (which is retried on resume).

**Fix:** prefer the stored error and report the observation failure as a diagnostic.

### LOW-9: B157 is not enforced at the window itself

`_run_mutation_impl` (`mutation.py:4075`) and `_execute_mutation_jobs` (`:4880`) still accept `jobs > 1`. A process-local "active window" guard in `_run_attempt` would enforce the real invariant and cover future bypasses. This is moot if HIGH-2's retry design is adopted.

### LOW-10: production code loosened to satisfy old test fixtures

The legacy receipt branch in `witness_from_receipt` (`mutation_witness.py:1547-1559`) exists only for frozen W10 fixtures; the plugin always writes the new key. Update the fixtures instead. The risk is minimal because the branch is non-cold only.

### LOW-11: the docs overstate what "candidate cgroup" means

It is the gate container's cgroup. That cgroup also holds Assay itself and its git/snapshot work, so their events are charged to the candidate. This is conservative, but the docs (`DESIGN-GUIDE.md:644`) should say so.

### LOW-12: identity is checked by the daemon only, and the daemon is privileged

- The daemon binds the leaf by `SO_PEERCRED` plus `cgroup.procs` membership. Assay cannot check that the returned leaf is its own cgroup, so a path-mapping bug would go unnoticed.
- A cheap fix: add the kernfs cgroup inode number (the same in every namespace) to the response. Assay compares it with `stat` of its own namespace-root cgroup directory, and checks that the leaf counters fall between its local `/sys/fs/cgroup/memory.events` readings.
- The observer is "data-only" by verb set, but it runs inside a daemon that holds a writable host cgroup view and the system bus (P6 placement). The isolation is in code, not in privilege.

### No findings in the reviewed parts

- CMRU KI-69: protected release-identity pair, clearing in cleanup and ordinary steps, reserved names.
- cgroup-profiler release identity (CP-21).
- `gate.sh` git common-directory mounts (CP-22).
- tester-unified layering.
- `.dockerignore`.
- Run-Gate RG-91 (single-line JSON framing).
- B161 `claim_diagnostics` (incomplete runs only; `rigor` is a string).
- Refactoring of the receipt proof (`survivor_proof_failure` matches the old `_facts_match`).

### Gate evidence

- At `aec06c3`: 9,086 passed, 21 skipped, 3 failed.
- The corrected tests are uncommitted and the full registered gate has not been rerun. The wave is **not green**.
- The new real-child Hypothesis tests run with plugin autoload, so they certify tester-unified's plugin set (anyio via mcp, xdist, and so on), not consumers' environments.

## 2. Design reflection

**What each concept claims:**
- **R2** claims the test suite *detects* behavioural changes to this code.
- **B145 event counters** add one negative guard: a failure caused by the platform must not count as detection. Only false *kills* can inflate the score; a resource event cannot plausibly make a suite pass, apart from rare xfail/skip-on-error cases. So the guard is only strictly needed on kills.
- **cgroup-profiler sampling** (CPU, peak memory, pressure) adds nothing to the correctness claim; it is capacity planning.

**Is the separation real?** In the evidence and docs, yes (`DESIGN-GUIDE.md:635-640`; `resource_limit_evidence` is separate from liveness and profile data). In status and operations, no:
- Guard outcomes are folded into `crashed` and `ERROR/EXEC_FAILED`.
- The observer is a listener inside the profiler daemon, so a profiler outage is an R2 outage, and (MEDIUM-4) an R0 outage.

**Is visibility a prerequisite for any valid kill?** For a *qualifying* kill, a guard is justified because the B145 false-kill incident was real. It should qualify the candidate, not the lane. My recommended model:
- Survivors stand.
- Kills with no event in their window stand.
- Kills with an event in their window are retried alone. If the event persists, the candidate is `resource_unattributed`.
- R2 status: PASS/FAIL when every candidate is classified. `INCONCLUSIVE` with `RESOURCE_EVENT_UNATTRIBUTED` or `CGROUP_OBSERVATION_UNAVAILABLE` when that is not true.

**Are ancestor-local counters the right tool for nested slices?**

What they prove:
- The candidate's own regular counter covers its whole subtree.
- An ancestor's local counter is raised only when that ancestor's own limit, or a controller attached there, enforced.
- A change in an ancestor's hierarchical count alone is a sibling's subtree, and is correctly ignored.
- A finite versus unlimited limit is only relevant in that `max` events cannot fire at an unlimited level; global OOM is charged to the victim's own cgroup via `oom_kill`.

What stays ambiguous: a local event at a *shared enforcing* parent (a sibling's reclaim or fork refusal versus the candidate's). No counter can resolve that.

Treating it conservatively is right; aborting the lane on it is disproportionate (MEDIUM-3), and counting reclaim `max` events there makes it frequent (HIGH-1).

There is one structural escape: if every child of `dev-gates` has a finite limit and admission keeps the sum of children's limits within the parent's limit, the parent cannot be the binding limit. Only the daemon can verify that across siblings. B155 rejected "inferring safety from the sum of sibling limits", but as an admission invariant that the observer checks, it is sound under cgroup v2's no-internal-process rule.

**The architectures compared:**

1. **Current (contract 2 plus conservative ambiguity).** Sound and privilege-light. Liveness is poor under shared memory pressure, and it hard-depends on a privileged daemon.
2. **A dedicated cgroup per candidate (B121).** Exact candidate attribution; restores parallelism. Needs a delegated, writable sub-tree, which conflicts with B145's read-only mount and `cgroup.procs` checks: those must be redesigned so the candidate cannot escape (Assay owns the delegation; the candidate runs unprivileged inside its own cgroup). Deployment cost: medium; probably systemd `Delegate=`. Ancestor ambiguity still exists, but the container becomes the controlled enforcing level.
3. **A root-level or higher gate container.** Not recommended:
   - it violates the mandatory `dev-gates` placement (production shares the host);
   - it drops admission control;
   - root and the slices are still ancestors;
   - it does not remove the shared-parent problem unless those parents have no limits;
   - `--cgroupns=host` would expose production cgroups' names and counters, and the doctrine forbids it as a fix.
4. **Decoupling R2 from resource evidence.** Correct for *reporting* (separate statuses). Unsafe if kills count with no guard at all. A cheaper partial guard needs no cgroup: record the exception type of the first failure in the receipt, and treat `AssertionError` kills differently from `OSError`/`MemoryError`/`BlockingIOError` kills. Useful for "R2 completed, resource evidence unavailable", not as a qualifying substitute.
5. **Daemon necessity.** Inside a private namespace, Assay can read only its own cgroup. Ancestors need an outside view.
   - Static read-only binds of ancestor `*.events.local` files would work without a socket, but they bind by the launcher's word: Assay cannot prove from inside that the files belong to its ancestors.
   - Deriving the cgroup from `SO_PEERCRED` makes the daemon boundary legitimately stronger.
   - The improvement: a **separate minimal observer process** with read-only `/sys/fs/cgroup` and `/hostproc`, no bus and no writable cgroup view, plus the inode identity field (LOW-12).

**How responsibilities divide across tools:**
- Mostly sensible: Run-Gate places and mounts, Assay judges, cgroup-profiler observes, CMRU releases.
- Weaknesses:
  - the observer is coupled to the profiler daemon;
  - version policy is inconsistent (exact pins under a `>=` floor policy);
  - B110's GO criteria require P8 readiness (§8.1 item 4), but B108 phase 1 is OPEN, so no pilot can reach GO by its own definition;
  - B144 (covering-test ordering) and B116 (snapshot index refresh) become the main throughput levers under serial execution, but are not prioritised.

### Decision memo

- **Keep:** B145's guard on `pids max`, `oom`, `oom_kill` and `oom_group_kill`; attribution by candidate subtree plus ancestor-local counts; the read-only mount, `cgroup.procs` permission and overmount checks; peer-bound observer identity; KI-69; CP-21/22; B153/B154/B160 memory bounds; B158's intent.
- **Change:**
  - make `memory max` non-invalidating, or kill-only with a retry alone;
  - turn ambiguity into a per-candidate bounded retry and `INCONCLUSIVE`, not a lane abort;
  - split the reason codes;
  - adopt retry-on-overlap for B157, or at least drop the v16 cut;
  - keep contract 1 or echo it in refusals;
  - allow a set of Hypothesis digests;
  - stop R0/R1 being invalidated by the R2 observer.
- **Defer:** per-candidate cgroups (B121) until pilot data exists; root placement (reject); B119/B120.
- **Smallest decisive experiment:** probe A below. It takes minutes and decides HIGH-1.

## 3. Backlog and sequence

1. **Decisions first (D-entries):** HIGH-1's memory policy and HIGH-2's v16 cut. Both are cheap now and costly after release.
2. **Probes A–C (§4)**, before any pilot.
3. **Commit the B158 test fix**, plus a fix for LOW-7 if accepted. Then the full gate matrix.
4. **cgroup-profiler 1.0.0 release and deploy**, only after the observer-consumer inventory (MEDIUM-5). CP-20 closes on the contract-2 exact-tree R2, not on the release.
5. **B110 bounded pilot** (B118 tooling, B105). It shows that serial candidate execution works under 2 GiB, gives per-candidate cost, cold-kill rate and resource-event rate. It does not show qualification, 8-hour liveness, behaviour under sibling contention, or a representative tail (64 of 3,760 candidates). Under the current criteria it cannot produce GO until B108/P8 lands; recompute the projection for 1 worker. A serial NO-GO is the input to the B121 / alternative-1 decision.
6. **B131 analysis pilot.** It is independent of B110's outcome and shares only infrastructure; run it next, or first, since its plan is smaller. It is a non-qualifying measurement.
7. **cgroup-profiler contract-2 R2** (about 1,473 candidates at about 48 s each, roughly 20 hours serial). This supports CP-20 and B155 qualification.
8. **B105 full qualification** only after GO.

**Promote:** B116 and B144 (serial throughput).
**Explicitly defer:** B119, B120 (P10a REVISE), B121 (until step 5's data).
**Fold into this wave:** B155 + B157 + B158 docs, already together.

## 4. Verification and release matrix (none of this has been run)

**Probe A (minutes):** in a container with `--memory=256m --memory-swap=1g` under `$CGROUP_PARENT_DEV_GATES`, read a 1 GiB file twice and compare `/sys/fs/cgroup/memory.events` before and after. A positive `max` delta confirms HIGH-1 on kernel 7.1.

**Probe B (about 40 minutes):** use the exact B105 container shape. Run the R0 command twice back to back, reading the leaf `memory.events` and the observer's ancestor local counts around the second run. This predicts the B110 pilot's fate.

**Probe C (passive, about 1 hour):** sample observer ancestor local counts every 60 s during normal host activity, with no candidates. This gives the rate of ambiguous aborts.

**Then, in order:**
1. Assay `tester-unified` registered gate, including the analysis lane.
2. MDT `smoke`.
3. SQL PostgreSQL 70-case hazard gate (B132/B151).
4. cgroup-profiler `r0-r1` + `r3` gate and image build through `build-push.py` (identity refusal cases).
5. Run-Gate unit gate (rev 65).
6. CMRU gate (KI-69, `tester_unified_image`).
7. tester-unified image rebuild with the closure assertions.
8. Release order: CMRU → Run-Gate → Assay (source-backed in-repo; repin out-of-repo consumers before the daemon change) → cgroup-profiler 1.0.0 → daemon redeploy → pilots.

## 5. Verdict

**Not ready for release progression.**

- HIGH-1 is a likely liveness blocker for every memory-capped native R2 lane, including the B105 pilot this wave exists to enable. It needs probe A or B before anyone spends pilot time.
- HIGH-2 is a design error that ships an avoidable estate-wide hard cut. It should be decided before release; it is not a correctness defect.
- The full registered gate is red at `aec06c3` and has not been rerun on the corrected tests.

I am confident the reasoning on overlapping windows (HIGH-2) is correct. I am fairly confident about the kernel `max` semantics; the actual event rate is unmeasured, which is exactly what probes A and B measure.

## Follow-up review

The following addendum was produced by resuming the same persistent Claude Opus 5.5 high-effort session, read-only, against the same code snapshot. It corrects and narrows several claims from the first pass.

---

# Addendum to the read-only review: snapshot `aa41988e8` → `aec06c3e6` plus the uncommitted test diff

Read-only again. The main correction is to HIGH-1: I overclaimed and have withdrawn its central recommendation (item 1). HIGH-2's main proof holds, but I also overclaimed that serial execution adds no safety (item 2). Items 3 and 4 are revised, item 5 found no blocker.

## 1. `memory.events:max` (HIGH-1): revised; the steady-saturation question is unresolved

**How each event affects outcomes:**
- Every positive delta sets `limit_hit` (`resource_limits.py:203-214`).
- `_classified_bucket` turns any bucket into `crashed` when `limit_hit` is set (`mutation.py:4185-4197`, `:3005-3011`). That covers killed, survived, hung, budget_exceeded and equivalent alike.
- Cold and declared attempts force `crashed` on a delta (`mutation.py:4578-4587`, `:4645-4651`).
- A replay with a delta stops the lane (`mutation.py:4454-4463`).
- A delta on an exceptional exit raises an error (`mutation.py:4314-4321`).
- Any `crashed` candidate makes the R2 claim `ERROR/EXEC_FAILED` (`judge_mutation`, `mutation.py:5723-5724`).
- An event at an ancestor with no event at the candidate raises before any bucket is assigned, aborting the lane (`resource_limits.py:344-350`).
- `pids max`, `memory max`, `oom` and `oom_kill` all behave identically.
- The baselines (R0 and the cold R2 baseline) are never sampled. A baseline failure caused by a resource limit becomes an R0 FAIL, which is never a false PASS.

**Correction:**
- `max` can contribute to a false kill. The event itself does not fail an allocation, but the reclaim stall it marks can trip test or subprocess timeouts. Allocations that are not allowed to retry can also return `ENOMEM` without an `oom` event.
- So my earlier "treat `max` as telemetry" advice is **withdrawn**. The current rule is sound: it is conservative, but correct for kills.
- It is over-conservative for **survivors and equivalents**, where pressure cannot plausibly make the tests pass; the rare exceptions (strict xfail, skip-on-`MemoryError`) err towards FAIL, not PASS.
- For hung and budget_exceeded, only the label changes.

**Which classes need invalidation:** killed, hung and budget_exceeded, for any of the four counters. Survivors could be kept.

**This conflicts with B145 as accepted:**
- B145's oracles require that "memory event `max` and `oom` deltas receive the same infrastructure classification".
- Its chosen contract says `assay verify` "rejects a positive one on another bucket" (`4-backlog.md:12118-12120`).
- A survivor-preserving rule therefore needs a recorded decision amending B145 plus verifier changes. It is a wire-semantics change, not a tweak.

**Retry-alone is not enough.** It separates transient events (a sibling's, at an ancestor) from real ones. It cannot separate steady-state reclaim in the candidate's own cgroup, because the retry hits `max` again. The fix for that case is environmental: give native R2 lanes `memory.max` headroom above the working set, using `memory.high` to throttle instead. Adding memory-pressure (PSI) stall time as evidence is the alternative.

**Kernel semantics:**
- The only local corroboration is `DESIGN-GUIDE.md:667-669` ("`max` counts attempts to exceed the memory boundary even when the OOM path does not run").
- My claim that `max` rises *continuously* once a cgroup is full of page cache is unproven inference from kernel behaviour I could not check locally.
- **Decisive probe:** in the exact B105 container shape (`--memory=2g --memory-swap=8g`, private namespace, `$CGROUP_PARENT_DEV_GATES`), run the R0 command twice back to back. Read the leaf `/sys/fs/cgroup/memory.events` around the second run, plus the observer's ancestor local counts. A positive `max` delta means every candidate in this setup crashes. That would be an availability problem, not a soundness problem.

## 2. B157 and v16 (HIGH-2): main proof confirmed; "no safety gain" revised

**Where the window sits:** the counters are read immediately around `execute_plan` (`mutation.py:4275-4313`). The after-read happens in `finally`, so exceptional exits are covered.

**Outside the window:**
- materialization (`:4225`);
- the integrity check and teardown, which run Assay-owned git children (`:4394-4426`);
- worker submission (`:4889`);
- the one-time initial read (`:4132-4133`);
- the zero-duration sample for candidates that never started (`:4952-4956`).

None of those is a candidate test process. Replay, cold and declared attempts each get their own window.

**What is proven:**
- A refused fork, an OOM kill, or a `max` event that hits the candidate happens while its command runs, so it is inside its window. The counters only increase (`CounterDelta`, `resource_limits.py:165-174`).
- Extra events only push a candidate towards `crashed` or the ambiguous abort.
- When the candidate's own count is positive, ancestor events are ignored (`:344-354`); that cannot turn a `crashed` candidate back into a kill.
- So another candidate's *counter events* cannot produce a false `killed`.
- A detached grandchild that outlives `execute_plan` charges later events to someone else's window: a false invalidation only.

**Contradicting evidence I under-weighted:**
- Concurrent candidates can cause false kills **without any cgroup event**: CPU or I/O contention under the 3-CPU cap, `memory.high` throttling, shared external fixtures such as the PostgreSQL instance in SQL lanes, ports, and caches.
- B145 never covered this. The old consumer guidance made it the consumer's job ("raise `jobs` only as far as your command tolerates concurrent execution").
- `jobs = 1` does remove the interference Assay causes itself. So it has some correctness value, just not the "attribution" value B157 claims.

**Revised conclusion:**
- A serial default or policy is defensible.
- The verdict-schema constant (v16 hard cut) still protects nothing a verifier can check: v15 already recorded `judgment.r2.jobs`, and serial execution does not prove absence of host contention.
- v15 `--reuse-from` cold-starts for no safety reason; reuse only picks a test node to replay.
- Retry-alone-on-overlap would preserve the cgroup soundness without a schema cut.
- Recommendation: keep serial as the loader/CLI policy, drop the v16 bump.

## 3. R0/R1 versus R2 observation: revised (feasible, but needs a verifier change)

**Current behaviour:** the pre-R0 refusal returns through `_deliver_refusal` (`cli.py:3534-3550`), which marks every claim `NO_MEASUREMENT` (see the fixture `r2_no_measurement_cgroup_observation_unavailable.json`).

**The product can already represent the alternative:**
- The `HEAD_CHANGED` fixture shows a real R0 PASS beside an R2 claim of `NO_MEASUREMENT`.
- The lane's overall outcome rolls up by precedence `ERROR > NO_MEASUREMENT > BUDGET_EXCEEDED > FAIL > INCONCLUSIVE` (`verdict.py:509-515`). So the lane outcome and exit code 3 are **unchanged** for consumers; they just gain real R0/R1 claims.

**The blocker:**
- `verify.py:3033-3043` (`_INDEPENDENT_R2_TERMINALS`) does not include `CGROUP_OBSERVATION_UNAVAILABLE`.
- Today's verifier would therefore reject an R2-only cgroup claim.
- Released verifiers would also reject such new artifacts, so this must ship in the same schema/verifier cut.

**Smallest safe change:**
1. Add the reason to that set, allowed only beside a passing R0.
2. Have `run_lane` emit it after R0/R1, following the `HEAD_CHANGED` path.
3. Keep the pre-R0 refusal only as an explicit opt-in, for lanes where R0 is expensive. That cost is B148's legitimate reason for refusing early.

## 4. B110 GO and P8: revised (no circular dependency, but GO is currently unsatisfiable)

**The criteria:**
- Plan §8.1.4: "P0–P8 … merged, reviewed and gated. The pilot's P8 analysis reports no evidence error."
- B110 acceptance requires "B108 phase 1 … merged" and a recorded Pilot GO (`4-backlog.md:11405, 11417`).
- The B108 index entry (line 224) and its section (line 10966) say "OPEN". B127 says "B108 phase 1 (P8) is built in this package" (line 11838). These statuses are inconsistent.

**What the code shows:** the P8 core exists (`assay_analysis/campaign.py`, `project()` at line 1278), but it refuses pilot progress streams: `raise ValueError("pilot selection unsupported before P7")` (`campaign.py:552-553`). B131 already notes that P8 "refuses selected-pilot progress".

**Consequence:**
- Nothing is circular: P8 does not need pilot data.
- But "P8 analysis reports no evidence error" cannot currently be met for a pilot stream, so the pilot can produce measurements but not a GO.
- The §7/§8 projection also still assumes `--project-jobs 3` and "/ effective workers".

**Recommendation:** amend the plan or backlog now:
1. Either teach P8 to accept `selection_sha256` progress (after P7), or name `b110_pilot_report_check.py` plus `project()` as the GO evidence.
2. Re-derive the GO projection for one worker.
3. Correct B108's status.

## 5. Scope check: no blocker found; limitations remain

**SQL:**
- The changed interface is that the core now passes a **one-pass generator** to the adapter's context preparer (`mutation.py:1085-1130`).
- Only the SQL adapter implements these hooks (`sql.py:5481-5498`), and `_collect_sql_mutation_context` consumes it in a single pass (`sql.py:4023-4046`).
- The target-aware preparer hook was removed from SQL, so the `getattr` at `mutation.py:890` falls through to the bounded preparer. That is benign.
- The target-exclusion identities match: both `MutationTarget.path` (`mutation.py:536-542`) and the context paths (`runner.py:3527-3546`) are relative to the repository top.
- Residual risk is low: any future preparer that iterates twice would silently see an empty second pass. A Protocol note or test would cover it.
- I did not review the SQL scanner logic itself (B151/B160 bounds). The 70-case PostgreSQL hazard gate remains the evidence.

**CMRU `test_tester_unified_image.py`:** not inspected, beyond confirming the Dockerfile interface it exercises. That limitation stands.

## Net effect on the verdict

- **HIGH-1 is downgraded** from "soundness/design" to "availability, pending the probe". The rule is sound; whether it makes every candidate in a 2 GiB lane crash is unproven.
- **HIGH-2 is narrowed** to "drop the v16 schema constant; serial as policy is fine".
- **Medium findings:** MEDIUM-4 needs a verifier rule. The new B110 GO/P8 gap is a planning blocker, not a code blocker.

The wave is still not ready for release progression: the B110 GO path, the full gate on the corrected tests, and the probe all remain open.

## Independent kernel-documentation cross-check

The official Linux cgroup v2 documentation defines `memory.events:max` as the count of times usage was about to exceed `memory.max`; it says the cgroup enters OOM only if direct reclaim fails. Therefore a positive `max` delta alone does not prove allocation failure. This supports reviewing how Assay uses `max`; it does not establish how often it will fire in the estate’s 2 GiB pilot container. That remains a runtime measurement.

Source: [Linux kernel cgroup v2 documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html#memory-interface-files).
