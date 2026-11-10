<!-- origin: 2026-11-06; status: evidence; copied from the controller program dir 2026-11-10 -->
<!-- source: reports/fix-r2-lanes-REPORT.md -->
<!-- relation: R2 lane fix evidence; verbatim source text follows -->

# fix-r2-lanes REPORT (2026-11-05)

Worktree `/workspaces/vbpub/.worktrees/fix-r2-lanes-20261105`, branch `fix-r2-lanes-20261105` from origin/main `506744371`. Nothing pushed or merged.

| # | Item | Status | Commit |
| --- | --- | --- | --- |
| 1 | cli-extended `r2` `No module named 'cowtree'` | FIXED | 601904faa |
| 2 | run-gate-project `assay-r2` R0 baseline | FIXED; `assay-r1` R0+R1 PASS in the lane (run 4, below); `assay-r2` dry-run PASS | ed5658be2, f6ac2d38b, 53fb00964, cf3e8af1a, 14fbef874 |
| 3 | nyxloom `session-extract` cap overflow | FIXED | 7af420ba8 |
| 4 | cgroup-profiler r2 base `eb3af770` GIT_FAILED | FIXED (docs + comment) | 0f9ef3181 |
| 5 | ciu default r2 plan empty | FIXED | 601904faa |
| 6 | cmru `mutation` lane facts | FIXED (script + docs) | see git log, "cmru: document and script" |
| - | CHANGES [Unreleased] | all five projects | f15b8d587 |

## Per item

