<!-- origin: controller report, historical date stated in source -->
<!-- status: scrubbed historical copy -->
<!-- source: reports/sockets-phase1-GATES-REPORT.md -->

# Sockets phase 1: integrated-tree gate report

Date: 2026-11-07 (gate runner, devcontainer on the controller host, OLD socket layout)
Tree: `<local-path>`, branch `sockets-phase1-20261107`, HEAD `b707c5e12`
(= a3dbf9983 + cgroup-profiler, topos, run-gate, assay merges). origin/main has since moved to `6b8836fad`
(known-failure comparisons below used a `git archive` of that origin/main).
Installed run-gate: 25.2.1 (pre-sockets). All runs `nice -n 10`, one lane at a time, detached, lane
containers capped at 3 CPUs (verified NanoCpus=3000000000 on the selftest container). The worktree stayed clean;
nothing was fixed, pushed, merged or released. No stop signal fired (memory PSI some max ~7, io full low).
The gate log results and timestamps are summarized in this report; raw controller logs remain outside the repo.
Note: another session's gate container (`vbpub-run-gate-pwmcp-r1-...`) ran briefly alongside mine; not mine, untouched.

## Verdict table

| # | Gate | Command | Container | Start-End (UTC) | Verdict | Counts / coverage |
|---|---|---|---|---|---|---|
| a1 | cgroup-profiler r0-r1 (registered unit+coverage, tools/gate.sh coverage) | `run-gate --worktree $WT r0-r1` | `cgprofile-gate-2344458-1794036162` | 07:22:37-07:26:18 | **FAIL** | 3355 passed, **1 failed** (test_docs mirror), 211 s; coverage step not reached (`&&` after failed pytest) |
| a2 | cgroup-profiler coverage-partial (same suite, excludes the mirror test, `coverage report --fail-under=100`) | `run-gate --worktree $WT coverage-partial` | `vbpub-run-gate-cgroup-profiler-coverage-partial-b707c5e1-261107-0726-649132347` | 07:26:41-07:30:04 | **FAIL** | 3355 passed, 1 deselected; TOTAL 99% (11976 stmts 0 miss, 4316 branches 1 partial); fail-under=100 |
| a3 | cgroup-profiler r3 (6 canaries) | `run-gate --worktree $WT r3` | `vbpub-run-gate-cgroup-profiler-r3-b707c5e1-261107-0730-333193425` | 07:30:23-07:30:35 | PASS | 6 rejected, 0 survived |
| a4 | cgroup-profiler r2 (24 h assay mutation) | not run | - | - | NOT RUN | deferred by design (cmru release gate excludes it); also observer-enabled |
| b1 | run-gate selftest (registered gate, strict diff-coverage) | `run-gate --worktree $WT selftest` | `vbpub-run-gate-run-gate-project-selftest-b707c5e1-261107-0730-415724457` | 07:30:51-07:37:00 | **PASS** | 2999 passed, 1 skipped (real-container smoke), 0 failed; diff-coverage 69/69 changed lines (100.0%), branches 38/38 |
| b2 | run-gate assay-r1 (base a3dbf9983) | `run-gate --worktree $WT assay-r1` | `vbpub-run-gate-run-gate-project-assay-r1-b707c5e1-261107-0737-238170815` | 07:37:12-07:42:22 | PASS | r1 PASS exit 0 (pytest tests, --cov-branch under the assay judge) |
| b3 | run-gate assay-r3 (2 canaries) | `run-gate --worktree $WT assay-r3` | `vbpub-run-gate-run-gate-project-assay-r3-b707c5e1-261107-0742-122756811` | 07:42:39-07:42:59 | PASS | 2 rejected, 0 survived |
| b4 | run-gate assay-r2 (4 h mutation, observer) | dry-run only | - | - | NOT RUN | observer-enabled; the installed (old) run-gate talks to the old `ctl.sock` so it would not even exercise the new socket code; see "cannot be shown here" |
| c1 | assay tester-unified (registered lane) | `run-gate --worktree $WT tester-unified` | none started | 07:43:27-07:43:31 | **REFUSED (by design)** | "cgprofile observer directory is not root:root: /run/cgprofile/observer; complete the host socket cutover" then "host observer group identity check failed", exit 3 -> ERROR; no container, worktree clean |
| c2 | assay self-qualification (registered lane) | `run-gate --worktree $WT self-qualification` | none started | 07:55:23-07:55:24 | **REFUSED (by design)** | same cutover refusal, exit 3 -> ERROR |
| c3 | assay `gate/tests` raw pytest (no test_self_hosting), devcontainer venv, userns fixtures built into the tests | `pytest gate/tests --ignore=gate/tests/test_self_hosting.py -p no:cacheprovider` | devcontainer | 07:43:49-07:54:42 | 4 failed (all environmental, also fail on origin/main) | 989 passed, 20 skipped, 4 failed in 651 s; all of `test_self_qualification_container.py` and `test_distribution_gate.py` PASS |
| c4 | assay touched tests/core (3 files) | `pytest tests/core/test_b148_cgroup_visibility_preflight.py tests/core/test_mutation_resource_limits.py tests/core/test_resource_observer.py` | devcontainer | 07:55:08-07:55:18 | PASS | 130 passed, 1 skipped |
| d | topos | skipped (gated by its owner, PASS on r1002 @6ef646471) | | | SKIPPED | |

