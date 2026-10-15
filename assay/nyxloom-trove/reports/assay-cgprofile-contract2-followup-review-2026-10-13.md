# Follow-up review: B110/P8, the cgroup-profiler survivors, and release blockers (HEAD `ad679a15a`)

This is the complete text of the review I just finished; nothing was re-run. It was read-only: no edits, tests, gates or containers. Gate outcomes are quoted only as the backlog records them. Findings are tagged **[DEFECT]** (proven from code or documents), **[EQUIV]** (likely equivalent mutant) or **[ASSUMPTION]** (unresolved).

**Verdict: not release-ready.**
- B110 cannot honestly reach its GO decision. Under B157 (`jobs = 1`) its 5-hour budget is impossible by arithmetic, and P8 cannot analyse the pilot anyway.
- cgroup-profiler's R2 lane would predictably FAIL. The survivors' code is unchanged, there is no equivalence ledger (B120 OPEN), and only 581 of 1,593 candidates were ever accounted.
- The full registered gate and the PostgreSQL hazard gate have not run on this tree.

## 0. What changed since `aec06c3e6`
- **`0c4b17e77`** saves the earlier system review report.
- **`6c46f59ef`** realigns three cold-witness tests in `assay/tests/core/test_mutation_witness_unit.py`. pytest-cov receipts now record `unsupported: true` with `unsupported_pytest_cov_only: true`.
  - The code accepts that combination for the declared-command proof and refuses it for a cold stop (`mutation_witness.py:557-574, 611-675`; `cold_witness_from_receipt` requires both flags false).
  - The dropped JUnit assertion is also consistent: the witness refuses `--junit-xml`/`--junitxml` arguments (`mutation_witness.py:71-73, 113`). The replacement oracle checks the receipt witness fields plus the `FAILED`/`ERROR` lines in stdout.
  - This aligns the tests with the code, not the reverse. No finding.
- **The merge `ad679a15a`** brings in no content: `68f8a477b` was already contained, and the diff stat is empty.
- **[ASSUMPTION]** I could not confirm from the repository that these three tests were the three failures recorded at `aec06c3`.

## 1. B110/B131 pilots, P8, and the B157 conflict

### 1.1 P8 cannot analyse the B110 pilot [DEFECT, against the plan]
- The pilot runs `assay run self-qualification --candidates-file … --pilot-jobs 1 --cold-witness …` (`assay/tools/self-qualification-gate.sh`, the `run_b110_pilot_inner` run step). That stream carries `selection_sha256` in its `candidates` milestone (`mutation.py:3941`).
- P8 refuses it twice in `assay/analysis/src/assay_analysis/campaign.py`:
  1. `:552-553` raises `"pilot selection unsupported before P7"`.
  2. Even with that removed, `:686-689` requires the selected total to equal the full plan or a deterministic shard (`"progress selected_total differs from the current selected inventory"`). A pilot subset is neither.
- The pilot report checker (`tools/b110_pilot_report_check.py`) only verifies integrity: plan, selection, state, progress, deadline and resource fields, bound to the source. It produces no projection.
- **Consequence:** B110 plan §8.1.4 ("the pilot's P8 analysis reports no evidence error") can never be met with the current code.

### 1.2 B157 makes the B110 GO arithmetic impossible [DEFECT, plan-level]
**What the plan assumes versus what the code allows:**
- Plan §7 (line 394) runs `--pilot-jobs 3` with up to 3 survivors at once. §8.1.1 divides the projection by "effective workers". The runtime analysis scenarios use 3,760 kills on 3 workers.
- B157 now makes the CLI refuse any value but 1 (`cli.py:738-747`). The loader, `run_mutation`, the verdict schema and `assay verify` all reject `jobs > 1` for native R2.