1. `libraries/cli-extended/run-gate.toml` lane `r2`: `PYTHONPATH={worktree}/assay/src:{worktree}/libraries/cowtree/src`. This is exactly the host-local edit (clone 76853c209, captured in `r2-session/cli-extended-run-gate.toml`). Proof: `run-gate --dry-run r2` shows the new export; `assay plan cli-extended` with that PYTHONPATH plans 1461 / 3000 candidates. (The RG-154 in run-gate's backlog is an unrelated cowtree probe issue; no run-gate release is needed for this fix.)
2. Real cause: not a mountinfo-access problem in the container and not a product bug. Since RG-98, with the default in-repo state root run-gate exports the state path to the judge as `RUN_GATE_TMPDIR` only (TMPDIR only for an external root). `run-gate-project/assay.toml` still forwarded `TMPDIR` via `env_passthrough`, which carried nothing, so pytest's `tmp_path` was `/tmp/pytest-of-tester` on the container overlay. `/.dockerenv` exists in the lane, so `run-gate doctor` correctly FAILs `physical_path` for a non-bind-mounted repo. Every in-process `doctor` test is exposed; `-x` showed the first. Fix: lanes `r1` and `r2` drop `TMPDIR` from `env_passthrough` and add `[lanes.<n>.infrastructure] TMPDIR = "required-env:RUN_GATE_TMPDIR"` (Assay refuses the same name in both). Oracle: `TestShippedGateFullDeclaration::test_assay_test_commands_use_the_tester_unified_python` now pins mapping and absence of the dead passthrough. Backlog RG-157 (max existing was RG-156), CHANGES entry. Live proof: see "Live verdict" below.
3. `nyxloom/assay.toml` `session-extract`: the board's "cap 350" was stale; main already had cap 1203, but the one-time SHA base pin `126ccc39e` had aged to a 1204-candidate plan (cap + 1 sentinel), est. 40 h worst case. Fixed at the source: `base_source = "request"` (refresh per release by passing the previous release tag: `run-gate --base nyxloom-v0.11.1 session-extract`), cap 1500 with documented reason (a plan above it means a wrong base). Measured: `--request-base nyxloom-v0.11.1` plans 885 / 1500; `nyxloom-v0.12.1` plans 0 (package unchanged since 0.12.1, honest empty plan); `nyxloom-v0.9.0` GIT_FAILED (diff > 64 MiB, base too old).
4. `scripts/cgroup-profiler`: the lane was already `base_source = "request"`; the stale thing was the recorded base in `docs/CONSUMERS.md` and the `assay.toml` comment. Retired `eb3af770` (GIT_FAILED), documented "previous release tag, refresh per release", measured `cgprofile-v1.2.0` = 557 / 2000 candidates (`assay plan r2 --request-base cgprofile-v1.2.0`).
5. `ciu/assay.toml` lane `ciu`: `base = "main"` -> `base_source = "request"` (it is the file's only lane, so no other lane is affected). `run-gate --dry-run --base ciu-v7.19.0 ciu` PASS; `assay plan ciu --request-base ciu-v7.19.0` = 256 / 10000 candidates. Without `--base` run-gate's RG-51 fallback applies (ciu-recorded fork point, then @{upstream}).
6. cmru: `cmru/tools/local_baseline_facts.py` builds `CMRU_ASSAY_BASELINE_FACTS` from local `cmru-v*` tags using the release flow's own `build_facts`; `cmru/docs/CONSUMERS.md` has the 3-step recipe. Discovered prerequisite: the checker also requires `cmru/assay.toml` `[lanes.cmru.judge] base` to equal the selected tag; on main it is `cmru-v6.1.0` while the latest published ancestor is `cmru-v7.2.0` (verified: "Assay R1 base 'cmru-v6.1.0' does not match latest published ancestor CMRU release tag 'cmru-v7.2.0'"), so the campaign checkout must commit the pin first (step 1 of the recipe). I did not change the pin on main because the release flow owns advancing it. `run-gate --dry-run mutation` with the helper's facts PASS. The helper has no unit test (tools/ is outside cmru's `source_roots`); it is a thin wrapper over the tested `build_facts`.

## Deviations
- The RG-157 backlog entry was appended with a shell heredoc instead of the Edit tool (rule slip, content verified by commit diff).
- `dry-run` output is the docker argv, not a mutation plan; for plans I used `python -m assay.cli plan <lane> [--request-base]` with `PYTHONPATH=assay/src:libraries/cowtree/src` (no container).
- Another run-gate container (not mine) was running during the R1 run; CPU PSI avg10 rose to ~28 at one point (the rule bound is 25 at start; it was 14 at start).

## Final status of item 2 (successor, 2026-11-05)
Final live verdict (quoted from `run-gate-project/.assay/verdict-r1.json`, commit `cf3e8af1a`, log `reports/fix-r2-lanes/assay-r1-run4.log`):
`lane 'assay-r1' verdict PASS; exit_code 0` with claims R0 PASS, R1 PASS. `run-gate --dry-run --base run-gate-v25.0.0 assay-r2` PASS (admitted).
The R2 mutation sweep itself (4 h budget) was NOT run; assay-r2 runs the same R0 command (with `-x`) first, so its baseline is expected green; this is the only unproven step.
- RG-158 FIXED (f6ac2d38b): the pinned-pyz oracle's "no cowtree" half now probes through a `-S` interpreter wrapper (image has cowtree installed). Passes in the lane and locally.
- RG-159 FIXED (f6ac2d38b, 53fb00964): released-reader test derives the oldest `run-gate-v24.>=3` tag that exists; skips only when the checkout has no `run-gate-v*` tag at all
  (tagless Assay snapshot) as the third documented structural exception (`tests/_testdeps.py`, policy test pins 3 markers); fails when tags exist but none usable. A first attempt with
  a plain skip failed `test_no_test_bypasses_the_policy` in lane run 2; fixed.
- RG-160 OPEN (watch) (cf3e8af1a, 14fbef874): `test_clean_session_passes` (the hang) did NOT reproduce in lane runs 2-4, in two narrowed lanes (one file; seven files) nor by hand: run 1 was one data point
  under host load (another run-gate container active). `test_reattach_adopts_recorded_session_stop_called_with_it` failed in lane runs 2 and 3 (`ctl stop` unparsable response from the in-test fake daemon), passed in run 4,
  by hand and in a full host run of the file. No root cause claimed; the warning now names response length and start so the next occurrence is diagnosable.
- Lane run history: run1 3 failed (see below); run2 reattach + policy-skip failed; run3 reattach failed; run4 PASS.

## Deviations (successor)
- CPU PSI avg10 was 29 at the start of one hand-run diagnostic container (`fix-r2-lanes-dbg2`, 9 s); other starts were < 25. Two temporary diagnostic commits to `run-gate-project/assay.toml` (narrowed argv) were created and removed with `git reset --hard HEAD~1` (my own local commits; never in the final history).
- Host pytest runs (not containers) of `tests/test_run_gate.py` under nice/ionice; two unrelated host-environment failures (`TestStall*`) appeared there only.

## Live verdict, run 1 (historical, item 2 PARTIAL)
`run-gate --base run-gate-v25.0.0 assay-r1` from run-gate-project at ed5658be2, log `reports/fix-r2-lanes/assay-r1.log`, verdict
`run-gate-project/.assay/verdict-r1.json` (read in a separate step): `r1: FAIL/COMMAND_FAILED`, R0 FAIL, R1 PASS (coverage 53/53, branches 6/6).
Full pytest tail inside the verdict: `3 failed, 2874 passed, 1 skipped in 482.67s`. The TMPDIR fix works: `env_effective.TMPDIR` is now the state root and the
doctor test (and every other in-process doctor test) passes. The old R0 stopped at that first failure under `-x`-like reading; behind it three more fail in
the assay lane container (all environment-dependent, none in the TMPDIR/mountinfo family), so the R2 baseline is STILL not green:
1. `tests/test_path_write_guard.py::test_clean_session_passes`: inner `pytest` subprocess printed `.  [100%]` then hung until the test's 120 s timeout (killed -9).
   Passes in an equivalent tester-unified container run by hand (3 variants incl. bind-mounted TMPDIR and `--cov`), so the cause is lane-specific (hang at
   interpreter exit; suspect the lane's snapshot/observer context). OPEN, needs a repro inside the real lane (e.g. `-k` selection via a command lane).
2. `TestR2JobsPR3::test_capacity_probe_resolves_cowtree_from_the_pinned_pyz`: expects `cowtree-probe-ModuleNotFoundError`, got `cowtree-probe-RootInvalid`
   (the "no cowtree" half of the oracle finds cowtree installed in the lane image and rejects the tmp_path root). Likely this oracle assumes an image without
   cowtree and a CoW-valid MDT_COW_ROOT. OPEN; may be a test-environment assumption, check with the owner of RG-154.
3. `TestHistoryStoreReadableByTheReleasedReader::test_released_reader_reads_the_new_store`: needs git tag `run-gate-v24.3.0`; the assay snapshot has no tags
   (`fatal: invalid object name 'run-gate-v24.3.0'`). OPEN: skip-if-tag-missing, or read the released file from a vendored fixture.
Not claimed as fixed: item 2's "R0 baseline passes". Items 1 and 3-6 are config/doc fixes proven by dry-run and `assay plan`, not by a full lane.

## Next
Continuation brief: `reports/fix-r2-lanes-CONTINUATION.md`. Backlog ids to file: RG-158..RG-160 for the three residual failures (max existing RG-157).