## Failures and root causes

### F1 (BLOCKER, integration defect): RG-55 interface contract mirror diverged
- Failing test: `scripts/cgroup-profiler/tests/test_docs.py:89` `test_package_contract_mirrors_canonical_contract_byte_for_byte`
  (`docs/RG55-INTERFACE-CONTRACT.md` vs `run-gate-project/nyxloom-trove/RG55-INTERFACE-CONTRACT.md`).
- Passes on origin/main (the two copies are byte-identical there; verified with `cmp`). It fails here because
  the cgroup-profiler branch and the run-gate branch each edited their own copy independently and the git
  merges were textually clean (different files), so no conflict was raised. The "canonical copy is now in
  the same tree" hope did not produce a pass; the copies are not equal.
- Divergence (first diff at byte 2538; hunks at lines 39-41, 54-56, 479-497, 666, 846-871):
  - cgp copy: "cpgd/cgpctl" wording, "Socket-only carrier (D-30)", `"transports": {"exec": false, ...}`, no `exec` row, observer directory "root-owned mode 0755", Docker-GID paragraph absent.
  - run-gate copy: "`cgprofile serve`/`cgprofile ctl`", "Two carriers, one protocol (D-30)", `exec` row still present, `"exec": true` in the sample, observer `lstat` text, new Docker-GID / control-GID refusal paragraph (host Docker GID from `RUN_GATE_HOST_DOCKER_GID` then `MDT_HOST_DOCKER_GID`).
- Needs a reconciled third version applied to both copies (integration rule), then re-run a1.
- The previously known `tests/test_damon.py::test_identity_pin_closes_descriptor_when_fstat_fails` did NOT fail in this environment (passed in a1 and a2), so there is no known-unrelated failure to list for cgp.

### F2 (BLOCKER, cgroup-profiler branch defect): coverage 99%, not 100%
- `scripts/cgroup-profiler/src/cgprofile/cli/installer.py:527->529` (in `_validate_users`): the branch where a missing user is
  seen a second time with `forgotten` supplied (`if user not in forgotten:` false) is never taken. `installer.py` does not exist on
  origin/main (new in the cgp branch; the cgp branch tip `138acdbdd` has an identical tree), so this is not caused by the integration or by the
  run-gate/assay merges. Existing test `test_cgpd_installer.py:2222` covers only duplicate EXISTING users.
- Deterministic (all 3355 tests pass; one partial branch). The registered r0-r1 will also fail on this once F1 is fixed.