**The arithmetic at one worker:**
- The 5-hour p90 limit leaves (18,000 s − up to 3,600 s fixed overhead) / 3,760 ≈ **3.8 s per candidate**.
- The B110 runtime analysis measured 9.4–13.4 s of **fixed** cost per kill (snapshot plus startup/collection), before any test prefix runs.
- So an honest pilot at `jobs = 1` produces NO-GO on criterion 8.1.1 by construction, missing by about 2.5–3.5×. The 6-hour hard target is missed as well.

**The false-certification risk:**
- P8's `--project-jobs` accepts 1 to 64 (`campaign.py:46, 1823-1830`). B157's oracles keep this on purpose ("planning projection still accepts a worker count greater than 1").
- A GO projected at 3 workers would describe a configuration the executor refuses: a check whose conclusion is broader than what it compares.

**Stale acceptance text:** the backlog line "The B105 lane's `jobs` is set from the pilot" (`4-backlog.md:11419`) is moot; it is fixed at 1.

### 1.3 Release-safe options

| Option | Change | What it proves | What it can't prove |
|---|---|---|---|
| **A. Pilot projection in P8** | Add `--selection <file>`. Check the file's digest against `selection_sha256`, use the file's IDs as the selected inventory, and never report `complete`. Fix `jobs` to 1 for native R2, or label higher values hypothetical. | Stratified p50/p90 cost per candidate from a bounded, plan-bound sample. Useful for sizing P11, remote capacity, or per-candidate cgroups. | GO at local capacity: at `jobs = 1` the result is a foreseeable NO-GO. Memory criterion 8.1.2 needs the run-gate cgroup's `memory.peak` and pressure-stall figures, which P8 does not read. |
| **B. Redefine GO on the existing B110 artifacts** | Feed the checker-verified state records into the already-pure `project()` (`campaign.py:1278`) with `jobs = 1`. | The same projection with less new code. The checker already binds plan, selection, state, progress and deadline to the source. | The same limits as A. It also drops P8's progress/verdict reconciliation, which a pilot does not produce anyway. |
| **C. Mark the B110 and B131 pilots pending; release without a qualification claim** | Status and documentation only. | That the tooling is implemented and gate-tested. | Any runtime projection or qualification. |

**Recommendation: C for this release.** Then:
- Amend B110 plan §7/§8 and the backlog: pilot jobs 1, effective workers 1, and P8's pilot analysis either removed from 8.1.4 or replaced by option A or B as recorded follow-up.
- Record a D-decision: under B157, criterion 8.1.1 cannot be met on the local 3 CPU / 2 GiB envelope.
- The next B110 step is therefore choosing a NO-GO branch (P11 isolation units, measured remote capacity, or parallelism with attributable per-candidate cgroups, §3.6), not running the pilot to "discover" GO.
- Option A or B is still worth doing later as input to that decision.

**B131** already has option C's shape. It has its own selector and checker (`tools/analysis_r2_pilot_*`), and its backlog entry says to measure before choosing an envelope or GO policy. It is release-safe as long as README and CHANGES claim no analysis-R2 qualification. Both pilots have so far only been refused before child launch for lack of `dev-gates.slice` headroom; no measurement exists.

## 2. The eight contract-1 cgroup-profiler survivors

**Source:** `rg55-cgprofile-cap-20261011`, judged tree `06e7804e…`, `scripts/cgroup-profiler/.assay/progress-r2.jsonl`.
- 1,593 candidates. The final sweep recorded 573 killed, 8 survived and 1,012 `budget_exceeded`, ending `BUDGET_EXCEEDED/LANE_TIMEOUT`.
- Median elapsed time: 43 s per kill, about 132 s per survivor.
- `lib/damon.py` and `lib/placement.py` are **byte-identical at HEAD**. The only relevant test change since is one new `test_damon.py` test.

