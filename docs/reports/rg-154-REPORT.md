<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/rg-154-REPORT.md -->

# rg-154 report

Branch fix-rg-154-20261104, worktree <local-path> one commit fc0ac39c9.

## Items
- RG-154 FIXED (oracles a, b as tests; c needs a live dstdns re-run). `r2_capacity_probe` passes the pinned `*.pyz` of a `python3 x.pyz` assay_command to the probe, which puts it on sys.path (still `-I`), so cowtree comes from the same artifact the run uses. `select_r2_jobs` refusal now carries `[cheap_clone_reason=...]`; probe failure reports `capacity-probe-unavailable`. Tests: real-subprocess probe with a fake pyz (stub cowtree) and no cowtree in the interpreter (True), same lane without the pyz gives `cowtree-probe-ModuleNotFoundError` (old behaviour); parametrized reason table. CONSUMERS.md paragraph added (image needs, `jobs_max >= N`).
- RG-155 PARTIAL. Header (`print_lane_bounds`, exec and bare-host paths) prints the assay.toml lane budget (from the inventory, LANE_TIMEOUT semantics) beside the run-gate wrapper budget and warns when it is smaller; test added. NOT done: warning against the plan's estimated wall time (needs `assay plan --json`, assay B213).
- cgprofile r3 FIXED as CP-56 (max existing CP id on main is CP-53; the cgp2-pkg3 branch uses CP-54/55). Cause: `tools/canary-run.sh` snapshot `tar ... -cf - .` under pipefail exits 1 with `tar: .: file changed as we read it` when the tree is written during the copy (shared main-checkout opt-in). Fix: `--warning=no-file-changed` on the creating tar (one line group plus comment; this is the only change to canary-run.sh, so expect at most a trivial merge with the cgprofile 2.0 branch). Reproduced rc=1 without the flag, rc=0 with it; `canary-run.sh counter-reset-negative` runs ok. The writer in the lane was not identified. The cgprofile gate was not run.

## Gate
Final: `run-gate selftest` from run-gate-project in the worktree, HEAD 0af5fb3b2: "2877 passed, 1 skipped", "diff-coverage OK: 31/31 ... 100.0%", "verdict PASS; exit_code 0" (read in a separate step; log /tmp/run-gate/lanes/selftest/2c5ae927d49830d65a1f4550c2c084ba.log).

History of the earlier runs, all on the same code path except as noted:
- Run 1: 1 failed (reattach `stop` test, DAMON-quarantine/memory-stall run). Controller run: 2 failed (`_direct_child_pids() == [17039]` after the stall test, and the reattach test). Neither exercises the capacity probe or the budget header (no `--jobs`, no assay inventory; the stall test runs before my tests in file order, so my new subprocess tests cannot leak into it).
- My rerun with no code change: 2877 passed, i.e. both failures did not recur, but the lane still FAILED on the diff-coverage floor: 6 uncovered changed lines (the RG-155 print loops on the exec/bare paths and the unparsable-budget branch). That was a real defect of my commit and is the cause of the persistent FAIL; fixed in 0af5fb3b2 (one `print_assay_lane_budget` helper, covered by tests).
- Outside the gate (plain pytest, this devcontainer) the two stall tests also fail on a clean `main` checkout, so they are environment-sensitive when run as a bare process; I could not separate them further. The two process/DAMON-related flakes should be watched on the next run; they passed in the final gate.

## Deviations
- Mid-run I ran `git checkout main -- .` by mistake; restored with `git checkout HEAD -- .`, tree matches the commit (verified clean). A no-op `git stash` ran too; the listed stashes are older and not mine.
- New backlog id: CP-56.