### F3 (environmental, not a blocker): 4 assay gate tests fail in the raw devcontainer
- `gate/tests/test_resource_limit_cgroup.py::test_tester_unified_exposes_pids_and_memory_event_counters`
- `gate/tests/test_standalone.py::test_a_real_r2_lane_kills_one_mutant_and_lets_another_survive_through_the_wheel`
- `gate/tests/test_standalone.py::test_a_real_r2_lane_diffs_the_resolved_merge_base_not_the_declared_ref`
- `gate/tests/test_standalone.py::test_a_real_r2_mutant_that_outlives_the_lane_budget_is_its_own_bucket`
- Cause: `CGROUP_OBSERVATION_UNAVAILABLE` ("the cgroup namespace hides the process's parent cgroups"): they need the observer
  endpoint inside the registered tester container. All 4 fail identically on a `git archive` of origin/main (`6b8836fad`) run in
  the same venv (4 failed in 8.5 s), so they are not caused by this change. They are expected to pass only in the registered
  tester-unified lane on a new-layout host.
- The 99 launcher failures the earlier report saw now PASS: the tests create the canonical observer path themselves inside an
  `unshare -rm` mount namespace (full `test_self_qualification_container.py` and `test_distribution_gate.py` green in c3).

### Not a failure: run-gate selftest known failures did not occur
In the registered tester-unified lane the six "known pre-existing" failures (two `TestStall*EndToEnd`, `test_a_file_owned_by_someone_else_is_refused_untouched`, three `test_tester_unified_base_variants`) did not occur: 2999 passed, 0 failed (they only appeared in the earlier raw run with `RUN_GATE_ALLOW_MISSING_TEST_DEPS=1`; that variable was not set for the registered gate).

## What cannot be demonstrated live on this cutover-pending host
- Assay registered `tester-unified` and `self-qualification` (also `-preflight`, `-provisional`, `b110-pilot`, B105/B131 launchers, which share `tools/resource-observer-gid.sh`): refuse at the first host observer identity check on this old layout (`/run/cgprofile/observer` is root:990 mode 751, no `cgprofile.sock`; the observed refusal text and exit 3 are exactly the intended new-only behavior, and the refusal is clean: no container, no residue, no old-path probe). The launcher logic itself is covered by `test_self_qualification_container.py`/`test_distribution_gate.py` (userns fixtures, c3). The inner stages (assay full `tests/` suite, R1 100% line+branch qualification, B145 probes, SQL qualification, R2/R3) did not run, so the "R1 100% / R2 mutation proof" evidence for assay is still outstanding. The assay Python sources are unchanged (tools/tests/docs only), so R1 risk is low but unproven. Also `MDT_HOST_DOCKER_GID` / `ASSAY_GATE_HOST_DOCKER_GID` are unset here; the next gate on the new-layout host needs one of them.
- Run-gate `assay-r2` and cgp `r2` (observer-enabled, hours long): not run; need the new-layout host plus a new run-gate release/pin (P-10). Run-gate observer/control preflight code is covered by the selftest unit tests and 100% diff coverage, not by a live connection.
- The 4 tests in F3 likewise.

## Recommendation: NOT READY

Blockers:
1. F1: reconcile `RG55-INTERFACE-CONTRACT.md` into one text and apply it to both `scripts/cgroup-profiler/docs/` and `run-gate-project/nyxloom-trove/` (then `test_docs.py` must pass).
2. F2: add the missing test for `installer.py:527->529` (duplicate missing user with a `forgotten` list) so cgp r0-r1 reaches 100%.
3. Re-run cgp `r0-r1` after 1 and 2 (it also has to include the coverage report step), and re-run run-gate `selftest` after 1 (the contract file is a run-gate document: confirm its docs tests and diff-coverage still pass).

Non-blocking / to do on the cutover host after the blockers are fixed: assay registered `tester-unified` + `self-qualification` (R1 100%, B145, SQL qualification, R3) with `MDT_HOST_DOCKER_GID` set, the 4 environmental tests (F3), run-gate `assay-r2` and cgp `r2` mutation, and the per-host live endpoint check.

PASS evidence that stands: run-gate `selftest` (2999/0, diff-coverage 100%), `assay-r1`, `assay-r3`; cgp `r3` and 3355 cgp tests green except the one mirror test; assay gate tests 989 passed with only the 4 pre-existing environmental failures; assay touched core tests 130 passed.