| # | Site, mutant | Class | Reasoning | Test coverage at HEAD |
|---|---|---|---|---|
| 1 | `damon.py:377` `allow_root` False→True | **[EQUIV]** through every caller | Differs only if `KDAMONDS_DIR/nr_kdamonds` resolves to the admin root, i.e. a directory, so the write fails anyway. Both callers catch every exception (`:541`, `:653`); only the exception type changes. | None. Killable directly: a symlink to the root should raise `HostWriteError` with no `_write_int` call. The fake's `_write_int` would rebuild the registry, so the mutant becomes visible. |
| 2 | `damon.py:495` True→False (foreign-growth flag in `_reconcile_live_indices`) | **[EQUIV]** in behaviour | Redundant with `:602-604` whenever a free slot exists. A foreign `nr_kdamonds` write rebuilds every slot (kernel semantics, modelled by the fake's `_replace_registry`), so `owns_slot` at `:640` refuses the shrink anyway. Only the flag differs. | Not covered. Existing foreign-growth tests hit `:531/:544/:552/:604`; none goes through reconcile with no free slot. |
| 3 | `damon.py:501` `<=`→`<` | **[EQUIV]** under the kernel model | When `current == idx` the slot directory is gone, so `owns_slot` is false. | **Killed at HEAD** by the new `test_pool_quarantines_free_slot_when_count_excludes_its_pinned_identity` (`test_damon.py:2046`), which writes the count text directly so it disagrees with the path. Not yet confirmed by a run. |
| 4 | `damon.py:639` Or→And (`current != expected_end and current <= baseline`) | **[EQUIV]** under the kernel model | Below `expected_end`, a missing slot fails `owns_slot`. Above it, a foreign rebuild changes the pinned inodes. Distinguishable only with a count/path-disagreement model like test 2046 plus a present foreign slot directory. | None. |
| 5 | `damon.py:639` `<=`→`<` | **Provably equivalent** | `current == baseline` already returned at `:633`. The whole `current <= baseline` clause is dead: below baseline, `current != expected_end` already refuses. | Not killable; remove the clause. |
| 6 | `damon.py:678` And→Or | **Provably equivalent** | `_restore_baseline` repeats the same guard (`:619`), and `release` ignores its return value. | Not killable; simplify. |
| 7 | `placement.py:627` And→Or (`_parse_bytes`) | **Behaviour gap** | The mutant refuses every float. The code accepts whole-number floats (e.g. `1073741824.0`), but PROTOCOL types the fields as `integer \| null` (`docs/PROTOCOL.md:131-132`). | Neither behaviour is pinned; only fractional and non-finite values are tested. `cpu_weight` has a pinned integral-float acceptance test (`test_serve_placement.py:540`). For integers the mutant is equivalent only on Python ≥ 3.12; the package declares ≥ 3.11 and the tester runs 3.14. |
| 8 | `placement.py:709` `allow_root` False→True (`CgroupWriteGuard.__init__`) | **Equivalent in practice, but it guards a real hazard** | Differs only if the container cgroup path is a symlink to the gates slice **and** the slice's own name matches the container scope pattern. Then the mutant sets `container_kill_abs` to the gates slice, and a `cgroup.kill` there would kill every gate. | None. Constructible in a fake cgroup tree (gates path `/x/docker-<id>.scope`, child symlink to the parent). |

**Why this matters for release.** B120 (the native Python equivalence ledger) is OPEN, and native Python R2 FAILs on any survivor (`assay/docs/CONSUMERS.md:3887`). Equivalent survivors cannot be waived, only eliminated.

**Minimal repairs:**
- **Remove dead code:**
  - delete `or current <= baseline` at `damon.py:639`, which removes both 639 mutants;
  - call `self._restore_baseline()` unconditionally in `release` (`:678`).
- **Tests to add:**
  - site 1: `_write_nr_kdamonds` with a symlink to the admin root raises `HostWriteError` and makes no write call;
  - site 2: a white-box `_reconcile_live_indices` test with owned slots and a directly written larger count, asserting the foreign-growth flag (same style as test 2046);
  - site 4: optionally, restore refuses when the count exceeds `expected_end` without the flag set and with identities intact;
  - site 7: decide the contract. Either pin acceptance of whole-number float memory caps, normalised to `int` (mirroring `cpu_weight`), and say "integral JSON number" in PROTOCOL, or tighten to integers only and test the refusal;
  - site 8: the gates-slice self-kill refusal.

**Expected remaining work:**
- The 1,012 unaccounted candidates are unclassified. At the observed 8/581 rate, expect about 14 more survivors.
- An exact-tree serial run is about 1,593 × ~41–45 s ≈ 18 h. That fits the 24-hour lane budget only if uninterrupted.
- The v16 hard cut cold-starts any reuse of v12–v15 state (`assay/src/assay/reuse.py:14`), so none of the 581 results carry over.

## 3. Integration, stale-doc, gate, release-order and cgroup-design findings

### 3.1 Gates: blocker, unchanged
- Every item in B151–B161 records "exact-tip registered gate pending" (B151/B153/B154/B160 in progress; B155/B157/B158 in progress in the contract-2 candidate; B159/B161 fixed or implemented, gate pending).
- The full registered Assay gate has not rerun since the test realignment; the last full run, at `aec06c3`, had 3 failures.
- B132's 70-case PostgreSQL hazard gate is pending, and B132's own entry says it is needed before merge or release. That gate is also the only behavioural evidence for B160's hand-written rewrite of the SQL scanners' token grammar, since the unit tests are structural.
- RG-84 says its registered selftest is still pending.

### 3.2 Stale B110 plan and acceptance [DEFECT]
- See §1. The plan still specifies `--pilot-jobs 3`, divides by effective workers, and requires P8 pilot analysis.
- The B110 acceptance text still says to set `jobs` from the pilot.
- Fix in the same release, so the documents don't prescribe a path the CLI refuses.

### 3.3 Backlog statuses contradict the integrated tree [DEFECT, documentation]
- KI-69 header says "open" (`cmru/KNOWN_ISSUES_TODO_BACKLOG.md:1796`), but the protected `CMRU_RELEASE_COMMIT`/`CMRU_RELEASE_TAG` implementation is in this candidate.
- CP-21 says "open" and CP-20 says "carved" (`scripts/cgroup-profiler/nyxloom-trove/backlog/INDEX.md:15-16`). The Dockerfile already requires its version and revision arguments, and contract 2 is the substance of this release.
- RG-89 says "IN PROGRESS"; B108 is OPEN while B110 treats "B108 phase 1 (P8)" as delivered.
- Release notes must not claim fixes the backlogs deny, or the reverse. Reconcile to "implemented, exact-tip gate pending" where that is true.

### 3.4 cgroup-profiler R2 would predictably FAIL [release-order]
- §2: seven known survivors remain on unchanged code, one is newly killed, and about 1,012 candidates are unknown.
- If the release order requires an exact-tree contract-2 R2 PASS before `cmru release` for cgroup-profiler: make the §2 repairs, freeze the tree, then run about 18 h.
- Otherwise, CHANGES and README must not claim contract-2 R2 qualification.

### 3.5 Observer upgrade flag day [design / release-order]
- In-repo lanes install Assay from worktree source (AGENTS.md), so a merge to `main` upgrades Assay immediately.
- New Assay requires observer contract 2 and fails closed with a clear message (`assay/src/assay/resource_limits.py:1093-1095`). That is correct, but it is an availability break.
- The deployed daemon stays on contract 1 until the cgroup-profiler image is released, pinned post-release in `ciu.toml.j2`, and redeployed with `ciu up`. Until then, `run-gate-project` `assay-r2` and cgroup-profiler `r2` refuse.
- Deploying the daemon first breaks every checkout still on older Assay, because contract 1 is retired (RW-569).
- The daemon's control `version` response does not advertise the observer listener or its contract (`scripts/cgroup-profiler/lib/serve.py:758-785`), so neither operators nor Run-Gate can preflight compatibility.
- Run-Gate checks only the socket's ownership and mode (`run-gate.py:6958-6999`).
- **Decide and record one of:**
  - (a) the daemon also serves contract 1 for a deprecation window;
  - (b) an announced flag day: merge → release cgroup-profiler → pin → `ciu up` → resume observer lanes.
- Either way, advertise the observer contract in `version`.

### 3.6 The measurement design is now the throughput bottleneck [design; ASSUMPTION on cgroup mechanics]
- B145, B155 and B157 make attribution correct by serialising candidates inside one shared cgroup. That trades throughput for correctness, and the cost is concrete: B110 cannot fit, and cgroup-profiler's R2 takes about 18 h.
- **The structural fix is one cgroup per candidate:** each candidate runs in its own leaf, and its local `pids.events`/`memory.events` counters are read there. Events become attributable with several workers.
- The daemon already creates delegated leaves and moves PIDs under the gates slice (`placement.py` `CgroupWriteGuard`, `leaf_name`, `is_leaf`). A future observer contract 3, "create candidate leaf, place PID, read its local counters", would make B157 a temporary measure.
- **Unverified constraints:**
  - Docker's private-namespace cgroup mount is normally read-only inside the container, so the host-side daemon would have to perform the writes;
  - cgroup v2's no-internal-process rule forces the container's own processes into a sibling leaf;
  - the daemon needs authorisation bound to the container ID, and kill/write whitelisting like the existing guard.
- Recommend a design spike before further B110 investment.

### 3.7 Measurement policy differs across tools [design; from the earlier addendum]
- CMRU `tester_gate` treats only the container's own `pids.events max` and `memory.events oom_kill` as infrastructure (`cmru/src/cmru/tester_gate.py:164-169, 814-817`).
- Assay B145 uses five counters, including ancestor local counters. Run-Gate uses none.
- If B145's premise holds (a refusal by an ancestor's limit does not appear in the leaf), CMRU can miss it. A shared vocabulary for resource-affected results, possibly served by the observer, would simplify this.

