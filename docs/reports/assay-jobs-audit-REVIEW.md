<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/assay-jobs-audit-REVIEW.md -->

# Review: assay-jobs-audit-20261105 (fe53428a8, 20bcd5648)

Verdict: MERGE. There are no blocking findings. Three stale comments should ideally be fixed in the same merge or soon after.

## Findings (severity-ranked)

### LOW-1 Stale "one candidate at a time" comments remain, contradicting the new rules
- `nyxloom/assay.toml:167`: "Native candidates now run serially so cgroup event deltas remain attributable." This sits on the session-extract lane that was just raised to `jobs_max = 3` (comment at :205). The two comments in one lane now disagree.
- `run-gate-project/run-gate.toml:109`: "Assay runs one candidate at a time and Docker caps this..." The same lane now has `jobs = "auto"` (line 133) and `jobs_max = 2`.
- `run-gate-project/assay.toml:119-123`: the old paragraph ("Keep one candidate active until per-worker cgroups ... exist") is kept in full and only a 3-line "Superseded" note was appended. It reads as a live rule followed by a contradiction. Delete the old sentences instead of appending.
- Failure scenario: a future editor restores `jobs = 1` because the comment says it is required. Nothing breaks today.

### LOW-2 cmru lane memory margin is thin for 2 workers
- `cmru/run-gate.toml` sets memory 4g, `candidate_ram` 1536m, `jobs_max` 2, cpus 3.
- Auto arithmetic: the budget is 75% of 4g (`ram_target` default, `run_gate.py:11449`), which is 3g. 3g / 1536m = 2, so jobs = min(2, 3, 2) = 2. This is consistent.
- The report measured RSS of 0.95-1.75 GiB per candidate. Two worst-case candidates plus the coordinator, coverage and page cache come to about 3.5 GiB or more, against a 4g cap with `memory_swap` 16g. That means swap thrash rather than a kill, and it is mitigated by the p95 floor: once history p95 exceeds 1.5g, auto drops to 1.
- It fits an 8 GB test host but leaves little headroom. Consider `candidate_ram` 1800m, which gives 1 worker under the same cap and 2 only if memory goes to 5g. Judgement call; not a blocker.

### LOW-3 `-x` in pwmcp's R2 argv blocks assay's cold-witness optimisation
- `pwmcp/assay.toml:53`. `mutation_witness.py:1310` returns "fail-fast option" for `-x`, so cold-witness R2 (B114) can never be enabled on this lane while `-x` stays.
- The lane does not opt into cold-witness today, so there is no effect now. Worth a comment, because the "-x" comment only mentions the coverage warning.

### INFO Checks that passed

1. Resource consistency. Auto = min(`jobs_max`, cpus, 75% of memory / `candidate_ram`, remaining):

   | Lane | Memory / cpus / `candidate_ram` | Budget calc | Result |
   |---|---|---|---|
   | pwmcp | 3g / 3 / 450m | 2.25g / 450m = 5 | 3 |
   | cmru | 4g / 3 / 1536m | 3g / 1536m = 2 | 2 |
   | run-gate-project | 2g / 3 / 700m | 1.5g / 700m = 2 | 2 |
   | worktree | 2g / 3 / 300m | 1.5g / 300m = 5 | 3 |
   | nyxloom session-extract | 2g / 3 / 400m | 1.5g / 400m = 3 | 3 |
   | cgroup-profiler | 2g / 3 / 500m | 1.5g / 500m = 3 | 2 |

   - Every lane has an explicit cpus (3), so auto does not fall back to 1.
   - `candidate_ram` is a valid lane-only key (`run_gate.py:1021`).
   - cpus 3 fits a 4 vCPU host. On the shared 8-core controller, pwmcp's three CPU-bound workers are the heaviest addition. Operators already carry the PSI > 25 rule.
   - All of these only parallelise on a btrfs `MDT_COW_ROOT` host. Otherwise auto clamps to 1, which is safe.

2. Fixed ports, shared directories and docker state.
   - pwmcp tests and client tests: sockets bind port 0, `/tmp/pwmcp-shared-ua.conf` is rewritten to a generated path, the docker in `test_installer_offline.py` is a fake, and the `pwmcp:3000` URLs are strings only. Nothing shared.
   - nyxloom session-extract argv: only session_extract tests. `usage_html_browser` acceptance uses `require_browser_acceptance` (optional), and the pdf test fakes pwmcp.
   - run-gate-project tests: docker is shimmed (`_fake_container_shim`) or mocked. I found no fixed ports or real docker state.
   - worktree and cgroup-profiler tests: no fixed ports found.
   - ciu, mdt-cockpit, gstammtisch and assay self lanes were correctly left alone.
   - cli-extended and debian-install-v2 / damon: only ceilings were raised. The cli-extended `r2` command lane ignores `jobs`, so it is inert (as the report says).

3. `-x` semantics (pwmcp R2).
   - Baseline: R0/R1 run the same argv, but only the green path matters. With a green suite, `-x` changes nothing, so coverage is complete. Red means R0 fails either way. Assay's coverage fail-fast warning is advisory only (DESIGN-GUIDE "coverage fail-fast warning").
   - Mutants: pytest exits 1 on the first failing test, as it does without `-x`. Collection and import errors are the same exit with or without `-x`. A killed mutant therefore stays killed. The "failed-call witness" for an unobserved window needs session and process exit 1 plus a clean leader exit, and `-x` still produces both. Survivors are mutants where the whole suite passes, which is identical.
   - Observed resource events and classification of PASS / non-PASS are unchanged. `-x` only makes kills faster and avoids BUDGET_EXCEEDED hangs after a broken cleanup. run-gate-project already ships `-x` the same way, so there is precedent.
   - Flaky-test effect is also identical: a flaky failure kills with or without `-x`.

4. Rewritten comments that are still correct: pwmcp, cmru, cgroup-profiler, debian-install-v2, worktree, damon. They match README.md:348 and 355 (concurrent windows, `overlap-conservative`, PASS stays `survived`). Assay `CHANGES.md` has no literal "B157" entry, but the README supports the claim.

5. The two changed tests pin the right values.
   - run-gate-project: the full `resources` dict now includes `candidate_ram` 700m, plus `jobs == "auto"` and `jobs_max == 2`.
   - cgroup-profiler: renamed, and asserts `jobs == "auto"` and `jobs_max == 2`.
   - No other test in the repo pins the old `jobs = 1` for these lanes (grep found none).
   - I did not run the tests, per the read-only brief.

6. Config checks.
   - All 15 changed toml files parse with tomllib.
   - `run-gate --list` parses for pwmcp, cmru, nyxloom, worktree, run-gate-project and cgroup-profiler, and the lane lists are consistent.

7. Merge-ability. The merge into current main (f19c3e088) was clean: it fast-forwarded with no conflicts, because main has no newer commits touching these files. The throwaway worktree and the `review-tmp-jobs` branch were removed by exact path and name.

## Verdict: MERGE (optionally fold in LOW-1 comment cleanup first)
