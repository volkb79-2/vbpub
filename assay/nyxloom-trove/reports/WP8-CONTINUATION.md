# WP8 continuation — 2026-11-01

## Delivered

- Assay emits verdict schema v17. Native R2 records its effective
  `judgment.r2.jobs` count and paired `attribution`; the analyzer checks both,
  compares the count to the lane ceiling, exposes them in campaign reports,
  and refuses v16 verdicts.
- The verifier accepts v17 only. The v17 schema digest and the one-diagnostic
  v16 refusal are covered by the verdict conformance tests. R3 records
  `canary_attempt.cause`, which verification uses to re-derive the import-break
  attempt.
- Process-tree evidence uses protocol `assay-process-tree/2`. Verdict evidence
  omits host-varying `cleanup_seconds`, records the leader signal and sorted
  descendant identities, and retains B171's underlying completed suite result
  and report summary on leaked descendants. Such candidates remain `crashed`.
- Resumed records from the previous judge identity are re-executed. The v16 to
  v17 adoption guidance explains this and the unchanged Run-Gate, CMRU and
  dstdns integration contracts.
- README, DESIGN-GUIDE, CONSUMERS, schema documents and `CHANGES.md` were
  updated. Existing shard and campaign bindings remain as previously pinned;
  no RS1/RS2 fields were introduced. B180 and B202 are closed in the backlog.
  B171's implementation is complete, with registered `tester-unified`
  acceptance pending the controller. No separate B203+ finding was identified.

## Verification

- Focused v17 producer/verifier selection: **270 passed**; frozen digest and
  single-diagnostic v16 refusal included.
- B171 candidate projection test, including retained FAIL/return code and
  descendant identity while staying crashed: **1 passed**.
- AP-1 shared termination alignment oracle across producer, resume
  revalidation, verifier and verdict model: **19 passed**.
- Docs examples, cross-document anchors and public vocabulary test:
  **63 passed**.
- Focused producer, process-tree, result-report, verifier, resume, SQL
  qualification, untrusted-JSON and result-report wiring batch:
  **1,204 passed**. Its first tracked-tree inventory check raced the staged
  test-file rename; the rename was staged and the inventory check then passed.
- Run-Gate top-level-field compatibility regression: **1 passed**.
- Analysis suite with the two live R2 probes excluded: **573 passed,
  2 deselected**, with **100% line and branch coverage** for
  `assay_analysis`.
- The unfiltered analysis suite was attempted: **572 passed, 2 failed**. Both
  failures require the tester container's judge provenance and visible cgroup
  hierarchy (`CGROUP_OBSERVATION_UNAVAILABLE`).
- Pyflakes on changed Assay source, test and gate Python files passed.
  `git diff --check` passed.

No Run-Gate lane or Docker command was run; those container gates are assigned
to the controller. The full unfiltered analysis result and tester-container
live R2 probes therefore remain unverified in this cockpit.

## Gate fix round 1 — 2026-11-01

The reported collection blocker in
`tests/core/test_w10_characterization_verdict_policy_presence.py` was fixed by
adding the required native v17 attribution to its `JudgmentR2` record. The
full runs then found a second stale producer expectation in
`tests/core/test_cli_run.py::test_run_evaluates_a_real_r2_pass_end_to_end`:
the emitted policy now includes `attribution: "serial"`. That expectation was
updated and the live CLI probe passed. The installed-wheel gate fixture now
derives serial versus overlap-conservative attribution from its configured
`jobs` value.

Other full-run failures and their fixes in this WP8 round:

- Updated native `JudgmentR2` builders, dynamic constructor fields and raw v17
  verifier documents to include attribution; refreshed the B105 exclusion
  line inventory, the reviewed dataclass contract fixture, and the v16 refusal
  expectation.
- Added the v17 `canary_attempt.cause` to standalone and pilot fixtures so
  import-break receipt, absent-receipt and pre-transform shapes are represented
  honestly.
- Added `UnderlyingOutcome` to the verifier import-boundary baseline and kept
  `ReportSummary` imported through its public package surface.
- Converted `signal.SIGKILL` to a plain `int` at the process-tree evidence
  boundary, which requires exact integer wire values. Updated outliving-child
  gate fixtures with descendant identities and consistent signal counts.
- Added direct tests for malformed process-tree identities, retained
  underlying outcomes, canary cause derivation, and the corresponding raw
  verifier checks. Removed a redundant signal-state guard whose states were
  already rejected by the preceding guard.
- The B105 checker subprocess initially picked up the older estate-installed
  Assay wheel. Reinstalled the worktree-built wheel in the estate venv; the
  checker then evaluated the current v17 config.

The final full local runs used serial `nice -n19 ionice -c3` execution:

- `tests` with `--cov=src/assay --cov-branch`: **9,699 passed, 2 skipped,
  4 deselected, 1 warning** in 585.93s. Coverage was **100% lines** across
  21,709 statements and **100% branches** across 10,062 branches. The
  real-signal campaign termination module was ignored; the four deselected
  tests are the pinned Hypothesis hook probes.
- `gate/tests`, excluding `test_self_hosting.py` and the cgroup observer probe,
  and deselecting the five real R2 probes: **964 passed, 20 skipped,
  5 deselected** in 575.13s.
- `analysis/tests`, deselecting its two live R2 probes and with both `src` and
  `analysis/src` on `PYTHONPATH`: **573 passed, 2 deselected** in 26.00s.
  Coverage was **100% lines** across 2,226 statements and **100% branches**
  across 1,078 branches. The deselected probes are
  `test_o15_a_real_rejudge_run_is_counted_once_and_records_its_resumed_event`
  and `test_op5_a_real_plan_and_a_real_run_progress_project_through_the_judge_entry_point`.
  The first analysis invocation omitted `analysis/src`
  and collected no package coverage; this rerun corrected the import path.
- The touched process-tree, mutation-state, canary, runner, verdict and
  verifier modules passed together: **699 passed**. The live CLI R2 policy
  expectation passed separately: **1 passed**.
- Pyflakes and `git diff --check` passed.

No new product finding required a B203+ backlog item. No Run-Gate lane or
Docker command was run; tester-unified acceptance remains with the controller.

## Reference availability

The requested `COW-PLAN-OPUS-2026-10-30.md`,
`reports/ASSAY-OPUS-REVIEW-AND-PLAN-2026-10-31.md`, and
`codex/assay-ap-common.md` were not present in this checkout. The available
PR1b continuation independently records their absence. This implementation
used the WP8 contract in the task, the available PR1b continuation and the
B171/B180/B202 backlog entries. No waiver-window or RS1/RS2 changes were made.