### 3.8 Process reaping and gate identity, still applicable [from the earlier addendum]
- `scripts/cgroup-profiler/tools/gate.sh:282-293` launches its container without `--init` or `--pids-limit`. That script is the r0-r1 lane of cgroup-profiler's CMRU release gate (`cmru.toml:55`). Same class as KI-52; not blocking, because the suite creates few Git objects.
- The tester-unified image is mutable and unlabelled (`tester-unified:local`; `assay/tools/tester-unified-gate.sh` uses the tag). B158 pins `hypothesis==6.169.0` and `pytest-cov==7.1.0`, so gate evidence is meaningful only together with the image identity.
- `cmru/tests/test_tester_unified_image.py` is static by its own docstring ("The image is not built here"). It proves Dockerfile and `.dockerignore` text, not the runtime image.
- cgroup-profiler's push step builds the image again with `bake cgprofile-release --push` instead of pushing the gated `cgprofile:local` (`scripts/cgroup-profiler/build-push.py`).
- `exact_build_source` registers a temporary worktree in the shared repository's metadata, and a failure in its `finally` can mask the original error.

### 3.9 Native R2 lanes without the observer [plan gap, from the earlier addendum]
- These lanes run in tester-unified with a private cgroup namespace and no `resource_observer`:
  - cli-extended `r2`;
  - nyxloom `session-extract`;
  - damon-analysis, debian-install-v2 and gstammtisch-guide `r2`.
- Under B148 they refuse `NO_MEASUREMENT/CGROUP_OBSERVATION_UNAVAILABLE` before R0. Only `run-gate-project/run-gate.toml:113` and `scripts/cgroup-profiler/run-gate.toml:65` declare the observer.
- This wave edited those lanes' `jobs` and budget comments as if they would run. Wire the observer in or mark them not runnable; otherwise the "migrated to `jobs = 1`" claim is cosmetic.

### 3.10 Integrated test changes: no defect
- The pytest-cov-only realignment and the JUnit removal match the code semantics (§0).
- The new `test_damon.py:2046` is a valid count/path-disagreement oracle.

## 4. Prioritized recommendations
1. **Do not call the wave green.** Run the full registered Assay gate and the B132 PostgreSQL hazard gate on the exact tip, after rebuilding tester-unified and recording its image ID.
2. **Release B110 and B131 as "pilot pending, no qualification claim"** (option C). Amend B110 plan §7/§8 and the backlog for `jobs = 1`, and record the D-decision that 8.1.1 cannot be met locally under B157. For P8, either refuse `--project-jobs > 1` for native R2 or label it hypothetical.
3. **Reconcile backlog statuses** (KI-69, CP-20, CP-21, RG-89, B108/B110 phase wording) with CHANGES before tagging.
4. **Repair cgroup-profiler's survivors** before any contract-2 R2 run whose PASS is needed: the two dead-code removals in `damon.py`, plus tests for sites 1, 2, 7 and 8 (and optionally 4). Then freeze the tree and run about 18 h serially. Without a needed PASS, make no R2 claim.
5. **Choose and record the observer upgrade strategy** (§3.5), and advertise the observer contract in `version`.
6. **Release order:**
   1. documentation, statuses and D-decision;
   2. survivor repairs;
   3. rebuild and record the tester image;
   4. exact-tip Assay and PostgreSQL gates;
   5. build the cgroup-profiler image, probe the observer from a tester container, then release, pin and redeploy per §3.5;
   6. exact-tree cgroup-profiler contract-2 R2;
   7. release Assay.
7. **Smaller follow-ups:**
   - add `--init` and `--pids-limit` to `gate.sh`;
   - push the image that was gated;
   - give tester-unified a source-identity label, checked by gate scripts;
   - wire the observer into the other native R2 lanes, or mark them not runnable.
8. **Before more B110 work:** a design spike on per-candidate cgroup leaves (observer contract 3).

## 5. Limitations
- I classified only the 8 recorded survivors; the 1,012 unaccounted candidates are unknown.
- The DAMON equivalence claims rely on the kernel's rebuild-all-slots semantics, as documented in `damon.py` and modelled by the fake. I did not verify them on a live kernel.
- I could not confirm that the three tests realigned in `6c46f59ef` were the three gate failures at `aec06c3`.
- I did not verify that the B110 wrapper records the run-gate cgroup's `memory.peak` and pressure-stall figures for criterion 8.1.2.
- The 3.8 s per candidate figure uses the plan's ≤ 60-minute fixed overhead and the runtime analysis's measured fixed costs; these are not new measurements.
- The ~18 h cgroup-profiler estimate comes from contract-1 timings on a shared host.
- The per-candidate cgroup proposal is a design direction; its Docker and cgroup v2 constraints are unverified.
- No gate logs were inspected; gate status comes from backlog text.
- From the earlier passes: unchanged SQL scanner bodies, the CMRU image test line by line, and pip's handling of duplicate requirement lines were not fully reviewed.

## Controller verification notes

The review's sentence in §1.3 that both pilots were only refused before child
launch is stale. The retained `.assay` records show:

- B110 at commit `31f09341bc819f96c42606785691e10cd0061b8e` entered the child,
  completed its R0/R1 coverage work, then exited 2 before mutation candidates
  because its cold-witness baseline rejected the tester image's untrusted
  Hypothesis hook configuration. B158 addresses that mismatch. It has not yet
  been retried on `ad679a15a`.
- The analysis-package pilot at commit
  `572aa1c7a92b4c9e3efb5ce10b6ba80217c79fec` entered the child and failed R0
  on `analysis/tests/test_analysis_campaign_real.py::test_o15_a_real_rejudge_run_is_counted_once_and_records_its_resumed_event`.
  It has not yet been retried on `ad679a15a`.

The before/after review snapshots were identical: HEAD
`ad679a15afd449a6752d42782edaa7a5e0c8461c`, clean tracked status. The review
used Claude Opus 5.5 at high effort in persistent session
`28227ce4-cbdc-471a-8b69-a430af63fb67`.
